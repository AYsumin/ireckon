# -*- coding: utf-8 -*-
"""唯一需要改的文件。"""

# ---- 必填 ----
API_KEY = "sk-你的key"
BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"          # 便宜、够用；要文采再换
REASON_MODEL = MODEL             # 日记/抽取也可以指定更贵的模型

# ---- 数据 ----
DB_PATH = "memory.db"            # SQLite 文件；放哪都行
TIMEZONE = "Asia/Shanghai"

# ---- 检索（默认锁死为实测最优，别乱调）----
TOP_K = 5                        # 单跳事实题的最优值（实测 5 条=全塞，成本 1/50）
CLIFF_RATIO = 0.60               # 断崖截断：低于首条分数的这个比例就停
MAX_CONTEXT_TOKENS = 198         # 注入记忆的 token 预算（146 读取 + 52 摊销）
CHARS_PER_TOKEN = 1.2            # 中文保守口径：1 token ≈ 1.2 字（1.5 会低估、导致顶穿预算）

# ---- 分层策略 ----
RECENT_TURNS = 12                # 时层：进上下文的最近对话条数
DISTILL_HOUR = 4                 # 日层：每天几点蒸馏（本地时间）
DISTILL_MINUTE = 0
KEEP_RAW_TURNS_DAYS = 90         # 时层原始对话保留天数

# ---- 行为开关 ----
AUTO_ROUTE = True                # 是否按时间词自动选层
SUPERSEDE_ON_WRITE = True        # 预留开关：当前蒸馏不自动判取代，需显式调 m.supersede()/m.correct()（半自动）
VERBOSE = False

# ---- 可选：向量后端（默认关）----
# 打开后检索走 BM25 + 向量 的 RRF 融合（实测 R@5 67.5% → 85.5%）
# 需要你自己提供一个 embed 函数，见 ireckon/vector_backend.py 顶部说明
# ---- 向量后端（可选的第二版，见 README “两个版本”）----
VECTOR_BACKEND = False          # 打开后检索走 BM25 + 向量 RRF 融合（R@5 67.5% → 85.5%）
EMBED_BASE_URL = "http://127.0.0.1:8010/v1"   # 任何 OpenAI 兼容的 /v1/embeddings
EMBED_MODEL    = "bge-small-zh-v1.5"          # 本地 bge；换 Ollama 就是 nomic-embed-text 之类
EMBED_API_KEY  = ""                            # 本地服务留空
VECTOR_MIN_COS = 0.35                          # 余弦低于此视为没对上（向量永远有最近邻）
