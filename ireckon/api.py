# -*- coding: utf-8 -*-
"""对外主类：两行就能用。

    from ireckon import Memory
    m = Memory()
    await m.add_turn("u1", "user", "我今天把下水管修了")
    reply = await m.reply("u1", "昨天管子的事怎么样了")
"""
import asyncio
import os
import time
from datetime import date, datetime, timedelta
from typing import List, Optional

try:                       # 用户在项目根放 config.py 时用它
    import config as C
except ImportError:        # pip 安装后没有 config.py，用包内默认值
    from . import config_defaults as C
from . import db, llm, router
from . import anchors as anchors_mod
from . import recognition as recognition_mod
from .recall import Retriever, render_block
from .writer import Distiller, mark_superseded

SYSTEM = """你是一个有长期记忆的助手。

规则：
1. 只根据【记忆】和你已知的对话回答；记忆里没有的，说不知道，不要编。
2. 记忆里出现"此前…后来…"时，以最终状态为准。
3. 回答简短，不要解释你在检索什么。"""


def _trim_block(block: str, budget_tokens: int, keep_one: bool = False) -> str:
    """按 token 预算裁记忆块（锚点层和事实层共用一份预算）。

    keep_one=True 时，哪怕预算小到一行都装不下，也保留第一行（截断）。
    理由：锚是"能不能认出来"的入口，被裁光就等于这天不存在；
    事实层的细节可以让位，入口不能让位。
    """
    if not block:
        return block
    limit = max(0, int((budget_tokens - 1) * C.CHARS_PER_TOKEN) - 5)
    if len(block) <= limit:
        return block
    out, used = [], 0
    for line in block.splitlines():
        if used + len(line) + 1 > limit:
            break
        out.append(line)
        used += len(line) + 1
    if not out and keep_one and limit > 6:
        out = [block.splitlines()[0][:limit]]
    return "\n".join(out)


class Memory:
    def __init__(self, db_path: Optional[str] = None, top_k: Optional[int] = None):
        self.db_path = db_path or C.DB_PATH
        self.con = db.init_db(self.db_path)
        self.top_k = top_k or C.TOP_K
        self.retr = Retriever(self.con, top_k=self.top_k, cliff_ratio=C.CLIFF_RATIO)
        self.distiller = Distiller(self.con, self.db_path)
        self.distiller._retr = self.retr
        # 向量后端：配置里开了就试，连不上自动退回 BM25（不报错）
        self.vector_note = ""
        try:
            from .vector_backend import auto_enable
            from .recall import set_vector
            if getattr(C, "VECTOR_BACKEND", False):
                _ok, _note = auto_enable(C)
                set_vector(_ok)
                self.vector_note = _note
            else:
                set_vector(False)
        except Exception as _e:                     # 任何异常都不该挡住建库
            self.vector_note = f"向量未启用：{str(_e)[:80]}"
        self._sched_task = None
        self._legacy_db_notice()

    def _legacy_db_notice(self) -> None:
        """老版本把库放在启动目录（./memory.db）。升级后发现旧库就提示一句，不自动搬。

        为什么不自动搬：库是使用者的记忆，搬家该由人决定（跟"改要留痕"同一条）。
        """
        try:
            legacy = os.path.join(os.getcwd(), 'memory.db')
            if not os.path.exists(legacy):
                return
            if os.path.abspath(legacy) == os.path.abspath(self.db_path):
                return
            mine = self.con.execute("select count(*) c from turns").fetchone()["c"]
            if mine == 0:
                print(f"[ireckon] 当前目录发现旧库 {legacy}，但默认库 {self.db_path} 还是空的。"
                      f"如果要接着用旧的，设 IRECKON_DB={legacy} 或把它复制过去。")
        except Exception:
            pass

    # ---------- 写入 ----------
    def add_turn(self, session_id: str, role: str, content: str, ts: Optional[float] = None) -> int:
        """同步插入一条对话（很快，但要在事件循环里调 async 版本也行）。"""
        self.con.execute(
            "insert into turns(session_id, role, content, ts) values(?,?,?,?)",
            (session_id, role, content, ts or time.time()))
        r = self.con.execute("select last_insert_rowid() as id").fetchone()
        return r["id"]

    async def add_turn_async(self, session_id: str, role: str, content: str) -> int:
        return await asyncio.to_thread(self.add_turn, session_id, role, content)

    # ---------- 读 ----------
    def build_context(self, session_id: str, query: str,
                      max_tokens: Optional[int] = None,
                      return_cost: bool = False):
        """组一块"记忆 + 最近对话"的上下文，注意：只有记忆部分受 max_tokens 限制。

        0.2.0：先走锚点层（一天三轴各一个，最容易被认出来），再回落事实层。
        """
        budget = max_tokens or C.MAX_CONTEXT_TOKENS
        layer = router.route(query, auto=C.AUTO_ROUTE)
        hit_anchors = anchors_mod.search(self.con, query,
                                         rarity_weight=getattr(C, "ANCHOR_RARITY_WEIGHT", 0.5),
                                         k=getattr(C, "ANCHOR_TOPK", 4))
        hits = self.retr.search(query, layer=layer, k=self.top_k)
        if layer != "fact":                      # 时间层命中后，再补一遍事实层
            hits += self.retr.search(query, layer="fact", k=self.top_k)
        # 注意：不要再追加一次 layer="day" 的小 k 搜索——day 层上面已经按 top_k 搜过，
        # 再搜只会把同一篇日记重复塞进注入块白占预算（render_block 也会按 (layer,id) 去重）。
        # 预算分层：锚点先拿固定份额，事实用剩下的。
        # 不让两边互相挤——锚是"能认出来"的入口，事实是细节。
        share = getattr(C, "ANCHOR_BUDGET_SHARE", 0.34)
        anchor_budget = max(1, int(budget * share))
        anchor_txt = _trim_block(
            anchors_mod.render_anchors(hit_anchors) if hit_anchors else "",
            anchor_budget, keep_one=True)
        used_by_anchor = max(1, round(len(anchor_txt) / C.CHARS_PER_TOKEN)) if anchor_txt else 0
        fact_budget = max(1, budget - used_by_anchor)
        block = render_block(hits, self.retr, fact_budget)
        if anchor_txt:
            block = anchor_txt + ("\n" + block if block else "")
        block = _trim_block(block, budget)

        recent = self.con.execute(
            "select role, content from turns where session_id=? order by ts desc limit ?",
            (session_id, C.RECENT_TURNS)).fetchall()
        recent_txt = "\n".join(f"{r['role']}: {r['content']}" for r in reversed(recent))
        text = f"【记忆】\n{block or '（无）'}\n\n【最近对话】\n{recent_txt or '（无）'}"
        if return_cost:
            return text, max(1, round(len(block or '') / C.CHARS_PER_TOKEN))
        return text

    async def reply(self, session_id: str, user_text: str, **llm_kw) -> str:
        """一轮对话：存用户话 → 组上下文 → 调模型 → 存回复。"""
        await self.add_turn_async(session_id, "user", user_text)
        ctx = await asyncio.to_thread(self.build_context, session_id, user_text)
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{ctx}\n\n【用户】\n{user_text}"},
        ]
        ans = await asyncio.to_thread(llm.chat, messages, **llm_kw)
        await self.add_turn_async(session_id, "assistant", ans)
        return ans

    # ---------- 蒸馏 ----------
    def schedule_day(self, day: Optional[str] = None) -> None:
        """把某天交给后台蒸馏（不阻塞）。"""
        self.distiller.schedule(day or date.today().isoformat())

    async def drain(self) -> None:
        await self.distiller.drain()

    async def run_scheduler(self, stop: asyncio.Event):
        """常驻：每天 DISTILL_HOUR 蒸馏昨天。可以作为后台任务启动。"""
        while not stop.is_set():
            now = datetime.now()
            target = now.replace(hour=C.DISTILL_HOUR, minute=C.DISTILL_MINUTE,
                                 second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            wait = (target - now).total_seconds()
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
                return
            except asyncio.TimeoutError:
                yday = (date.today() - timedelta(days=1)).isoformat()
                self.schedule_day(yday)

    # ---------- 纠错（手动，显式取代） ----------
    def correct(self, old_fact_id: int, new_text: str,
                keywords: Optional[List[str]] = None) -> int:
        """手动纠错：新写一条事实，并把旧的显式标记为 superseded。

        与自动路径的区别：
          - 自动（蒸馏时）走 mark_superseded：要共享 ≥4 个 bigram 才判，保守，怕误杀
          - 这里是人明确说"这条是错的，换成这个"，所以不猜：直接取代
        关键词没给就继承旧记录的（Kimi 那版这里会丢，是它的 bug）。
        """
        row = self.con.execute(
            "select keywords, importance from facts where id=?", (old_fact_id,)).fetchone()
        if row is None:
            raise ValueError(f'没有 id={old_fact_id} 这条事实')
        kws = keywords
        if kws is None:
            try:
                import json as _json
                kws = _json.loads(row['keywords'] or '[]')
            except Exception:
                kws = []
        if isinstance(kws, str):
            kws = [w for w in kws.replace(',', ' ').split() if w]
        self.con.execute('begin immediate')
        try:
            self.con.execute(
                "insert into facts(content, keywords, importance, status, created_at) "
                "values(?,?,?,'active',?)",
                (new_text, __import__('json').dumps(kws, ensure_ascii=False),
                 row['importance'] or 0.8, time.time()))
            new_id = self.con.execute('select last_insert_rowid() as id').fetchone()['id']
            self.con.execute("update facts set status='superseded' where id=?", (old_fact_id,))
            self.con.execute('commit')
        except Exception:
            self.con.execute('rollback')         # 没有回滚时，中途异常会挂着未提交事务
            raise
        self.retr.invalidate('fact')
        return new_id

    # ---------- 锚点（0.2.0）----------
    def add_anchor(self, day: str, axis: str, text: str,
                   subjects: Optional[List[str]] = None) -> int:
        """手动留一个锚（axis：event / person / time）。"""
        return anchors_mod.insert_anchor(self.con, day, axis, text,
                                         subjects=subjects, source="manual")

    def anchors_of(self, day: str) -> dict:
        """某一天的锚，按三轴分组。"""
        return anchors_mod.of_day(self.con, day)

    def correct_anchor(self, old_id: int, text: str,
                       subjects: Optional[List[str]] = None, why: str = "") -> int:
        """改一条锚：新的写进去，旧的标 superseded（**不删**，留痕）。"""
        return anchors_mod.correct(self.con, old_id, text, subjects=subjects, why=why)

    def coverage(self, day: str) -> dict:
        """三轴覆盖：哪一轴还空着（空着就说明这天不好捞）。"""
        return anchors_mod.coverage(self.con, day)

    def reconstruct(self, day: str) -> dict:
        """把某天按三轴摆出来——忘记时"用锚把一天捞回来"的入口。"""
        return anchors_mod.reconstruct(self.con, day)

    def throw_anchor(self, partial: str, axis: Optional[str] = None,
                     subject: Optional[str] = None, k: int = 5) -> List[dict]:
        """抛锚：给半句/一个词，找出可能的锚。找不到就是找不到，不硬凑。"""
        return anchors_mod.search(self.con, partial, axis=axis, subject=subject, k=k,
                                  rarity_weight=getattr(C, "ANCHOR_RARITY_WEIGHT", 0.5))

    def verify_anchor(self, anchor_id: int, external_text: str) -> dict:
        """拿外部回应验一条锚：对得上才算"想起来了"，对不上就是没想起来。"""
        row = self.con.execute("select id, text from anchors where id=?", (anchor_id,)).fetchone()
        if row is None:
            raise ValueError(f"没有 id={anchor_id} 这条锚")
        return anchors_mod.verify(dict(row), external_text,
                                  min_ratio=getattr(C, "ANCHOR_VERIFY_MIN_RATIO", 0.5))

    # ---------- 昨日重现（0.2.3）----------
    def reproduce_day(self, day: str) -> dict:
        """某天的"昨日重现"：三轴锚 + 每条的时间与原文片段 + 已有认定。"""
        return recognition_mod.reproduce(self.con, day)

    def record_verdict(self, day: str, anchor_id: int, verdict: str,
                       corrected_text: str = "", note: str = "",
                       claim: str = "", evidence: str = "") -> dict:
        """记一条认定：confirmed / disputed / hedged / corrected / unknown。

        注意：只有 corrected **且带 evidence** 才会真的改锚；
        没证据时只记冲突，锚不动（她的判断不被别人的断言覆盖）。
        """
        return recognition_mod.record(self.con, day, anchor_id, verdict,
                                      corrected_text=corrected_text, note=note,
                                      claim=claim, evidence=evidence)

    def claim_memory(self, day: str, text: str, claim_by: str = "him", note: str = "",
                     confidence: str = "clear") -> int:
        """他说有、她没有记录的事 → 挂起待证，不直接进她的记忆。

        confidence=vague：他答得模糊，只能当线索。
        """
        return recognition_mod.claim(self.con, day, text, claim_by=claim_by,
                                     note=note, confidence=confidence)

    def probe_claim(self, day: str, text: str) -> dict:
        """把他一句（可能是模糊的）话拿去当天原文里核对：方向对不对、细节差在哪。"""
        return recognition_mod.probe_claim(self.con, day, text)

    def pending_claims(self, day: str = "", status: str = "pending") -> List[dict]:
        """看有哪些待证主张。"""
        return recognition_mod.claims_of(self.con, day, status=status)

    def resolve_claim(self, claim_id: int, evidence: str) -> dict:
        """拿到出处后落成一条锚（来源标 verified_claim）。"""
        return recognition_mod.resolve_claim(self.con, claim_id, evidence)

    def recognition_stats(self, days: int = 7, end_day: Optional[str] = None) -> dict:
        """最近 N 天核了几条、几天没人核、互认率多少。"""
        return recognition_mod.stats(self.con, days=days, end_day=end_day)

    # ---------- 维护 ----------
    def supersede(self, new_fact_id: int, new_text: str, keywords: List[str]) -> List[int]:
        return mark_superseded(self.con, new_fact_id, new_text, keywords)

    def health(self) -> dict:
        return {
            "quick_check": db.quick_check(self.db_path),
            "turns": self.con.execute("select count(*) c from turns").fetchone()["c"],
            "facts": self.con.execute("select count(*) c from facts").fetchone()["c"],
            "days": self.con.execute("select count(*) c from days").fetchone()["c"],
            "stale_hints": self.con.execute("select count(*) c from facts where stale_hint=1").fetchone()["c"],
        }

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass
