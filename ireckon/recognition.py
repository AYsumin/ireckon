# -*- coding: utf-8 -*-
"""昨日重现（0.2.3）：把某一天的锚摆出来，由人认；认的结果留在库里。

为什么要有这一层：
    记忆不是"存了就记得"，是"给得出锚、且你认"。所以需要记录"你认不认"。
    没有这张表，互认率就是一句空话——你忘了核，系统也不会有任何痕迹。

三条已定的规矩（2026-10-31 他说的）：
    ① 我做不出/不确定时，去外部核对（查时间、翻原文）—— 所以输出里必须带时间与原文片段
    ② 认不出 ≠ 记错了：前者标 disputed（留着等第二条证据），后者才改（correct_anchor）
    ③ 不许硬圆：核不上的记 unknown，不催、不假装
"""
import json
import time
from datetime import datetime, timedelta
from typing import List, Optional

from . import state_guard as G
from .bm25 import tokenize

_STOP_GRAMS = set("""
的了 了 是 在 我 你 他 她 它 我们 你们 他们 和 与 跟 就 也 都 还 又 很 太 更 最
好像 像是 大概 可能 应该 记得 忘了 不 没 有 个 那 这 什么 怎么 事情 的事
下午 上午 中午 晚上 早上 昨天 今天 明天 前天
""".split())

VERDICTS = ("confirmed", "disputed", "hedged", "corrected", "unknown")


def _day_range(day: str):
    d = datetime.strptime(day, "%Y-%m-%d")
    lo = d.timestamp()
    return lo, lo + 86400


def _best_snippet(con, day: str, text: str, width: int = 60) -> dict:
    """在当天原文里找最像的一段，给出时间 + 片段（供人外部核对用）。"""
    lo, hi = _day_range(day)
    rows = con.execute(
        "select role, content, ts from turns where ts>=? and ts<? order by ts", (lo, hi)).fetchall()
    if not rows:
        return {}
    ga = set(tokenize(text))
    best, best_ratio = None, 0.0
    for r in rows:
        gf = set(tokenize(r["content"] or ""))
        if not ga or not gf:
            continue
        ratio = len(ga & gf) / len(ga)
        if ratio > best_ratio:
            best, best_ratio = r, ratio
    if best is None:
        return {}
    return {
        "at": datetime.fromtimestamp(best["ts"]).strftime("%H:%M"),
        "role": best["role"],
        "snippet": (best["content"] or "")[:width],
        "ratio": round(best_ratio, 2),
    }


def reproduce(con, day: str) -> dict:
    """某天的"昨日重现"：三轴锚 + 每条的时间与原文片段 + 已有的认定。"""
    from . import anchors as A
    got = A.of_day(con, day)
    verdicts = {}
    try:
        for r in con.execute(
                "select anchor_id, verdict, corrected_text, note, at from recognition_log "
                "where day=? order by at", (day,)).fetchall():
            verdicts[r["anchor_id"]] = dict(r)
    except Exception:
        pass

    out = {"day": day, "axes": {}, "coverage": {}, "counts": {}}
    for axis in A.AXES:
        items = []
        for a in got.get(axis) or []:
            v = verdicts.get(a["id"], {})
            items.append({
                "id": a["id"],
                "text": a["text"],
                "subjects": a.get("subjects") or [],
                "event_time": a.get("event_time") or "",
                "why": a.get("why") or "",
                "source": _best_snippet(con, day, a["text"]),
                "verdict": v.get("verdict") or "unknown",
                "corrected_text": v.get("corrected_text") or "",
            })
        out["axes"][axis] = items
        out["coverage"][axis] = bool(items)

    try:
        out["claims"] = claims_of(con, day, status="pending")
        for c in out["claims"]:
            c["probe"] = probe_claim(con, day, c["text"])
    except Exception:
        out["claims"] = []

    counts = {"total": 0, "confirmed": 0, "disputed": 0, "hedged": 0,
              "corrected": 0, "unknown": 0}
    for axis in A.AXES:
        for it in out["axes"][axis]:
            counts["total"] += 1
            counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
    out["counts"] = counts
    answered = counts["confirmed"] + counts["disputed"] + counts["corrected"]
    out["rate"] = round(counts["confirmed"] / answered, 3) if answered else None
    return out


def record(con, day: str, anchor_id: int, verdict: str,
           corrected_text: str = "", note: str = "", claim: str = "",
           evidence: str = "") -> dict:
    """记一条认定，并按规则决定"要不要真改锚"。

    规则（2026-10-31 他定：她得有自己的判断）：
      · 只有 corrected **且带证据** 才允许改锚；没有证据 → 只记冲突，锚一个字不动
      · hedged（模糊）与 unknown（没答）都不算互认，也不降权
      · 认定只追加，不覆盖旧记录
    """
    verdict = (verdict or "").strip().lower()
    if verdict not in VERDICTS:
        raise G.InvariantError(f"verdict 只能是 {VERDICTS}，收到 {verdict!r}")
    row = G.require_anchor_day(con, anchor_id, day)

    applied = False
    new_anchor_id = None
    if verdict == "corrected":
        if not (corrected_text or "").strip():
            raise G.InvariantError("corrected 必须给 corrected_text")
        if not (evidence or "").strip():
            # 没有出处：记下来，但不改锚
            note = (note + " | 未给证据，锚未改动").strip(" |")
        else:
            from . import anchors as A
            new_anchor_id = A.correct(con, anchor_id, corrected_text.strip())
            applied = True

    con.execute(
        "insert into recognition_log(day, anchor_id, verdict, corrected_text, note, claim, evidence, at) "
        "values(?,?,?,?,?,?,?,?)",
        (day, anchor_id, verdict, corrected_text or "", note or "", claim or "",
         evidence or "", time.time()))
    # 改锚成功后，新锚也要有一条认定，否则当天的"✎"会跟着旧锚一起消失（旧锚被 superseded 了）
    if applied and new_anchor_id:
        con.execute(
            "insert into recognition_log(day, anchor_id, verdict, corrected_text, note, claim, evidence, at) "
            "values(?,?,?,?,?,?,?,?)",
            (day, new_anchor_id, "corrected", "", f"由锚 {anchor_id} 修正而来",
             claim or "", evidence or "", time.time()))
    con.commit()
    return {
        "log_id": con.execute("select last_insert_rowid() as id").fetchone()["id"],
        "applied": applied,
        "new_anchor_id": new_anchor_id,
        "note": note,
    }


def claim(con, day: str, text: str, claim_by: str = "him", note: str = "",
          confidence: str = "clear") -> int:
    """他主张一件她没有记录的事。挂起，不进她的记忆。

    confidence=vague 表示"他答得模糊"——那就只是线索，不是证据。
    """
    day = G.require_day(day)
    text = (text or "").strip()
    if not text:
        raise G.InvariantError("主张的正文不能为空")
    confidence = (confidence or "clear").strip().lower()
    if confidence not in ("clear", "vague"):
        raise G.InvariantError("confidence 只能是 clear / vague")
    con.execute(
        "insert into pending_claims(day, text, claim_by, confidence, status, note, created_at) "
        "values(?,?,?,?,'pending',?,?)",
        (day, text, claim_by, confidence, note or "", time.time()))
    con.commit()
    return con.execute("select last_insert_rowid() as id").fetchone()["id"]




def probe_claim(con, day: str, text: str, direction_min: float = 0.35) -> dict:
    """把他一句模糊的话拿去当天原文里找：方向对不对、细节差在哪。

    判据：
      direction_ok  = 与最像原文片段的字组重合 >= direction_min（方向对）
      missing       = 原文里有、他的话里没有的字组（他漏掉的细节）
      extra_in_his  = 他的话里有、原文里没有的字组（他多说/记错的）
    """
    text = (text or "").strip()
    if not text:
        return {"found": False, "why": "他的话是空的"}
    best = _best_snippet(con, day, text, width=200)
    if not best:
        return {"found": False, "why": "当天原文里没有相似片段"}
    lo, hi = _day_range(day)
    rows = con.execute(
        "select role, content, ts from turns where ts>=? and ts<? order by ts", (lo, hi)).fetchall()
    target = None
    for r in rows:
        if (r["content"] or "").startswith(best["snippet"][:20]):
            target = r["content"] or ""
            break
    target = target or best["snippet"]
    gh = set(tokenize(text))
    go = set(tokenize(target))
    # 方向判断不看"字组比例"，看"命中几个实词字组"：
    # 模糊回答里全是"好像/下午/的事"这类虚词，按比例算会把方向对的判成对不上
    # （2026-10-31 实测：他说"好像是下午在弄影像资料的事"，比例 0.25，但方向明明是对的）
    shared = (gh & go) - _STOP_GRAMS
    ratio = len(shared) / max(len(gh - _STOP_GRAMS), 1)
    direction_ok = len(shared) >= 2 or ratio >= direction_min
    return {
        "found": True,
        "at": best["at"],
        "role": best["role"],
        "snippet": best["snippet"],
        "ratio": round(ratio, 3),
        "shared_content": len(shared),
        "direction_ok": direction_ok,
        "missing": sorted((go - gh) - _STOP_GRAMS, key=len, reverse=True)[:8],
        "extra_in_his": sorted((gh - go) - _STOP_GRAMS, key=len, reverse=True)[:8],
        "why": "方向对，细节要按原文校" if direction_ok else "方向都不太对",
    }


def render_probe(p: dict) -> str:
    """把一次核对渲染成一句人能读的话。"""
    if not p.get("found"):
        return f"按原文没找到（{p.get('why', '')}）"
    head = f"原文 {p['at']} [{p['role']}]：{p['snippet'][:50]}"
    tail = "方向对，但细节要按原文校" if p.get("direction_ok") else "方向也对不上"
    if p.get("missing"):
        tail += f"；原文有、他没提：{'、'.join(p['missing'][:3])}"
    if p.get("extra_in_his"):
        tail += f"；他多说：{'、'.join(p['extra_in_his'][:3])}"
    return head + " —— " + tail


def claims_of(con, day: str, status: str = "pending") -> List[dict]:
    sql = ("select id, day, text, claim_by, confidence, evidence, status, anchor_id, note, created_at "
           "from pending_claims")
    args: list = []
    if day:
        sql += " where day=?"
        args.append(day)
        if status:
            sql += " and status=?"
            args.append(status)
    elif status:
        sql += " where status=?"
        args.append(status)
    return [dict(r) for r in con.execute(sql + " order by id", args).fetchall()]


def resolve_claim(con, claim_id: int, evidence: str) -> dict:
    """拿到出处之后，把她说有的事落成一条锚（来源标 verified_claim）。"""
    evidence = G.require_evidence(evidence, what="解决主张")
    row = G.require_claim(con, claim_id)
    if (row["status"] or "pending") != "pending":
        # 幂等：已经解决过的，直接返回已有结果，不再造一条重复锚
        return {"anchor_id": row["anchor_id"], "evidence": "", "already": True}
    from . import anchors as A
    axis = "event"
    new_id = A.insert_anchor(con, row["day"], axis, row["text"],
                             source="verified_claim", why=f"他说有，证据：{evidence[:60]}")
    con.execute("update pending_claims set status='verified', evidence=?, anchor_id=? where id=?",
                (evidence, new_id, claim_id))
    con.commit()
    return {"anchor_id": new_id, "evidence": evidence}


def stats(con, days: int = 7, end_day: Optional[str] = None) -> dict:
    """最近 N 天的核账：每天几条、认了几条、几天没人核。"""
    end = datetime.strptime(end_day, "%Y-%m-%d") if end_day else datetime.now()
    out = {"days": [], "totals": {"total": 0, "confirmed": 0, "disputed": 0,
                                  "hedged": 0, "corrected": 0, "unknown": 0},
           "unanswered_days": 0, "rate": None}
    answered_all = 0
    for i in range(days):
        d = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        rep = reproduce(con, d)
        c = rep["counts"]
        answered = c["confirmed"] + c["disputed"] + c["corrected"]
        if c["total"] == 0:
            continue
        if answered == 0:
            out["unanswered_days"] += 1
        for k in out["totals"]:
            out["totals"][k] += c.get(k, 0)
        answered_all += answered
        out["days"].append({"day": d, "total": c["total"], "answered": answered,
                            "rate": rep["rate"]})
    t = out["totals"]
    if answered_all:
        out["rate"] = round(t["confirmed"] / answered_all, 3)
    return out


def render(rep: dict, with_source: bool = True) -> str:
    """把重现渲染成给人看的一段。"""
    label = {"event": "事", "person": "人", "time": "时"}
    lines = [f"# 昨日重现 · {rep['day']}",
             f"三轴：事件 {'有' if rep['coverage'].get('event') else '缺'} · "
             f"人物 {'有' if rep['coverage'].get('person') else '缺'} · "
             f"时刻 {'有' if rep['coverage'].get('time') else '缺'}"]
    c = rep["counts"]
    if c["total"]:
        lines.append(f"认定：{c['confirmed']} 认 / {c['disputed']} 认不出 / "
                     f"{c['corrected']} 记错了 / {c['unknown']} 未核")
    for axis in ("event", "person", "time"):
        for it in rep["axes"].get(axis) or []:
            head = f"- [{label[axis]}] {it['text']}"
            subs = "、".join(it["subjects"])
            if subs:
                head += f"（{subs}）"
            mark = {"confirmed": " ✓", "disputed": " ?", "hedged": " ~",
                    "corrected": " ✎"}.get(it["verdict"], "")
            lines.append(head + mark)
            if it["verdict"] == "corrected" and it["corrected_text"]:
                lines.append(f"    改成：{it['corrected_text']}")
            if with_source and it["source"]:
                s = it["source"]
                lines.append(f"    原文 {s['at']} [{s['role']}]：{s['snippet']}（像度 {s['ratio']}）")
    for c in rep.get("claims") or []:
        tag = "模糊线索" if (c.get("confidence") or "clear") == "vague" else "待证"
        lines.append(f"- [{tag}] {c['text']}（{c.get('claim_by') or '他'}说的，我没有记录）")
        if c.get("probe"):
            lines.append(f"    {render_probe(c['probe'])}")
    return "\n".join(lines)
