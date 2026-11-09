"""资料包缺口 / 关联章查询须包含要点上的 element_id，不能只看章级 element_ids。"""

from __future__ import annotations

from pathlib import Path

from report_writing.domain.models import (
    ElementNode,
    FileSlotDef,
    OutlineNode,
    OutlineTree,
    WritingBrief,
    WritingTask,
)


def _task_with_point_only_file_slot() -> WritingTask:
    task = WritingTask(id="t-point-link")
    task.element_nodes = [
        ElementNode(
            id="el-fs",
            name="法律名称清单",
            kind="file_slot",
            file_slots=[FileSlotDef(no=1, name="法律法规", query="法律")],
        )
    ]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="laws",
                title="法律",
                sequence="1.5.1",
                element_ids=[],
                point_items=[
                    {
                        "id": "p0",
                        "text": "按本项目填写法律名称清单",
                        "element_id": "el-fs",
                        "element_name": "法律名称清单",
                    }
                ],
            )
        ]
    )
    task.brief = WritingBrief(
        template_id="tpl",
        template_version="1",
        chapters=[],
    )
    return task


def test_materials_pack_gate_sees_point_linked_file_slot(tmp_path: Path):
    from report_writing.chat.materials_pack_gate import _chapter_element_ids, collect_write_slot_gaps

    task = _task_with_point_only_file_slot()
    assert _chapter_element_ids(task, "laws") == ["el-fs"]
    gaps = collect_write_slot_gaps(tmp_path, task, ["laws"])
    assert gaps and gaps[0]["element_id"] == "el-fs"


def test_chapters_linking_element_includes_point_links():
    from report_writing.chat.element_document_actions import chapters_linking_element

    task = _task_with_point_only_file_slot()
    rows = chapters_linking_element(task, "el-fs")
    assert rows and rows[0][0] == "laws"


def test_action_slots_chapter_elements_includes_point_links():
    from report_writing.chat.action_slots import _chapter_elements

    task = _task_with_point_only_file_slot()
    rows = _chapter_elements(task, "laws")
    assert rows and rows[0][0] == "el-fs"
