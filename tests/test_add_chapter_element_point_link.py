"""add_chapter_element：要素写入任务并关联 point_items 行。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.writing import _apply_add_chapter_element
from report_writing.controller import ensure_task
from report_writing.domain.models import OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_add_element_links_point_row_by_slot(tmp_path: Path):
    home = tmp_path
    tid = "w_el_link"
    path = home / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1.1",
                title="岗位职责",
                points=["说明岗位（待补充：任职文件或岗位说明书）"],
                point_items=[
                    {
                        "id": "p0",
                        "text": "说明岗位（待补充：任职文件或岗位说明书）",
                        "slot": "任职文件或岗位说明书",
                        "needs_supplement": True,
                    }
                ],
            )
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    el = _apply_add_chapter_element(
        task,
        "c1",
        {
            "name": "任职文件或岗位说明书",
            "kind": "kv",
            "kv_pairs": [{"key": "任职文件或岗位说明书", "value": ""}],
            "slot": "任职文件或岗位说明书",
            "point_index": 0,
        },
    )
    node = task.outline.chapters[0]
    assert el.id in (node.element_ids or [])
    assert node.point_items[0].get("element_id") == el.id
    assert node.point_items[0].get("needs_supplement") is False
    assert any(el.id == x.id for x in task.element_nodes)


def test_add_element_does_not_use_id_as_name(tmp_path: Path):
    home = tmp_path
    tid = "w_el_name"
    path = home / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c12",
                sequence="1.2",
                title="年度工作目标与任务清单",
                point_items=[
                    {
                        "id": "p0",
                        "text": "先说明来源（待补充：年度目标责任书或绩效考核表）",
                        "slot": "年度目标责任书或绩效考核表",
                        "needs_supplement": True,
                    }
                ],
            )
        ]
    )
    el = _apply_add_chapter_element(
        task,
        "c12",
        {
            "id": "el-1793773802966-abc",
            "name": "",
            "kind": "file",
            "slot": "年度目标责任书或绩效考核表",
            "point_index": 0,
        },
    )
    assert el.name == "年度目标责任书或绩效考核表"
    assert not el.name.startswith("el-")
    node = task.outline.chapters[0]
    assert node.point_items[0].get("element_id") == el.id
    assert node.point_items[0].get("element_name") == "年度目标责任书或绩效考核表"
