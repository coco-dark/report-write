"""对话管理本文档要素：删除、取消关联、同名消歧。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.element_document_actions import (
    dissociate_chapter_element,
    extract_element_name_hint,
    match_task_elements,
    remove_document_element,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, ElementNode, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _seed_task(home: Path) -> str:
    tid = "el_doc_chat"
    ensure_task(home / "tasks" / tid)
    task = TaskStore(home / "tasks" / tid).load()
    task.title = "要素对话测"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", element_ids=["el-a", "el-b"]),
            OutlineNode(key="c2", sequence="2", title="详情", element_ids=["el-a"]),
        ]
    )
    task.element_nodes = [
        ElementNode(id="el-a", name="建设规模", kind="kv"),
        ElementNode(id="el-b", name="建设规模", kind="kv"),
        ElementNode(id="el-c", name="其它", kind="kv"),
    ]
    TaskStore(home / "tasks" / tid).save(task)
    return tid


def test_extract_element_name_hint():
    assert extract_element_name_hint("删除建设规模要素") == "建设规模"
    assert "建设规模" in extract_element_name_hint("把建设规模从2.1章取消关联")


def test_remove_document_element_disambiguate(tmp_path: Path):
    tid = _seed_task(tmp_path)
    session = ChatSession(id="s-el", phase=ChatPhase.CONFIRM_EXPORT.value, task_id=tid)
    reply, cards = remove_document_element(
        tmp_path, session, "删掉建设规模要素", {"task_id": tid}
    )
    assert cards
    assert cards[0].type == "intent_clarify"
    assert "建设规模" in reply or "哪一个" in reply


def test_remove_document_element_single(tmp_path: Path):
    tid = _seed_task(tmp_path)
    session = ChatSession(id="s-el2", phase=ChatPhase.CONFIRM_EXPORT.value, task_id=tid)
    reply, cards = remove_document_element(
        tmp_path,
        session,
        "删除其它要素",
        {"task_id": tid, "element_name": "其它"},
    )
    assert not cards
    assert "已从本文档删除" in reply
    task = TaskStore(tmp_path / "tasks" / tid).load()
    assert len(task.element_nodes or []) == 2
    assert not any(str(e.id) == "el-c" for e in task.element_nodes)


def test_dissociate_keeps_node(tmp_path: Path):
    tid = _seed_task(tmp_path)
    session = ChatSession(id="s-el3", phase=ChatPhase.CONFIRM_EXPORT.value, task_id=tid)
    reply, cards = dissociate_chapter_element(
        tmp_path,
        session,
        "取消概述章的建设规模关联",
        {"task_id": tid, "chapter_key": "c1", "element_id": "el-a"},
    )
    assert not cards
    assert "已取消" in reply
    task = TaskStore(tmp_path / "tasks" / tid).load()
    ch1 = next(c for c in task.outline.chapters if c.key == "c1")
    assert "el-a" not in list(ch1.element_ids or [])
    assert any(str(e.id) == "el-a" for e in task.element_nodes or [])


def test_match_task_elements_by_name(tmp_path: Path):
    tid = _seed_task(tmp_path)
    task = TaskStore(tmp_path / "tasks" / tid).load()
    hits = match_task_elements(task, name_hint="建设规模")
    assert len(hits) == 2
