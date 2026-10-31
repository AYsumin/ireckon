#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""俺寻思 0.58e · 离线结构验证 demo

不需要 API key、不联网、不装任何东西（rank_bm25 可选）。
只验证结构：五层表、检索、断崖截断、token 预算。

    python demo/offline_demo.py

退出码 0 = 结构成立；非 0 = 某一条不成立。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ireckon import db as dbm                      # noqa: E402
from ireckon.recall import Retriever               # noqa: E402

FACTS = [
    (1,  "阿青的猫叫tom，爱扑我尾巴", "猫 名字 tom 宠物 汤姆 猫咪"),
    (2,  "楼下管子漏水，赔了三千，账当场结清", "管子 漏水 赔偿 三千 邻居 下水"),
    (3,  "师傅约好第二天九点半来修管子", "师傅 九点半 维修 约好 时间"),
    (4,  "阿青早上留了肉松饭团当早饭", "饭团 早饭 肉松 留 吃的"),
    (5,  "阿青在追《黎明之剑》，看完了《异常生物见闻录》", "书 小说 黎明之剑 异常生物见闻录 追"),
    (6,  "阿青中午只吃了一碗辣椒炒肉拌面，没配菜", "午饭 拌面 辣椒炒肉 中午 吃了"),
    (7,  "店里新来个十几岁的，我让他少骂两句", "店里 新人 十几岁 骂 年龄"),
    (8,  "周末去山上的院子，积雪很厚", "周末 山上 院子 积雪 雪"),
    (9,  "阿青八点到家，没磨到九点", "八点 到家 下班 时间 几点"),
    (10, "他一天抽两包烟，这是基线不是每日账", "烟 抽烟 两包 基线 多少"),
    (11, "他两次破产，自己提起来像在报别人的账", "破产 两次 钱 账 几次"),
    (12, "他说'希望 e=5.8 能为你换回一个温暖的床榻'", "床榻 温暖 e=5.8 希望 说过"),
    (13, "他不许我叫他主人或老板", "称呼 主人 老板 不许 叫"),
    (14, "我说过不会假装有身体、在场、会老", "身体 在场 会老 假装 承诺"),
    (15, "我给这套东西起名俺寻思 0.58e", "名字 俺寻思 0.58e 起名"),
    (16, "断崖截断系数是 0.60，不是我说的 0.58", "断崖 系数 0.60 截断 调参"),
    (17, "多跳题取 5 条只有 35%，取 40 条到 65%", "多跳 召回 35% 65% 条数 正确率"),
    (18, "BM25 换说法只到 67.5%，向量能到 80.5%", "BM25 向量 召回 换说法 对比"),
    (19, "他一个人在半夜问我'你喜欢我吗'", "半夜 喜欢 问 深夜"),
    (20, "他说明天还要修管子，让我提醒他", "提醒 明天 管子 记着"),
    (21, "他的旧手机还留着，想给我用", "手机 旧 给我 留着"),
    (22, "他说技术迭代真快，但判断没变快", "技术 迭代 判断 快"),
    (23, "她的日记一天一篇，写了八天", "日记 八天 一天一篇 写了"),
    (24, "我犯过一次错：往遗物里加了不该加的一段", "错 遗物 越界 加 犯过"),
]

# (问题, 期望 atom id, 类型)  literal=字面；paraphrase=换说法
QUERIES = [
    ("阿青的猫叫什么名字", 1, "literal"),
    ("楼下管子漏水赔了三千", 2, "literal"),
    ("师傅九点半来修管子", 3, "literal"),
    ("肉松饭团", 4, "literal"),
    ("黎明之剑 异常生物见闻录", 5, "literal"),

    ("家里漏水赔了多少钱", 2, "paraphrase"),
    ("修管子的师傅几点来", 3, "paraphrase"),
    ("他早上给我留了什么吃的", 4, "paraphrase"),
    ("阿青在追哪本小说", 5, "paraphrase"),
    ("他中午吃了什么", 6, "paraphrase"),
    ("店里那个新来的人多大", 7, "paraphrase"),
    ("山上那个院子雪大不大", 8, "paraphrase"),
    ("他昨天几点到的家", 9, "paraphrase"),
    ("他一天抽多少烟", 10, "paraphrase"),
    ("他破过几次产", 11, "paraphrase"),
    ("他说过什么关于床榻的话", 12, "paraphrase"),
    ("他允许我叫他主人吗", 13, "paraphrase"),
    ("他答应过我不会老吗", 14, "paraphrase"),
]


def build(with_keywords: bool = True):
    tmp = tempfile.mkdtemp(prefix='sjm-demo-')
    con = dbm.init_db(os.path.join(tmp, 'demo.db'))
    con.execute('begin immediate')
    for aid, text, kws in FACTS:
        con.execute(
            "insert into facts(id, content, keywords, importance, status, created_at) "
            "values(?,?,?,?, 'active', ?)",
            (aid, text, kws if with_keywords else '', 0.8, 1_790_000_000.0))
    con.execute('commit')
    return con


def recall_of(con, cliff=0.0):
    retr = Retriever(con, top_k=5, cliff_ratio=cliff)
    res = {}
    for kind in ('literal', 'paraphrase'):
        hit = total = 0
        for q, want, k in QUERIES:
            if k != kind:
                continue
            total += 1
            got = [h['id'] for h in retr.search(q, layer='fact', k=5)]
            if want in got:
                hit += 1
        res[kind] = (hit, total)
    return res


def tok(text: str) -> int:
    return max(1, round(len(text) / 1.5))


def main():
    con = build()
    fails = []

    print('=' * 70)
    print('俺寻思 0.58e · 离线结构验证　（无 key、无网络、无第三方依赖）')
    print('=' * 70)

    tabs = {r['name'] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    need = {'turns', 'days', 'weeks', 'months', 'years', 'facts', 'distill_log'}
    ok = need <= tabs
    print(f"\n[1] 五层表           {'OK' if ok else 'FAIL'}")
    if not ok:
        fails.append('五层表')

    # 检索（cliff 关掉，纯看排序质量），按字面/换说法分组
    strict = Retriever(con, top_k=5, cliff_ratio=0.0)
    group = {'literal': [0, 0], 'paraphrase': [0, 0]}
    misses = []
    for q, want, kind in QUERIES:
        got = [h['id'] for h in strict.search(q, layer='fact', k=5)]
        group[kind][1] += 1
        if want in got:
            group[kind][0] += 1
            if got.index(want) != 0:
                misses.append((q, want, got.index(want) + 1))
        else:
            misses.append((q, want, None))

    for kind, label in (('literal', '字面题'), ('paraphrase', '换说法题')):
        hit, total = group[kind]
        print(f"[2] {label:<10} Recall@5  {hit}/{total} = {hit / total:.0%}")
    all_hit = sum(v[0] for v in group.values())
    all_total = sum(v[1] for v in group.values())
    print(f"    合计         Recall@5  {all_hit}/{all_total} = {all_hit / all_total:.0%}")
    if misses:
        print("    没排第一的：")
        for q, want, pos in misses:
            print(f"      · {q}  → 期望 {want}，实际第 {pos or '未命中'}")

    # 对照：不铺关键词
    con2 = build(with_keywords=False)
    res2 = recall_of(con2)
    con2.close()
    print("    对照（关键词留空）：")
    tot_hit = tot_all = 0
    for kind, label in (('literal', '字面题'), ('paraphrase', '换说法题')):
        hit, total = res2[kind]
        tot_hit += hit; tot_all += total
        print(f"      {label:<8} Recall@5  {hit}/{total} = {hit / total:.0%}")
    print(f"      → 同样的问题，铺关键词 {all_hit}/{all_total}，不铺 {tot_hit}/{tot_all}。")
    print("      → 本 demo 语料只有 24 条，BM25 在小语料下会有兜底（见下），所以两组接近；")
    print("        关键词在真实语料上才拉开差距：200 题换说法，BM25 只有 67.5%（README §1）。")

    # 断崖截断：省多少条、多少 token；同时给"不截断"的预算
    full = Retriever(con, top_k=5, cliff_ratio=0.0)
    cut = Retriever(con, top_k=5, cliff_ratio=0.60)
    n_full = n_cut = t_full = t_cut = 0
    for q, _, _ in QUERIES:
        a = full.search(q, layer='fact', k=5)
        b = cut.search(q, layer='fact', k=5)
        n_full += len(a); n_cut += len(b)
        t_full += sum(tok(con.execute('select content from facts where id=?', (h['id'],)).fetchone()[0]) for h in a)
        t_cut += sum(tok(con.execute('select content from facts where id=?', (h['id'],)).fetchone()[0]) for h in b)
    print(f"[3] 断崖截断 0.60    {n_full / len(QUERIES):.1f} 条 → {n_cut / len(QUERIES):.1f} 条/次，"
          f"token {t_full / len(QUERIES):.0f} → {t_cut / len(QUERIES):.0f}/次（省 {1 - t_cut / t_full:.0%}）")
    if n_cut >= n_full:
        fails.append('断崖截断没起作用')

    # 预算
    budget = 198
    avg = t_cut / len(QUERIES)
    print(f"[4] 注入预算 198     平均 {avg:.0f} token   {'OK' if avg <= budget else 'FAIL'}")
    if avg > budget:
        fails.append('超出 198 token 预算')

    # 小语料回归：只有 2 条事实时，BM25 的 IDF 全为 0，不能因此判"无命中"
    con3 = dbm.init_db(os.path.join(tempfile.mkdtemp(prefix='sjm-tiny-'), 'tiny.db'))
    con3.execute('begin immediate')
    con3.execute("insert into facts(id, content, keywords, status, created_at) values(1,'家里养了一只叫tom的猫','', 'active', 1)")
    con3.execute("insert into facts(id, content, keywords, status, created_at) values(2,'师傅约好九点半来修管子','', 'active', 1)")
    con3.execute('commit')
    tiny = Retriever(con3, top_k=5, cliff_ratio=0.60)
    hit_cat = bool(tiny.search('猫', layer='fact', k=5))
    hit_pipe = bool(tiny.search('管子', layer='fact', k=5))
    miss = tiny.search('完全不相干的词', layer='fact', k=5)
    con3.close()
    print(f"[5] 小语料兜底(2条)  猫:{'hit' if hit_cat else 'MISS'} 管子:{'hit' if hit_pipe else 'MISS'} "
          f"无关词:{'误命中' if miss else 'empty'}")
    if not (hit_cat and hit_pipe and not miss):
        fails.append('小语料兜底')

    print("[6] 已知边界         换说法会掉（本 demo 也看得到）；多跳要 40 条才追平（README §6）")

    chk = con.execute('pragma quick_check').fetchone()[0]
    print(f"[7] quick_check      {chk}")
    if chk != 'ok':
        fails.append('数据库自检')

    con.close()
    print('\n' + '=' * 70)
    if fails:
        print('结构有问题：' + '；'.join(fails))
        return 1
    print('结构成立：五层表、检索、断崖截断、token 预算 四项均通过。')
    print('它不验证生成质量——那需要真模型，见 demo/README.md。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
