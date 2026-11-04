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
    assert "生成目录" in reply or "粘贴" in reply
