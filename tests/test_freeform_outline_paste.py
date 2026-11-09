"""粘贴目录不误写入各章要点。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.actions import _collect_freeform_outline
from report_writing.chat.freeform_outline import looks_like_outline_directory, normalize_pasted_outline
from report_writing.controller import confirm_outline, ensure_task, save_outline, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode


def test_normalize_pasted_outline_splits_meta():
    raw = "述职报告｜2024年度个人工作述职报告\n1 概述\n2 工作完成情况"
    cat, title, body = normalize_pasted_outline(raw)
    assert cat == "述职报告"
    assert "2024" in title
    assert "1 概述" in body
    assert looks_like_outline_directory(raw)


def test_outline_with_dot_after_number_is_directory():
    raw = """述职报告 | 2024年度个人工作述职报告
1. 述职概述
2. 全年主要工作完成情况
3. 结语"""
    assert looks_like_outline_directory(raw)


def test_paste_in_points_phase_goes_to_outline_editor(tmp_path: Path):
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id="w20260101120000bb",
        freeform={"meta_confirmed": True, "category": "述职报告", "title": "报告"},
    )
    wdir = tmp_path / "tasks" / session.task_id
    ensure_task(wdir)
    save_spec_meta(wdir, title="报告", category="述职报告")
    save_outline(wdir, [OutlineNode(key="c1", sequence="1", title="旧章", level=1)])
    confirm_outline(wdir)

    pasted = """述职报告｜2024年度个人工作述职报告
1 述职概述
2 全年主要工作完成情况
3 结语"""
    from report_writing.chat.freeform_intent import suggest_tool_instead_of_submit_points_freeform

    hit = suggest_tool_instead_of_submit_points_freeform(pasted, session=session, invoke=None)
    assert hit and hit.get("tool") == "submit_outline"
    reply, cards = _collect_freeform_outline(tmp_path, session, pasted)
    assert session.phase == ChatPhase.FREEFORM_OUTLINE.value
    preview = next(c for c in cards if c.type == "outline_preview")
    assert preview.payload.get("editable") is True
    assert session.freeform.get("outline_pending_edit") is True
