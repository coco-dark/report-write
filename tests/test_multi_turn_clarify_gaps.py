"""P0/P1：缺槽与改名/目录确认应 hang 多轮澄清，禁止静默默认或硬断。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.action_slots import ACTION_SLOT_SOURCE
from report_writing.chat.intent_clarify import get_pending
from report_writing.chat.intent_dispatch import execute_dispatch_tool
from report_writing.chat.resource_actions import _prompt_confirm_outline_before_study
from report_writing.chat.session_goal_actions import run_session_goal_action
from report_writing.chat.write_chapter_intent import try_write_chapters_from_message
from report_writing.controller import ensure_task, save_outline, save_spec_meta
from report_writing.domain.models import (
    ChapterState,
    ChapterStatus,
    ChatPhase,
    ChatSession,
    OutlineNode,
    OutlineTree,
)
from report_writing.services.store import TaskStore


def _writing_task(home: Path, tid: str = "w20260101140000dd"):
    path = home / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="总则"),
            OutlineNode(key="c2", sequence="2.2", title="建设规模"),
        ]
    )
    task.chapters = [
        ChapterState(key="c2", status=ChapterStatus.AWAITING_CONFIRM),
    ]
    TaskStore(path).save(task)
    return tid, task


def test_configure_chapter_does_not_silent_bind_awaiting(tmp_path: Path):
    tid, _ = _writing_task(tmp_path)
    session = ChatSession(id="s1", phase=ChatPhase.WRITING.value, task_id=tid)
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "configure_chapter", "args": {}},
        "帮我改配置",
    )
    assert hit is not None
    _reply, cards, _ = hit
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending is not None
    assert pending["source"] == ACTION_SLOT_SOURCE
    labels = [str(o.get("label") or "") for o in pending.get("options") or []]
    assert any("待确认" in lb for lb in labels)


def test_write_miss_hangs_action_slots(tmp_path: Path):
    bid = "bp_write_miss"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", points=["a"]),
            OutlineNode(key="c2", sequence="2", title="正文", points=["b"]),
        ]
    )
    TaskStore(path).save(task)
    session = ChatSession(
        id="s2",
        phase=ChatPhase.READY_TO_WRITE.value,
        blueprint_id=bid,
        task_id=bid,
    )
    hit = try_write_chapters_from_message(tmp_path, session, "帮我写一下")
    assert hit is not None
    reply, cards, _ = hit
    assert cards and any(c.type == "intent_clarify" for c in cards)
    pending = get_pending(session)
    assert pending and pending["source"] == ACTION_SLOT_SOURCE
    assert pending["candidate"]["name"] == "select_write_chapters"
    assert "章节" in reply or "写" in reply


def test_rename_unclear_hangs_session_meta(tmp_path: Path):
    tid = "w20260101140000ee"
    session = ChatSession(
        id="s3",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        task_id=tid,
        freeform={
            "meta_confirmed": True,
            "category": "工作汇报",
            "title": "工作汇报",
            "outline_pending_edit": True,
            "skip_blueprint": True,
        },
    )
    wdir = tmp_path / "tasks" / tid
    ensure_task(wdir)
    save_spec_meta(wdir, title="工作汇报", category="工作汇报")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="一", level=1),
            OutlineNode(key="c2", sequence="2", title="二", level=1),
        ],
    )
    reply, cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "嗯嗯"},
        invoke=None,
    )
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending and pending["source"] == "session_meta"
    assert "文档名" in reply or "类型" in reply


def test_outline_before_study_hangs_wizard_clarify(tmp_path: Path):
    bid = "bp_outline_block"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="章一"),
            OutlineNode(key="c2", sequence="2", title="章二"),
        ]
    )
    task.outline_confirmed = False
    TaskStore(path).save(task)
    session = ChatSession(
        id="s4",
        phase="blueprint_wizard",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study", "mode": "create"}},
    )
    blocked = _prompt_confirm_outline_before_study(tmp_path, session, bid, want="分析")
    assert blocked is not None
    reply, cards = blocked
    assert any(c.type == "intent_clarify" for c in cards)
    pending = get_pending(session)
    assert pending and pending["source"] == "wizard"
    assert pending["candidate"]["name"] == "confirm_outline"
    assert "确认" in reply
