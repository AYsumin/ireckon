# -*- coding: utf-8 -*-
"""昨日重现（0.2.3）的测试。

要点：
  · 重现必须带"时间 + 原文片段"——不然人没法外部核对（他 2026-10-31 说的）
  · 没认定 = unknown，不是 confirmed；不许把沉默当认可
  · 认不出（disputed）与记错了（corrected）是两件事，分开记
  · 连续没人核要有数（unanswered_days），机制不能悄悄失效
"""
import os
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ireckon import Memory, recognition as R     # noqa: E402


@pytest.fixture()
def mem():
    import shutil
    tmp = tempfile.mkdtemp(prefix='recog-test-')
    m = Memory(db_path=os.path.join(tmp, 'r.db'))
    ts0 = datetime(2026, 10, 30, 9, 0).timestamp()
    for i, (role, content) in enumerate([
        ('user', '上午把单位那批影像资料分类了，三百多条，重新命名了一遍'),
        ('assistant', '三百多条都改完了？'),
        ('user', '晚上我妈来了，表妹也来看她'),
        ('user', '睡前在弄 ireckon 0.2.0，跟 dsh 聊到快三点'),
    ]):
        m.add_turn('life', role, content, ts=ts0 + i * 900)
    yield m
    m.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _seed(mem):
    a1 = mem.add_anchor('2026-10-30', 'event', '给三百多条影像资料分类并重命名', ['同事'])
    a2 = mem.add_anchor('2026-10-30', 'person', '我妈和表妹来家里', ['我妈', '表妹'])
    a3 = mem.add_anchor('2026-10-30', 'time', '睡前在弄 ireckon 0.2.0')
    return a1, a2, a3


def test_reproduce_has_three_axes_and_source(mem):
    _seed(mem)
    rep = mem.reproduce_day('2026-10-30')
    assert rep['coverage'] == {'event': True, 'person': True, 'time': True}
    first = rep['axes']['event'][0]
    assert first['source'], '必须带原文片段，否则人没法外部核对'
    assert first['source']['at'] and first['source']['snippet']


def test_silence_is_unknown_not_confirmed(mem):
    _seed(mem)
    rep = mem.reproduce_day('2026-10-30')
    assert rep['counts']['unknown'] == 3
    assert rep['rate'] is None, '一条都没答时不该算出互认率'


def test_verdicts_are_recorded_and_rates_move(mem):
    a1, a2, a3 = _seed(mem)
    mem.record_verdict('2026-10-30', a1, 'confirmed')
    mem.record_verdict('2026-10-30', a2, 'disputed', note='认不出')
    mem.record_verdict('2026-10-30', a3, 'corrected', corrected_text='睡前在弄 0.2.2',
                       evidence='原文 02:40 他说在弄 0.2.2')
    rep = mem.reproduce_day('2026-10-30')
    assert rep['counts'] == {'total': 3, 'confirmed': 1, 'disputed': 1, 'hedged': 0,
                             'corrected': 1, 'unknown': 0}
    assert rep['rate'] == 0.333
    # 改锚是"新写一条 + 旧的标 superseded"，所以当天的时轴上看到的是新文本
    assert rep['axes']['time'][0]['text'] == '睡前在弄 0.2.2'
    assert rep['axes']['time'][0]['verdict'] == 'corrected'


def test_bad_verdict_rejected(mem):
    a1, _, _ = _seed(mem)
    with pytest.raises(ValueError):
        mem.record_verdict('2026-10-30', a1, 'probably')


def test_verdict_on_missing_anchor_rejected(mem):
    with pytest.raises(ValueError):
        mem.record_verdict('2026-10-30', 9999, 'confirmed')


def test_stats_counts_unanswered_days(mem):
    _seed(mem)
    # 10-31 这天没有锚 → 不计入；10-30 有锚但没人核 → 算一天没人核
    st = mem.recognition_stats(days=3, end_day='2026-10-31')
    assert st['unanswered_days'] == 1
    assert st['totals']['unknown'] == 3
    assert st['rate'] is None


def test_verdict_is_append_only(mem):
    a1, _, _ = _seed(mem)
    mem.record_verdict('2026-10-30', a1, 'disputed')
    mem.record_verdict('2026-10-30', a1, 'confirmed')
    rows = mem.con.execute(
        'select verdict from recognition_log where anchor_id=? order by id', (a1,)).fetchall()
    assert [r['verdict'] for r in rows] == ['disputed', 'confirmed'], '改认定要留痕，不许覆盖'
    rep = mem.reproduce_day('2026-10-30')
    assert rep['axes']['event'][0]['verdict'] == 'confirmed', '取最新一次'


def test_corrected_without_evidence_does_not_touch_anchor(mem):
    """她说自己有判断：没有出处，就不许改锚。"""
    a1, _, _ = _seed(mem)
    before = mem.con.execute('select text, status from anchors where id=?', (a1,)).fetchone()
    r = mem.record_verdict('2026-10-30', a1, 'corrected', corrected_text='改成别的事')
    after = mem.con.execute('select text, status from anchors where id=?', (a1,)).fetchone()
    assert r['applied'] is False
    assert dict(after) == dict(before), '没证据却改了锚'
    assert mem.anchors_of('2026-10-30')['event'][0]['text'] == before['text']


def test_corrected_with_evidence_changes_anchor(mem):
    a1, _, _ = _seed(mem)
    r = mem.record_verdict('2026-10-30', a1, 'corrected',
                           corrected_text='给三百多条影像资料分类重命名并上传',
                           evidence='原文 09:00 他说下午上传到系统')
    assert r['applied'] is True and r['new_anchor_id']
    texts = [x['text'] for x in mem.anchors_of('2026-10-30')['event']]
    assert texts == ['给三百多条影像资料分类重命名并上传']
    old = mem.con.execute('select status from anchors where id=?', (a1,)).fetchone()
    assert old['status'] == 'superseded'


def test_hedged_is_not_a_confirmation(mem):
    a1, a2, a3 = _seed(mem)
    mem.record_verdict('2026-10-30', a1, 'confirmed')
    mem.record_verdict('2026-10-30', a2, 'hedged', claim='好像是吧')
    rep = mem.reproduce_day('2026-10-30')
    assert rep['counts']['hedged'] == 1
    assert rep['rate'] == 1.0, '分母只算答过的（confirmed/disputed/corrected），模糊不算'
    assert rep['counts']['unknown'] == 1


def test_claim_is_pending_not_memory(mem):
    _seed(mem)
    cid = mem.claim_memory('2026-10-31', '他说昨天丢了一把伞')
    rep = mem.reproduce_day('2026-10-31')
    assert rep['axes']['event'] == [], '他的主张不能直接变成她的锚'
    assert rep['claims'] and rep['claims'][0]['text'] == '他说昨天丢了一把伞'
    assert '我没有记录' in R.render(rep)


def test_claim_resolve_requires_evidence(mem):
    cid = mem.claim_memory('2026-10-31', '他说昨天丢了一把伞')
    with pytest.raises(ValueError):
        mem.resolve_claim(cid, '')


def test_claim_resolve_becomes_marked_anchor(mem):
    cid = mem.claim_memory('2026-10-31', '他说昨天丢了一把伞')
    r = mem.resolve_claim(cid, '原文 18:20 他说伞落在车上')
    assert r['anchor_id']
    row = mem.con.execute('select source, text from anchors where id=?', (r['anchor_id'],)).fetchone()
    assert row['source'] == 'verified_claim'
    assert mem.pending_claims('2026-10-31') == []


def test_probe_vague_answer_direction_right_details_wrong(mem):
    """他说得方向对、细节错：要能指出"方向对，但原文是 09:00 那批"。

    原文：上午把单位那批影像资料分类了，三百多条，重新命名了一遍
    他：好像是下午在弄资料的事
    """
    r = mem.probe_claim('2026-10-30', '好像是下午在弄影像资料的事')
    assert r['found'] is True
    assert r['direction_ok'] is True, r
    assert r['at'] == '09:00'
    assert '三百' in ' '.join(r['missing']) or '三百多条' in r['snippet']


def test_probe_no_match_when_topic_unrelated(mem):
    r = mem.probe_claim('2026-10-30', '量子计算机散热方案')
    assert r['found'] is False


def test_vague_claim_is_marked_as_lead(mem):
    _seed(mem)
    cid = mem.claim_memory('2026-10-31', '好像是昨天丢了个东西', confidence='vague')
    rep = mem.reproduce_day('2026-10-31')
    assert rep['claims'][0]['confidence'] == 'vague'
    out = R.render(rep)
    assert '模糊线索' in out
    assert '我没有记录' in out


def test_bad_confidence_rejected(mem):
    with pytest.raises(ValueError):
        mem.claim_memory('2026-10-31', '随便', confidence='maybe')


# ---- 2026-10-31 外部验证发现的问题，逐条钉死 ----

def test_correct_superseded_refused(mem):
    """对已被取代的锚再修正，必须拒绝（否则会出现两个 active 版本）。"""
    from ireckon import anchors as A
    old = mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    new = mem.correct_anchor(old, '给三百多条影像资料重命名', why='第一次修正')
    with pytest.raises(ValueError):
        A.correct(mem.con, old, '第二次修正')
    actives = mem.con.execute(
        "select count(*) c from anchors where status='active'").fetchone()['c']
    assert actives == 1, '被取代的锚不许再产生新版本'


def test_resolve_claim_twice_is_idempotent(mem):
    cid = mem.claim_memory('2026-10-31', '他说昨天丢了一把伞')
    r1 = mem.resolve_claim(cid, '原文 18:20 他说伞落在车上')
    r2 = mem.resolve_claim(cid, '原文 18:20 他说伞落在车上')
    assert r2.get('already') is True and r2['anchor_id'] == r1['anchor_id']
    n = mem.con.execute("select count(*) c from anchors where status='active'").fetchone()['c']
    assert n == 1, '同一条主张只能落成一条锚'


def test_verdict_day_mismatch_rejected(mem):
    a1, _, _ = _seed(mem)     # 锚属于 2026-10-30
    with pytest.raises(ValueError):
        mem.record_verdict('2026-10-31', a1, 'confirmed')
    rows = mem.con.execute('select count(*) c from recognition_log').fetchone()['c']
    assert rows == 0, '错日期的认定不该被写入'


def test_version_consistent():
    """版本自证：pyproject / __init__ / CHANGELOG 顶部 必须同一个号。"""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyproject = open(os.path.join(root, 'pyproject.toml'), encoding='utf-8').read()
    init = open(os.path.join(root, 'ireckon', '__init__.py'), encoding='utf-8').read()
    cl = open(os.path.join(root, 'CHANGELOG.md'), encoding='utf-8').read()
    v1 = re.search(r'version\s*=\s*"([^"]+)"', pyproject).group(1)
    v2 = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    v3 = re.search(r'##\s*([\d.]+)', cl).group(1)
    assert v1 == v2 == v3, f'版本不一致：pyproject={v1} init={v2} changelog={v3}'
    # 2026-10-31 他说"包号与版号不一致"：README/pyproject 描述里还挂着内部代号与旧号
    readme = open(os.path.join(root, 'README.md'), encoding='utf-8').read()
    desc = re.search(r'description\s*=\s*"([^"]+)"', pyproject).group(1)
    assert f'0.2.4' in readme or v1 in readme, 'README 里没有当前版本号'
    assert 'IRckon' in desc, f'pyproject description 里没有对外名 IRckon：{desc[:40]}'
    # 描述里刻意不写版本号（否则每次升版都要改两处、还会过期）；版本只在 version 字段
    assert '对外名是' in readme and 'IRckon' in readme, 'README 缺对外名（IRckon）的说明'
    # 包名与产物名必须同源：pyproject name=irckon、package.py 产物前缀 IRckon
    pkgname = re.search(r'name\s*=\s*"([^"]+)"', pyproject).group(1)
    assert pkgname == 'irckon', f'包名应改成 irckon，实际 {pkgname}'
    pkg = open(os.path.join(root, 'tools', 'package.py'), encoding='utf-8').read()
    assert 'IRckon-{_version()}.zip' in pkg, '打包产物名应以 IRckon 开头'
    initp = open(os.path.join(root, 'ireckon', '__init__.py'), encoding='utf-8').read()
    assert '__name_ascii__ = "IRckon"' in initp, '__init__ 缺 __name_ascii__'  


def test_schema_has_final_recognition_table():
    """新建库不该靠迁移才能用：schema 里就得是最终结构。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ('sql/schema.sql', 'ireckon/schema.sql'):
        s = open(os.path.join(root, rel), encoding='utf-8').read()
        assert "'hedged'" in s, f'{rel} 的 CHECK 没含 hedged'
        assert 'claim          TEXT' in s or 'claim TEXT' in s, f'{rel} 缺 claim 列'
        assert 'evidence' in s, f'{rel} 缺 evidence 列'
