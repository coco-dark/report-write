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
    # 要点关联不得写入章级 element_ids
    assert el.id not in (node.element_ids or [])
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
    assert el.id not in (node.element_ids or [])


def test_add_element_point_scope_flag_without_index_stays_off_chapter(tmp_path: Path):
    """associate_scope=point 且无 point_index 时，仍不得写入章级 element_ids。"""
    home = tmp_path
    tid = "w_el_point_scope"
    path = home / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1",
                title="法律",
                points=["按本项目填写法律名称清单（待补充：法律名称清单）"],
                point_items=[
                    {
                        "id": "p0",
                        "text": "按本项目填写法律名称清单（待补充：法律名称清单）",
                        "slot": "法律名称清单",
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
            "name": "法律名称清单",
            "kind": "file_slot",
            "associate_scope": "point",
            "slot": "法律名称清单",
        },
    )
    node = task.outline.chapters[0]
    assert el.id not in (node.element_ids or [])
    assert node.point_items[0].get("element_id") == el.id


def test_add_element_chapter_scope_writes_element_ids(tmp_path: Path):
    home = tmp_path
    tid = "w_el_chapter"
    path = home / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1",
                title="概述",
                points=["交代项目背景"],
                point_items=[{"id": "p0", "text": "交代项目背景"}],
            )
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    el = _apply_add_chapter_element(
        task,
        "c1",
        {
            "name": "项目背景",
            "kind": "kv",
            "kv_pairs": [{"key": "项目背景", "value": "示例"}],
        },
    )
    node = task.outline.chapters[0]
    assert el.id in (node.element_ids or [])
    assert not any(
        str(row.get("element_id") or "").strip() == el.id
        for row in (node.point_items or [])
        if isinstance(row, dict)
    )


def test_apply_chapter_config_points_do_not_auto_link_chapter_elements(tmp_path: Path):
    from report_writing.chat.writing import _apply_chapter_config

    home = tmp_path
    tid = "w_el_no_autolink"
    path = home / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                title="建设规模",
                points=["按本项目填写特性表（待补充：建设规模）"],
                point_items=[
                    {
                        "id": "p0",
                        "text": "按本项目填写特性表（待补充：建设规模）",
                        "slot": "建设规模",
                        "needs_supplement": True,
                    }
                ],
                element_ids=["el-scale"],
            )
        ]
    )
    task.element_nodes = []
    _apply_chapter_config(
        task,
        "c1",
        {"points_text": "按本项目填写特性表（待补充：建设规模）\n补充说明"},
    )
    node = task.outline.chapters[0]
    assert node.element_ids == ["el-scale"]
    assert all(
        not str(row.get("element_id") or "").strip()
        for row in (node.point_items or [])
        if isinstance(row, dict)
    )
