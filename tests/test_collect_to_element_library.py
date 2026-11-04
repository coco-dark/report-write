"""将本任务要素收入个人要素库。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.collect_element_library import (
    collect_task_elements_to_library,
    extract_collect_element_name,
)
from report_writing.chat.writing_control_route import apply_writing_control_classification
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree
from report_writing.services.element_library import list_library_items
from report_writing.services.store import TaskStore


def test_extract_collect_element_name():
    assert extract_collect_element_name("将知识库要素添加到要素库") == "知识库要素"
    assert extract_collect_element_name("把项目基本信息收入要素库") == "项目基本信息"


def test_collect_imports_and_links(tmp_path: Path):
    tid = "bp1"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c22", sequence="2.2", title="建设规模", element_ids=["el-1"]),
        ]
    )
    task.element_nodes = [
        ElementNode(id="el-1", name="知识库要素", kind="rag_search", query="建设规模"),
    ]
    TaskStore(path).save(task)
    session = ChatSession(id="s1", phase="writing", task_id=tid, blueprint_id=tid)

    reply, cards = collect_task_elements_to_library(
        tmp_path,
        session,
        "将知识库要素添加到要素库",
        {"element_name": "知识库要素"},
    )
    assert "收入" in reply
    assert not cards
    items = list_library_items(tmp_path)
    assert any(i.name == "知识库要素" for i in items)
    reloaded = TaskStore(path).load().element_nodes[0]
    assert reloaded.library_item_id.startswith("lib-el-")


def test_collect_tool_not_rewritten_from_add_element(tmp_path: Path):
    """收录须分类器选 collect_to_element_library；不再用口语正则把 add 改道。"""
    tid = "bp2"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(id="el-1", name="知识库要素", kind="rag_search", query="q"),
    ]
    TaskStore(path).save(task)
    session = ChatSession(id="s1", phase="writing", task_id=tid)

    # 误选 add_chapter_element：不再静默改成 collect
    hit_add = apply_writing_control_classification(
        tmp_path,
        session,
        "将知识库要素添加到要素库",
        {"tool": "add_chapter_element", "payload": {"kind": "rag_search"}},
    )
    assert hit_add is not None
    assert not any(i.name == "知识库要素" for i in list_library_items(tmp_path))

    hit = apply_writing_control_classification(
        tmp_path,
        session,
        "将知识库要素添加到要素库",
        {"tool": "collect_to_element_library", "payload": {"element_name": "知识库要素"}},
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "收入" in reply or "要素库" in reply
    assert "草稿" not in reply
    assert any(i.name == "知识库要素" for i in list_library_items(tmp_path))
