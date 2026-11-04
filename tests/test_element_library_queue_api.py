"""文档窗新增要素：入队待收录。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.element_library_pending import list_pending, queue_element_pending
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_queue_new_element_pending_for_doc_window(tmp_path: Path):
    tid = "w_el_queue"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", sequence="1", title="概述", element_ids=["el-new"])]
    )
    el = ElementNode(id="el-new", name="站址概况", kind="file", query="站址")
    task.element_nodes = [el]
    TaskStore(path).save(task)

    session = ChatSession(id="s-queue", task_id=tid, freeform={})
    entry = queue_element_pending(
        session,
        el,
        task_id=tid,
        chapter_key="c1",
        chapter_title="1 概述",
    )
    assert entry.get("pending_id")
    pending = list_pending(session)
    assert len(pending) == 1
    assert (pending[0].get("element") or {}).get("id") == "el-new"
    assert pending[0].get("task_id") == tid
