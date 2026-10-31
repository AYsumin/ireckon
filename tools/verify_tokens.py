# -*- coding: utf-8 -*-
"""实测注入预算：模拟 90 天规模语料（100 条事实 + 30 篇日记 + 12 轮对话），
量【记忆】块与【整块】的 token。

    python tools/verify_tokens.py

注意：198 的预算只约束【记忆】块；整块还包含最近对话，见 README §数字。
"""
import os, sys, tempfile, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config as C
from ireckon.api import Memory
from ireckon.recall import render_block

try:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    def real_tokens(s): return len(enc.encode(s))
    HAS_TIK = True
except ImportError:
    HAS_TIK = False
    def real_tokens(s): return len(s) / C.CHARS_PER_TOKEN

def heuristic_tokens(s): return len(s) / C.CHARS_PER_TOKEN

def main():
    tmp = tempfile.mkdtemp()
    m = Memory(db_path=os.path.join(tmp, "t.db"))

    # --- 模拟 90 天真实规模：100 条事实 + 30 篇日记 + 最近对话 ---
    topics = [
        ("家里下水管漏了，赔了三千块", "管子 漏水 赔偿"),
        ("师傅约好 2026-10-28 九点半来修管子", "师傅 九点半 维修"),
        ("养了一只叫 tom 的猫，三岁了", "猫 tom 宠物"),
        ("女儿读小学三年级，班主任姓王", "女儿 小学 班主任"),
        ("对青霉素过敏，开药要避开", "过敏 青霉素 用药"),
    ]
    con = m.con
    # 直接灌 100 条事实（模拟蒸馏产出）
    import time, json
    from ireckon import db as _db
    y_id = _db.upsert_year(con, "2026")
    mo_id = _db.upsert_month(con, "2026-10", y_id)
    w_id = _db.upsert_week(con, "2026-W40", mo_id)
    con.execute("insert into days(day,title,diary,year_id,month_id,week_id,created_at) values('2026-10-01','初始','',?,?,?,0)",
                (y_id, mo_id, w_id))
    day_id = con.execute("select id from days limit 1").fetchone()[0]
    for i in range(100):
        t, kw = topics[i % 5]
        con.execute("insert into facts(day_id,content,keywords,event_time,importance,status,source,created_at) values(?,?,?,?,?,'active','distill',?)",
                    (day_id, f"{t}（第{i//5+1}次提及的相关事项{i}）", json.dumps(kw.split()), "2026-10-01", 0.8, time.time()))
    # 30 篇日记
    for d in range(1, 31):
        con.execute("insert or ignore into days(day,title,diary,year_id,month_id,week_id,created_at) values(?,?,?,?,?,?,0)",
                    (f"2026-10-{d:02d}", f"第{d}天", "今天处理了管子的事。" * 20, y_id, mo_id, w_id))
    con.commit()
    m.retr.invalidate()

    # 最近 12 条对话（RECENT_TURNS 默认 12）
    for i in range(12):
        m.add_turn("u1", "user" if i % 2 == 0 else "assistant", f"这是第 {i} 条最近的对话内容，随便说点什么。")

    queries = [
        "师傅什么时候来修管子？",
        "我养的猫叫什么",
        "昨天发生了什么",
        "青霉素过敏的事",
        "班主任姓什么",
        "一个完全无关的问题_xyz",
    ]

    print(f"tiktoken 可用: {HAS_TIK}")
    print(f"预算 MAX_CONTEXT_TOKENS = {C.MAX_CONTEXT_TOKENS}（只约束【记忆】块）\n")
    mem_h, mem_r, full_h, full_r = [], [], [], []
    for q in queries:
        ctx = m.build_context("u1", q)
        mem_part = ctx.split("【最近对话】")[0]
        mh, mr = heuristic_tokens(mem_part), real_tokens(mem_part)
        fh, fr = heuristic_tokens(ctx), real_tokens(ctx)
        mem_h.append(mh); mem_r.append(mr); full_h.append(fh); full_r.append(fr)
        if mr > C.MAX_CONTEXT_TOKENS:
            print(f'    [超预算] 字符 {len(mem_part)} → {mr:.0f} token（预算 {C.MAX_CONTEXT_TOKENS}）per-query 明细')
        print(f"Q: {q}")
        print(f"   记忆块: 估算 {mh:.0f} tok / 实测 {mr:.0f} tok | 整块(含最近对话): {fh:.0f} / {fr:.0f}")
    print()
    print(f"记忆块   平均: 估算 {statistics.mean(mem_h):.0f} / 实测 {statistics.mean(mem_r):.0f} token")
    print(f"整块注入 平均: 估算 {statistics.mean(full_h):.0f} / 实测 {statistics.mean(full_r):.0f} token")
    print(f"\n【记忆】块最大: 实测 {max(mem_r):.0f} token（预算 198）")

if __name__ == "__main__":
    main()
