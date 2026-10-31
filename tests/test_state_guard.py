# -*- coding: utf-8 -*-
"""状态契约（0.2.4）的不变量测试。

不写"某个例子"，写**转移矩阵**：每一格都断言允许还是拒绝。
这是外部验证给的教训——例子测试只证明"写下的行为成立"，
矩阵测试才证明"没写的组合也不会乱来"。
"""
import os
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ireckon import Memory, anchors as A             # noqa: E402
from ireckon import state_guard as G                 # noqa: E402


@pytest.fixture()
def mem():
    import shutil
    tmp = tempfile.mkdtemp(prefix='guard-test-')
    m = Memory(db_path=os.path.join(tmp, 'g.db'))
    yield m
    m.close()
    shutil.rmtree(tmp, ignore_errors=True)


# ---------- 日期不变量 ----------

@pytest.mark.parametrize('day, ok', [
    ('2026-10-30', True),
    ('2026-1-1', False),        # 位数不对
    ('2026-02-30', False),      # 不存在的日期
    ('20261030', False),
    ('', False),
    ('2026-10-30T10:00', False),
])
def test_day_format_matrix(day, ok):
    if ok:
        assert G.require_day(day) == day
    else:
        with pytest.raises(G.InvariantError):
            G.require_day(day)


# ---------- 锚的写路径 ----------

def test_insert_is_idempotent_for_same_text(mem):
    a = mem.add_anchor('2026-10-30', 'event', '同一句话')
    b = mem.add_anchor('2026-10-30', 'event', '同一句话')
    assert a == b, '同一天同轴同文本不该落两条'
    n = mem.con.execute("select count(*) c from anchors where status='active'").fetchone()['c']
    assert n == 1


def test_insert_rejects_bad_axis(mem):
    with pytest.raises(G.InvariantError):
        mem.add_anchor('2026-10-30', 'feeling', '不该有的轴')


def test_insert_rejects_bad_day(mem):
    with pytest.raises(G.InvariantError):
        mem.add_anchor('2026-2-30', 'event', '坏日期')


def test_correct_matrix(mem):
    """active → 可改一次；superseded → 拒绝。"""
    old = mem.add_anchor('2026-10-30', 'event', '原始')
    new = mem.correct_anchor(old, '第一次修正')
    assert new != old
    with pytest.raises(G.InvariantError):
        A.correct(mem.con, old, '第二次修正')
    # 新锚仍可继续修正（它是 active）
    newer = A.correct(mem.con, new, '第二次修正')
    assert newer != new
    actives = mem.con.execute("select count(*) c from anchors where status='active'").fetchone()['c']
    assert actives == 1, '任何时候同一条链只能有一个 active'


# ---------- 认定的写路径 ----------

def test_record_matrix(mem):
    a = mem.add_anchor('2026-10-30', 'event', '某条锚')
    # 正确日期 + confirmed → 允许
    mem.record_verdict('2026-10-30', a, 'confirmed')
    # 错误日期 → 拒绝
    with pytest.raises(G.InvariantError):
        mem.record_verdict('2026-10-31', a, 'confirmed')
    # 非法 verdict → 拒绝
    with pytest.raises(G.InvariantError):
        mem.record_verdict('2026-10-30', a, 'probably')
    # corrected 无证据 → 记下但不改锚
    r = mem.record_verdict('2026-10-30', a, 'corrected', corrected_text='改了')
    assert r['applied'] is False
    # corrected 带证据 → 改锚
    r2 = mem.record_verdict('2026-10-30', a, 'corrected', corrected_text='真的改了',
                            evidence='原文 09:00')
    assert r2['applied'] is True


# ---------- 主张的写路径 ----------

def test_claim_matrix(mem):
    with pytest.raises(G.InvariantError):
        mem.claim_memory('2026-2-30', '坏日期的主张')
    with pytest.raises(G.InvariantError):
        mem.claim_memory('2026-10-31', '  ')
    with pytest.raises(G.InvariantError):
        mem.claim_memory('2026-10-31', '正文', confidence='maybe')
    cid = mem.claim_memory('2026-10-31', '正文', confidence='vague')
    # 解决要证据
    with pytest.raises(G.InvariantError):
        mem.resolve_claim(cid, '')
    r = mem.resolve_claim(cid, '原文 18:20')
    # 二次解决幂等，不重复落锚
    r2 = mem.resolve_claim(cid, '原文 18:20')
    assert r2.get('already') is True and r2['anchor_id'] == r['anchor_id']


def test_no_partial_state_after_rejections(mem):
    """所有被拒绝的写，都不许留下半成品。"""
    a = mem.add_anchor('2026-10-30', 'event', '某条锚')
    for bad in (
        lambda: mem.add_anchor('2026-2-30', 'event', '坏日期'),
        lambda: mem.record_verdict('2026-10-31', a, 'confirmed'),
        lambda: mem.claim_memory('2026-10-31', ''),
    ):
        with pytest.raises(G.InvariantError):
            bad()
    assert mem.con.execute("select count(*) c from anchors").fetchone()['c'] == 1
    assert mem.con.execute("select count(*) c from recognition_log").fetchone()['c'] == 0
    assert mem.con.execute("select count(*) c from pending_claims").fetchone()['c'] == 0


# ---- 2026-10-31 复验 0.2.4 发现的两个回归 ----

def test_correct_same_text_keeps_active(mem):
    """同文本修正必须是"无操作"，绝不能让锚自我取代（复验发现）。"""
    a = mem.add_anchor('2026-10-30', 'event', '原始文本')
    same = mem.correct_anchor(a, '原始文本')
    assert same == a
    row = mem.con.execute('select status, superseded_by from anchors where id=?', (a,)).fetchone()
    assert row['status'] == 'active', '同文本修正把锚弄没了'
    assert row['superseded_by'] is None
    actives = mem.con.execute(
        "select count(*) c from anchors where status='active'").fetchone()['c']
    assert actives == 1


def test_record_same_text_correction_keeps_anchor(mem):
    """走 record_verdict(corrected) 的同文本路径也不能弄丢锚。"""
    a = mem.add_anchor('2026-10-30', 'event', '原始文本')
    r = mem.record_verdict('2026-10-30', a, 'corrected', corrected_text='原始文本',
                           evidence='原文 09:00')
    assert r['new_anchor_id'] in (a, None) or r['new_anchor_id'] == a
    row = mem.con.execute('select status from anchors where id=?', (a,)).fetchone()
    assert row['status'] == 'active'


def test_upgrade_old_db_with_duplicate_anchors(tmp_path):
    """含重复 active 锚的 0.2.3 老库，必须能升级（不崩、不丢锚）。"""
    import sqlite3
    db = str(tmp_path / 'old.db')
    con = sqlite3.connect(db)
    con.executescript(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sql', 'schema.sql'),
        encoding='utf-8').read())
    con.execute("drop index if exists uq_anchor_active")
    now = 1.0
    for text in ('重复的锚', '重复的锚'):
        con.execute("insert into anchors(day, axis, text, subjects, event_time, rarity, why, "
                    "status, source, created_at) values('2026-10-30','event',?, '[]','',0,'',"
                    "'active','distill',?)", (text, now))
    con.commit()
    con.close()

    # 用当前代码打开：不该抛异常
    m = Memory(db_path=db)
    actives = m.con.execute(
        "select count(*) c from anchors where status='active'").fetchone()['c']
    total = m.con.execute("select count(*) c from anchors").fetchone()['c']
    assert actives == 1, '升级后应只保留一条 active'
    assert total == 2, '重复的那条必须还在（标 superseded，不是删）'
    sup = m.con.execute(
        "select superseded_by from anchors where status='superseded'").fetchone()
    assert sup['superseded_by'] is not None

    # 派生数据一致性：被取代的锚不该再计入 df（2026-10-31 外部复验发现）
    grams = [g for g in ('重复', '复的', '的锚')]
    for g in grams:
        row = m.con.execute('select df from anchor_gram_df where gram=?', (g,)).fetchone()
        assert row is None or row['df'] == 1, f'{g} 的 df 不该还是 2：{row["df"] if row else None}'

    # 罕见度要跟"干净库重算一遍"一致
    from ireckon import anchors as _A
    before = {r['id']: r['rarity'] for r in m.con.execute(
        "select id, rarity from anchors where status='active'")}
    _A.recompute_rarity(m.con)
    after = {r['id']: r['rarity'] for r in m.con.execute(
        "select id, rarity from anchors where status='active'")}
    assert before == after, '迁移后的 rarity 与重建结果不一致' 
    m.close()


# ---- 0.2.8：库位置与版本审计 ----

def test_default_db_is_fixed_not_cwd(monkeypatch, tmp_path):
    """默认库必须在用户目录，不随工作目录变（否则换个目录跑就像失忆）。"""
    import importlib
    from ireckon import config_defaults as cd
    monkeypatch.delenv('IRECKON_DB', raising=False)
    importlib.reload(cd)
    assert '.ireckon' in cd.DB_PATH, cd.DB_PATH
    assert not cd.DB_PATH.endswith('memory.db') or '.ireckon' in cd.DB_PATH
    # 换到别的目录也不该改
    old = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        importlib.reload(cd)
        assert '.ireckon' in cd.DB_PATH
    finally:
        os.chdir(old)
        monkeypatch.undo()
        importlib.reload(cd)


def test_env_overrides_db_path(monkeypatch, tmp_path):
    import importlib
    from ireckon import config_defaults as cd
    target = str(tmp_path / 'mine.db')
    monkeypatch.setenv('IRECKON_DB', target)
    importlib.reload(cd)
    assert cd.DB_PATH == target
    monkeypatch.delenv('IRECKON_DB')
    importlib.reload(cd)


def test_legacy_db_is_not_moved(tmp_path, monkeypatch, capsys):
    """当前目录有旧 memory.db 且默认库为空 → 只提示，不动数据。"""
    import importlib
    from ireckon import config_defaults as cd
    workdir = tmp_path / 'work'
    workdir.mkdir()
    legacy = workdir / 'memory.db'
    legacy.write_bytes(b'')
    monkeypatch.chdir(workdir)
    monkeypatch.setenv('IRECKON_DB', str(tmp_path / 'new.db'))
    importlib.reload(cd)
    m = Memory(db_path=str(tmp_path / 'new.db'))
    out = capsys.readouterr().out
    assert '旧库' in out or 'memory.db' in out, out
    assert legacy.exists(), '旧库不许被自动搬走'
    assert m.con.execute('select count(*) c from turns').fetchone()['c'] == 0
    m.close()
    monkeypatch.delenv('IRECKON_DB')
    importlib.reload(cd)


def test_version_audit_passes():
    """版本/包名审计：任何一处退回旧号，这条会红。"""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'tools', 'version_audit.py')
    spec = importlib.util.spec_from_file_location('version_audit', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.audit(strict=True) == 0
