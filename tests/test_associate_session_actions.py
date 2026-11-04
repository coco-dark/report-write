import json
from pathlib import Path

from report_writing.chat.cards import _associate_element_knowledge_card, _associate_elements_card
from report_writing.chat.writing_control_route import (
    _action_associate_elements,
    apply_writing_control_classification,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_associate_elements_via_llm(tmp_path: Path):
    home = tmp_path
    task_id = "t1"
    path = home / "tasks" / task_id
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c22", sequence="2.2", title="建设规模")]
    )
    TaskStore(path).save(task)
    session = ChatSession(id="s1", phase="writing", task_id=task_id)

    hit = apply_writing_control_classification(
        home,
        session,
        "给2.2章关联要素",
        {
            "tool": "associate_elements",
            "confidence": 0.92,
            "payload": {"chapter_key_hint": "2.2"},
        },
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "建设规模" in reply or "2.2" in reply
    assert cards and cards[0].type == "associate_elements"
    assert cards[0].payload["chapter_key"] == "c22"


def test_associate_elements_action_card(tmp_path: Path):
    home = tmp_path
    task_id = "t1"
    path = home / "tasks" / task_id
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c22", sequence="2.2", title="建设规模")]
    )
    TaskStore(path).save(task)
    session = ChatSession(id="s1", phase="writing", task_id=task_id)
    reply, cards = _action_associate_elements(
        home, session, "给2.2章关联要素", {"chapter_key_hint": "2.2"}
    )
    assert "建设规模" in reply or "2.2" in reply
    assert cards and cards[0].type == "associate_elements"
    assert cards[0].payload["chapter_key"] == "c22"
    assert cards[0].payload["action"] == "associate_elements"
    assert cards[0].payload.get("auto_open") is True
    assert "打开关联面板" in reply
    assert "点下面" not in reply


def test_associate_knowledge_via_classification(tmp_path: Path):
    home = tmp_path
    task_id = "t1"
    path = home / "tasks" / task_id
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c22", sequence="2.2", title="建设规模")]
    )
    TaskStore(path).save(task)
    session = ChatSession(id="s1", phase="writing", task_id=task_id)
    hit = apply_writing_control_classification(
        home,
        session,
        "给要素关联知识库",
        {"tool": "associate_element_knowledge", "payload": {"chapter_key_hint": "2.2"}},
    )
    assert hit is not None
    reply, cards, _ = hit
    assert cards == []
    assert "下线" in reply
    assert "文件" in reply or "资料库" in reply


def test_associate_knowledge_card_shape():
    card = _associate_element_knowledge_card(
        task_id="t1",
        chapter_key="c22",
        chapter_label="2.2 建设规模",
        element_id="el1",
        element_name="规模要素",
    )
    assert card.type == "associate_element_knowledge"
    assert card.payload["action"] == "associate_element_knowledge"
    assert card.payload["element_id"] == "el1"
    assert card.payload["element_name"] == "规模要素"
    card2 = _associate_elements_card(task_id="t1", chapter_key="c22", chapter_label="2.2")
    assert card2.type == "associate_elements"
    assert card2.payload.get("auto_open") is True
