# -*- coding: utf-8 -*-
"""动态路由：靠时间词决定先查哪一层。

⚠️ 实测说明（别忽略）：
    在 50 + 50 道人工标注题上，用 LLM 判"这题要几条记忆"的准确率只有 63%：
      单条题被判成多跳 79%（多花 token，不会答错）
      多跳题被判成单条  2%（**会答错**）
    所以这里**不做语义分类**，只做「时间词 → 层」的确定性映射：
    规则可解释、零误判成本、也不额外花 token。

    层选择只影响"先查哪一层"，不是"只查这一层"——最终仍会合并 top-k。
"""
import re
from typing import List, Tuple

# (层, 正则, 优先级)；命中多个时按列表顺序优先
_RULES: List[Tuple[str, str]] = [
    ("day",   r"(昨天|昨晚|昨日|前天|前晚|今天|今早|今晚|刚才|刚刚|那天)"),
    ("week",  r"(上个?星期|这周|本周|上周|礼拜|这礼拜|上礼拜|这几天|最近几天)"),
    ("month", r"(上个?月|这个月|本月|月初|月底|上个月)"),
    ("year",  r"(去年|今年|前年|当年|那时候|这[一]?年|多年前|很多年前|以前)"),
    ("fact",  r"(还记得|记不记得|提过|说过|上次说|那个事|那件事)"),
]


def route(query: str, auto: bool = True) -> str:
    """返回 fact / day / week / month / year。默认 fact（最便宜也最准）。"""
    if not auto or not query:
        return "fact"
    for layer, pattern in _RULES:
        if re.search(pattern, query):
            return layer
    return "fact"


def explain(query: str) -> str:
    """给人看的解释，方便调参。"""
    for layer, pattern in _RULES:
        m = re.search(pattern, query)
        if m:
            return f"{layer}（命中“{m.group(0)}”）"
    return "fact（没有时间词，走事实层）"
