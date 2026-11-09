"""Agent configure_chapter 不得用臆造要点/要素覆盖库内关联。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.actions import _action_configure_chapter
from report_writing.chat.chapter_configure_sanitize import prepare_configure_chapter_body
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_prepare_configure_strips_inline_content_without_persist_mark():
    body = prepare_configure_chapter_body(
        {
            "chapter_key": "c1",
            "point_items": [{"id": "p0", "text": "猜测要点"}],
            "element_ids": ["el-x"],
        }
    )
    assert "point_items" not in body
    assert "element_ids" not in body
    assert body.get("chapter_key") == "c1"


def test_prepare_configure_keeps_inline_when_explicitly_allowed():
    body = prepare_configure_chapter_body(
        {
            "allow_inline_chapter_config": True,
            "point_items": [{"id": "p0", "text": "信任内联"}],
        }
    )
    assert body.get("point_items") == [{"id": "p0", "text": "信任内联"}]


def test_prepare_configure_agent_allows_points_text():
    body = prepare_configure_chapter_body(
        {
            "_via_agent_tool": True,
            "chapter_key": "c1",
            "points_text": "第一条\n第二条",
            "point_items": [{"id": "p0", "text": "幻觉"}],
            "element_ids": ["el-x"],
        }
    )
    assert body.get("points_text") == "第一条\n第二条"
    assert "point_items" not in body
    assert "element_ids" not in body


def test_prepare_configure_keeps_structure_only_for_agent():
    body = prepare_configure_chapter_body(
        {
            "_via_agent_tool": True,
            "structure_only": True,
            "point_items": [{"id": "p0", "text": "x"}],
        }
    )
    assert body.get("structure_only") is True
    assert "point_items" not in body


def test_prepare_configure_strips_agent_content_fields():
    body = prepare_configure_chapter_body(
        {
            "_via_agent_tool": True,
            "chapter_key": "c1",
            "describes": "新说明",
            "point_items": [{"id": "p0", "text": "幻觉要点", "needs_supplement": True}],
            "element_ids": ["el-x"],
        }
    )
    assert body.get("describes") == "新说明"
    assert "point_items" not in body
    assert "element_ids" not in body
    assert "_via_agent_tool" not in body


def test_agent_configure_updates_points_text_preserves_element_link(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wAgentPts"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.outline_confirmed = True
    task.element_nodes = [ElementNode(id="el_a", name="资料A", kind="fixed", value="x")]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1",
                title="章",
                element_ids=["el_a"],
                points=["说明"],
                point_items=[
                    {
                        "id": "p0",
                        "text": "说明",
                        "element_id": "el_a",
                        "element_name": "资料A",
                    }
                ],
            )
        ]
    )
    TaskStore(path).save(task)
    session = ChatSession(id="s1", task_id=wid, phase="ready_to_write")
    payload = {
        "task_id": wid,
        "chapter_key": "c1",
        "_via_agent_tool": True,
        "points_text": "说明\n新增第二条",
    }
    _action_configure_chapter(home, session, payload)
    reloaded = TaskStore(path).load()
    node = reloaded.outline.chapters[0]
    assert len(node.points) == 2
    assert node.point_items[0].get("element_id") == "el_a"
    assert "新增第二条" in node.points[1]


def test_agent_configure_keeps_disk_point_links(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("report_writing.infra.llm.live_model", lambda **kw: None)
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wAgentCfg"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.outline_confirmed = True
    task.element_nodes = [ElementNode(id="el_a", name="资料A", kind="fixed", value="x")]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1",
                title="章",
                element_ids=["el_a"],
                points=["说明"],
                point_items=[
                    {
                        "id": "p0",
                        "text": "说明",
                        "element_id": "el_a",
                        "element_name": "资料A",
                    }
                ],
            )
        ]
    )
    TaskStore(path).save(task)
    session = ChatSession(id="s1", task_id=wid, phase="ready_to_write")
    stale = {
        "task_id": wid,
        "chapter_key": "c1",
        "_via_agent_tool": True,
        "describes": "口语改的说明",
        "point_items": [
            {
                "id": "p0",
                "text": "说明（待补充：资料A）",
                "needs_supplement": True,
                "element_name": "资料A",
            }
        ],
        "element_ids": [],
    }
    _action_configure_chapter(home, session, stale)
    reloaded = TaskStore(path).load()
    node = reloaded.outline.chapters[0]
    assert node.describes == "口语改的说明"
    assert node.point_items[0].get("element_id") == "el_a"
    assert node.element_ids == ["el_a"]
