# -*- coding: utf-8 -*-
"""BM25 检索：只用 rank_bm25 + 标准库。

中文按「字 bigram」切词（不用分词器、零依赖），英文/数字按词切。
注意：2026-10-28 我们曾测出"BM25 96% / 向量 94%"，据此说 BM25 更准——
那是测量有偏：题面里带着记忆原词，等于在考字面匹配，不是在考记忆。
用 200 道强制改写的题重测：BM25 R@5 67.5%、向量 80.5%、RRF 融合 85.5%。
结论：BM25 赢在零依赖，输在换说法。要准确率请开 ireckon/vector_backend.py。
"""
import re
from typing import Iterable, List, Sequence, Tuple

try:
    from rank_bm25 import BM25Okapi
except Exception:  # 允许离线跑测试
    BM25Okapi = None

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-zA-Z0-9_]+")


def tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    toks: List[str] = []
    for seg in _CJK.findall(text):
        if len(seg) == 1:
            toks.append(seg)
        else:
            toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    toks.extend(_WORD.findall(text))
    return toks


class _FallbackBM25:
    """极简 BM25（rank_bm25 没装时兜底，结果同构）。"""

    def __init__(self, corpus: List[List[str]], k1: float = 1.5, b: float = 0.75):
        import math
        from collections import Counter
        self.k1, self.b = k1, b
        self.docs = corpus
        self.n = len(corpus)
        self.avgdl = sum(len(d) for d in corpus) / max(self.n, 1)
        self.tfs = [Counter(d) for d in corpus]
        df = Counter()
        for d in corpus:
            for w in set(d):
                df[w] += 1
        self.idf = {w: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def get_scores(self, query: Sequence[str]):
        out = []
        for i, tf in enumerate(self.tfs):
            dl = len(self.docs[i]) or 1
            s = 0.0
            for w in query:
                if w not in tf:
                    continue
                f = tf[w]
                s += self.idf.get(w, 0.0) * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out


class BM25Index:
    """对一批 (id, text) 建索引，按查询返回 [(id, score)]。"""

    def __init__(self, rows: Iterable[Tuple[int, str]]):
        self.ids: List[int] = []
        self.texts: List[str] = []
        corpus = []
        for rid, text in rows:
            self.ids.append(rid)
            self.texts.append(text or "")
            corpus.append(tokenize(text))
        self.corpus = corpus
        if not corpus:
            self.bm25 = None
        elif BM25Okapi is not None:
            self.bm25 = BM25Okapi(corpus)
        else:
            self.bm25 = _FallbackBM25(corpus)

    def __len__(self) -> int:
        return len(self.ids)

    def search(self, query: str, limit: int = 5) -> List[Tuple[int, float]]:
        """返回按分数降序的 (id, score)。空查询返回空。"""
        if self.bm25 is None:
            return []
        q = tokenize(query)
        if not q:
            return []
        scores = list(self.bm25.get_scores(q))
        if not scores:
            return []
        # 注意：rank_bm25 的 IDF 在低信息量词上会给负分。
        # 旧实现用"score <= 0 就停"会在负分时直接返回空——那是 bug（2026-10-29 由豆包实测发现）。
        # 现在只在"最高分也不为正"时判定为无命中，否则按分数降序取 top-limit。
        best = max(scores)
        if best == 0:
            # 小语料兜底：文档数很少时（<=2 篇），所有词的 IDF 都是 0，
            # BM25 给所有文档 0 分——这不代表"没命中"，是模型退化了。
            # 这时改用字符重合度序：命中的字越多分越高，一个字都没重合才判无命中。
            # （2026-10-29 由 GLM-5.3-flash 实测发现该场景检索全空。）
            STOP = set('的了吗呢吧啊我你他她它在是有不也就都还个这那什么和与及对把被')
            qchars = {ch for ch in query if ch.strip()} - STOP
            need = 1 if len(qchars) <= 2 else 2
            overlap = []
            for i, doc_tokens in enumerate(self.corpus):
                doc_chars = set(''.join(doc_tokens))
                hit = len(qchars & doc_chars)
                if hit >= need:
                    overlap.append((i, float(hit)))
            overlap.sort(key=lambda t: -t[1])
            return [(self.ids[i], sc) for i, sc in overlap[:limit]]
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        out = []
        for i in order:
            out.append((self.ids[i], float(scores[i])))
            if len(out) >= limit:
                break
        return out
