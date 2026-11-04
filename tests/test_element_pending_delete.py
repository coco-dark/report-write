"""待收录要素：删除时从任务与大纲关联移除。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.element_library_pending import (
    delete_pending_elements,
    list_pending,
    queue_element_pending,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_delete_pending_removes_element_from_task(tmp_path: Path):
    bid = "bp_el_del"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    el = ElementNode(id="el-del", name="立项审批信息", kind="kv", value="v")
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                title="章一",
                sequence="1",
                element_ids=["el-del"],
                point_items=[
                    {
                        "id": "p0",
                        "text": "审批（待补充：文号）",
                        "slot": "文号",
                        "element_id": "el-del",
                        "element_name": "立项审批信息",
                    }
                ],
            )
        ]
    )
    task.element_nodes = [el]
    TaskStore(path).save(task)

    session = ChatSession(id="s_del", freeform={})
    entry = queue_element_pending(
        session,
        el,
        task_id=bid,
        chapter_key="c1",
        chapter_title="章一",
    )
    pid = str(entry["pending_id"])
    result = delete_pending_elements(session, [pid], home=tmp_path)
    assert result["removed_pending"] == 1
    assert result["removed_elements"] == 1
    assert not list_pending(session)

    task2 = TaskStore(path).load()
    assert task2.element_nodes == []
    ch = task2.outline.chapters[0]
    assert ch.element_ids == []
    assert ch.point_items[0].get("element_id") in (None, "")
    assert ch.point_items[0].get("needs_supplement") is True
