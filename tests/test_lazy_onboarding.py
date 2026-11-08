"""按需 onboarding：无写意图不 mint / 不挂起名；用蓝本写优先 match。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.session_documents import (
    focus_document_key,
    focus_needs_doc_name,
    message_confirms_write_document_intent,
    onboarding_lane_active,
    user_wants_existing_blueprint_match,
)
from report_writing.chat.session_gaps import observe_session_gaps
from report_writing.domain.models import ChatPhase, ChatSession


def test_fresh_session_no_doc_name_gap():
    session = ChatSession(id="lazy_fresh", phase=ChatPhase.INTENT.value)
    assert not onboarding_lane_active(session)
    assert not focus_needs_doc_name(session)
    assert not message_confirms_write_document_intent("你好")
    gaps = observe_session_gaps(session, "你好")
    assert "doc_name" not in gaps.missing


def test_user_wants_match_use_named_blueprint_write():
    msg = "使用lzb安全报告 给我写一份安全报告"
    assert user_wants_existing_blueprint_match(msg)
    assert message_confirms_write_document_intent(msg)


def test_observe_match_path_use_blueprint_write():
    session = ChatSession(id="lazy_match", phase=ChatPhase.INTENT.value)
    gaps = observe_session_gaps(session, "使用lzb安全报告 给我写一份安全报告")
    assert gaps.declared.get("writing_path") == "match"


def test_named_doc_chapter_ops_skip_onboarding_llm(tmp_path: Path):
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        confirm_writing_document_name,
        ensure_write_onboarding_started,
        user_wants_existing_blueprint_match,
    )

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="lazy_named", phase=ChatPhase.CREATE_HANDOFF.value)
    begin_document_onboarding(session, tmp_path, message="帮我写一份安全报告", force_new=True)
    confirm_writing_document_name(session, "安全报告", home=tmp_path, message="安全报告")
    calls = {"n": 0}

    def invoke(system: str, user: str) -> str:
        calls["n"] += 1
        return '{"intent":"match_blueprints","confidence":0.9}'

    assert ensure_write_onboarding_started(session, tmp_path, "分析第4.2章", invoke=invoke) is False
    assert user_wants_existing_blueprint_match("分析第4.2章", session=session, invoke=invoke) is False
    assert calls["n"] == 0


def test_write_intent_mints_lane(tmp_path: Path):
    from report_writing.chat.session_documents import ensure_write_onboarding_started

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="lazy_mint", phase=ChatPhase.INTENT.value)
    assert not onboarding_lane_active(session)
    ensure_write_onboarding_started(
        session, tmp_path, "帮我写一份安全报告", invoke=None
    )
    assert onboarding_lane_active(session)
    assert focus_document_key(session)
    from report_writing.chat.session_documents import (
        focus_working_title,
        is_writing_document_named,
        should_hang_onboarding_gap,
    )

    assert focus_working_title(session) == "安全报告"
    assert not is_writing_document_named(session)
    assert not should_hang_onboarding_gap(session, "帮我写一份安全报告")


def test_handle_chat_hello_no_name_ask(tmp_path: Path):
    from report_writing.chat import handle_chat
    from report_writing.domain.models import ChatRequest
    from report_writing.services.robots import create_robot

    create_robot(tmp_path, {"id": "r1", "name": "bot"})
    resp = handle_chat(
        tmp_path,
        ChatRequest(message="你好", robot_id="r1"),
        invoke=lambda s, u: f"echo:{u[:20]}",
    )
    assert "先给这份文档起个名字" not in (resp.reply or "")
