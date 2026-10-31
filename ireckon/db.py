# -*- coding: utf-8 -*-
"""数据库：自动建表、WAL、忙等、外键。"""
import os
import sqlite3
import time

# schema.sql 有两个可能位置：
#   1. 包内 ireckon/schema.sql（pip 安装场景，pyproject 以 package-data 打入）
#   2. 源码树的 sql/schema.sql（从 zip/git 直接跑的场景）
# 只有第 2 个时，pip 装完 Memory() 直接 FileNotFoundError——发不出去的 bug。
_PKG_SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
_REPO_SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sql", "schema.sql")
_SCHEMA = _PKG_SCHEMA if os.path.exists(_PKG_SCHEMA) else _REPO_SCHEMA


def connect(db_path: str) -> sqlite3.Connection:
    """开一个连接：WAL + 外键 + 忙等。所有写操作都要走这里。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30, isolation_level=None, check_same_thread=False)
    con.execute("pragma journal_mode=WAL")
    con.execute("pragma synchronous=NORMAL")
    con.execute("pragma busy_timeout=20000")
    con.execute("pragma foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str) -> sqlite3.Connection:
    """启动时调用：建表（幂等）+ 开 WAL + 补齐后来加的列。"""
    con = connect(db_path)
    with open(_SCHEMA, encoding="utf-8") as f:
        con.executescript(f.read())
    _migrate(con)
    return con


def _migrate(con) -> None:
    """给老库补列（0.1.x 的库没有 anchors 相关列时不会炸）。

    CREATE TABLE IF NOT EXISTS 只对"表还不存在"生效，已有表加列必须 ALTER。
    """
    needed = {
        "anchors": [("superseded_by", "INTEGER")],
        "recognition_log": [("claim", "TEXT"), ("evidence", "TEXT")],
        "pending_claims": [("confidence", "TEXT")],
    }
    for table, cols in needed.items():
        have = {r[1] for r in con.execute(f"pragma table_info({table})")}
        for name, decl in cols:
            if name not in have:
                try:
                    con.execute(f"alter table {table} add column {name} {decl}")
                except sqlite3.OperationalError:
                    pass
    _fix_recognition_check(con)
    _ensure_unique_active(con)


def _fix_recognition_check(con) -> None:
    """0.2.4：verdict 多了 hedged，但 CHECK 约束没法 ALTER —— 只能重建表。

    做法：改名 → 建新表 → 搬数据 → 删旧表。数据一条不丢（含 claim/evidence）。
    """
    row = con.execute(
        "select sql from sqlite_master where type='table' and name='recognition_log'").fetchone()
    if not row or not row[0] or "'hedged'" in row[0]:
        return
    con.execute("alter table recognition_log rename to recognition_log_old")
    con.executescript("""
CREATE TABLE recognition_log (
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
""")
    con.execute("""insert into recognition_log(id, day, anchor_id, verdict, corrected_text, note, claim, evidence, at)
                   select id, day, anchor_id, verdict, corrected_text, note, claim, evidence, at
                   from recognition_log_old""")
    con.execute("drop table recognition_log_old")
    con.commit()


def quick_check(db_path: str) -> str:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute("pragma quick_check").fetchone()[0]
    finally:
        con.close()


def now() -> float:
    return time.time()


def upsert_year(con, year: str) -> int:
    con.execute("insert or ignore into years(year, created_at) values(?,?)", (year, now()))
    return con.execute("select id from years where year=?", (year,)).fetchone()["id"]


def upsert_month(con, month: str, year_id: int) -> int:
    con.execute("insert or ignore into months(month, year_id, created_at) values(?,?,?)",
                (month, year_id, now()))
    return con.execute("select id from months where month=?", (month,)).fetchone()["id"]


def upsert_week(con, week: str, month_id: int) -> int:
    con.execute("insert or ignore into weeks(week, month_id, created_at) values(?,?,?)",
                (week, month_id, now()))
    return con.execute("select id from weeks where week=?", (week,)).fetchone()["id"]


def _ensure_unique_active(con) -> None:
    """建"同一天同轴同文本只能一条 active"的唯一索引。

    老库可能已经有重复（0.2.3 不拦这个）。处理顺序：
      1. 先试着建索引
      2. 建不起来 → 每组重复里保留 id 最小的那条 active，其余标 superseded（**不删**）
      3. 把处理清单写进 logs/anchor-dups.txt，再建一次索引
    这样老库能起来，也不丢任何一条记录。
    """
    ddl = ("create unique index if not exists uq_anchor_active "
           "on anchors(day, axis, text) where status='active'")
    try:
        con.execute(ddl)
        return
    except sqlite3.IntegrityError:
        pass

    dups = con.execute(
        "select day, axis, text, min(id) as keep_id, count(*) c from anchors "
        "where status='active' group by day, axis, text having c > 1").fetchall()
    notes = ["发现重复 active 锚，已保留最早一条、其余标 superseded（未删除）："]
    all_keep = con.execute(
        "select id from anchors where status='active'").fetchall()
    try:
        con.execute("begin immediate")
        for r in dups:
            con.execute(
                "update anchors set status='superseded', superseded_by=? "
                "where day=? and axis=? and text=? and status='active' and id<>?",
                (r["keep_id"], r["day"], r["axis"], r["text"], r["keep_id"]))
            notes.append(f"  {r['day']} / {r['axis']} / {r['text'][:30]} × {r['c']}"
                         f" → 保留 id={r['keep_id']}")
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        return
    # 派生数据必须跟着状态走：锚被取代后，它的字组不能再算进 df，否则罕见度/排序被污染
    # （2026-10-31 外部复验发现：状态去重了，df 还按重复前计数）
    try:
        from . import anchors as _A
        _A.rebuild_df(con)
        _A.recompute_rarity(con)
        notes.append("已按 active 锚重建 df 与罕见度。")
    except Exception as e:
        notes.append(f"重建 df 失败：{type(e).__name__}: {e}")

    try:
        con.execute(ddl)
    except sqlite3.IntegrityError:
        notes.append("收拾之后仍建不起索引，请手工检查。")
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs = os.path.join(root, 'logs')
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, 'anchor-dups.txt'), 'w', encoding='utf-8') as f:
            f.write("\n".join(notes) + "\n")
    except Exception:
        pass
