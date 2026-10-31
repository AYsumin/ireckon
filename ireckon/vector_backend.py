# -*- coding: utf-8 -*-
"""可选：向量后端 + RRF 融合（默认关闭）。

为什么要有它：200 道换说法的题上量过——
    BM25   R@5 67.5%
    向量   R@5 80.5%
    融合   R@5 85.5%
BM25 赢在零依赖，输在"换说法就漏"。需要准确率时把 enable_vector() 打开。

用法:
    from ireckon import vector_backend as vb
    vb.enable_vector(embed_fn)     # embed_fn(texts: list[str]) -> list[list[float]]
    hits = vb.hybrid_search(bm25_index, embed_fn, query, k=5)

注意：本模块不绑定任何 embedding 服务。你自己给 embed_fn：
    · 本地（推荐）：llama.cpp / Ollama 的 /v1/embeddings，或 sentence-transformers
    · 云端：任何 OpenAI 兼容的 embeddings 接口
"""
from typing import Callable, List, Sequence, Tuple

import math
import os

_EMBED = None


def enable_vector(embed_fn: Callable[[Sequence[str]], List[List[float]]]) -> None:
    """打开向量检索。传一个批量 embed 函数进来。"""
    global _EMBED
    _EMBED = embed_fn


def disable_vector() -> None:
    global _EMBED
    _EMBED = None


def is_enabled() -> bool:
    return _EMBED is not None


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return num / (na * nb)


MIN_COS = 0.35          # 低于这个余弦就当作"没对上"（向量永远会给最近邻，必须自己设闸）

# 向量缓存：按 (文档 id, 文本 hash) 缓存 embedding。
# 没有缓存时每次查询都对全库现场 embed——401 条记忆的库每问就是 401+ 次 embedding 调用。
# 内容没变就命中缓存；内容变了 hash 变、自动重算。超过上限整体清空（LRU 对这个规模是过度设计）。
import hashlib as _hashlib

_VEC_CACHE: dict = {}
_VEC_CACHE_MAX = 20000


def _vec_key(rid, text: str) -> str:
    return f"{rid}:{_hashlib.md5((text or '').encode('utf-8')).hexdigest()}"


def _embed_docs(ids, texts) -> List[List[float]]:
    """批量取向量，带缓存。返回与 ids 等长的向量列表；失败抛异常（调用方退回 BM25）。"""
    global _VEC_CACHE
    keys = [_vec_key(rid, t) for rid, t in zip(ids, texts)]
    missing = [i for i, kk in enumerate(keys) if kk not in _VEC_CACHE]
    if missing:
        new_vecs = _EMBED([texts[i] for i in missing])
        if len(_VEC_CACHE) + len(missing) > _VEC_CACHE_MAX:
            _VEC_CACHE = {}
        for i, v in zip(missing, new_vecs):
            _VEC_CACHE[keys[i]] = v
    return [_VEC_CACHE[kk] for kk in keys]


def hybrid_search(bm25_index, query: str, k: int = 5, rrf_k: int = 60,
                  min_cos: float = None) -> List[Tuple[int, float]]:
    """BM25 + 向量 的 RRF 融合。返回 [(id, 融合分)]。

    没打开向量后端时，退化成纯 BM25——行为与之前完全一致。
    """
    bm_hits = bm25_index.search(query, limit=max(k * 3, 20)) if bm25_index else []

    if _EMBED is None:
        return bm_hits[:k]

    # 向量这一路：对索引里的文本取向量（带缓存；大库仍应建独立向量表）
    ids = getattr(bm25_index, "ids", [])
    texts = getattr(bm25_index, "texts", [])
    if not ids:
        return bm_hits[:k]

    try:
        vectors = _embed_docs(ids, texts)
        qv = _EMBED([query])[0]
    except Exception:
        return bm_hits[:k]

    thr = MIN_COS if min_cos is None else min_cos
    scored = sorted(
        ((aid, _cos(qv, v)) for aid, v in zip(ids, vectors)),
        key=lambda t: -t[1])
    scored = [(aid, c) for aid, c in scored if c >= thr][: max(k * 3, 20)]
    if not bm_hits and not scored:
        return []

    scores: dict = {}
    for pos, (aid, _) in enumerate(bm_hits):
        scores[aid] = scores.get(aid, 0.0) + 1.0 / (rrf_k + pos + 1)
    for pos, (aid, _) in enumerate(scored):
        scores[aid] = scores.get(aid, 0.0) + 1.0 / (rrf_k + pos + 1)

    ranked = sorted(scores.items(), key=lambda t: -t[1])
    return [(aid, round(s, 6)) for aid, s in ranked[:k]]

# ---------------------------------------------------------------------------
# 内置 HTTP 提供商：任何 OpenAI 兼容的 /v1/embeddings 都能用
# 默认指向本机 bge 服务（llama.cpp / Ollama / 自建都行）
# ---------------------------------------------------------------------------
import json as _json
import urllib.request as _req

DEFAULT_BASE = os.environ.get("IRECKON_EMBED_BASE", "http://127.0.0.1:8010/v1")
DEFAULT_MODEL = os.environ.get("IRECKON_EMBED_MODEL", "bge-small-zh-v1.5")


def http_embed(texts, base_url=None, model=None, api_key=None, timeout=20):
    """批量取向量。失败抛异常（调用方会退回 BM25）。"""
    base = (base_url or DEFAULT_BASE).rstrip('/')
    body = _json.dumps({"input": list(texts), "model": model or DEFAULT_MODEL}).encode('utf-8')
    headers = {"Content-Type": "application/json"}
    key = api_key or os.environ.get("IRECKON_EMBED_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = _req.Request(f"{base}/embeddings", data=body, headers=headers, method="POST")
    with _req.urlopen(request, timeout=timeout) as resp:
        data = _json.loads(resp.read().decode('utf-8'))
    return [item["embedding"] for item in data["data"]]


def enable_http(base_url=None, model=None, api_key=None):
    """打开向量检索，用 HTTP embedding。首次调用会做一次连通性探测。"""
    probe = http_embed(["ping"], base_url=base_url, model=model, api_key=api_key, timeout=5)
    if not probe or not probe[0]:
        raise RuntimeError("embedding 服务没有返回向量")
    enable_vector(lambda texts: http_embed(texts, base_url=base_url, model=model,
                                           api_key=api_key))
    return len(probe[0])


def auto_enable(config=None):
    """按配置自动打开；失败就保持 BM25。返回 (是否打开, 说明)。"""
    if config is None:
        try:
            import config as config  # noqa: WPS433
        except Exception:
            from . import config_defaults as config
    base = getattr(config, "EMBED_BASE_URL", DEFAULT_BASE)
    model = getattr(config, "EMBED_MODEL", DEFAULT_MODEL)
    key = getattr(config, "EMBED_API_KEY", "") or None
    try:
        dim = enable_http(base, model, key)
        return True, f"向量后端已开（{base} / {model} / {dim} 维）"
    except Exception as e:
        return False, f"向量后端没开成，退回 BM25：{str(e)[:80]}"
