from pathlib import Path

from report_writing.chat.writing_control_route import apply_writing_control_classification
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


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


def test_apply_expected_tool_mismatch_does_not_execute(tmp_path: Path):
    task_id = "w-el-hop"
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True)
    task = WritingTask(
        id=task_id,
        outline=OutlineTree(chapters=[OutlineNode(key="c1", sequence="1", title="概述")]),
    )
    TaskStore(task_dir).save(task)
    session = ChatSession(id="s-hop", phase=ChatPhase.FREEFORM_POINTS.value, task_id=task_id)
    hit = apply_writing_control_classification(
        tmp_path,
        session,
        "将知识库要素添加到要素库",
        {
            "tool": "collect_to_element_library",
            "payload": {"element_name": "知识库要素"},
        },
        expected_tool="add_chapter_element",
    )
    assert hit is not None
    reply, cards, events = hit
    assert not cards
    assert "collect_to_element_library" in reply
    assert any(
        isinstance(e, dict)
        and e.get("type") == "suggested_next_tool"
        and e.get("tool") == "collect_to_element_library"
        for e in events
    )
