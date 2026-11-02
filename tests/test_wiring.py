# -*- coding: utf-8 -*-
"""接线回归：确认 _distill_day() 真正发出去的 system prompt 里，两条规矩都在。

来历：0.2.13 第一次发布前，外部复核发现"主语规则只写在一个从不被调用的字符串里"
（FACT_SYS），默认蒸馏路径拿不到。这条测试专门防这种"写了但没接上"的错。
"""
import asyncio
import time

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from ireckon import config_defaults as cd
    monkeypatch.setattr(cd, "DB_PATH", str(tmp_path / "m.db"), raising=False)
    from ireckon import Memory
    return Memory(db_path=str(tmp_path / "m.db"))


def _capture(monkeypatch):
    captured = {}

    def fake_chat_json(messages, **kw):
        captured["system"] = messages[0]["content"] if messages else ""
        if False:
            pass
        return {
            "title": "t",
            "diary": "d",
            "facts": [],
            "anchors": [
                {"axis": "event", "text": "x", "subjects": ["p"]},
                {"axis": "person", "text": "p", "subjects": ["p"]},
                {"axis": "time", "text": "23:00", "subjects": []},
            ],
        }

    import ireckon.writer as w
    # writer 里是 llm.chat_json 的形式调用；patch 到模块对象上即可
    monkeypatch.setattr(w.llm, "chat_json", fake_chat_json, raising=False)
    import ireckon.llm as llm
    monkeypatch.setattr(llm, "chat_json", fake_chat_json, raising=False)
    return captured


def test_distill_sends_both_rules(mem, monkeypatch):
    day = "2026-11-01"
    ts = time.mktime(time.strptime(day + " 12:00", "%Y-%m-%d %H:%M"))
    mem.con.execute(
        "insert into turns (session_id, role, content, ts) values (?,?,?,?)",
        ("t", "user", "今天下雨，我在家修好了漏水的水龙头", ts),
    )
    mem.con.commit()

    cap = _capture(monkeypatch)
    mem.schedule_day(day)
    asyncio.run(mem.drain())

    sys_prompt = cap.get("system", "")
    assert sys_prompt, "没有捕获到系统提示词（蒸馏没跑起来）"
    assert "不写成事实的" in sys_prompt, "默认路径里没有过程话规则"
    assert ("主语不许猜" in sys_prompt) or ("必须有明确主语" in sys_prompt), "默认路径里没有主语规则"


def test_no_hardcoded_names_in_prompts():
    import ireckon.writer as w
    for name in ("COMBINED_SYS", "ANCHOR_SYS", "MISSING_AXIS_SYS"):
        text = getattr(w, name)
        assert "阿裕" not in text and "苏敏" not in text, f"{name} 里写死了具体称呼"
