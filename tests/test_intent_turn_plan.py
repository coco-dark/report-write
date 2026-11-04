"""整轮意图 JSON：确定执行 / 缺项引导 / 不确定不执行。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.intent_clarify import get_pending
from report_writing.chat.intent_turn_plan import apply_turn_plan, parse_turn_plan
from report_writing.chat.session_documents import (
    focus_has_display_title,
    is_writing_document_named,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_parse_turn_plan_normalizes_slots_and_missing():
    plan = parse_turn_plan(
        {
            "confidence": 0.91,
            "uncertain": False,
            "slots": {"title": "lzb述职报告", "entry_path": "match"},
            "tools": [{"tool": "set_session_meta", "args": {"title": "lzb述职报告"}}],
            "missing": ["title", "entry_branch"],
        }
    )
    assert plan.slots.get("doc_name") == "lzb述职报告"
    assert plan.slots.get("writing_path") == "match"
    assert plan.missing == ["doc_name", "writing_path"]
    assert [s.get("tool") for s in plan.tools] == ["set_session_meta"]
    assert plan.should_execute is True


def test_name_only_confirms_and_asks_writing_path(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="tp-name", phase=ChatPhase.INTENT.value)
    payload = {
        "confidence": 0.93,
        "uncertain": False,
        "slots": {"doc_name": "lzb述职报告", "writing_path": None},
        "tools": [{"tool": "set_session_meta", "args": {"title": "lzb述职报告"}}],
        "missing": ["writing_path"],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    hit = apply_turn_plan(
        tmp_path,
        session,
        "帮我生成一份述职报告 名字叫lzb述职报告",
        invoke=invoke,
    )
    assert hit is not None
    reply, cards, _events = hit
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "lzb述职报告"
    assert "起个名字" not in (reply or "")
    assert "skip_blueprint" not in (reply or "")
    assert "submit_outline" not in (reply or "")
    pending = get_pending(session) or {}
    assert pending.get("source") == "entry_branch"


def test_name_and_existing_blueprint_dispatches_match(tmp_path: Path, monkeypatch):
    session = ChatSession(id="tp-match", phase=ChatPhase.INTENT.value)
    payload = {
        "confidence": 0.94,
        "uncertain": False,
        "slots": {"doc_name": "lzb述职报告", "writing_path": "match"},
        "tools": [
            {"tool": "set_session_meta", "args": {"title": "lzb述职报告"}},
            {"tool": "match_blueprints", "args": {}},
        ],
        "missing": [],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    seen: list[str] = []

    def fake_execute(home, sess, steps, message, *, emit=None, invoke=None, early_focus=None):
        del home, sess, message, emit, invoke, early_focus
        seen.extend(str(s.get("tool") or "") for s in steps)
        return "找到几份可用蓝本。", [], []

    monkeypatch.setattr(
        "report_writing.chat.intent_dispatch.execute_intent_plan",
        fake_execute,
    )
    hit = apply_turn_plan(
        tmp_path,
        session,
        "名字叫lzb述职报告，用现有蓝本",
        invoke=invoke,
    )
    assert hit is not None
    reply, _cards, _events = hit
    assert "match_blueprints" in seen
    assert "skip_blueprint" not in seen
    assert "match_blueprints" not in (reply or "")
    assert is_writing_document_named(session) or focus_has_display_title(session)


def test_neither_slot_hangs_name_and_path_cards(tmp_path: Path):
    session = ChatSession(id="tp-none", phase=ChatPhase.INTENT.value)
    payload = {
        "confidence": 0.88,
        "uncertain": False,
        "slots": {"doc_name": None, "writing_path": None},
        "tools": [],
        "missing": ["doc_name", "writing_path"],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    hit = apply_turn_plan(tmp_path, session, "帮我写一份报告", invoke=invoke)
    assert not is_writing_document_named(session)
    if hit is not None:
        reply, cards, _events = hit
        sources = {
            str((getattr(c, "payload", {}) or {}).get("source") or "") for c in cards
        }
        assert "doc_name" not in sources
        assert "先给这份文档起个名字" not in (reply or "")
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"


def test_uncertain_does_not_execute_hard_tools(tmp_path: Path, monkeypatch):
    session = ChatSession(id="tp-unc", phase=ChatPhase.INTENT.value)
    payload = {
        "confidence": 0.9,
        "uncertain": True,
        "slots": {},
        "tools": [
            {"tool": "skip_blueprint", "args": {}},
            {"tool": "start_writing", "args": {}},
        ],
        "missing": [],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    def boom(*_a, **_k):
        raise AssertionError("uncertain must not dispatch hard tools")

    monkeypatch.setattr(
        "report_writing.chat.intent_dispatch.execute_intent_plan",
        boom,
    )
    hit = apply_turn_plan(tmp_path, session, "那就随便写吧", invoke=invoke)
    assert hit is not None
    _reply, cards, _events = hit
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)


def test_template_topic_does_not_auto_name_via_slots(tmp_path: Path):
    """「安全报告 用我的模版」：slots.doc_name=安全报告 不得静默定名 / 题目记下了。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="tp-tpl-topic", phase=ChatPhase.INTENT.value)
    msg = "我有模版你根据我的模版 写一份安全报告"
    payload = {
        "confidence": 0.92,
        "uncertain": False,
        "slots": {"doc_name": "安全报告", "writing_path": "template"},
        "tools": [
            {
                "tool": "set_session_meta",
                "args": {"title": "安全报告", "category": "安全报告"},
            },
            {"tool": "begin_blueprint_wizard", "args": {"explicit_create": True}},
        ],
        "missing": [],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    hit = apply_turn_plan(tmp_path, session, msg, invoke=invoke)
    assert hit is not None
    reply, cards, _events = hit
    assert not is_writing_document_named(session)
    assert "题目记下了" not in (reply or "")
    assert "安全报告｜安全报告" not in (reply or "")
    assert "起个名字" in (reply or "") or "名字" in (reply or "")
    assert any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "doc_name"
        or getattr(c, "type", "") == "blueprint_wizard"
        for c in cards
    )


def test_short_template_line_does_not_auto_name(tmp_path: Path):
    """短句「安全报告 用我的模版」同样禁止把题材当文档名。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="tp-tpl-short", phase=ChatPhase.INTENT.value)
    msg = "安全报告 用我的模版"
    payload = {
        "confidence": 0.91,
        "uncertain": False,
        "slots": {"doc_name": "安全报告", "writing_path": "template"},
        "tools": [
            {"tool": "set_session_meta", "args": {"title": "安全报告", "category": "安全报告"}},
            {"tool": "begin_blueprint_wizard", "args": {"explicit_create": True}},
        ],
        "missing": [],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    hit = apply_turn_plan(tmp_path, session, msg, invoke=invoke)
    assert hit is not None
    reply, _cards, _events = hit
    assert not is_writing_document_named(session)
    assert "题目记下了" not in (reply or "")


def test_mid_session_rename_and_write_executes(tmp_path: Path, monkeypatch):
    """写作中途复合句同样走 JSON 执行，不只开场。"""
    from report_writing.chat.session_documents import patch_focus_onboarding

    session = ChatSession(
        id="tp-mid",
        phase=ChatPhase.WRITING.value,
        blueprint_id="bp1",
        task_id="bp1",
        freeform={"title": "旧名", "writing_doc_named": True, "meta_confirmed": True},
    )
    patch_focus_onboarding(
        session, name_confirmed=True, entry_path="match", entry_branch="with_blueprint"
    )
    payload = {
        "confidence": 0.9,
        "uncertain": False,
        "slots": {"doc_name": "年度总结"},
        "tools": [
            {"tool": "set_session_meta", "args": {"title": "年度总结"}},
            {"tool": "start_writing", "args": {}},
        ],
        "missing": [],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    seen: list[str] = []

    def fake_execute(home, sess, steps, message, *, emit=None, invoke=None, early_focus=None):
        del home, sess, message, emit, invoke, early_focus
        seen.extend(str(s.get("tool") or "") for s in steps)
        return "已改名并开始写。", [], []

    monkeypatch.setattr(
        "report_writing.chat.intent_dispatch.execute_intent_plan",
        fake_execute,
    )
    hit = apply_turn_plan(tmp_path, session, "改名叫年度总结然后开始写", invoke=invoke)
    assert hit is not None
    assert "set_session_meta" in seen
    assert "start_writing" in seen


def test_mid_session_missing_chapter_asks(tmp_path: Path, monkeypatch):
    from report_writing.chat.session_documents import patch_focus_onboarding, upsert_session_document

    session = ChatSession(
        id="tp-ch",
        phase=ChatPhase.WRITING.value,
        blueprint_id="bp1",
        task_id="t1",
        freeform={"title": "报告", "writing_doc_named": True},
    )
    upsert_session_document(
        session,
        kind="writing",
        task_id="t1",
        blueprint_id="bp1",
        title="报告",
    )
    patch_focus_onboarding(
        session, name_confirmed=True, entry_path="match", entry_branch="with_blueprint"
    )
    payload = {
        "confidence": 0.86,
        "uncertain": False,
        "slots": {},
        "tools": [{"tool": "select_write_chapters", "args": {}}],
        "missing": ["chapter_key"],
    }

    def invoke(system: str, user: str) -> str:
        del system, user
        return json.dumps(payload, ensure_ascii=False)

    def fake_execute(home, sess, steps, message, *, emit=None, invoke=None, early_focus=None):
        del home, sess, message, emit, invoke, early_focus
        return "请选章。", [], []

    monkeypatch.setattr(
        "report_writing.chat.intent_dispatch.execute_intent_plan",
        fake_execute,
    )
    monkeypatch.setattr(
        "report_writing.chat.session_documents.resolve_document_scope",
        lambda *a, **k: {"action": "continue", "need_clarify": False, "force_new": False},
    )
    hit = apply_turn_plan(tmp_path, session, "把这一章写了", invoke=invoke)
    assert hit is not None
    reply, cards, _events = hit
    assert "哪一章" in (reply or "") or any(
        str((getattr(c, "payload", {}) or {}).get("source") or "") == "action_slots" for c in cards
    )
