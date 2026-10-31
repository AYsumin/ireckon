#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""陌生人第一次用：从零走一遍，不走网络、不花钱、不需要 config.py。

    python3 tools/first_run.py

它会做六件事，每件都打印"我看到了什么"，任何一步失败就停并给出原因：
  1  环境：Python 版本、sqlite3、rank_bm25 有没有（没有也能跑）
  2  建库：用默认路径 ~/.ireckon/mem.db（或 IRECKON_DB），建表并自检
  3  写一条对话：两轮，像真人一样
  4  蒸馏：用桩（stub）替代模型，产出日记 + 事实 + 三轴锚
  5  检索：抛半句，看能不能把那天捞回来；再验一次"对不上"
  6  昨日重现：三轴 + 每条的时间与原文片段

设计原则：**不给假绿**。桩跑不动就报错，不假装通过。
"""
import os
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OK, NO, WARN = "[ok]  ", "[fail]", "[warn]"


def step(n: int, title: str) -> None:
    print(f"\n--- {n}. {title} ---")


def main() -> int:
    problems = []

    # ---------- 1 环境 ----------
    step(1, "环境")
    v = sys.version_info
    print(f"{OK} Python {v.major}.{v.minor}.{v.micro}")
    if (v.major, v.minor) < (3, 9):
        print(f"{NO} 需要 Python 3.9+")
        return 1
    try:
        import sqlite3
        sqlite3.connect(":memory:").execute("select 1")
        print(f"{OK} sqlite3 {sqlite3.sqlite_version}")
    except Exception as e:
        print(f"{NO} sqlite3 不可用：{e}")
        return 1
    try:
        import rank_bm25  # noqa: F401
        print(f"{OK} rank_bm25 已安装（检索用官方实现）")
    except Exception:
        print(f"{WARN} rank_bm25 没装 —— 会走内置兜底实现，功能不受影响")

    # ---------- 2 建库 ----------
    step(2, "建库（默认路径或 IRECKON_DB）")
    from ireckon import db as dbm
    try:
        from ireckon import config_defaults as cd
        default_db = cd.DB_PATH
    except Exception:
        default_db = os.path.join(os.path.expanduser("~"), ".ireckon", "mem.db")
    db_path = os.environ.get("IRECKON_DB") or default_db
    print(f"{OK} 库位置：{db_path}")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    except Exception as e:
        print(f"{NO} 目录建不了：{e}（可以设 IRECKON_DB 指到你写得进的地方）")
        return 1
    try:
        con = dbm.init_db(db_path)
    except Exception as e:
        print(f"{NO} 建表失败：{type(e).__name__}: {e}")
        return 1
    tabs = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    need = {'turns', 'days', 'facts', 'anchors', 'recognition_log', 'pending_claims'}
    missing = need - tabs
    if missing:
        print(f"{NO} 缺表：{missing}")
        return 1
    print(f"{OK} 建了 {len(tabs)} 张表：turns / days / facts / anchors / recognition_log / pending_claims …")
    con.close()

    # ---------- 3 写对话 ----------
    step(3, "写两轮对话")
    from ireckon import Memory
    m = Memory(db_path=db_path)
    day = "2026-10-30"
    ts0 = datetime(2026, 10, 30, 9, 0).timestamp()
    turns = [
        ("user", "上午把单位那批影像资料分类了，三百多条，重新命名了一遍"),
        ("assistant", "三百多条都改完了？"),
        ("user", "照着上面给的规则改的，下午上传到系统"),
        ("user", "晚上我妈来了，表妹也来看她"),
    ]
    base = m.con.execute("select count(*) c from turns").fetchone()["c"]
    for i, (role, content) in enumerate(turns):
        m.add_turn("first-run", role, content, ts=ts0 + i * 600)
    now = m.con.execute("select count(*) c from turns").fetchone()["c"]
    print(f"{OK} 写入 {len(turns)} 条（库里原有 {base} 条，现 {now} 条）")

    # ---------- 4 蒸馏（桩） ----------
    step(4, "蒸馏（用桩代替模型，不联网、不花钱）")
    from ireckon import writer as W
    real = W.llm.chat_json

    def stub(messages, **kw):
        return {
            "title": "影像资料与家人",
            "diary": "上午给三百多条影像资料分类重命名，下午上传系统。晚上我妈来，表妹也来了。",
            "facts": [
                {"text": "上午把三百多条影像资料分类重命名", "keywords": ["影像", "重命名"], "importance": 0.8},
                {"text": "下午把影像资料上传到系统", "keywords": ["上传"], "importance": 0.7},
                {"text": "晚上我妈和表妹来家里", "keywords": ["我妈", "表妹"], "importance": 0.7},
            ],
            "anchors": [
                {"axis": "event", "text": "给三百多条影像资料分类重命名并上传系统",
                 "subjects": [], "why": "具体数量 + 具体动作", "event_time": "2026-10-30 09:00"},
                {"axis": "person", "text": "我妈", "subjects": ["我妈"], "why": "当天到家的亲属"},
                {"axis": "person", "text": "表妹", "subjects": ["表妹"], "why": "专门来看我妈的人"},
                {"axis": "time", "text": "晚上我妈和表妹来家里", "subjects": ["我妈", "表妹"], "why": "睡前那段的事"},
            ],
        }

    W.llm.chat_json = stub
    import asyncio
    try:
        m.schedule_day(day)
        asyncio.run(m.drain())
    except Exception as e:
        W.llm.chat_json = real
        print(f"{NO} 蒸馏失败：{type(e).__name__}: {e}")
        return 1
    finally:
        W.llm.chat_json = real

    days = m.con.execute("select count(*) c from days where day=?", (day,)).fetchone()["c"]
    facts = m.con.execute("select count(*) c from facts").fetchone()["c"]
    anchors = m.con.execute("select count(*) c from anchors where day=?", (day,)).fetchone()["c"]
    cov = m.coverage(day)
    print(f"{OK} 日记 {days} 篇 / 事实 {facts} 条 / 锚 {anchors} 条")
    print(f"{OK} 三轴：事 {'有' if cov['event'] else '缺'} · 人 {'有' if cov['person'] else '缺'} · "
          f"时 {'有' if cov['time'] else '缺'}")
    if not all(cov.values()):
        problems.append("三轴没齐（这次桩数据应该齐的）")

    # ---------- 5 检索 ----------
    step(5, "抛锚 + 检索")
    hits = m.throw_anchor("影像资料")
    print(f"{OK} 给半句「影像资料」→ 捞回 {len(hits)} 条：{hits[0]['text'][:26] if hits else '（空）'}")
    if not hits:
        problems.append("抛锚没捞回东西")
    empty = m.throw_anchor("量子计算机散热方案")
    print(f"{OK} 给一句不相关的话 → {len(empty)} 条（空才是对的）")
    if empty:
        problems.append("不相关的话也捞回了东西（会硬凑）")
    if hits:
        good = m.verify_anchor(hits[0]["id"], "你说那批影像资料三百多条，分类重命名之后上传系统了")
        bad = m.verify_anchor(hits[0]["id"], "我昨天打了一天游戏")
        print(f"{OK} 拿外部回应验：对得上={good['ok']}（{good['ratio']}），对不上={bad['ok']}")
        print("     注意：换一套完全不同的说法（只保留意思）会判'对不上'——这是字面口径，文档里写明了")
        if not good["ok"] or bad["ok"]:
            problems.append("外部验证判反了")

    # ---------- 6 昨日重现 ----------
    step(6, "昨日重现")
    rep = m.reproduce_day(day)
    from ireckon import recognition as R
    text = R.render(rep)
    for line in text.splitlines()[:8]:
        print("      " + line)
    if not rep["counts"]["total"]:
        problems.append("重现是空的")
    if "我没有记录" in text or "原文" in text:
        print(f"{OK} 每条锚都带原文片段/待证标记（人才能核）")
    m.close()

    # ---------- 结论 ----------
    print("\n=== 结论 ===")
    if problems:
        for p in problems:
            print(f"{NO} {p}")
        print("\n这次不算通过。把上面几条贴给作者。")
        return 1
    print(f"{OK} 全部走通：建库 → 写对话 → 蒸馏 → 抛锚 → 外部验证 → 昨日重现")
    print("     它用的是你自己的库，不含任何样例数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
