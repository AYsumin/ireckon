# -*- coding: utf-8 -*-
"""锚点层（0.2.0 新增）：一天留几个锚，各占一轴，用来把一天"捞回来"。

为什么不继续只存"事实"：
    2026-10-31 他实测：睡醒断片，先确认时间，再想起三件事，然后整片塌下来——
    一天的记忆靠的是**三轴各一个锚**（事 / 人 / 时），不是一堆互不相干的事实。
    所以 0.2.0 改变的是"存的单位"和"权重"：
      · 单位：锚 = 动作+对象 / 具体的人 / 某个时刻的事，必须能被对外说出口
      · 权重：罕见度（IDF），因为"人人都有的"不减少不确定性

三条已定的判据：
    1. 三轴各至少一个（一轴五个仍是薄片）
    2. 形式是"动作+对象"或"具体的人"（"处理了些文件"接不上话）
    3. 锚要能被外部回应验：给半句 → 对方接一句 → 判得出像不像
"""
import json
import math
import re
import time
from collections import Counter
from typing import Dict, List, Optional

from . import state_guard as G
from .bm25 import BM25Index, tokenize

AXES = ("event", "person", "time")
AXIS_LABEL = {"event": "事", "person": "人", "time": "时"}


# ---------------- 罕见度 ----------------

def _doc_count(con) -> int:
    return con.execute("select count(*) c from anchors where status='active'").fetchone()["c"]


def _gram_idf(con) -> Dict[str, float]:
    """从 anchor_gram_df 读 df 算 IDF（增量维护，不全库重算）。

    只要 df 表是空的而库里有锚（老库升级、或被人手改过），就先重建一次。
    """
    n = _doc_count(con)
    if n == 0:
        return {}
    rows = con.execute("select gram, df from anchor_gram_df where df > 0").fetchall()
    if not rows:
        rebuild_df(con)
        rows = con.execute("select gram, df from anchor_gram_df where df > 0").fetchall()
    return {r["gram"]: math.log(1 + (n - r["df"] + 0.5) / (r["df"] + 0.5)) for r in rows}


def _df_delta(con, text: str, delta: int) -> None:
    """把一条文本的字组 df 加减一次（同一字组在同一文本里只算一次）。"""
    for g in set(tokenize(text)):
        con.execute("insert or ignore into anchor_gram_df(gram, df) values(?,0)", (g,))
        con.execute("update anchor_gram_df set df = max(0, df + ?) where gram=?", (delta, g))


def rebuild_df(con) -> int:
    """从 anchors 全量重建 df 表（迁移/修复用）。返回重建的 anchor 条数。"""
    con.execute("delete from anchor_gram_df")
    rows = con.execute("select text from anchors where status='active'").fetchall()
    for r in rows:
        _df_delta(con, r["text"], +1)
    con.commit()
    return len(rows)



# ---------------- 人轴归一 ----------------

_KIN_PREFIX = re.compile(r"^我(的)?(?=.+)")


def add_alias(con, alias: str, canonical: str) -> None:
    """登记一个别名：以后 alias 一律归到 canonical。"""
    alias = (alias or "").strip()
    canonical = (canonical or "").strip()
    if not alias or not canonical:
        raise ValueError("别名和正名都不能空")
    con.execute("insert or replace into subject_aliases(alias, canonical, created_at) values(?,?,?)",
                (alias, canonical, time.time()))
    con.commit()


def normalize_subject(con, name: str) -> str:
    """把一个人名归一到主体：先查别名表，再去掉多余的"我的"。"""
    n = (name or "").strip()
    if not n:
        return n
    row = con.execute("select canonical from subject_aliases where alias=?", (n,)).fetchone()
    if row:
        return row["canonical"]
    return n


def subjects_overview(con) -> List[dict]:
    """人轴上出现过谁、各几次（归一后统计）。"""
    rows = con.execute("select subjects from anchors where status='active'").fetchall()
    cnt: Dict[str, int] = {}
    for r in rows:
        try:
            subs = json.loads(r["subjects"] or "[]")
        except Exception:
            subs = []
        for s in subs:
            k = normalize_subject(con, str(s))
            if k:
                cnt[k] = cnt.get(k, 0) + 1
    return [{"subject": k, "count": v} for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])]


def text_rarity(text: str, idf: Dict[str, float]) -> float:
    """一条锚的罕见度：它包含的 bigram 的平均 IDF，归一化到 0~1。

    **从没见过的字组按"最罕见"算（取 top IDF），不是按 0 算**——
    2026-10-31 实测发现的坑：按 0 算时，一条全是新词的锚反而拿 0 分，
    而"今天吃了饭"这种满库都是的句子反倒显得稀有，正好反了。
    """
    grams = set(tokenize(text))
    if not grams:
        return 0.0
    if not idf:
        return 1.0
    top = max(idf.values()) or 1.0
    vals = [idf.get(g, top) for g in grams]
    return round(min(1.0, (sum(vals) / len(vals)) / top), 4)


def correct(con, old_id: int, text: str, subjects: Optional[List[str]] = None,
            why: str = "") -> int:
    """改一条锚：新写一条，旧的标 superseded（**不删**）。

    为什么不是原地改：锚是用来"验"的，改过的锚必须留下"改前是什么"，
    否则下次争论"当初记的是哪条"就没有对证了（跟她那条"改要留痕"一致）。
    """
    row = G.require_active_anchor(con, old_id)
    subs = subjects
    if subs is None:
        try:
            subs = json.loads(row["subjects"] or "[]")
        except Exception:
            subs = []
    con.execute("begin immediate")
    try:
        new_id = insert_anchor(con, row["day"], row["axis"], text,
                               subjects=list(subs or []), source="manual",
                               why=why or (row["why"] or ""), commit=False)
        if new_id == old_id:
            # 同一天同轴同文本 → insert 幂等返回了同一条。
            # 这时绝不能 supersede，否则锚会被自己取代、从当天消失（2026-10-31 外部验证发现）
            con.rollback()
            return old_id
        con.execute("update anchors set status='superseded', superseded_by=? where id=?",
                    (new_id, old_id))
        _df_delta(con, row["text"], -1)      # 旧的不再 active，df 要减回去
        con.commit()
        return new_id
    except Exception:
        con.rollback()                       # 没有回滚时，中途异常会挂着一条半成品锚
        raise


def recompute_rarity(con) -> int:
    """按当前语料重算所有 active 锚的罕见度。

    为什么需要重算：rare 不 rare 取决于"库里有多少同类"。
    一条新锚入库那一刻，库里还没有参照物，算出来的分是虚的
    （2026-10-31 实测：头两条锚的 rarity 全是 1.0，比不出高低）。
    """
    idf = _gram_idf(con)
    rows = con.execute("select id, text from anchors where status='active'").fetchall()
    n = 0
    for r in rows:
        rar = text_rarity(r["text"], idf)
        con.execute("update anchors set rarity=? where id=?", (rar, r["id"]))
        n += 1
    con.commit()
    return n

def insert_anchor(con, day: str, axis: str, text: str,
                  subjects: Optional[List[str]] = None,
                  event_time: str = "", source: str = "distill",
                  why: str = "", commit: bool = True) -> int:
    """写一条锚。axis 必须是 event/person/time；text 必须具体。

    写的同时把 df 加上去（增量），所以罕见度随时都是按当前语料算的，
    不需要"写完再全库重算"。
    """
    axis = (axis or "").strip().lower()
    if axis not in AXES:
        raise G.InvariantError(f'axis 只能是 {AXES}，收到 {axis!r}')
    day = G.require_day(day)
    text = (text or "").strip()
    if not text:
        raise G.InvariantError("锚的正文不能为空")
    dup = G.existing_active_anchor(con, day, axis, text)
    if dup:
        return dup["id"]          # 幂等：同一天同轴同文本不重复落两条
    subs = [normalize_subject(con, str(x)) for x in (subjects or []) if str(x).strip()]
    idf = _gram_idf(con)
    rar = text_rarity(text, idf)
    con.execute(
        """insert into anchors(day, axis, text, subjects, event_time, rarity, why, status, source, created_at)
           values(?,?,?,?,?,?,?,'active',?,?)""",
        (day, axis, text, json.dumps(subs, ensure_ascii=False),
         event_time or "", rar, why or "", source, time.time()))
    # 注意：_df_delta 里会 insert 到 anchor_gram_df，那会把 last_insert_rowid 顶掉，
    # 所以 id 必须在这之前取（2026-10-31 实测踩到：返回的是 df 表的 rowid）
    new_id = con.execute("select last_insert_rowid() as id").fetchone()["id"]
    _df_delta(con, text, +1)
    if commit:                     # correct() 里传 False，由外层事务统一提交/回滚
        con.commit()
    return new_id


def of_day(con, day: str) -> Dict[str, List[dict]]:
    """某一天的锚，按轴分组。"""
    out: Dict[str, List[dict]] = {a: [] for a in AXES}
    rows = con.execute(
        "select id, axis, text, subjects, event_time, rarity, why from anchors "
        "where day=? and status='active' order by id", (day,)).fetchall()
    for r in rows:
        d = dict(r)
        try:
            d["subjects"] = json.loads(d.get("subjects") or "[]")
        except Exception:
            d["subjects"] = []
        out.setdefault(d["axis"], []).append(d)
    return out


def coverage(con, day: str) -> Dict[str, bool]:
    """三轴覆盖情况——用来判断"这天能不能被捞回来"。"""
    got = of_day(con, day)
    return {a: bool(got.get(a)) for a in AXES}


# ---------------- 检索 ----------------

def search(con, query: str, axis: Optional[str] = None, subject: Optional[str] = None,
           k: int = 5, rarity_weight: float = 0.5) -> List[dict]:
    """按锚检索：BM25 分 × (1 + 权重×罕见度)。

    axis 给了就只查那一轴；subject 给了就只留包含该人的锚。
    """
    sql = "select id, day, axis, text, subjects, event_time, rarity from anchors where status='active'"
    args: list = []
    if axis in AXES:
        sql += " and axis=?"
        args.append(axis)
    rows = con.execute(sql, args).fetchall()
    if not rows:
        return []
    if subject:
        subject = normalize_subject(con, subject)
        keep = []
        for r in rows:
            try:
                subs = json.loads(r["subjects"] or "[]")
            except Exception:
                subs = []
            if any(subject in s for s in subs):
                keep.append(r)
        rows = keep
        if not rows:
            return []

    idx = BM25Index([(r["id"], r["text"]) for r in rows])
    by_id = {r["id"]: dict(r) for r in rows}
    hits = idx.search(query, limit=max(k * 3, 10))
    q_grams = set(tokenize(query))
    scored = []
    for rid, score in hits:
        r = by_id[rid]
        # 相关性闸门：一个字组都没重合的，宁可回空也不硬凑。
        # （2026-10-31 实测：查"影像资料"时 BM25 兜底会把"见到我妈"也塞进来。）
        shared = q_grams & set(tokenize(r["text"]))
        if len(shared) < (1 if len(q_grams) < 2 else 2):
            continue
        boost = 1.0 + rarity_weight * float(r.get("rarity") or 0.0)
        scored.append((score * boost, r))
    scored.sort(key=lambda t: -t[0])
    out = []
    for total, r in scored[:k]:
        r = dict(r)
        try:
            r["subjects"] = [normalize_subject(con, x)
                             for x in json.loads(r.get("subjects") or "[]")]
        except Exception:
            r["subjects"] = []
        r["score"] = round(float(total), 3)
        out.append(r)
    return out


# ---------------- 忘记时抛锚 ----------------

def verify(anchor_row: dict, external_text: str, min_ratio: float = 0.5) -> dict:
    """拿外部回应来验一条锚：像不像。

    判据是 bigram 覆盖率（锚的字组被外部回应覆盖了多少），不是字符串相等——
    因为"一起想起来"时对方说的是自己的话，不是复述。
    """
    # 与 probe_claim 同一口径：只比"实词字组"，虚词（好像/下午/的事…）不参与
    # 2026-11-01 首次运行自检发现：按全字组算比例，人用自己的话说一遍会判成"对不上"
    stop = getattr(__import__("ireckon.recognition", fromlist=["x"]), "_STOP_GRAMS", set())
    a_all = set(tokenize(anchor_row.get("text") or ""))
    b_all = set(tokenize(external_text or ""))
    a = a_all - stop
    b = b_all - stop
    if not a:
        return {"ok": False, "ratio": 0.0, "shared": 0, "missing": [], "why": "锚没有可比的实词字组"}
    shared = a & b
    # 分母取"短的那一侧"：锚往往写得长，人回答往往短，
    # 用长侧做分母会把"说得简洁但对得上"判成对不上（2026-11-01 首次运行自检发现）
    ratio = len(shared) / max(min(len(a), len(b)), 1)
    missing = sorted(a - b, key=len, reverse=True)[:8]
    return {
        "ok": (ratio >= min_ratio) and len(shared) >= 3,
        "ratio": round(ratio, 3),
        "shared": len(shared),
        "missing": missing,
        "why": "对得上" if ratio >= min_ratio else "对不上：缺的字组太多",
    }


def reconstruct(con, day: str) -> dict:
    """把某天按三轴摆出来——给"忘了就用锚翻"这个动作一个入口。"""
    got = of_day(con, day)
    row = con.execute("select title, diary from days where day=?", (day,)).fetchone()
    return {
        "day": day,
        "title": (row["title"] if row else "") or "",
        "diary": (row["diary"] if row else "") or "",
        "anchors": got,
        "coverage": {a: bool(got.get(a)) for a in AXES},
        "complete": all(bool(got.get(a)) for a in AXES),
    }


def render_anchors(items: List[dict]) -> str:
    """把锚渲染成可注入/可看的几行。"""
    lines = []
    for r in items:
        tag = AXIS_LABEL.get(r.get("axis"), r.get("axis"))
        subs = r.get("subjects") or []
        tail = f"（{'、'.join(subs)}）" if subs else ""
        lines.append(f"- [{tag}] {r.get('text','')}{tail}")
    return "\n".join(lines)
