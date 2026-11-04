"""定名后不得只「记下了」；缺入口时挂选路卡，「继续」推进而非再记目标。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.agents.chat_control import ChatControlState, build_agent_user_text, build_chat_tools
from report_writing.chat.next_step import infer_suggested_next_step
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    focus_needs_entry_path,
    is_writing_document_named,
)
from report_writing.domain.models import ChatCard, ChatPhase, ChatSession


def _entry_sources(cards: list) -> set[str]:
    return {
        str((getattr(c, "payload", None) or {}).get("source") or "")
        for c in (cards or [])
    }


def test_set_session_meta_hangs_entry_after_named_write(tmp_path: Path):
    """以题写文档 → 定名后同轮挂有蓝本/无蓝本入口卡。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="unstick_meta", phase=ChatPhase.INTENT.value)
    msg = '以「建设美丽中国」为题，帮我写一份文档'
    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message=msg,
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["set_session_meta"].invoke(
        {
            "message": msg,
            "title": "建设美丽中国",
        }
    )
    data = json.loads(raw)
    assert data.get("ok") is True, data
    assert is_writing_document_named(session)
    assert focus_needs_entry_path(session)
    assert data.get("needs_entry_path") or data.get("awaiting_path_choice")
    sources = _entry_sources(state.cards)
    assert sources & {"entry", "entry_branch", "entry_freeform_mode"}
    hint = str(data.get("reply_hint") or "")
    assert "有蓝本" in hint or "无蓝本" in hint or "写作方式" in hint or state.cards
    # 要素不只 doc_name：仍缺 writing_path
    assert "writing_path" in str(data.get("observation") or "") or focus_needs_entry_path(
        session
    )


def test_remember_keeps_entry_cards_and_hangs_when_needs_path(tmp_path: Path):
    """remember_writing_goal 不得冲掉入口卡；缺 writing_path 时主动挂入口。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="unstick_rem", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "建设美丽中国", message="建设美丽中国")
    assert is_writing_document_named(session)
    assert focus_needs_entry_path(session)

    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message='以「建设美丽中国」为题，帮我写一份文档',
        cards=[
            ChatCard(
                type="intent_clarify",
                title="选路",
                payload={
                    "source": "entry_branch",
                    "question": "有蓝本还是无蓝本？",
                    "options": [{"id": "with", "label": "有蓝本"}],
                },
            )
        ],
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["remember_writing_goal"].invoke(
        {"notes": '以「建设美丽中国」为题，帮我写一份文档'}
    )
    data = json.loads(raw)
    assert data.get("ok") is True
    assert data.get("needs_entry_path") is True
    sources = _entry_sources(state.cards)
    assert sources & {"entry", "entry_branch", "entry_freeform_mode"}
    hint = str(data.get("reply_hint") or "")
    # 可短确认，但不得只剩「后面选蓝本都会按这个来」且无卡
    assert state.cards
    assert "writing_path" in (data.get("missing_slots") or []) or "ask_entry_path" in str(
        data.get("observation") or ""
    )
    assert "有蓝本" in hint or "无蓝本" in hint or "写作" in hint or sources


def test_continue_after_named_advances_entry_not_re_remember(tmp_path: Path):
    """已定名+缺入口时「继续」→ 推进入口，禁止再只记目标。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="unstick_cont", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "建设美丽中国", message="建设美丽中国")
    assert focus_needs_entry_path(session)

    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="继续",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["remember_writing_goal"].invoke({"notes": "继续"})
    data = json.loads(raw)
    assert data.get("ok") is True
    assert data.get("needs_entry_path") is True
    hint = str(data.get("reply_hint") or "")
    assert "好，我记下了" not in hint
    sources = _entry_sources(state.cards)
    assert sources & {"entry", "entry_branch", "entry_freeform_mode"}

    step = infer_suggested_next_step(session, home=tmp_path, message="继续")
    assert "ask_entry_path" in (step.get("priority_tools") or [])
    assert "remember_writing_goal" not in (step.get("priority_tools") or [])

    body = json.loads(
        build_agent_user_text(
            action="",
            payload={},
            message="继续",
            session=session,
            home=tmp_path,
        )
    )
    assert "ask_entry_path" in str(body.get("routing_hint") or "")
    assert "remember_writing_goal" in str(body.get("routing_hint") or "")
