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
    }
    for table, cols in needed.items():
        have = {r[1] for r in con.execute(f"pragma table_info({table})")}
        for name, decl in cols:
            if name not in have:
                try:
                    con.execute(f"alter table {table} add column {name} {decl}")
                except sqlite3.OperationalError:
                    pass


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
