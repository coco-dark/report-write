"""资料包按写作文档绑定：新开写默认无包；有 file_slot 则对话提示；未配不检索。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.materials_pack_gate import (
    materials_pack_gate_satisfied,
    soft_prompt_materials_pack_if_needed,
    task_needs_materials_pack,
)
from report_writing.chat.writing import clone_blueprint_to_writing_task
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChatSession,
    ElementNode,
    FileSlotDef,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.store import TaskStore


def _blueprint_with_pack(home: Path, bp_id: str = "bp_pack_doc") -> WritingTask:
    ensure_task(home / "tasks" / bp_id)
    bp = TaskStore(home / "tasks" / bp_id).load()
    bp.title = "带资料包引用蓝本"
    bp.outline_confirmed = True
    bp.materials_pack_id = "mp-should-not-inherit"
    bp.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", element_ids=["el-slot"]),
        ]
    )
    bp.element_nodes = [
        ElementNode(
            id="el-slot",
            name="概况槽",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="概况", query="工程")],
        )
    ]
    TaskStore(home / "tasks" / bp_id).save(bp)
    return bp


def test_clone_clears_materials_pack_id(tmp_path: Path):
    home = tmp_path
    _blueprint_with_pack(home)
    cloned = clone_blueprint_to_writing_task(home, "bp_pack_doc", chapter_keys=["c1"])
    assert cloned.materials_pack_id == ""
    assert task_needs_materials_pack(cloned) is True


def test_soft_prompt_when_blueprint_has_file_slot(tmp_path: Path):
    home = tmp_path
    bp = _blueprint_with_pack(home)
    session = ChatSession(id="s1", blueprint_id=bp.id)
    tip, cards = soft_prompt_materials_pack_if_needed(session, home, bp)
    assert tip
    assert "资料包" in tip
    assert cards and cards[0].payload.get("hint_only") is True
    assert cards[0].payload.get("per_document") is True
    # 软提示不得把包绑回蓝本
    reloaded = TaskStore(home / "tasks" / bp.id).load()
    assert reloaded.materials_pack_id == "mp-should-not-inherit"


def test_soft_prompt_skipped_without_file_slot(tmp_path: Path):
    home = tmp_path
    ensure_task(home / "tasks" / "bp_plain")
    bp = TaskStore(home / "tasks" / "bp_plain").load()
    bp.element_nodes = [ElementNode(id="e1", name="固定", kind="fixed", value="x")]
    TaskStore(home / "tasks" / "bp_plain").save(bp)
    tip, cards = soft_prompt_materials_pack_if_needed(ChatSession(id="s2"), home, bp)
    assert tip == ""
    assert cards == []


def test_gate_blocks_until_pack_or_skip(tmp_path: Path):
    home = tmp_path
    _blueprint_with_pack(home)
    writing = clone_blueprint_to_writing_task(home, "bp_pack_doc", chapter_keys=["c1"])
    session = ChatSession(id="s3", blueprint_id="bp_pack_doc", task_id=writing.id)
    assert materials_pack_gate_satisfied(session, writing) is False
    writing.materials_pack_id = "mp-x"
    assert materials_pack_gate_satisfied(session, writing) is True
    writing.materials_pack_id = ""
    from report_writing.chat.materials_pack_gate import mark_materials_pack_skipped

    mark_materials_pack_skipped(session, writing.id, skipped=True)
    assert materials_pack_gate_satisfied(session, writing) is True


def test_create_pack_before_writing_task_stashes_pending(tmp_path: Path):
    """选用蓝本后、尚未开写也可新建资料包（不必先有写作任务）。"""
    from report_writing.chat.materials_pack_gate import (
        apply_materials_pack_choice,
        peek_pending_materials_pack,
    )

    home = tmp_path
    bp = _blueprint_with_pack(home)
    session = ChatSession(id="s4", blueprint_id=bp.id, task_id="")
    reply, cards = apply_materials_pack_choice(
        home,
        session,
        "materials_pack_create",
        {"name": "项目资料", "blueprint_id": bp.id},
    )
    assert "已新建资料包" in reply
    assert "写作任务" not in reply
    pending = peek_pending_materials_pack(session, blueprint_id=bp.id)
    assert pending
    assert cards and cards[0].payload.get("pack_id") == pending
    # 蓝本本身不应被绑上资料包
    reloaded = TaskStore(home / "tasks" / bp.id).load()
    assert reloaded.materials_pack_id == "mp-should-not-inherit"
