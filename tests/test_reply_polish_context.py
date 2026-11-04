"""润色场景约束：无蓝本时禁止粘贴目录话术。"""

from __future__ import annotations

from report_writing.chat.helpers import compose_polish_situation
from report_writing.domain.models import ChatCard, ChatPhase, ChatSession


def test_skip_blueprint_situation_forbids_paste_outline():
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "plan": {"goal": "skip_blueprint"},
            "meta_confirmed": True,
            "category": "述职报告",
            "title": "2026个人述职",
            "outline_pending_edit": True,
        },
    )
    cards = [
        ChatCard(
            type="outline_preview",
            title="目录",
            payload={"editable": True, "task_id": "w1"},
        )
    ]
    ctx = compose_polish_situation(session, cards)
    assert "不用蓝本" in ctx or "粘贴" in ctx
    assert "拓扑" in ctx or "卡片" in ctx
