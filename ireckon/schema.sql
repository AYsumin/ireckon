-- I Reckon（俺寻思） schema（五层 + 指针外键）
-- 全部 CREATE IF NOT EXISTS，启动时自动执行

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 时层：原始对话（滚动窗口就是它的最近 N 条）
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK (role IN ('user','assistant','system')),
    content     TEXT    NOT NULL,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_sess_ts ON turns(session_id, ts);

-- 年 / 月 / 周：越往上越抽象
CREATE TABLE IF NOT EXISTS years (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    year    TEXT UNIQUE NOT NULL,
    summary TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS months (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    month   TEXT UNIQUE NOT NULL,
    year_id INTEGER REFERENCES years(id) ON DELETE SET NULL,
    summary TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS weeks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    week    TEXT UNIQUE NOT NULL,
    month_id INTEGER REFERENCES months(id) ON DELETE SET NULL,
    summary TEXT,
    created_at REAL
);

-- 日层：一天一篇日记；指针指向上层
CREATE TABLE IF NOT EXISTS days (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    day     TEXT UNIQUE NOT NULL,
    title   TEXT,
    diary   TEXT,
    week_id INTEGER REFERENCES weeks(id)  ON DELETE SET NULL,
    month_id INTEGER REFERENCES months(id) ON DELETE SET NULL,
    year_id INTEGER REFERENCES years(id)  ON DELETE SET NULL,
    created_at REAL
);

-- 事实层：原子事实；被推翻的用 superseded_by 指出去
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    day_id        INTEGER REFERENCES days(id)  ON DELETE SET NULL,   -- 属于哪一天的蒸馏
    turn_id       INTEGER REFERENCES turns(id) ON DELETE SET NULL,   -- 预留：当前版本不写入，溯源粒度到天（day_id）
    content       TEXT    NOT NULL,
    keywords      TEXT,                       -- JSON 数组，给 BM25 加权重
    event_time    TEXT,                       -- 这件事本身发生的时间
    importance    REAL    DEFAULT 0.8,
    status        TEXT    DEFAULT 'active',   -- active / archived / superseded
    superseded_by INTEGER REFERENCES facts(id) ON DELETE SET NULL,
    stale_hint    INTEGER DEFAULT 0,          -- 1 = 可能过时（保守判据下不归档）
    source        TEXT,                       -- 抽取来源标记：fact|manual
    created_at    REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);

-- 蒸馏流水：防止同一天重复蒸馏
CREATE TABLE IF NOT EXISTS distill_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    day     TEXT NOT NULL,
    stage   TEXT NOT NULL,             -- diary | facts | anchors
    ok      INTEGER DEFAULT 1,
    note    TEXT,
    ts      REAL NOT NULL,
    UNIQUE(day, stage)
);

-- ============ 0.2.0 新增：锚点层 ============
-- 一天的记忆靠三轴各一个锚（事 event / 人 person / 时 time），不是一堆事实。
-- 依据：2026-10-31 他睡醒断片，靠"时间 + 三件事"把一天整片捞回来。
-- 判据：锚要具体（动作+对象 / 具体的人）、要罕见（罕见度=IDF）、要能被外部回应验。
CREATE TABLE IF NOT EXISTS anchors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT NOT NULL,                        -- 属于哪一天（YYYY-MM-DD）
    axis       TEXT NOT NULL CHECK (axis IN ('event','person','time')),
    text       TEXT NOT NULL,                        -- "分类影像资料并重命名"这种，可对外说出口
    subjects   TEXT,                                 -- JSON 数组：涉及的人
    event_time TEXT,                                 -- 这件事发生的时间（可空）
    rarity     REAL DEFAULT 0,                       -- 罕见度（0~1），入库时按 IDF 算
    why        TEXT,                                 -- 为什么值得当锚（罕见点在哪）
    status     TEXT DEFAULT 'active',                -- active / superseded
    source     TEXT DEFAULT 'distill',               -- distill | manual
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anchors_day    ON anchors(day, axis);
CREATE INDEX IF NOT EXISTS idx_anchors_status ON anchors(status);

-- 罕见度的增量原料：每个 bigram 出现在多少个锚里（df）
-- 有了它，加一条锚只动它自己的字组，不用全库重算（2026-10-31 补）
CREATE TABLE IF NOT EXISTS anchor_gram_df (
    gram TEXT PRIMARY KEY,
    df   INTEGER NOT NULL DEFAULT 0
);

-- 人轴别名：我妈 / 母亲 / 妈 归到同一个主体（2026-10-31 补）
CREATE TABLE IF NOT EXISTS subject_aliases (
    alias     TEXT PRIMARY KEY,
    canonical TEXT NOT NULL,
    created_at REAL
);

-- ============ 0.2.3 新增：昨日重现的核账 ============
-- 记忆不是"存了就记得"，是"给得出锚、且人认"。这张表记的就是"认不认"。
CREATE TABLE IF NOT EXISTS recognition_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    day            TEXT NOT NULL,
    anchor_id      INTEGER NOT NULL,
    verdict        TEXT NOT NULL CHECK (verdict IN ('confirmed','disputed','hedged','corrected','unknown')),
    corrected_text TEXT,
    note           TEXT,
    claim          TEXT,
    evidence       TEXT,
    at             REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recog_day ON recognition_log(day, anchor_id);

-- ============ 0.2.4：不对称记忆 ============
-- 他说有、她没有记录的事，挂在这里等证据；不算她的记忆，也不删。
CREATE TABLE IF NOT EXISTS pending_claims (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT NOT NULL,
    text       TEXT NOT NULL,              -- 他主张的事
    claim_by   TEXT DEFAULT 'him',         -- 谁说的
    confidence TEXT DEFAULT 'clear',        -- clear / vague（他答得模糊就是线索，不是证据）
    evidence   TEXT,                       -- 后来的出处（原文/第三方）
    status     TEXT DEFAULT 'pending',     -- pending / verified / dropped
    anchor_id  INTEGER,                    -- verified 之后落到哪条锚
    note       TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_day ON pending_claims(day, status);

-- 注意：唯一索引 uq_anchor_active 不写在这里。
-- 原因（2026-10-31 外部验证）：schema 执行阶段建索引，老库一旦有重复数据，
-- init_db 会直接崩在 UNIQUE constraint 上，迁移动都没跑。
-- 它现在由 db._ensure_unique_active() 在迁移里建，先收拾重复、再建索引。
