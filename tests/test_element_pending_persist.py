"""要素暂不收录时保留任务上的要素与分类/标签/作用。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.element_library_pending import (
    dismiss_pending,
    queue_element_pending,
)
from report_writing.chat.writing import _apply_chapter_config
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChatSession,
    ElementNode,
    OutlineNode,
    OutlineTree,
)
from report_writing.services.store import TaskStore


def test_empty_elements_payload_does_not_wipe(tmp_path: Path):
    bid = "bp_el_wipe"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    task.element_nodes = [ElementNode(id="el-1", name="建设规模", kind="rag_search", query="q")]
    task.outline.chapters[0].element_ids = ["el-1"]
    TaskStore(path).save(task)

    task = TaskStore(path).load()
    _apply_chapter_config(
        task,
        "c1",
        {"points_text": "要点A", "elements": [], "element_ids": ["el-1"]},
    )
    assert len(task.element_nodes) == 1
    assert task.element_nodes[0].name == "建设规模"


def test_dismiss_pending_persists_library_meta(tmp_path: Path):
    bid = "bp_el_meta"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    el = ElementNode(id="el-2", name="建设规模", kind="rag_search", query="规模")
    task.element_nodes = [el]
    task.outline.chapters[0].element_ids = ["el-2"]
    TaskStore(path).save(task)

    session = ChatSession(id="s_el", freeform={})
    entry = queue_element_pending(
        session,
        el,
        task_id=bid,
        chapter_key="c1",
        chapter_title="章一",
        category="工程",
        tags=["规模"],
        purpose="写建设规模段落",
    )
    assert entry.get("pending_id")
    removed = dismiss_pending(
        session,
        [entry["pending_id"]],
        home=tmp_path,
        entries=[
            {
                "pending_id": entry["pending_id"],
                "category": "工程信息",
                "tags": ["规模", "概况"],
                "purpose": "段落用途",
            }
        ],
    )
    assert removed == 1
    assert list_pending_empty(session)
    task2 = TaskStore(path).load()
    hit = next(e for e in task2.element_nodes if e.id == "el-2")
    assert hit.category == "工程信息"
    assert hit.tags == ["规模", "概况"]
    assert hit.purpose == "段落用途"
    assert hit.query == "规模"


def list_pending_empty(session: ChatSession) -> bool:
    from report_writing.chat.element_library_pending import list_pending

    return not list_pending(session)


def test_pending_library_card_after_queue(tmp_path: Path):
    from report_writing.chat.element_library_pending import pending_library_card

    bid = "bp_el_card"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    el = ElementNode(id="el-3", name="评价目的", kind="kv")
    session = ChatSession(id="s_card", freeform={})
    queue_element_pending(
        session,
        el,
        task_id=bid,
        chapter_key="c1",
        chapter_title="1.1",
        category="评价",
        tags=["目的"],
        purpose="写目的段",
    )
    card = pending_library_card(session)
    assert card is not None
    assert card.type == "element_library_pending"
    assert card.payload.get("count") == 1
    assert card.payload["items"][0]["element"]["name"] == "评价目的"
