"""确认大纲由意图模型判定；同轮多意图时优先确认。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.actions import _action_skip_blueprint, _collect_freeform_outline
from report_writing.chat.freeform_intent import classify_freeform_actions, wants_outline_confirm
from report_writing.chat.freeform_route import _apply_single_freeform_intent
from report_writing.domain.models import ChatPhase, ChatSession


def test_confirm_phrases_via_lexical():
    for msg in (
        "确认大纲",
        "目录可以了，确认大纲",
        "先这样吧，目录可以了，确认大纲",
        "就这个目录，确认并继续",
        "大纲可以了",
    ):
        assert wants_outline_confirm(msg), msg
        assert classify_freeform_actions(msg) == ["confirm_outline"], msg


def test_confirm_outline_phrase_not_rename(tmp_path: Path):
    """确认大纲短语优先，不走改名。"""
    msg = "先这样吧，目录可以了，确认大纲"
    assert wants_outline_confirm(msg, phase="freeform_outline")


def test_freeform_route_confirm_does_not_regenerate(tmp_path: Path):
    session = ChatSession(id="fc1", phase=ChatPhase.INTENT.value)
    _action_skip_blueprint(session, home=tmp_path, invoke=None)
    _collect_freeform_outline(
        tmp_path,
        session,
        "工作说明｜确认覆盖测试\n1 概述\n2 正文\n3 附录",
        invoke=None,
        emit=None,
    )
    session.phase = ChatPhase.FREEFORM_ASK.value
    session.freeform = {
        **(session.freeform or {}),
        "meta_confirmed": True,
        "category": "工作说明",
        "title": "确认覆盖测试",
        "outline_text": "1 概述\n2 正文\n3 附录",
        "outline_pending_edit": True,
    }
    session.phase = ChatPhase.FREEFORM_OUTLINE.value
    if not session.task_id:
        from report_writing.controller import ensure_task
        from report_writing.services.store import TaskStore
        from report_writing.domain.models import OutlineNode, OutlineTree

        tid = "ff_confirm_ov"
        ensure_task(tmp_path / "tasks" / tid)
        task = TaskStore(tmp_path / "tasks" / tid).load()
        task.title = "确认覆盖测试"
        task.category = "工作说明"
        task.outline = OutlineTree(
            chapters=[
                OutlineNode(key="c1", sequence="1", title="概述"),
                OutlineNode(key="c2", sequence="2", title="正文"),
            ]
        )
        TaskStore(tmp_path / "tasks" / tid).save(task)
        session.task_id = tid
        session.freeform = {
            **(session.freeform or {}),
            "outline_text": "1 概述\n2 正文",
            "outline_pending_edit": True,
        }

    hit = _apply_single_freeform_intent(
        tmp_path,
        session,
        "先这样吧，目录可以了，确认大纲",
        "confirm_outline",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        tid=str(session.task_id or ""),
        invoke=None,
        llm=None,
        emit=None,
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "搭了" not in reply
    assert "确认" in reply or session.phase in {
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.READY_TO_WRITE.value,
    }
