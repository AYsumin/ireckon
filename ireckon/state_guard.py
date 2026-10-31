# -*- coding: utf-8 -*-
"""写路径的状态契约（0.2.4）。

为什么要有这个文件：
    2026-10-31 外部验证发现五个问题——重复修正旧锚、重复解决主张、跨日错记认定、
    CLI 走不通、版本不一致。它们不是五个独立的疏忽，是同一个缺口：
    **每个写函数各自判断状态，没有一层统一的"什么状态允许什么操作"。**
    所以这一版把允许/拒绝写成一张矩阵，所有写路径都走它。

矩阵（当前实现）：

    anchors.insert       任意（同一天同轴同文本的 active 已存在 → 幂等返回原 id）
    anchors.correct      仅 active 锚；已 superseded → 拒绝
    recognition.record   day 必须等于锚所属 day；corrected 必须带 evidence
    recognition.claim    day 必须是合法日期；confidence ∈ {clear, vague}
    resolve_claim        仅 pending 主张；已解决 → 幂等返回已有 anchor_id

所有拒绝都抛 InvariantError（ValueError 子类），错误里写清"为什么不行、该怎么改"。
"""
import re
from datetime import datetime

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InvariantError(ValueError):
    """状态不变量被违反。所有写路径的拒绝都用它。"""


def require_day(day: str) -> str:
    """日期必须是 YYYY-MM-DD 且真实存在（挡 2026-02-30 这种）。"""
    d = (day or "").strip()
    if not _DAY.match(d):
        raise InvariantError(f"日期格式必须是 YYYY-MM-DD，收到 {day!r}")
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise InvariantError(f"日期不存在：{d}") from None
    return d


def require_anchor(con, anchor_id: int):
    # 取整行：调用方要拿 subjects / why 等字段，只选几列会让它们 KeyError
    row = con.execute("select * from anchors where id=?", (anchor_id,)).fetchone()
    if row is None:
        raise InvariantError(f"没有 id={anchor_id} 这条锚")
    return row


def require_active_anchor(con, anchor_id: int):
    """只有 active 的锚可以再被修正——否则会造出两个有效版本。"""
    row = require_anchor(con, anchor_id)
    if (row["status"] or "active") != "active":
        raise InvariantError(
            f"锚 {anchor_id} 已被取代（superseded_by={row['superseded_by']}），"
            f"不要再改它；要改请改当前生效的那条")
    return row


def require_anchor_day(con, anchor_id: int, day: str):
    """认定必须写在锚所属的那一天，跨日写等于静默丢失。"""
    day = require_day(day)
    row = require_anchor(con, anchor_id)
    if row["day"] and day != row["day"]:
        raise InvariantError(f"日期不符：这条锚属于 {row['day']}，不能按 {day} 记认定")
    return row


def require_evidence(evidence: str, what: str = "改锚") -> str:
    """没有出处就不许改账。注意：只校验非空，不校验真实性（文档里已写明）。"""
    e = (evidence or "").strip()
    if not e:
        raise InvariantError(f"{what}必须带 evidence（原文片段 / 时间 / 第三方），否则只记冲突不动锚")
    return e


def require_claim(con, claim_id: int):
    row = con.execute("select id, day, text, status, anchor_id from pending_claims where id=?",
                      (claim_id,)).fetchone()
    if row is None:
        raise InvariantError(f"没有 id={claim_id} 这条主张")
    return row


def existing_active_anchor(con, day: str, axis: str, text: str):
    """同一天、同一轴、同一文本的 active 锚——已存在就别再插一条（幂等）。"""
    return con.execute(
        "select id from anchors where day=? and axis=? and text=? and status='active'",
        (day, axis, text)).fetchone()
