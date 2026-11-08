"""目录已确认后，不得再被 outline_done / go_outline_confirm 拽回确认目录。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.resource_actions import _action_blueprint_wizard, execute_wizard_command
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _seed_confirmed(home: Path, bid: str = "bp_confirmed") -> str:
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "已确认目录"
    task.sample_ready = True
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述"),
            OutlineNode(key="c2", sequence="2", title="工程"),
        ]
    )
    TaskStore(path).save(task)
    return bid


def test_go_outline_confirm_when_already_confirmed_goes_study(tmp_path: Path):
    bid = _seed_confirmed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "outline_confirm"}},
    )
    reply, _cards = execute_wizard_command(tmp_path, session, "go_outline_confirm")
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"
    assert "确认目录" not in reply or "已经确认" in reply
    assert "开始分析" in reply or "分析" in reply


def test_outline_done_when_already_confirmed_does_not_reprompt(tmp_path: Path):
    bid = _seed_confirmed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "outline_confirm",
                "outline_confirmed": True,
            }
        },
    )
    reply, _cards = _action_blueprint_wizard(
        tmp_path,
        session,
        {"step": "outline_done", "blueprint_id": bid, "chapter_count": 2},
    )
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"
    assert "已经从样板里提出来了" not in reply
    assert "已经确认" in reply or "开始分析" in reply


def test_outline_confirm_idempotent_advances_to_study(tmp_path: Path):
    bid = _seed_confirmed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "outline_confirm",
                "outline_confirmed": True,
            }
        },
    )
    reply, _cards = _action_blueprint_wizard(
        tmp_path,
        session,
        {"step": "outline_confirm", "blueprint_id": bid, "outline_confirmed": True},
    )
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"
    assert "已经确认" in reply


def test_go_study_after_confirm_does_not_ask_confirm(tmp_path: Path):
    bid = _seed_confirmed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study", "outline_confirmed": True}},
    )
    reply, _cards = execute_wizard_command(
        tmp_path, session, "go_study", study_method="sample"
    )
    assert "还没确认" not in reply
    assert "确认目录" not in reply or "已经确认" in reply
    assert "学" in reply or "分析" in reply


def test_step_outline_when_confirmed_does_not_restart_extract(tmp_path: Path):
    """已确认目录后 step=outline（无 re_extract）不得回滚重提，避免章数漂移循环。"""
    bid = _seed_confirmed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "study",
                "outline_confirmed": True,
            }
        },
    )
    reply, _cards = _action_blueprint_wizard(
        tmp_path,
        session,
        {"step": "outline", "blueprint_id": bid, "outline_extract_active": True},
    )
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"
    assert wizard.get("outline_confirmed") is True
    assert not wizard.get("outline_extract_active")
    assert "开始从样板提取目录" not in reply
    assert "已经确认" in reply or "开始分析" in reply
    task = TaskStore(tmp_path / "tasks" / bid).load()
    assert task.outline_confirmed is True
    assert len(task.outline.chapters) == 2


def test_step_study_without_command_does_not_auto_begin(tmp_path: Path):
    """确认目录后前端只推 step=study：进分析步但不自动开跑。"""
    bid = _seed_confirmed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "outline_confirm"}},
    )
    reply, cards = _action_blueprint_wizard(
        tmp_path,
        session,
        {
            "step": "study",
            "blueprint_id": bid,
            "outline_confirmed": True,
            "trigger_begin_study": False,
        },
    )
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"
    assert not wizard.get("trigger_begin_study")
    assert cards and cards[0].payload.get("step") == "study"
    assert "开始分析全部" in reply or "指定范围" in reply
    assert "按当前目录开始学全部" not in reply
