"""无蓝本：粘贴/生成目录须先进可编辑草案，不能静默确认。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.actions import _collect_freeform_outline
from report_writing.domain.models import ChatPhase, ChatSession


def test_pasted_outline_stays_editable(tmp_path: Path):
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "meta_confirmed": True,
            "category": "述职报告",
            "title": "2024年度个人工作述职报告",
        },
    )
    pasted = "1 概述\n2 工作完成情况\n3 计划"
    reply, cards = _collect_freeform_outline(tmp_path, session, pasted)
    assert session.freeform.get("outline_pending_edit") is True
    preview = next(c for c in cards if c.type == "outline_preview")
    assert preview.payload.get("editable") is True
    assert (
        "草案" in reply
        or "修改" in reply
        or "搭了" in reply
        or "随便改" in reply
        or "确认大纲" in reply
        or "卡片" in reply
    )
