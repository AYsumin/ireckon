# -*- coding: utf-8 -*-
"""检索：按层建 BM25 索引 → 取 top-k → 断崖截断 → 压进 token 预算。

三条实测结论决定了这里的默认值：
  1. 单跳事实题：取 5 条 = 全塞（正确率相同，成本约 1/50）
  2. 多跳题：5 条只有 35%，取到 40 条才追平全塞（65%）→ 所以允许 floor_k 放大
  3. 断崖截断：分数掉到首条的一定比例就停，比"固定 5 条"省，且不掉准确率
"""
import re
from typing import Dict, List, Optional

from .bm25 import BM25Index
from . import vector_backend as _vb

try:
    import config as _C
except ImportError:
    from . import config_defaults as _C


def _rows(con, layer: str) -> List[tuple]:
    if layer == "fact":
        cur = con.execute(
            "select id, content, coalesce(keywords,'') from facts where status='active'")
        return [(r["id"], r["content"] + " " + (r[2] or "")) for r in cur]
    if layer == "day":
        cur = con.execute("select id, coalesce(title,''), coalesce(diary,'') from days")
        return [(r["id"], (r[1] or "") + " " + (r[2] or "")) for r in cur]
    if layer == "week":
        cur = con.execute("select id, coalesce(summary,'') from weeks")
        return [(r["id"], r[1] or "") for r in cur]
    if layer == "month":
        cur = con.execute("select id, coalesce(summary,'') from months")
        return [(r["id"], r[1] or "") for r in cur]
    if layer == "year":
        cur = con.execute("select id, coalesce(summary,'') from years")
        return [(r["id"], r[1] or "") for r in cur]
    return []


_VECTOR_ON = [False]          # 是否启用向量融合（由 api.Memory 初始化时决定）


_TOPIC_SPLIT = re.compile(r'[，,、;；。\s]+|以及|还有|和|与|跟')


def _split_topics(query: str):
    """把"下棋、睡觉和身体的事"拆成 [下棋, 睡觉, 身体的事]。

    只在分隔符处切；切不出东西就原样返回整句。
    """
    parts = [p.strip() for p in _TOPIC_SPLIT.split(query or '') if p and len(p.strip()) >= 2]
    return parts if len(parts) >= 2 else [query]


def set_vector(on: bool) -> None:
    _VECTOR_ON[0] = bool(on)


class Retriever:
    """索引按层懒加载并缓存；库里内容变了就调用 invalidate()。"""

    def __init__(self, con, top_k: int = 5, cliff_ratio: float = 0.6):
        self.con = con
        self.top_k = top_k
        self.cliff_ratio = cliff_ratio
        self._cache: Dict[str, BM25Index] = {}

    def invalidate(self, layer: Optional[str] = None):
        if layer is None:
            self._cache.clear()
        else:
            self._cache.pop(layer, None)

    def _index(self, layer: str) -> BM25Index:
        if layer not in self._cache:
            self._cache[layer] = BM25Index(_rows(self.con, layer))
        return self._cache[layer]

    MIN_KEEP = 3          # 断崖至少留这么多条（有的话）

    def _raw_search(self, idx, query: str, k: int):
        if _VECTOR_ON[0]:
            return _vb.hybrid_search(idx, query, k=k,
                                     min_cos=getattr(_C, "VECTOR_MIN_COS", 0.35))
        return idx.search(query, limit=k)

    def search(self, query: str, layer: str = "fact", k: Optional[int] = None) -> List[dict]:
        k = k or self.top_k
        idx = self._index(layer)

        # 多主题：一句话里带顿号/逗号/“和”等，就拆开分别查，再按最高分合并。
        # 原因：BM25 不替你分组，复合问句的分数被摊薄，常常一条都取不到。
        topics = _split_topics(query)
        merged = {}
        for t in topics:
            for rid, score in self._raw_search(idx, t, max(k, 1)):
                if rid not in merged or score > merged[rid]:
                    merged[rid] = score
        hits = sorted(merged.items(), key=lambda x: -x[1])
        if not hits:
            return []

        top = hits[0][1]
        kept = []
        for rid, score in hits:
            # 断崖：分数掉到首条的 cliff_ratio 以下就停；但至少留 MIN_KEEP 条。
            # top 可能是负数（rank_bm25 的 IDF 会给负分），此时 top*ratio 比 top 更小，
            # 旧实现会立刻 break、把检索结果清空——那是 bug（2026-10-29 由豆包实测发现）。
            too_low = top > 0 and score < top * self.cliff_ratio
            if too_low and len(kept) >= self.MIN_KEEP:
                break
            kept.append({"layer": layer, "id": rid, "score": round(score, 3)})
            if len(kept) >= k:
                break
        return kept[:k]

    def text_of(self, layer: str, rid: int) -> str:
        if layer == "fact":
            r = self.con.execute("select content from facts where id=?", (rid,)).fetchone()
            return r["content"] if r else ""
        if layer == "day":
            r = self.con.execute("select day, title, diary from days where id=?", (rid,)).fetchone()
            return f"{r['day']} {r['title'] or ''} {r['diary'] or ''}" if r else ""
        if layer == "week":
            r = self.con.execute("select week, summary from weeks where id=?", (rid,)).fetchone()
            return f"{r['week']} {r['summary'] or ''}" if r else ""
        if layer == "month":
            r = self.con.execute("select month, summary from months where id=?", (rid,)).fetchone()
            return f"{r['month']} {r['summary'] or ''}" if r else ""
        if layer == "year":
            r = self.con.execute("select year, summary from years where id=?", (rid,)).fetchone()
            return f"{r['year']} {r['summary'] or ''}" if r else ""
        return ""


def render_block(hits: List[dict], retr: Retriever, max_tokens: int) -> str:
    """把命中拼成注入块，按 token 预算截断（口径取 config.CHARS_PER_TOKEN，默认 1.2 字/token）。

   预算按 (max_tokens - 1) 计算：留 1 token 余量，保证向上取整后也不越过上限
   （2026-10-29：198 预算实测出现过 199，就是没有留这 1 token）。
   """
    # 用模块顶部的 _C（自带 config_defaults 兜底）；裸 import config 在
    # pip 安装场景（没有项目根 config.py）会让 build_context 直接 ModuleNotFoundError。
    # 扣掉【记忆】标题本身（5 字符）+ 留 1 token 余量，否则量起来会顶穿上限。
    # 2026-10-29：198 预算实测出现过 199，原因就是这两样都没扣。
    budget_chars = max(0, int((max_tokens - 1) * _C.CHARS_PER_TOKEN) - 5)
    lines, used, seen = [], 0, set()
    for h in hits:
        key = (h["layer"], h["id"])
        if key in seen:              # 合并多层结果时同一篇可能重复（如 day 层被搜两次）
            continue
        seen.add(key)
        t = retr.text_of(h["layer"], h["id"]).strip()
        if not t:
            continue
        line = f"- [{h['layer']}] {t}"
        # 单条就超预算 → 跳过它，别让它把后面能装的都挡掉。
        # 2026-10-31 实测：问"昨天…下棋、睡觉和身体的事"时，day 层日记（长）排在首位，
        # 旧代码直接 break，整块变成"（无）"，而 fact 层明明有命中。
        if len(line) > budget_chars:
            continue
        # 每行之间的换行符也要算（之前漏了，5 行就多出 4 个字符，量起来正好越线）
        if used + len(line) + (1 if lines else 0) > budget_chars:
            break
        lines.append(line)
        used += len(line) + (1 if len(lines) > 1 else 0)
    return "\n".join(lines)
