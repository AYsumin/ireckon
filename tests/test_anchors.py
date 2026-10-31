# -*- coding: utf-8 -*-
"""0.2.0 锚点层的测试。

覆盖三件事：
  ① 三轴能不能存、能不能按轴/按人取
  ② 罕见度有没有真的起作用；不相关时宁可爱空也不硬凑
  ③ "抛锚 → 外部回应 → 判得出像不像"这个动作
外加两条回归：
  · 材料截断不再从头切（昨天的坑：[:6000] 丢掉了当天尾巴）
  · 旧接口（facts / build_context）没被改坏
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ireckon import Memory                     # noqa: E402
from ireckon import anchors as A              # noqa: E402
from ireckon.writer import _material          # noqa: E402


@pytest.fixture()
def mem():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    os.remove(path)
    m = Memory(db_path=path)
    yield m
    m.close()
    for suffix in ('', '-wal', '-shm'):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_three_axes_roundtrip(mem):
    mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名', ['同事'])
    mem.add_anchor('2026-10-30', 'person', '见到我妈和来看她的表妹', ['我妈', '表妹'])
    mem.add_anchor('2026-10-30', 'time', '睡前在弄 ireckon 0.2.0')
    got = mem.anchors_of('2026-10-30')
    assert len(got['event']) == 1
    assert got['person'][0]['subjects'] == ['我妈', '表妹']
    cov = mem.coverage('2026-10-30')
    assert cov == {'event': True, 'person': True, 'time': True}


def test_bad_axis_rejected(mem):
    with pytest.raises(ValueError):
        mem.add_anchor('2026-10-30', 'feeling', '今天很充实')


def test_empty_text_rejected(mem):
    with pytest.raises(ValueError):
        mem.add_anchor('2026-10-30', 'event', '   ')


def test_axis_filter_does_not_leak(mem):
    mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    mem.add_anchor('2026-10-30', 'person', '见到我妈和来看她的表妹', ['表妹'])
    only_person = mem.throw_anchor('影像资料', axis='person')
    assert only_person == [], '按人轴查事轴的锚，不该有结果'
    only_event = mem.throw_anchor('影像资料', axis='event')
    assert len(only_event) == 1


def test_subject_filter(mem):
    mem.add_anchor('2026-10-30', 'person', '见到我妈和来看她的表妹', ['我妈', '表妹'])
    mem.add_anchor('2026-10-30', 'person', '同事来问资料统计口径', ['同事'])
    hit = mem.throw_anchor('资料统计', subject='同事')
    assert len(hit) == 1 and '同事' in hit[0]['text']


def test_no_match_returns_empty(mem):
    mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    assert mem.throw_anchor('量子计算机的散热方案') == [], '不相关就该空，不许硬凑'


def test_rarity_prefers_rare(mem):
    # 先造几天的语料，让"常见"与"罕见"有参照物，再比
    common = mem.add_anchor('2026-10-27', 'event', '今天吃了饭然后说了话')
    for d in ('2026-10-28', '2026-10-29', '2026-10-30'):
        mem.add_anchor(d, 'event', '今天吃了饭然后说了话')
    rare = mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    A.recompute_rarity(mem.con)          # 罕见度取决于语料，必须在有参照后重算
    rows = {r['id']: dict(r) for r in mem.con.execute(
        'select id, rarity from anchors where id in (?,?)', (common, rare)).fetchall()}
    assert rows[rare]['rarity'] > rows[common]['rarity'], rows


def test_verify_matched_and_mismatched(mem):
    aid = mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    good = mem.verify_anchor(aid, '那批影像资料你分类上传还改了名字')
    bad = mem.verify_anchor(aid, '我昨天打了一天游戏')
    assert good['ok'] is True and good['ratio'] >= 0.5
    assert bad['ok'] is False and bad['ratio'] == 0


def test_reconstruct_complete_and_incomplete(mem):
    mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    mem.add_anchor('2026-10-30', 'person', '见到我妈和来看她的表妹', ['表妹'])
    r1 = mem.reconstruct('2026-10-30')
    assert r1['complete'] is False and r1['coverage']['time'] is False
    mem.add_anchor('2026-10-30', 'time', '睡前在弄 ireckon 0.2.0')
    r2 = mem.reconstruct('2026-10-30')
    assert r2['complete'] is True


def test_build_context_includes_anchors(mem):
    mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    ctx = mem.build_context('s1', '昨天影像资料那事弄完了吗')
    assert '[事]' in ctx, ctx[:200]


def test_material_keeps_tail():
    """回归：旧实现 [:6000] 从头切，丢掉当天最后的对话。"""
    rows = [{'role': 'user', 'content': f'第{i}条消息' + '填充' * 40} for i in range(300)]
    rows.append({'role': 'user', 'content': '睡前在弄 ireckon 0.2.0，这是当天最后一句'})
    text = _material(rows, limit=6000)
    assert len(text) <= 6000 * 1.2
    assert '这是当天最后一句' in text, '当天尾巴被截掉了——就是昨天那个坑'


def test_correct_anchor_supersedes(mem):
    """改锚：新的生效，旧的标 superseded 但**还在**（留痕不删）。"""
    old = mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    new = mem.correct_anchor(old, '给三百多条影像资料重新命名，下午上传系统', why='补上数量和去向')
    got = mem.anchors_of('2026-10-30')
    texts = [a['text'] for a in got['event']]
    assert '给三百多条影像资料重新命名，下午上传系统' in texts
    assert '给影像资料分类上传并重命名' not in texts, '旧的不该再出现在 active 里'
    row = mem.con.execute('select status, superseded_by from anchors where id=?', (old,)).fetchone()
    assert row['status'] == 'superseded' and row['superseded_by'] == new


def test_no_match_returns_empty_after_correct(mem):
    old = mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    mem.correct_anchor(old, '整理各部门上报资料并做统计表')
    assert mem.throw_anchor('影像资料') == []
    assert len(mem.throw_anchor('统计表')) == 1


def test_df_incremental_without_recompute(mem):
    """0.2.2：加一条锚就自动更新 df，不需要全库重算。"""
    for d in ('2026-10-27', '2026-10-28', '2026-10-29'):
        mem.add_anchor(d, 'event', '今天吃了饭然后说了话')
    # 不调 recompute_rarity，直接看 df 表里"今天"的 df 是否等于 3
    df = mem.con.execute("select df from anchor_gram_df where gram=?", ('今天',)).fetchone()
    assert df is not None and df['df'] == 3, dict(df) if df else None
    rare = mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    rar = mem.con.execute('select rarity from anchors where id=?', (rare,)).fetchone()['rarity']
    assert rar > 0.5, f'新锚的罕见度应该偏高，实际 {rar}'


def test_rebuild_df_matches_incremental(mem):
    for d in ('2026-10-27', '2026-10-28'):
        mem.add_anchor(d, 'event', '今天吃了饭然后说了话')
    before = {r['gram']: r['df'] for r in mem.con.execute('select gram, df from anchor_gram_df')}
    A.rebuild_df(mem.con)
    after = {r['gram']: r['df'] for r in mem.con.execute('select gram, df from anchor_gram_df')}
    assert before == after


def test_alias_normalization(mem):
    """我妈 / 母亲 / 妈 → 同一个主体。"""
    A.add_alias(mem.con, '母亲', '我妈')
    A.add_alias(mem.con, '妈', '我妈')
    a = mem.add_anchor('2026-10-30', 'person', '母亲来了家里', ['母亲'])
    mem.add_anchor('2026-10-30', 'person', '妈带了菜过来', ['妈'])
    subs = [s for r in mem.anchors_of('2026-10-30')['person'] for s in r['subjects']]
    assert set(subs) == {'我妈'}, subs
    hit = mem.throw_anchor('家里', subject='母亲')
    assert hit and all('我妈' in r['subjects'] for r in hit)


def test_subjects_overview_counts(mem):
    A.add_alias(mem.con, '母亲', '我妈')
    mem.add_anchor('2026-10-30', 'person', '我妈来了', ['母亲'])
    mem.add_anchor('2026-10-30', 'person', '表妹来看我妈', ['表妹', '我妈'])
    rows = {r['subject']: r['count'] for r in A.subjects_overview(mem.con)}
    assert rows.get('我妈') == 2 and rows.get('表妹') == 1, rows


def test_budget_split_keeps_anchor(mem):
    """预算很小时，锚也要留得下（它是入口，不能被事实挤掉）。"""
    mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')
    ctx = mem.build_context('s1', '影像资料那事弄完了吗', max_tokens=60)
    assert '[事]' in ctx, ctx[:200]


def test_correct_rolls_back_on_failure(mem, monkeypatch):
    """本该失败：改锚中途抛异常，不能留下半成品（新锚不生效、旧锚还是 active）。"""
    old = mem.add_anchor('2026-10-30', 'event', '给影像资料分类上传并重命名')

    def boom(con, text, delta):
        raise RuntimeError('模拟：更新 df 时炸了')

    monkeypatch.setattr(A, '_df_delta', boom)
    with pytest.raises(RuntimeError):
        mem.correct_anchor(old, '改成另一件事')
    monkeypatch.undo()

    rows = {r['id']: dict(r) for r in mem.con.execute('select id, text, status from anchors')}
    assert list(rows) == [old], f'不该留下半成品锚：{rows}'
    assert rows[old]['status'] == 'active', rows
