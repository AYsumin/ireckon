# -*- coding: utf-8 -*-
"""冒烟测试：不联网、不花钱，验证五层建表 + 检索 + 蒸馏链路。

    python tests/test_smoke.py
"""
import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config as C  # noqa: E402
from ireckon import db, llm, router  # noqa: E402
from ireckon.recall import Retriever  # noqa: E402
from ireckon.api import Memory  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + f"  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def fake_chat(messages, **kw):
    return "（测试回复）"


def fake_chat_json(messages, **kw):
    sys_prompt = messages[0]["content"]
    if "日记" in sys_prompt:
        return {"title": "管子漏了", "diary": "他说下水管漏了，赔了三千。师傅九点半来。"}
    return {"facts": [
        {"text": "家里下水管漏了，赔了三千", "keywords": ["管子", "漏水", "赔偿"], "importance": 0.9},
        {"text": "师傅约好九点半来修管子", "keywords": ["师傅", "九点半", "维修"], "importance": 0.8},
    ]}


def main():
    llm.chat = fake_chat
    llm.chat_json = fake_chat_json

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.db")
    C.DB_PATH = path
    C.AUTO_ROUTE = True

    print("1) 建库与五层表")
    m = Memory(db_path=path)
    tabs = {r["name"] for r in m.con.execute(
        "select name from sqlite_master where type='table'")}
    for t in ("turns", "days", "weeks", "months", "years", "facts", "distill_log"):
        check(f"表 {t}", t in tabs)
    mode = m.con.execute("pragma journal_mode").fetchone()[0]
    check("WAL 已开", str(mode).lower() == "wal", mode)

    print("2) 写入对话")
    m.add_turn("s1", "user", "家里下水管漏了，赔了三千")
    m.add_turn("s1", "assistant", "三千不是小数")
    m.add_turn("s1", "user", "师傅说九点半来")
    n = m.con.execute("select count(*) c from turns").fetchone()["c"]
    check("turns=3", n == 3, str(n))

    print("3) 蒸馏（异步后台）")
    from datetime import date
    today = date.today().isoformat()
    m.schedule_day(today)
    asyncio.run(m.drain())
    days = m.con.execute("select count(*) c from days").fetchone()["c"]
    facts = m.con.execute("select count(*) c from facts where status='active'").fetchone()["c"]
    check("日层 1 篇", days == 1, str(days))
    check("事实 2 条", facts == 2, str(facts))

    print("4) 时间分层与指针外键")
    row = m.con.execute("select week_id, month_id, year_id from days").fetchone()
    check("日层指向上三层", all(row[k] for k in ("week_id", "month_id", "year_id")), str(dict(row)))

    print("5) 路由")
    check("昨天 → day", router.route("昨天那个师傅几点来") == "day",
          router.explain("昨天那个师傅几点来"))
    check("上周 → week", router.route("上周我们去哪了") == "week")
    check("普通问句 → fact", router.route("他喜欢吃什么") == "fact")

    print("6) 检索与上下文预算")
    ctx = m.build_context("s1", "管子的事怎么样了")
    check("上下文里有记忆", "记忆" in ctx and "下水管" in ctx, ctx[:60].replace("\n", " "))
    toks = len(ctx) / C.CHARS_PER_TOKEN
    check("上下文未超预算太多", toks < 1200, f"≈{toks:.0f} token")

    print("7) 断崖截断")
    hits = m.retr.search("管子", layer="fact", k=5)
    check("能命中事实层", len(hits) >= 1, str(hits[:2]))

    print("8) 小语料兜底（2 条事实时 IDF 全为 0，不能判无命中）")
    tmp2 = tempfile.mkdtemp()
    m2 = Memory(db_path=os.path.join(tmp2, 'tiny.db'))
    m2.con.execute('begin immediate')
    m2.con.execute("insert into facts(id, content, keywords, status, created_at) values(1,'家里养了一只叫tom的猫','', 'active', 1)")
    m2.con.execute("insert into facts(id, content, keywords, status, created_at) values(2,'师傅约好九点半来修管子','', 'active', 1)")
    m2.con.execute('commit')
    r2 = Retriever(m2.con, top_k=5, cliff_ratio=0.60)
    check("2 条语料能命中「猫」", bool(r2.search('猫', layer='fact', k=5)))
    check("2 条语料能命中「管子」", bool(r2.search('管子', layer='fact', k=5)))
    check("无关词不误命中", not r2.search('完全不相干的词', layer='fact', k=5))
    m2.close()
    print()

    print("9) 数据库健康")
    h = m.health()
    check("quick_check=ok", h["quick_check"] == "ok", h["quick_check"])

    m.close()
    print()
    if FAILED:
        print("失败：", FAILED)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
