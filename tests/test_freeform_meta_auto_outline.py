"""定好类型标题后不自动起草目录，等用户/总控再调度 submit_outline。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from report_writing.chat.actions import _collect_freeform_meta
from report_writing.domain.models import ChatPhase, ChatSession


def test_meta_line_confirms_without_auto_outline(tmp_path: Path):
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_ASK.value,
        freeform={"plan": {"goal": "skip_blueprint"}},
    )
    invoke = MagicMock(return_value="1 概述\n2 工作完成情况\n3 结语")
    reply, cards = _collect_freeform_meta(
        session,
        "述职报告｜2026个人述职报告",
        home=tmp_path,
        invoke=invoke,
    )
    assert session.freeform.get("meta_confirmed") is True
    assert session.phase == ChatPhase.FREEFORM_OUTLINE.value
    assert not any(c.type == "outline_preview" for c in cards)
    assert "2026个人述职报告" in reply or "就叫" in reply


def test_meta_generate_phrase_asks_for_title_without_classifying(tmp_path: Path):
    session = ChatSession(id="s_gen", phase=ChatPhase.FREEFORM_ASK.value)

    def invoke(_s: str, _u: str) -> str:
        return '{"intent":"generate_outline","confidence":0.99}'

    reply, cards = _collect_freeform_meta(
        session, "帮我生成目录", home=tmp_path, invoke=invoke
    )
    assert session.freeform.get("meta_confirmed") is not True
    assert "叫什么" in reply or "题目" in reply or "类型" in reply or "名字" in reply
    assert not any(c.type == "outline_preview" for c in cards)


def test_meta_generate_phrase_reuses_existing_goal(tmp_path: Path):
    session = ChatSession(
        id="s_goal",
        phase=ChatPhase.FREEFORM_ASK.value,
        freeform={
            "session_goal": {
                "doc_category": "述职报告",
                "doc_title": "2026个人述职",
            }
        },
    )
    reply, cards = _collect_freeform_meta(
        session, "帮我生成目录", home=tmp_path, invoke=None
    )
    assert session.freeform.get("meta_confirmed") is True
    assert session.freeform.get("title") == "2026个人述职"
    assert not any(c.type == "outline_preview" for c in cards)
    assert "2026个人述职" in reply or "就叫" in reply
