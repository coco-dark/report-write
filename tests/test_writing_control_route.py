import json
from pathlib import Path

from report_writing.chat.intent_llm import classify_writing_control_llm
from report_writing.chat.writing_control_route import apply_writing_control_classification
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


def test_classify_add_element_via_llm():
    def invoke(system: str, user: str) -> str:
        return json.dumps(
            {
                "tool": "add_chapter_element",
                "confidence": 0.9,
                "payload": {
                    "kind": "kv",
                    "name": "个人信息",
                    "category": "个人信息",
                    "kv_pairs": [
                        {"key": "姓名", "value": "nico"},
                        {"key": "职位", "value": "java开发"},
                    ],
                },
            }
        )

    out = classify_writing_control_llm(
        "增加要素 个人信息 姓名nico 职位java开发",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id="w-test",
        invoke=invoke,
    )
    assert out.get("tool") == "add_chapter_element"
    assert out["payload"]["name"] == "个人信息"


def test_apply_add_element_route(tmp_path: Path):
    task_id = "w-el"
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True)
    task = WritingTask(
        id=task_id,
        outline=OutlineTree(chapters=[OutlineNode(key="c1", sequence="1", title="概述")]),
    )
    TaskStore(task_dir).save(task)

    session = ChatSession(id="s1", phase=ChatPhase.FREEFORM_POINTS.value, task_id=task_id)
    data = {
        "tool": "add_chapter_element",
        "confidence": 0.9,
        "payload": {
            "kind": "kv",
            "name": "个人信息",
            "chapter_key": "c1",
            "kv_pairs": [{"key": "姓名", "value": "nico"}],
        },
    }
    hit = apply_writing_control_classification(
        tmp_path,
        session,
        "增加要素 个人信息 姓名nico",
        data,
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "要素" in reply
    assert any(getattr(c, "type", None) == "element_configure_pending" for c in cards)
