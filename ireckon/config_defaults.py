# -*- coding: utf-8 -*-
import os
"""包内默认配置：当用户的 config.py 不在 sys.path 时（pip 安装场景）用它。"""

API_KEY = ""
BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"
REASON_MODEL = MODEL

DB_PATH = os.environ.get("IRECKON_DB") or os.path.join(
    os.path.expanduser("~"), ".ireckon", "mem.db")   # 固定一处，不随工作目录撒
TIMEZONE = "Asia/Shanghai"

TOP_K = 5
CLIFF_RATIO = 0.60
MAX_CONTEXT_TOKENS = 198
CHARS_PER_TOKEN = 1.2

RECENT_TURNS = 12
DISTILL_HOUR = 4
DISTILL_MINUTE = 0
KEEP_RAW_TURNS_DAYS = 90

AUTO_ROUTE = True
SUPERSEDE_ON_WRITE = True   # 预留：蒸馏不自动判取代，需显式调 supersede()/correct()
VERBOSE = False

# ---- 向量后端（可选的第二版，见 README “两个版本”）----
VECTOR_BACKEND = False          # 打开后检索走 BM25 + 向量 RRF 融合（R@5 67.5% → 85.5%）
EMBED_BASE_URL = "http://127.0.0.1:8010/v1"   # 任何 OpenAI 兼容的 /v1/embeddings
EMBED_MODEL    = "bge-small-zh-v1.5"          # 本地 bge；换 Ollama 就是 nomic-embed-text 之类
EMBED_API_KEY  = ""                     # 本地服务留空
VECTOR_MIN_COS = 0.35                   # 余弦低于此视为没对上（向量永远有最近邻）

# ---- 0.2.0 锚点层 ----
ANCHOR_RARITY_WEIGHT = 0.5      # 罕见度在锚检索里的加成权重（0 = 只用 BM25）
ANCHOR_TOPK = 4                 # 一次带几条锚进上下文
ANCHOR_VERIFY_MIN_RATIO = 0.5   # 外部回应要覆盖锚的字组多少才判"对得上"
ANCHOR_BUDGET_SHARE = 0.34      # 记忆预算里给锚点留的比例（剩下给事实）
