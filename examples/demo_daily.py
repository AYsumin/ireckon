# -*- coding: utf-8 -*-
"""只跑蒸馏：把某一天的对话压成一篇日记 + 若干事实。

用法：
    python examples/demo_daily.py 2026-10-27
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ireckon import Memory  # noqa: E402


async def main():
    day = sys.argv[1] if len(sys.argv) > 1 else None
    m = Memory()
    m.schedule_day(day)
    await m.drain()

    row = m.con.execute(
        "select day, title, diary from days order by id desc limit 1").fetchone()
    if row:
        print(f"【{row['day']}】{row['title']}")
        print(row["diary"])
    print()
    print("事实：")
    for r in m.con.execute("select id, content from facts order by id desc limit 12"):
        print(f"  [{r['id']}] {r['content']}")
    print()
    print("库状态：", m.health())
    m.close()


if __name__ == "__main__":
    asyncio.run(main())
