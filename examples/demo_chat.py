# -*- coding: utf-8 -*-
"""最小对话示例：三行接上。

跑之前：
    1. pip install -r requirements.txt
    2. 在 config.py 里填 API_KEY
    3. python examples/demo_chat.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ireckon import Memory  # noqa: E402


async def main():
    m = Memory()

    # 直接把这三句写成"昨天"的对话（不改时间戳，避免相对时间错位）
    import time
    from datetime import date, datetime, timedelta
    yday = (date.today() - timedelta(days=1)).isoformat()
    base = datetime.strptime(yday, "%Y-%m-%d").timestamp() + 20 * 3600   # 昨天 20:00
    m.add_turn("demo", "user", "家里下水管漏了，把楼下天花板泡透了，赔了三千", ts=base)
    m.add_turn("demo", "assistant", "三千不是小数，明天先叫师傅来看", ts=base + 30)
    m.add_turn("demo", "user", "师傅说明天九点半来", ts=base + 60)

    # 蒸馏「昨天」，模拟凌晨 04:00 的那一步
    m.schedule_day(yday)
    await m.drain()

    print("库状态：", m.health())
    print("\n--- 问一句 ---")
    print(await m.reply("demo", "管子的事怎么样了？"))
    print("\n--- 换个时间词 ---")
    print(await m.reply("demo", "昨天那个师傅几点来？"))
    m.close()


if __name__ == "__main__":
    asyncio.run(main())
