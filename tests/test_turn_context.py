from report_writing.chat.turn_context import (
    build_writing_turn_context,
    persist_turn_snapshot,
    turn_context_prompt_block,
)
from report_writing.domain.models import ChatMessage, ChatPhase, ChatSession, SessionGoal


def test_turn_context_includes_dialogue_and_goal():
    session = ChatSession(
        id="s1",
        phase=ChatPhase.WRITING.value,
        task_id="w1",
        messages=[
            ChatMessage(role="user", text="我要写述职报告"),
            ChatMessage(role="assistant", text="好的，我们先定目录"),
        ],
    )
    session.freeform = {
        "session_goal": {
            "doc_category": "述职报告",
            "doc_title": "2024年度述职",
            "notes": "姓名：李志",
        }
    }
    ctx = build_writing_turn_context(session, "请重写本章，部门研发")
    block = turn_context_prompt_block(ctx)
    assert isinstance(ctx.session_goal, SessionGoal)
    assert "李志" in ctx.session_goal_brief or "李志" in str(ctx.session_goal)
    assert ctx.session_goal.doc_title == "2024年度述职"
    assert "请重写本章" in block
    assert ctx.to_dict()["session_goal"]["doc_category"] == "述职报告"
    assert ctx.to_dict()["user_message"] == "请重写本章，部门研发"
    assert persist_turn_snapshot(session, ctx)
    snap = (session.freeform or {}).get("last_turn_snapshot") or {}
    assert snap.get("user_message") == "请重写本章，部门研发"
    assert "李志" in str(snap.get("session_goal_brief") or "")


def test_turn_context_revision_includes_config_revision():
    from report_writing.chat.turn_context import _turn_context_revision

    session = ChatSession(id="s1", phase=ChatPhase.WRITING.value, task_id="w1")
    session.freeform = {"config_revision": 3}
    rev = _turn_context_revision(session, home=None)
    assert rev.split(":")[2] == "3"
