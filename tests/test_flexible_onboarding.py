"""灵活收集：工作名推进写作，正式名仅收录/导出；车道 goal 隔离。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.session_documents import (
    apply_working_title,
    begin_document_onboarding,
    confirm_writing_document_name,
    document_lane,
    ensure_write_onboarding_started,
    flush_lane,
    focus_document_key,
    focus_has_display_title,
    focus_needs_doc_name,
    focus_working_title,
    hydrate_lane,
    is_writing_document_named,
    should_hang_onboarding_gap,
)
from report_writing.chat.session_goal import get_session_goal, merge_session_goal
from report_writing.chat.tool_capability import missing_for_tool
from report_writing.domain.models import ChatPhase, ChatSession


def test_write_intent_sets_working_title_not_named(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="flex_work", phase=ChatPhase.INTENT.value)
    ensure_write_onboarding_started(session, tmp_path, "帮我写一份安全报告")
    assert focus_document_key(session)
    assert focus_working_title(session) == "安全报告"
    assert focus_has_display_title(session)
    assert not is_writing_document_named(session)
    assert focus_needs_doc_name(session)
    assert not should_hang_onboarding_gap(session, "帮我写一份安全报告")


def test_match_tool_has_no_name_prereq(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="flex_match", phase=ChatPhase.INTENT.value)
    ensure_write_onboarding_started(
        session, tmp_path, "使用lzb安全报告 给我写一份安全报告"
    )
    assert missing_for_tool(session, "match_blueprints", home=tmp_path) == []
    assert missing_for_tool(session, "begin_blueprint_wizard", home=tmp_path) == []
    assert missing_for_tool(session, "list_blueprints", home=tmp_path) == []


def test_export_requires_formal_name(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="flex_export", phase=ChatPhase.INTENT.value)
    ensure_write_onboarding_started(session, tmp_path, "帮我写一份安全报告")
    assert "doc_name" in missing_for_tool(
        session, "prepare_export_word", home=tmp_path
    )
    assert "doc_name" in missing_for_tool(
        session, "save_freeform_as_blueprint", home=tmp_path
    )
    confirm_writing_document_name(session, "lzb安全报告", home=tmp_path, message="名字叫lzb安全报告")
    assert missing_for_tool(session, "prepare_export_word", home=tmp_path) == []


def test_start_writing_needs_outline_not_name(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="flex_write", phase=ChatPhase.INTENT.value)
    ensure_write_onboarding_started(session, tmp_path, "帮我写一份安全报告")
    miss = missing_for_tool(session, "start_writing", home=tmp_path)
    assert "doc_name" not in miss
    assert "outline" in miss


def test_hello_still_does_not_hang():
    session = ChatSession(id="flex_hi", phase=ChatPhase.INTENT.value)
    assert not should_hang_onboarding_gap(session, "你好")
    assert not should_hang_onboarding_gap(session, "看看有哪些蓝本")


def test_session_goal_isolated_per_lane(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="flex_goal", phase=ChatPhase.INTENT.value)
    begin_document_onboarding(
        session, tmp_path, message="写一份环境报告", force_new=True
    )
    apply_working_title(session, "环境报告", home=tmp_path, message="写一份环境报告")
    confirm_writing_document_name(
        session, "环境报告", home=tmp_path, message="名字叫环境报告"
    )
    merge_session_goal(session, {"doc_title": "环境报告", "summary": "环境报告｜工作名"})
    old_key = focus_document_key(session)
    assert old_key
    flush_lane(session, key=old_key)

    begin_document_onboarding(
        session, tmp_path, message="再写一份述职报告", title_hint="述职报告", force_new=True
    )
    apply_working_title(session, "述职报告", home=tmp_path, message="再写一份述职报告")
    merge_session_goal(session, {"doc_title": "述职报告", "summary": "述职报告｜工作名"})
    new_key = focus_document_key(session)
    assert new_key and new_key != old_key
    assert "述职" in str(get_session_goal(session).get("doc_title") or "")

    hydrate_lane(session, old_key)
    old_goal = get_session_goal(session)
    assert "环境" in str(old_goal.get("doc_title") or old_goal.get("summary") or "")
    assert "述职" not in str(old_goal.get("doc_title") or "")
    assert "述职" not in str(document_lane(session, old_key).get("session_goal") or {})


def test_handle_chat_write_intent_no_name_card(tmp_path: Path):
    from report_writing.chat_orchestrator import handle_chat
    from report_writing.domain.models import ChatRequest
    from report_writing.services.robots import create_robot

    create_robot(tmp_path, {"id": "r1", "name": "bot"})
    resp = handle_chat(
        tmp_path,
        ChatRequest(message="帮我写一份安全报告", robot_id="r1"),
        invoke=lambda s, u: f"echo:{u[:20]}",
    )
    assert "先给这份文档起个名字" not in (resp.reply or "")
    session = None
    from report_writing.services.chat_session import get_session

    if resp.session_id:
        session = get_session(tmp_path, resp.session_id)
    if session is not None:
        assert not is_writing_document_named(session)
        assert "先给这份文档起个名字" not in (resp.reply or "")


def test_ingest_does_not_pollute_active_lane_on_new_write_topic(tmp_path: Path):
    from report_writing.chat.writing_brief import ingest_user_turn

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="flex_ingest", phase=ChatPhase.INTENT.value)
    begin_document_onboarding(
        session, tmp_path, message="帮我写一份安全报告", force_new=True
    )
    apply_working_title(session, "安全报告", home=tmp_path, message="帮我写一份安全报告")
    confirm_writing_document_name(
        session, "lzb 安全报告", home=tmp_path, message="名字改为lzb 安全报告"
    )
    merge_session_goal(session, {"doc_title": "lzb 安全报告", "doc_category": "安全报告"})
    old_key = focus_document_key(session)
    assert old_key

    changed = ingest_user_turn(session, "再给我写一份述职报告 三个章节 名字叫做lzb述职报告")
    assert changed is False
    goal = get_session_goal(session)
    assert "述职" not in str(goal.get("doc_title") or "")
    assert "安全" in str(goal.get("doc_title") or "")
