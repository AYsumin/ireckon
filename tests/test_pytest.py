# -*- coding: utf-8 -*-
"""标准测试套件（pytest 可收集；没有 pytest 也能直接跑）。

    pytest -q            # 有 pytest 时
    python tests/test_pytest.py    # 没有 pytest 时（自己跑一遍所有用例）
"""
import asyncio
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ireckon import Memory, db, router  # noqa: E402
from ireckon.recall import Retriever, render_block  # noqa: E402


# ---------- 准备 ----------
def _mk():
    tmp = tempfile.mkdtemp(prefix='ireckon-test-')
    return Memory(db_path=os.path.join(tmp, 't.db'))


class StubLLM:
    """不联网的蒸馏桩。

    2026-10-31：蒸馏改成**一次调用产齐三样**（日记 / 事实 / 锚），
    所以桩也先认这一种；下面的旧分支留着，是为了还能单独测老的三个提示词。
    """
    calls = []

    @staticmethod
    def chat_json(messages, **kw):
        StubLLM.calls.append(messages)
        sys_msg = (messages[0] or {}).get('content', '')
        if '一次产齐三样' in sys_msg:
            return {
                "title": "修水管",
                "diary": "今天修了水管。",
                "facts": [{"text": "下水管漏了赔了三千", "keywords": ["管子", "三千"], "importance": 0.8},
                          {"text": "师傅九点半来", "keywords": ["师傅", "九点半"], "importance": 0.7}],
                "anchors": [
                    {"axis": "event", "text": "找师傅修下水管，赔了三千", "subjects": [], "why": "具体数字"},
                    {"axis": "person", "text": "师傅九点半来家里", "subjects": ["师傅"], "why": "具体时刻"},
                    {"axis": "time", "text": "上午九点半师傅上门", "subjects": ["师傅"], "why": "具体时刻"},
                ],
            }
        if '日记' in sys_msg:
            return {"title": "修水管", "diary": "今天修了水管。"}
        if '锚' in sys_msg:
            return {"anchors": [{"axis": "event", "text": "找师傅修下水管，赔了三千"}]}
        return {"facts": [{"text": "下水管漏了赔了三千", "keywords": ["管子", "三千"], "importance": 0.8},
                          {"text": "师傅九点半来", "keywords": ["师傅", "九点半"], "importance": 0.7}]}


# ---------- 用例 ----------
def test_schema_and_wal():
    m = _mk()
    tabs = {r['name'] for r in m.con.execute(
        "select name from sqlite_master where type='table'")}
    need = {'turns', 'days', 'weeks', 'months', 'years', 'facts', 'distill_log'}
    assert need <= tabs, f'缺表: {need - tabs}'
    assert m.con.execute('pragma journal_mode').fetchone()[0].lower() == 'wal'
    m.close()


def test_add_turn_roundtrip():
    m = _mk()
    tid = m.add_turn('u1', 'user', '我今天把下水管修了')
    assert isinstance(tid, int) and tid > 0
    n = m.con.execute('select count(*) from turns').fetchone()[0]
    assert n == 1
    m.close()


def test_distill_with_stub():
    import ireckon.writer as w
    m = _mk()
    old = w.llm.chat_json
    w.llm.chat_json = StubLLM.chat_json
    try:
        from datetime import datetime as _dt
        ts = _dt(2026, 10, 28, 12, 0).timestamp()   # 蒸馏按"日"取材料，时间要落在那天
        m.add_turn('u1', 'user', '下水管漏了，赔了三千，师傅九点半来', ts=ts)
        m.schedule_day('2026-10-28')
        asyncio.run(m.drain())
    finally:
        w.llm.chat_json = old
    days = m.con.execute('select count(*) from days').fetchone()[0]
    facts = m.con.execute('select count(*) from facts').fetchone()[0]
    assert days >= 1, '没有生成日层'
    assert facts >= 1, '没有抽取事实'
    m.close()


def test_layering_pointers():
    """日层必须指向上三层（用真实列名，不假设 schema）。"""
    m = _mk()
    cols = [r[1] for r in m.con.execute('pragma table_info(days)')]
    need = {'week_id', 'month_id', 'year_id'}
    assert need <= set(cols), f'days 表缺指针列: {need - set(cols)}（实际: {cols}）'
    m.close()


def test_router_rules():
    assert router.route('昨天那个师傅几点来') == 'day'
    assert router.route('上周我们去哪了') == 'week'
    assert router.route('他喜欢吃什么') == 'fact'


def test_recall_and_budget():
    m = _mk()
    m.con.execute('begin immediate')
    m.con.execute("insert into facts(id, content, keywords, status, created_at) "
                  "values(1,'家里下水管漏了，赔了三千','下水管 漏水 三千','active',1)")
    m.con.execute("insert into facts(id, content, keywords, status, created_at) "
                  "values(2,'师傅约好九点半来修管子','师傅 九点半 修','active',1)")
    m.con.execute('commit')
    m.add_turn('u1', 'user', '管子修好了吗')
    ctx = m.build_context('u1', '昨天的管子怎么样了')
    assert '记忆' in ctx
    assert ('三千' in ctx) or ('九点半' in ctx), f'记忆块没命中: {ctx[:120]}'
    m.close()


def test_cliff_truncation_saves():
    m = _mk()
    m.con.execute('begin immediate')
    for i in range(1, 6):
        m.con.execute("insert into facts(id, content, keywords, status, created_at) "
                      "values(?,?,?,'active',1)",
                      (i, f'第{i}条关于管子的记忆内容比较长一些', '管子 记忆'))
    m.con.execute('commit')
    full = Retriever(m.con, top_k=5, cliff_ratio=0.0).search('管子', layer='fact', k=5)
    cut = Retriever(m.con, top_k=5, cliff_ratio=0.6).search('管子', layer='fact', k=5)
    assert len(full) >= 2, '全量检索应有结果'
    assert len(cut) <= len(full)
    m.close()


def test_tiny_corpus_no_false_empty():
    """2 条语料时 IDF 全 0，不能因此判"无命中"（GLM-5.3-flash 发现）。"""
    m = _mk()
    m.con.execute('begin immediate')
    m.con.execute("insert into facts(id, content, keywords, status, created_at) "
                  "values(1,'家里养了一只叫tom的猫','', 'active',1)")
    m.con.execute("insert into facts(id, content, keywords, status, created_at) "
                  "values(2,'师傅约好九点半来修管子','', 'active',1)")
    m.con.execute('commit')
    r = Retriever(m.con, top_k=5, cliff_ratio=0.6)
    assert r.search('猫', layer='fact', k=5), '小语料下「猫」不该为空'
    assert r.search('管子', layer='fact', k=5), '小语料下「管子」不该为空'
    assert not r.search('完全不相干的词', layer='fact', k=5), '无关词不该误命中'
    m.close()


def test_negative_score_kept():
    """负分命中不能被当无命中（豆包发现）。"""
    from ireckon.bm25 import BM25Index

    class Fake:
        def get_scores(self, q):
            return [-0.53]

    idx = BM25Index([(1, '阿青的猫叫tom')])
    idx.bm25 = Fake()
    assert idx.search('猫', limit=5) == [(1, -0.53)]


def test_health_ok():
    m = _mk()
    assert m.con.execute('pragma quick_check').fetchone()[0] == 'ok'
    m.close()


def test_pip_install_without_config():
    """没有 config.py 时也能 import（pip 安装场景）。"""
    import subprocess
    code = (f"import sys; sys.path.insert(0,{ROOT!r});"
            "import ireckon; print('ok')")
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    assert p.returncode == 0, p.stderr[-300:]


def test_hybrid_with_fake_embed():
    """向量融合：用假 embed（不联网），换说法也要能命中。"""
    from ireckon import vector_backend as vb

    def fake_embed(texts):
        out = []
        for t in texts:
            v = [0.0] * 8
            if '猫' in t or 'tom' in t.lower():
                v[0] = 1.0
            if '管子' in t or '师傅' in t:
                v[1] = 1.0
            v[7] = 0.1
            out.append(v)
        return out

    m = _mk()
    m.con.execute('begin immediate')
    m.con.execute("insert into facts(id,content,keywords,status,created_at) values(1,'家里养了一只叫tom的猫','','active',1)")
    m.con.execute("insert into facts(id,content,keywords,status,created_at) values(2,'师傅约好九点半来修管子','','active',1)")
    m.con.execute('commit')
    from ireckon.recall import set_vector
    vb.enable_vector(fake_embed)
    set_vector(True)
    try:
        hits = m.retr.search('我养的那只猫叫啥', layer='fact', k=3)
        assert hits and hits[0]['id'] == 1, f'融合检索没命中: {hits}'
    finally:
        set_vector(False)
        vb.disable_vector()
        m.close()


def test_correct_explicit():
    """手动纠错：旧记录变 superseded、新记录能查到、关键词继承。"""
    m = _mk()
    m.con.execute('begin immediate')
    m.con.execute("insert into facts(id,content,keywords,status,created_at) "
                  "values(1,'家里养了一只叫tom的猫','[\"猫\",\"tom\"]','active',1)")
    m.con.execute('commit')
    m.retr.invalidate('fact')
    new_id = m.correct(1, '猫改名了，现在叫mimi')
    old = m.con.execute('select status, keywords from facts where id=1').fetchone()
    assert old['status'] == 'superseded', '旧记录没被取代'
    new = m.con.execute('select keywords from facts where id=?', (new_id,)).fetchone()
    assert '猫' in (new['keywords'] or ''), '新记录没继承关键词'
    hits = m.retr.search('我养的猫叫什么', layer='fact', k=5)
    ids = [h['id'] for h in hits]
    assert 1 not in ids, f'旧的还被捞出来: {ids}'
    m.close()


def test_context_returns_cost():
    """build_context(return_cost=True) 返回 (文本, 实测 token)；默认仍是字符串。"""
    m = _mk()
    m.con.execute('begin immediate')
    m.con.execute("insert into facts(id,content,keywords,status,created_at) "
                  "values(1,'家里下水管漏了赔了三千','[\"管子\"]','active',1)")
    m.con.execute('commit')
    m.retr.invalidate('fact')
    text = m.build_context('u1', '管子怎么了')
    assert isinstance(text, str)
    text2, cost = m.build_context('u1', '管子怎么了', return_cost=True)
    assert isinstance(cost, int) and cost > 0
    assert text2.startswith('【记忆】')
    m.close()


# ---------- 没有 pytest 时的自跑 ----------
if __name__ == '__main__':
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f'  OK   {name}')
        except Exception as e:
            bad += 1
            print(f'  FAIL {name}: {e}')
    print(f'\n{len(fns) - bad}/{len(fns)} 通过')
    sys.exit(1 if bad else 0)
