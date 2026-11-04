"""session_goal action（路由认领已废弃，测执行层）。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.session_goal import get_session_goal
from report_writing.chat.session_goal_actions import run_session_goal_action
from report_writing.domain.models import ChatPhase, ChatSession


def test_run_set_session_meta_merges_goal(tmp_path: Path):
    session = ChatSession(id="s1", phase=ChatPhase.WRITING.value, task_id="t1")
    reply, _cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "工作说明｜测试项目"},
        invoke=None,
    )
    assert get_session_goal(session)["doc_title"] == "测试项目"
    assert reply


def test_route_meta_line(tmp_path: Path):
    session = ChatSession(id="s1", phase=ChatPhase.FREEFORM_ASK.value)
    reply, _cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "工作说明｜路由测试"},
        invoke=None,
    )
    assert reply
    assert get_session_goal(session).get("doc_title") == "路由测试"
