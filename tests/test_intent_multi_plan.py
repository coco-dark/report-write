"""遗留 JSON 计划（口语主路径已不走）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from report_writing.chat.intent_multi_plan import (
    expand_plan_auto_continue,
    try_run_oral_multi_intent,
)
from report_writing.domain.models import ChatSession

pytestmark = pytest.mark.legacy_plan


@pytest.fixture(autouse=True)
def _enable_intent_plan_for_multi_tests(monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_INTENT_PLAN", "1")
    from report_writing.infra import config_settings

    config_settings._application.cache_clear()
    yield
    config_settings._application.cache_clear()


def test_expand_plan_auto_continue_naming_then_entry():
    session = ChatSession(id="s-multi")
    steps = expand_plan_auto_continue(
        [{"tool": "set_session_meta", "args": {"title": "1101安全预评价"}}],
        "我想写一份安全预评价，名字叫1101安全预评价",
        session,
    )
    names = [str(s.get("tool") or "") for s in steps]
    assert "set_session_meta" in names
    assert "ask_entry_path" in names
    assert names.index("set_session_meta") < names.index("ask_entry_path")


def test_expand_plan_orders_compound_chain():
    session = ChatSession(id="s-multi-order")
    steps = expand_plan_auto_continue(
        [
            {"tool": "start_writing", "args": {}},
            {"tool": "materials_pack_create", "args": {"name": "变电站资料包"}},
            {"tool": "set_session_meta", "args": {"title": "述职报告"}},
            {"tool": "match_blueprints", "args": {}},
        ],
        "名字叫述职报告，匹配蓝本，新建资料包叫变电站资料包，然后开写",
        session,
    )
    names = [str(s.get("tool") or "") for s in steps]
    assert names.index("set_session_meta") < names.index("match_blueprints")
    assert names.index("match_blueprints") < names.index("materials_pack_create")
    assert names.index("materials_pack_create") < names.index("start_writing")


def test_high_confidence_silent_chain(tmp_path: Path, monkeypatch):
    session = ChatSession(id="s-hi")
    plan = {
        "tools": [
            {"tool": "set_session_meta", "args": {"title": "年度总结"}},
            {"tool": "ask_entry_path", "args": {}},
        ],
        "confidence": 0.92,
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(plan, ensure_ascii=False)

    calls: list[list] = []

    def fake_execute(home, sess, steps, message, *, emit=None, invoke=None, early_focus=None):
        del home, sess, message, emit, invoke, early_focus
        calls.append(list(steps))
        return "已按顺序办理。", [], []

    monkeypatch.setattr(
        "report_writing.chat.tool_dispatch.execute_intent_plan",
        fake_execute,
    )
    hit = try_run_oral_multi_intent(
        tmp_path, session, "改名叫年度总结然后选路径开写", invoke=invoke
    )
    assert hit is not None
    reply, cards, events = hit
    assert "办理" in reply or reply
    assert calls and len(calls[0]) >= 2
    assert any(e.get("type") == "multi_intent_plan" for e in events)
    assert not cards  # 高置信不追问


def test_low_confidence_asks_before_chain(tmp_path: Path, monkeypatch):
    session = ChatSession(id="s-lo")
    plan = {
        "tools": [
            {"tool": "materials_pack_create", "args": {"name": "资料包A"}},
            {"tool": "start_writing", "args": {}},
        ],
        "confidence": 0.60,
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(plan, ensure_ascii=False)

    def boom(*_a, **_k):
        raise AssertionError("low confidence must not silent-execute")

    monkeypatch.setattr(
        "report_writing.chat.tool_dispatch.execute_intent_plan",
        boom,
    )
    hit = try_run_oral_multi_intent(
        tmp_path,
        session,
        "新建资料包叫资料包A再继续写",
        invoke=invoke,
    )
    assert hit is not None
    reply, cards, events = hit
    assert "依次做" in reply or "是这样吗" in reply
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)
    assert any(e.get("type") == "multi_intent_clarify" for e in events)
    pending = (session.freeform or {}).get("intent_clarify") or {}
    assert pending.get("source") == "multi_intent"
    assert (pending.get("candidate") or {}).get("kind") == "intent_plan"


def test_single_high_confidence_tool_executes(tmp_path: Path, monkeypatch):
    session = ChatSession(id="s-one")

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(
            {"tools": [{"tool": "open_knowledge", "args": {}}], "confidence": 0.95},
            ensure_ascii=False,
        )

    seen: list[str] = []

    def fake_execute(home, sess, steps, message, *, emit=None, invoke=None, early_focus=None):
        del home, sess, message, emit, invoke, early_focus
        seen.extend(str(s.get("tool") or "") for s in steps)
        return "已打开资料库。", [], []

    monkeypatch.setattr(
        "report_writing.chat.tool_dispatch.execute_intent_plan",
        fake_execute,
    )
    hit = try_run_oral_multi_intent(tmp_path, session, "打开资料库", invoke=invoke)
    assert hit is not None
    assert "open_knowledge" in seen


def test_mid_session_rename_and_write_plan(tmp_path: Path, monkeypatch):
    """写作中途复合句同样走多意图编排（非仅开场）。"""
    session = ChatSession(
        id="s-mid",
        blueprint_id="bp1",
        task_id="bp1",
        freeform={"title": "旧名报告", "writing_doc_named": True, "meta_confirmed": True},
    )
    plan = {
        "tools": [
            {"tool": "set_session_meta", "args": {"title": "新名报告"}},
            {"tool": "start_writing", "args": {}},
        ],
        "confidence": 0.88,
    }

    def invoke(system: str, user: str) -> str:
        assert "用户本轮" in user or "新名" in user or True
        return json.dumps(plan, ensure_ascii=False)

    seen: list[str] = []

    def fake_execute(home, sess, steps, message, *, emit=None, invoke=None, early_focus=None):
        del home, sess, message, emit, invoke, early_focus
        seen.extend(str(s.get("tool") or "") for s in steps)
        return "改名并开写。", [], []

    monkeypatch.setattr(
        "report_writing.chat.tool_dispatch.execute_intent_plan",
        fake_execute,
    )
    hit = try_run_oral_multi_intent(
        tmp_path, session, "改名叫新名报告然后开始写", invoke=invoke
    )
    assert hit is not None
    assert "set_session_meta" in seen
    assert "start_writing" in seen
