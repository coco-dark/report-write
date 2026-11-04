"""「不知道怎么写 / 你帮我写」应先走无蓝本大纲，不能误开写。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from report_writing.chat.actions import _action_start_writing, _collect_freeform_meta
from report_writing.chat.freeform_help import wants_freeform_help_draft
from report_writing.domain.models import ChatPhase, ChatSession


def test_help_write_phrase_detected():
    def invoke(_s: str, _u: str) -> str:
        return '{"intent":"help_draft","confidence":0.9}'

    assert wants_freeform_help_draft(
        "我不知道怎么写 你帮我写",
        invoke=invoke,
        phase="freeform_points",
    )


def test_start_writing_blocked_without_outline_confirmed(tmp_path: Path):
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_ASK.value,
        task_id="w20260101120000aa",
        freeform={"meta_confirmed": True, "category": "述职", "title": "报告"},
    )
    reply, cards, events = _action_start_writing(
        tmp_path,
        session,
        writing_pace="batch",
    )
    assert events == []
    assert "大纲" in reply or "确认" in reply
    assert session.phase in {ChatPhase.FREEFORM_OUTLINE.value, ChatPhase.FREEFORM_POINTS.value}


def test_help_write_with_session_goal_confirms_meta_without_auto_outline(tmp_path: Path):
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_ASK.value,
        freeform={"plan": {"goal": "skip_blueprint"}},
    )
    session.freeform = {
        **(session.freeform or {}),
        "session_goal": {
            "doc_category": "述职报告",
            "doc_title": "2024年度个人工作述职报告",
        },
    }
    invoke = MagicMock(return_value="1 概述\n2 工作完成情况\n3 结语")
    reply, cards = _collect_freeform_meta(session, "你帮我写", home=tmp_path, invoke=invoke)
    assert session.freeform.get("meta_confirmed") is True
    assert session.phase == ChatPhase.FREEFORM_OUTLINE.value
    assert not any(c.type == "outline_preview" for c in cards)
    assert "生成目录" in reply or "粘贴" in reply or "目录" in reply
