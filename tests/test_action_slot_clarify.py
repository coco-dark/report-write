"""Action 缺参 → intent_clarify（source=action_slots）。"""

from pathlib import Path

from report_writing.chat.action_slots import (
    ACTION_SLOT_SOURCE,
    build_action_slot_clarify,
    missing_action_slots,
)
from report_writing.chat.clarify_flow import get_pending
from report_writing.chat.tool_dispatch import execute_dispatch_tool
from report_writing.chat.writing_control_route import apply_writing_control_classification
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _task_with_chapters(home: Path, task_id: str = "t1"):
    path = home / "tasks" / task_id
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c11", sequence="1.1", title="总则", structure_only=True),
            OutlineNode(key="c22", sequence="2.2", title="建设规模"),
            OutlineNode(key="c23", sequence="2.3", title="站址"),
        ]
    )
    TaskStore(path).save(task)
    return task_id, task


def test_missing_action_slots_chapter():
    miss = missing_action_slots("add_chapter_element", {"task_id": "t1"})
    assert miss == ["chapter_key"]
    assert missing_action_slots("add_chapter_element", {"chapter_key": "c22"}) == []


def test_add_chapter_element_missing_chapter_clarify(tmp_path: Path):
    tid, task = _task_with_chapters(tmp_path)
    session = ChatSession(id="s1", phase="writing", task_id=tid)
    hit = apply_writing_control_classification(
        tmp_path,
        session,
        "加一个知识库要素",
        {"tool": "add_chapter_element", "payload": {"kind": "rag_search"}},
    )
    assert hit is not None
    reply, cards, _ = hit
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending is not None
    assert pending["source"] == ACTION_SLOT_SOURCE
    assert pending["candidate"]["name"] == "add_chapter_element"
    labels = [o["label"] for o in pending["options"] if isinstance(o, dict)]
    assert any("建设规模" in x for x in labels)
    assert "总则" not in "".join(labels)  # structure_only 不进选项
    assert "哪一章" in reply or "章节" in reply


def test_associate_elements_missing_chapter_clarify(tmp_path: Path):
    tid, _ = _task_with_chapters(tmp_path)
    session = ChatSession(id="s1", phase="writing", task_id=tid)
    hit = apply_writing_control_classification(
        tmp_path,
        session,
        "关联要素",
        {"tool": "associate_elements", "payload": {}},
    )
    assert hit is not None
    _reply, cards, _ = hit
    assert cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending["candidate"]["name"] == "associate_elements"


def test_configure_chapter_missing_clarify(tmp_path: Path):
    tid, _ = _task_with_chapters(tmp_path)
    session = ChatSession(id="s1", phase="writing", task_id=tid)
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "configure_chapter", "args": {}},
        "帮我改配置",
    )
    assert hit is not None
    _reply, cards, _ = hit
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending["source"] == ACTION_SLOT_SOURCE
    assert pending["candidate"]["name"] == "configure_chapter"


def test_associate_knowledge_offline_stub(tmp_path: Path):
    tid, task = _task_with_chapters(tmp_path)
    TaskStore(tmp_path / "tasks" / tid).save(task)
    session = ChatSession(id="s1", phase="writing", task_id=tid)
    hit = apply_writing_control_classification(
        tmp_path,
        session,
        "关联知识库",
        {
            "tool": "associate_element_knowledge",
            "payload": {"chapter_key": "c22"},
        },
    )
    assert hit is not None
    reply, cards, _ = hit
    assert cards == []
    assert "下线" in reply


def test_build_action_slot_clarify_preserves_payload(tmp_path: Path):
    tid, task = _task_with_chapters(tmp_path)
    session = ChatSession(id="s1", phase="writing", task_id=tid)
    reply, cards = build_action_slot_clarify(
        session,
        "add_chapter_element",
        {"task_id": tid, "kind": "rag_search", "query": "站址"},
        ["chapter_key"],
        task=task,
        message="加要素",
    )
    assert cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending["candidate"]["payload"]["kind"] == "rag_search"
    assert pending["candidate"]["payload"]["query"] == "站址"
    assert reply


def test_select_blueprint_missing_id_clarify(tmp_path: Path):
    session = ChatSession(id="s1", phase="intent")
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "select_blueprint", "args": {}},
        "就用这份",
    )
    assert hit is not None
    _reply, cards, _ = hit
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending["candidate"]["name"] == "select_blueprint"
    assert any(
        str(o.get("id") or "") == "match_again" for o in pending.get("options") or []
    )
