# -*- coding: utf-8 -*-
"""异步写入：日记蒸馏与事实抽取都在后台队列里做，绝不阻塞对话。

设计要点：
  · 对话路径只做两件轻活：插入 turn、必要时入队蒸馏
  · 蒸馏走单独的 asyncio 任务，失败只记日志、不抛给用户
  · distill_log 表保证同一天只蒸馏一次（幂等）
  · 写事实时做保守的"取代"判断：被推翻的旧事实只挂 stale_hint，不直接归档
"""
import asyncio
import json
import time
import traceback
from datetime import date, datetime

from . import db, llm
from .recall import Retriever

DIARY_SYS = """你在为人写一篇当天的日记，第一人称，短句，不用感叹号。

要求：
1. 写具体的事和话，不写总结、不写评价、不写"今天收获很大"这类空话。
2. 只写素材里有的内容；素材里没有的不要编。
3. **状态型事实写最终结果**：如果某件事中途变了（没到→到了、没好→好了），写成带时间点的最终状态，不要留下已被推翻的说法。
4. 400 字以内。

只输出 json：{"title":"10字内标题","diary":"正文"}"""

FACT_SYS = """从今天的材料里抽取"值得长期记住"的事实，第一人称，一条一件事。

要求：
1. 一条 15-45 字，必须带具体的物、数、名，不要抽象概括。
2. 只抽素材里明确说过的；不要推断、不要合并两件事。
3. **状态型事实写成最终状态**（例："裙子 10-27 到了，是大两号"），不要把中间状态留下来。
4. **把"今天/明天/昨天/下周"换成具体日期**再写（材料开头给了日期，自己推）。
   错误示例："师傅说明天九点半来"（明天是哪天？）
   正确示例："师傅约好 2026-10-28 九点半来修管子"
5. 最多 12 条。

只输出 json：{"facts":[{"text":"事实","keywords":["同义词","上位词","口语说法"],"importance":0.0-1.0}]}"""

ANCHOR_SYS = """从今天的材料里挑"锚"——用来在忘记时把这一天捞回来的入口。

三轴各至少一条（最多 6 条）：
- event（事）：做成的 / 发生的事，写成 **动作 + 对象**
    好："给影像资料分类上传并重命名"  坏："处理了一些文件"
- person（人）：今天真的出现的人（名字或称呼），一行一个，写在 subjects 里
- time（时）：睡前做的最后一件事，或某个具体时刻发生的事

要求：
1. 只从材料里挑，不推断、不合并。
2. 具体、可对外说出口：别人听了能接话（"那个文件夹你放哪了"）。
3. 不要抽象评价（"今天很充实"不是锚）。
4. event 允许写 1-3 条，但三轴都不能空——宁可写"没记录"也不要编。

只输出 json：
{"anchors":[{"axis":"event|person|time","text":"锚的内容","subjects":["涉及的人"],"event_time":"YYYY-MM-DD HH:MM 或空","why":"罕见点在哪（为什么这条最不可能属于别人）"}]}"""

COMBINED_SYS = """你在为人整理"今天这一天"，一次产齐三样：日记、事实、锚。

【1 日记】第一人称，短句，不用感叹号，400 字以内；写具体的事和话，不写总结与评价；
状态型事实写最终结果；素材里没有的不要编。

【2 事实】值得长期记住的事实，第一人称，一条一件事，15-45 字，必须带具体的物、数、名；
把"今天/明天/昨天"换成具体日期；最多 12 条；只抽材料里明确说过的，不推断、不合并。

【3 锚】用来在忘记时把这一天捞回来的入口，最多 6 条，三轴各至少一条：
  · event（事）：做成的/发生的事，写成"动作 + 对象"（好："给影像资料分类上传并重命名"；坏："处理了一些文件"）
  · person（人）：今天真的出现的人，只写"谁"，不写"一起做了什么"（做了什么放 event / time），名字或称呼填进 subjects
  · time（时）：睡前做的最后一件事，或某个具体时刻发生的事
  要求：具体、可对外说出口（别人听了能接话）；不要抽象评价；三轴都不能空，宁可写"没记录"也不要编。

只输出这一个 json（三样都在里面）：
{"title":"10字内标题",
 "diary":"日记正文",
 "facts":[{"text":"事实","keywords":["同义词"],"importance":0.0-1.0}],
 "anchors":[{"axis":"event|person|time","text":"锚的内容","subjects":["涉及的人"],"event_time":"YYYY-MM-DD HH:MM 或空","why":"罕见点在哪"}]}"""

MISSING_AXIS_SYS = """你只补一种东西：今天的"锚"里缺掉的那几轴。

要求：只输出缺的那几轴，每条具体、可对外说出口，形式是"动作+对象"或"具体的人"；
不推断、不合并、不编；没有就输出空数组。

只输出 json：{"anchors":[{"axis":"event|person|time","text":"...","subjects":[...],"event_time":"","why":"..."}]}"""


def _material(rows, limit: int = 12000) -> str:
    """把一天的对话拼成材料。

    2026-10-31 实测的坑：旧实现是 join(...)[:6000]，**从头切**——
    一天的材料超过上限时，丢掉的正好是当天后面的对话（晚上下的棋、睡前的话），
    所以"关键事全丢"不是模型没抽到，是根本没喂进去。
    现在改成**按时间均匀取样**：头、中、尾都保住，最后一条一定在里面。
    """
    lines = [f"{r['role']}: {r['content']}" for r in rows]
    total = sum(len(x) for x in lines)
    if total <= limit:
        return "\n".join(lines)

    avg = total / max(len(lines), 1)
    want = max(3, int(limit / max(avg, 1)))
    n = min(want, len(lines))
    if n >= len(lines):
        idxs = list(range(len(lines)))
    else:
        id1 = len(lines) - 1
        idxs = sorted({round(i * id1 / max(n - 1, 1)) for i in range(n)})
        if id1 not in idxs:
            idxs.append(id1)
    keep = [lines[i] for i in idxs]
    # 取样后仍可能略超：从中段往外删，保住头尾（头是开场，尾是睡前）
    while len("\n".join(keep)) > limit and len(keep) > 3:
        keep.pop(len(keep) // 2)
    return "\n".join(keep)



class Distiller:
    """后台蒸馏器。用完调 await drain() 等它跑完（测试/退出时）。"""

    def __init__(self, con, db_path: str):
        self.con = con
        self.db_path = db_path
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pending = []          # 没有事件循环时先排队
        self._worker = None
        self._retr = None

    # ---- 对外 ----
    def schedule(self, day: str) -> None:
        """交给后台。没有运行中的事件循环就只排队，等 drain() 再跑。"""
        self.pending.append(day)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                      # 同步上下文：排着
        if loop.is_running():
            self._ensure_worker()

    def _ensure_worker(self):
        for d in self.pending:
            self.queue.put_nowait(d)
        self.pending.clear()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._loop())

    async def drain(self) -> None:
        self._ensure_worker()
        await self.queue.join()
        await asyncio.sleep(0)

    # ---- 内部 ----
    async def _loop(self) -> None:
        while True:
            day = await self.queue.get()
            try:
                await self._distill_day(day)
            except Exception:
                if _verbose():
                    traceback.print_exc()
            finally:
                self.queue.task_done()

    async def _distill_day(self, day: str) -> None:
        # 幂等：已经蒸馏过就跳过
        done = self.con.execute(
            "select 1 from distill_log where day=? and stage='facts' and ok=1", (day,)).fetchone()
        if done:
            return

        lo, hi = _day_range(day)
        rows = self.con.execute(
            "select role, content from turns where ts>=? and ts<? order by ts", (lo, hi)).fetchall()
        if not rows:
            return
        material = _material(rows)

        # 一次调用产齐三样（日记 / 事实 / 锚）。
        # 2026-10-31 改：原来是三次调用喂同一份材料，同样的输入 token 付了三遍。
        try:
            data = await asyncio.to_thread(
                llm.chat_json,
                [{"role": "system", "content": COMBINED_SYS},
                 {"role": "user", "content": f"日期：{day}\n材料：\n{material}"}],
                temperature=0.5, max_tokens=2600)
        except Exception as e:
            self._log(day, "diary", 0, f"合并调用失败: {str(e)[:180]}")
            return

        # 1) 日记
        title = (data.get("title") or "").strip()
        diary = (data.get("diary") or "").strip()
        if not diary:
            self._log(day, "diary", 0, "模型没给日记正文")
            return
        day_id = self._upsert_day(day, title, diary)
        self._log(day, "diary", 1, f"{len(diary)} chars")

        # 2) 事实
        facts = data.get("facts") or []
        try:
            added = self._insert_facts(day, day_id, facts)
            self._log(day, "facts", 1, f"{added} facts")
        except Exception as e:
            self._log(day, "facts", 0, str(e)[:200])

        # 3) 锚点：三轴各留一个，用来把这一天捞回来
        picked = data.get("anchors") or []
        n_anchor = 0
        if picked:
            try:
                n_anchor = self._insert_anchors(day, picked)
                self._log(day, "anchors", 1, f"{n_anchor} anchors")
            except Exception as e:
                self._log(day, "anchors", 0, str(e)[:200])

        # 三轴自检：缺一轴就单独补抽那一轴（一次小调用，比整天重跑便宜）
        try:
            n_anchor += await self._fill_missing_axes(day, material)
        except Exception as e:
            self._log(day, "anchors_topup", 0, str(e)[:200])

        if self._retr is not None:
            self._retr.invalidate("fact")
            self._retr.invalidate("day")

    async def _fill_missing_axes(self, day: str, material: str) -> int:
        """三轴缺哪补哪。返回补了几条。"""
        from . import anchors as A
        cov = A.coverage(self.con, day)
        missing = [ax for ax, ok in cov.items() if not ok]
        if not missing:
            return 0
        sys_prompt = MISSING_AXIS_SYS
        data = await asyncio.to_thread(
            llm.chat_json,
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": f"日期：{day}\n只要这几轴：{missing}\n材料：\n{material}"}],
            temperature=0.3, max_tokens=600)
        picked = [a for a in (data.get("anchors") or [])
                  if str(a.get("axis", "")).strip().lower() in missing]
        return self._insert_anchors(day, picked) if picked else 0

    # ---- 写锚（三轴）----
    def _insert_anchors(self, day: str, picked) -> int:
        from . import anchors as A
        got = A.of_day(self.con, day)
        # 同一轴已有锚就不重复插（幂等重跑时不会翻倍）
        have = {ax: [r["text"] for r in items] for ax, items in got.items()}
        n = 0
        for it in picked:
            if not isinstance(it, dict):
                continue
            axis = str(it.get("axis") or "").strip().lower()
            text = str(it.get("text") or "").strip()
            if axis not in A.AXES or not text:
                continue
            if text in have.get(axis, []):
                continue
            subs = it.get("subjects") or []
            if isinstance(subs, str):
                subs = [subs]
            A.insert_anchor(self.con, day, axis, text,
                            subjects=[str(s) for s in subs if str(s).strip()],
                            event_time=str(it.get("event_time") or ""),
                            source="distill", why=str(it.get("why") or ""))
            n += 1
        if n:
            A.recompute_rarity(self.con)     # 罕见度要在有参照物之后重算，否则全是 1.0
        return n

    # ---- 写库（同步、短事务）----
    def _upsert_day(self, day: str, title: str, diary: str) -> int:
        d = datetime.strptime(day, "%Y-%m-%d")
        y_id = db.upsert_year(self.con, str(d.year))
        m_id = db.upsert_month(self.con, d.strftime("%Y-%m"), y_id)
        w_id = db.upsert_week(self.con, d.strftime("%G-W%V"), m_id)
        self.con.execute("begin immediate")
        self.con.execute(
            """insert into days(day, title, diary, year_id, month_id, week_id, created_at)
               values(?,?,?,?,?,?,?)
               on conflict(day) do update set title=excluded.title, diary=excluded.diary""",
            (day, title, diary, y_id, m_id, w_id, time.time()))
        self.con.execute("commit")
        r = self.con.execute("select id from days where day=?", (day,)).fetchone()
        return r["id"]

    def _insert_facts(self, day: str, day_id: int, facts: list) -> int:
        """写事实。同一天里内容重复的直接跳过——这样"一天内多次重蒸"不会翻倍。

        2026-11-01 加：她要改成"每 20 轮蒸一次"，一天会蒸好几遍，
        旧实现每次都 insert，同一件事会重复落好几条。
        """
        added = 0
        self.con.execute("begin immediate")
        try:
            for f in facts:
                text = (f.get("text") or "").strip()
                if not text:
                    continue
                # 只挡"同一天里已经有一条 active 的同文本"；
                # 若旧那条已被 superseded，允许重新写（改过又改回来是合法情形）
                dup = self.con.execute(
                    "select 1 from facts where day_id=? and content=? and status='active' limit 1",
                    (day_id, text)).fetchone()
                if dup:
                    continue
                kws = f.get("keywords") or []
                imp = float(f.get("importance", 0.8) or 0.8)
                self.con.execute(
                    """insert into facts(day_id, content, keywords, event_time,
                                         importance, status, source, created_at)
                       values(?,?,?,?,?,'active','distill',?)""",
                    (day_id, text, json.dumps(kws, ensure_ascii=False), day, imp, time.time()))
                added += 1
            self.con.execute("commit")
        except Exception:
            self.con.execute("rollback")
            raise
        return added

    def _log(self, day: str, stage: str, ok: int, note: str) -> None:
        self.con.execute(
            "insert or replace into distill_log(day, stage, ok, note, ts) values(?,?,?,?,?)",
            (day, stage, ok, note, time.time()))


def _day_range(day: str):
    d = datetime.strptime(day, "%Y-%m-%d")
    lo = d.timestamp()
    return lo, lo + 86400


def mark_superseded(con, new_fact_id: int, new_text: str, keywords: list) -> list:
    """保守的取代判断：共享字 bigram ≥ 4 的旧事实打 stale_hint（不删）。

    为什么不像常见方案那样直接归档：归档=真的忘了，判错的代价不可逆。
    """
    import re
    def grams(t):
        t = re.sub(r"[^\w\u4e00-\u9fff]", " ", t)
        out = set()
        for seg in t.split():
            if re.fullmatch(r"[\u4e00-\u9fff]+", seg):
                out |= {seg[i:i + 2] for i in range(len(seg) - 1)}
            elif len(seg) >= 2:
                out.add(seg)
        return out

    g_new = grams(new_text) | {str(k) for k in keywords}
    rows = con.execute(
        "select id, content from facts where status='active' and id!=? order by id desc limit 200",
        (new_fact_id,)).fetchall()
    hinted = []
    for r in rows:
        shared = g_new & grams(r["content"])
        if len(shared) >= 4:
            con.execute("update facts set stale_hint=1, superseded_by=? where id=?",
                        (new_fact_id, r["id"]))
            hinted.append(r["id"])
    return hinted

def _verbose() -> bool:
    """读 config.VERBOSE；当 config 不在 sys.path（例如被 pip 安装）时默认 False。"""
    try:
        from . import config_defaults as _c
        import config as _user  # noqa: WPS433
        _c = _user
    except Exception:
        from . import config_defaults as _c
    return bool(getattr(_c, 'VERBOSE', False))
