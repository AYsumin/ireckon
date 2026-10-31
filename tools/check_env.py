#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装之前先跑这个：把缺什么、该敲什么命令，一次性说清楚。

    python tools/check_env.py

它不联网、不花钱、不改任何东西。只回答一个问题：
「我这台机器，能不能跑起来？」
"""
import importlib
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OK = "  [OK]  "
NO = "  [缺]  "
WARN = "  [注意]"

problems = []


def line(mark, text):
    print(mark + text)


def main():
    print("=== I Reckon（俺寻思） 环境自检 ===\n")

    # 1) Python 版本
    v = sys.version_info
    if v >= (3, 9):
        line(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        line(NO, f"Python {v.major}.{v.minor} —— 需要 3.9 及以上")
        problems.append("升级 Python 到 3.9+")

    # 2) sqlite3（标准库，几乎一定有）
    try:
        sqlite3.connect(":memory:").execute("select 1")
        line(OK, f"sqlite3 {sqlite3.sqlite_version}")
    except Exception as e:
        line(NO, f"sqlite3 不可用：{e}")
        problems.append("Python 安装不完整，重新安装（勾选 sqlite3）")

    # 3) WAL 能不能开（有些网络盘不行）
    try:
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.db")
        c = sqlite3.connect(p)
        mode = c.execute("pragma journal_mode=WAL").fetchone()[0]
        c.close()
        if str(mode).lower() == "wal":
            line(OK, "SQLite 可以开 WAL")
        else:
            line(WARN, f"WAL 没能开启（当前 {mode}）—— 数据目录可能在网络盘上，换本地盘")
    except Exception as e:
        line(WARN, f"WAL 测试失败：{str(e)[:60]}")

    # 4) 依赖
    try:
        importlib.import_module("rank_bm25")
        line(OK, "rank_bm25 已安装")
    except Exception:
        line(WARN, "没装 rank_bm25 —— 会用内置的兜底 BM25（结果一致，速度略慢）")
        print("        想装就敲：pip install rank_bm25==0.2.2")

    # 5) 配置
    try:
        import config as C
        key = getattr(C, "API_KEY", "")
        if key and not key.startswith("sk-你的"):
            line(OK, "config.py 里已经填了 API_KEY")
        else:
            line(NO, "config.py 里的 API_KEY 还是占位符")
            problems.append("编辑 config.py，填上 API_KEY")
        line(OK, f"参数：TOP_K={C.TOP_K}, CLIFF_RATIO={C.CLIFF_RATIO}, "
                 f"MAX_CONTEXT_TOKENS={C.MAX_CONTEXT_TOKENS}")
        d = os.path.dirname(os.path.abspath(C.DB_PATH)) or "."
        if os.access(d, os.W_OK):
            line(OK, f"数据目录可写：{d}")
        else:
            line(NO, f"数据目录不可写：{d}")
            problems.append(f"给目录写权限或改 config.DB_PATH：{d}")
    except Exception as e:
        line(NO, f"读不到 config.py：{str(e)[:80]}")
        problems.append("确认在项目根目录下运行（config.py 与 ireckon/ 同级）")

    # 6) 库表能不能建
    try:
        from ireckon import Memory
        tmp = os.path.join(tempfile.mkdtemp(), "probe.db")
        m = Memory(db_path=tmp)
        tables = {r["name"] for r in m.con.execute(
            "select name from sqlite_master where type='table'")}
        need = {"turns", "days", "weeks", "months", "years", "facts", "distill_log"}
        m.close()
        if need <= tables:
            line(OK, "五层表建得出来（turns/days/weeks/months/years/facts）")
        else:
            line(NO, f"建表不全，缺：{need - tables}")
            problems.append("sql/schema.sql 不完整或损坏")
    except Exception as e:
        line(NO, f"初始化失败：{str(e)[:100]}")
        problems.append("看上面报错；或先跑 python tests/test_smoke.py")

    print()
    if problems:
        print("需要你做的事：")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
        print("\n做完再跑一次：python tools/check_env.py")
        return 1
    print("全部就绪。下一步：python tests/test_smoke.py（不联网）")
    print("然后：python examples/demo_chat.py（真对话）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
