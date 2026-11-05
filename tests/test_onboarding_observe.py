"""观察式 onboarding：一句多意图，非强制串行。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.intent_clarify import (
    build_entry_path_clarify,
    execute_clarify_spec,
    get_pending,
)
from report_writing.chat.session_documents import (
    focus_needs_doc_name,
    focus_needs_entry_branch,
    focus_needs_entry_path,
    is_writing_document_named,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_write_report_and_auto_outline_one_shot(tmp_path: Path):
    from report_writing.chat.session_documents import confirm_writing_document_name

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="obs1", phase=ChatPhase.INTENT.value)
    build_entry_path_clarify(
        session,
        message="写一份工作汇报 自动生成目录",
        invoke=None,
        home=tmp_path,
    )
    pending0 = get_pending(session) or {}
    assert pending0.get("source") != "doc_name"
    confirm_writing_document_name(session, "工作汇报", home=tmp_path, message="名字叫工作汇报")
    reply, cards = build_entry_path_clarify(
        session,
        message="写一份工作汇报 自动生成目录",
        invoke=None,
        home=tmp_path,
        after_doc_name_confirm=True,
    )
    assert is_writing_document_named(session)
    assert (session.freeform or {}).get("title") == "工作汇报"
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    assert "请按顺序调用" not in (reply or "")
    remaining = list((session.freeform or {}).get("suggested_next_tools") or [])
    assert all(str(t.get("tool") or "") != "submit_outline" for t in remaining)


def test_vague_write_still_asks_name():
    session = ChatSession(id="obs2", phase=ChatPhase.INTENT.value)
    reply, cards = build_entry_path_clarify(session, message="帮我写一份", invoke=None)
    pending = get_pending(session) or {}
    assert pending.get("source") == "doc_name" or "写什么" in (reply or "") or "名字" in (
        reply or ""
    ) or cards


def test_template_intent_asks_doc_name_not_outline(tmp_path: Path):
    from report_writing.chat.cards import turn_has_outline_preview_cards

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="obs_tpl", phase=ChatPhase.INTENT.value)
    reply, cards = build_entry_path_clarify(
        session,
        message="依据模版写一份环境报告",
        invoke=None,
        home=tmp_path,
    )
    pending = get_pending(session) or {}
    wiz = [c for c in (cards or []) if getattr(c, "type", "") == "blueprint_wizard"]
    assert wiz or pending.get("source") == "doc_name"
    assert not turn_has_outline_preview_cards(cards)
    assert wiz or "按你的模版" in (reply or "") or "起个名字" in (reply or "")
    if wiz:
        assert str(wiz[0].payload.get("step") or "") == "meta"


def test_two_step_entry_branch_then_freeform_mode(tmp_path: Path):
    from report_writing.chat.session_documents import confirm_writing_document_name

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="obs3", phase=ChatPhase.INTENT.value)
    build_entry_path_clarify(
        session, message="帮我写一份安全报告", invoke=None, home=tmp_path
    )
    pending = get_pending(session)
    assert not pending or pending.get("source") != "doc_name"
    confirm_writing_document_name(session, "安全报告", home=tmp_path)
    build_entry_path_clarify(
        session,
        message="帮我写一份安全报告",
        invoke=None,
        home=tmp_path,
        after_doc_name_confirm=True,
    )
    pending = get_pending(session)
    assert pending and pending.get("source") == "entry_branch"

    hit = {
        "source": "entry_branch",
        "option_id": "without_blueprint",
        "slots": {"entry_branch": "without_blueprint"},
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="无蓝本"
    )
    pending2 = get_pending(session)
    assert pending2 and pending2.get("source") == "entry_freeform_mode"
    assert "模版" in reply or "大纲" in reply or cards


def test_ask_entry_path_observation_fields(tmp_path: Path):
    import json

    from report_writing.agents.chat_control import ChatControlState, build_chat_tools

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="obs4", phase=ChatPhase.INTENT.value)
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["ask_entry_path"].invoke({"message": "帮我写一份安全报告"})
    data = json.loads(raw)
    assert data.get("ok") is True
    assert isinstance(data.get("onboarding"), dict)
    assert "pending_clarify" in data
    assert "suggested_next_tools" in data
    assert data.get("pending_clarify") != "doc_name"


def test_ask_entry_path_does_not_auto_dispatch_onboarding(tmp_path: Path, monkeypatch):
    from report_writing.agents.chat_control import ChatControlState, build_chat_tools
    from report_writing.chat.session_documents import confirm_writing_document_name

    def _boom(*_a, **_k):
        raise AssertionError("ask_entry_path must not auto-dispatch onboarding tools")

    monkeypatch.setattr(
        "report_writing.chat.intent_clarify.dispatch_suggested_onboarding_tools",
        _boom,
    )
    monkeypatch.setattr(
        "report_writing.chat.intent_clarify._apply_auto_freeform_and_dispatch",
        _boom,
    )
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="obs_no_dispatch", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(
        session, "lzb的述职报告", home=tmp_path, message="名字叫做lzb的述职报告 你帮我生成大纲"
    )
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["ask_entry_path"].invoke(
        {"message": "名字叫做lzb的述职报告 你帮我生成大纲"}
    )
    data = json.loads(raw)
    assert data.get("ok") is True
    assert "目录我先帮你搭" not in str(data.get("reply_hint") or "")


def test_unnamed_blueprint_library_intent_suggests_match():
    from report_writing.chat.session_documents import (
        observe_new_document_gaps,
        suggest_next_tools_from_gaps,
    )

    session = ChatSession(id="obs5", phase=ChatPhase.INTENT.value)
    msg = "给我写份安全报告，用现在有的蓝本"
    gaps = observe_new_document_gaps(session, msg, invoke=None)
    assert gaps.get("missing_name") is True
    suggested = suggest_next_tools_from_gaps(gaps, message=msg)
    assert [t.get("tool") for t in suggested] == ["match_blueprints"]


def test_observe_generate_outline_phrase_is_residual_not_llm():
    from report_writing.chat.session_documents import observe_new_document_gaps

    session = ChatSession(id="obs_gen", phase=ChatPhase.INTENT.value)
    gaps = observe_new_document_gaps(session, "帮我生成目录", invoke=None)
    assert "generate_outline" in (gaps.get("residual_intents") or [])
    gaps_write = observe_new_document_gaps(session, "帮我写一份安全报告", invoke=None)
    assert "generate_outline" not in (gaps_write.get("residual_intents") or [])
