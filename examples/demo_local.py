# -*- coding: utf-8 -*-
"""接本地模型：把任何 OpenAI 兼容的本地端点接到 I Reckon 上。

适用：llama.cpp server / Ollama / vLLM / LM Studio / text-generation-webui，
只要它提供 /v1/chat/completions。

用法（PowerShell）：
    # llama.cpp（Bonsai 27B 三值量化，示例）
    set IRECKON_LOCAL=http://127.0.0.1:8080/v1
    set IRECKON_MODEL=bonsai-27b
    python examples/demo_local.py

    # Ollama
    set IRECKON_LOCAL=http://127.0.0.1:11434/v1
    set IRECKON_MODEL=qwen3:27b
    python examples/demo_local.py

它会做三件事：
 1. 把 BASE_URL / MODEL 指到本地端点（覆盖 config.py）
 2. 存两轮对话 → 蒸馏出日记与事实
 3. 问三个问题，打印：命中了几条记忆、注入了多少 token、模型答了什么
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as C  # noqa: E402
from ireckon import Memory  # noqa: E402


def main():
    base = os.environ.get('IRECKON_LOCAL', 'http://127.0.0.1:8080/v1')
    model = os.environ.get('IRECKON_MODEL', 'local-model')
    C.BASE_URL = base
    C.MODEL = model
    C.REASON_MODEL = model
    C.API_KEY = os.environ.get('IRECKON_KEY', 'local')   # 本地端点通常不校验 key，占位即可

    print(f'端点: {base}')
    print(f'模型: {model}')
    print('（如果连不上，先确认它在 /v1/models 上有响应）\n')

    tmp = tempfile.mkdtemp(prefix='ireckon-local-')
    m = Memory(db_path=os.path.join(tmp, 'local.db'))
    print(f'向量后端: {getattr(m, "vector_note", "") or "未开"}\n')

    # 1) 两轮真实对话（走本地模型）
    for user_text in ('我家楼下的下水管漏了，赔了邻居三千块，师傅约好九点半来修',
                      '我养了只猫叫 tom，三岁了，特别爱扑我尾巴'):
        try:
            ans = asyncio.run(m.reply('u1', user_text))
            print(f'[我] {user_text}')
            print(f'[它] {ans[:120]}\n')
        except Exception as e:
            print(f'对话失败（本地端点没起来？）: {str(e)[:160]}')
            return 1

    # 2) 蒸馏
    from datetime import date
    try:
        m.schedule_day(date.today().isoformat())
        asyncio.run(m.drain())
        days = m.con.execute('select count(*) from days').fetchone()[0]
        facts = m.con.execute('select count(*) from facts').fetchone()[0]
        print(f'蒸馏结果：日层 {days} 篇、事实 {facts} 条\n')
    except Exception as e:
        print(f'蒸馏失败: {str(e)[:160]}\n')

    # 3) 三个问题，看记忆有没有用上
    for q in ('我家管子的事花了多少钱', '我那只猫叫什么名字', '火星上做梦是什么感觉'):
        ctx = m.build_context('u1', q)
        mem_part = ctx.split('【最近对话】')[0]
        est = max(1, round(len(mem_part) / C.CHARS_PER_TOKEN))
        try:
            ans = asyncio.run(m.reply('u1', q))
        except Exception as e:
            ans = f'（调用失败：{str(e)[:80]}）'
        print(f'问: {q}')
        print(f'   记忆块约 {est} token')
        print(f'   答: {ans[:160]}\n')

    m.close()
    print('完成。以上每一步的 token 都来自本地模型，没有联网、没有花钱。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
