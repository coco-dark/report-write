"""章节要点规范化与开写前检查一致性。

手工验收（例：目录 1.2 第一行写作要点）：
1. 关联配置要素 → 保存（或静默落盘）
2. 点「保存并开始写作」
3. 不应出现「开写前待补充」卡；目录标签为「已配要点」而非「待关联」
4. GET doc-outline / task 中该行 `point_items[].element_id` 与文档窗关联列一致
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from report_writing.chat.actions import _action_retry_chapter
from report_writing.chat.writing_supplement_check import collect_deterministic_gaps, supplement_gate_needed
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree, WritingTask
from report_writing.domain.point_items_sync import (
    normalize_chapter_point_items_on_node,
    sanitize_point_item_row,
)
from report_writing.services.store import TaskStore


def test_sanitize_strips_ghost_element_name(tmp_path: Path):
    row = {"text": "x", "element_name": "幽灵名", "needs_supplement": True}
    out = sanitize_point_item_row(row, {})
    assert "element_name" not in out
    assert "element_id" not in out


def test_normalize_links_and_clears_needs_supplement(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wNorm"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [
        ElementNode(id="el_a", name="资料A", kind="fixed", value=""),
    ]
    node = OutlineNode(
        key="c1",
        sequence="1",
        title="章",
        point_items=[
            {
                "id": "p0",
                "text": "说明（待补充：资料A）",
                "slot": "资料A",
                "needs_supplement": True,
                "element_name": "资料A",
            },
        ],
        element_ids=["el_a"],
    )
    task.outline = OutlineTree(chapters=[node])
    normalize_chapter_point_items_on_node(task, node)
    assert node.element_ids == ["el_a"]
    assert not str(node.point_items[0].get("element_id") or "").strip()
    gaps = collect_deterministic_gaps(task, ["c1"], home)
    assert gaps == []


def test_supplement_gate_false_when_all_linked(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wGate"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [ElementNode(id="el_a", name="资料A", kind="fixed", value="x")]
    node = OutlineNode(
        key="c1",
        sequence="1",
        title="章",
        point_items=[{"id": "p0", "text": "说明", "element_id": "el_a"}],
        element_ids=["el_a"],
    )
    task.outline = OutlineTree(chapters=[node])
    normalize_chapter_point_items_on_node(task, node)
    session = ChatSession(id="s1", task_id=wid)
    from report_writing.chat.writing_supplement_check import run_supplement_check

    run_supplement_check(home, session, task, ["c1"], invoke=None)
    assert not supplement_gate_needed(session, task, ["c1"], home=home)


def test_retry_already_persisted_ignores_stale_configure_payload(tmp_path: Path):
    """文档窗已 PUT 落盘后，retry 仅带 already_persisted，不得用会话旧 point_items 覆盖关联。"""
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wPersist"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.outline_confirmed = True
    task.element_nodes = [ElementNode(id="el_a", name="资料A", kind="fixed", value="已填")]
    node = OutlineNode(
        key="c1",
        sequence="1.2",
        title="章",
        point_items=[
            {
                "id": "p0",
                "text": "说明",
                "element_id": "el_a",
                "element_name": "资料A",
            },
        ],
        element_ids=["el_a"],
        points=["说明"],
    )
    task.outline = OutlineTree(chapters=[node])
    normalize_chapter_point_items_on_node(task, node)
    TaskStore(path).save(task)
    session = ChatSession(id="s_persist", task_id=wid, phase="ready_to_write")
    stale_payload = {
        "task_id": wid,
        "chapter_key": "c1",
        "_write_mode": "start",
        "already_persisted": True,
        "from_doc_window": True,
        "point_items": [
            {
                "id": "p0",
                "text": "说明（待补充：资料A）",
                "needs_supplement": True,
                "element_name": "资料A",
            },
        ],
    }
    with patch("report_writing.services.pipeline.run_chapters") as run_mock:
        _reply, cards, _events = _action_retry_chapter(
            home, session, stale_payload
        )
    run_mock.assert_called_once()
    assert not any(getattr(c, "type", "") == "writing_supplement_check" for c in cards)
    reloaded = TaskStore(path).load()
    assert reloaded.outline.chapters[0].point_items[0].get("element_id") == "el_a"


def test_normalize_repairs_id_like_element_name(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wIdName"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    eid = "el-1793773802966-eevk1uu"
    task.element_nodes = [ElementNode(id=eid, name=eid, kind="file", files=[])]
    node = OutlineNode(
        key="c12",
        sequence="1.2",
        title="年度工作目标",
        point_items=[
            {
                "id": "p0",
                "text": "说明来源（待补充：年度目标责任书或绩效考核表）",
                "slot": "年度目标责任书或绩效考核表",
                "element_id": eid,
                "element_name": eid,
            }
        ],
        element_ids=[eid],
    )
    task.outline = OutlineTree(chapters=[node])
    normalize_chapter_point_items_on_node(task, node)
    assert task.element_nodes[0].name == "年度目标责任书或绩效考核表"
    assert node.point_items[0].get("element_name") == "年度目标责任书或绩效考核表"
    assert collect_deterministic_gaps(task, ["c12"], home) == []


def test_run_supplement_check_no_false_count_three_linked_points(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wThree"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    els = [
        ElementNode(id="el1", name="A", kind="fixed", value="1"),
        ElementNode(id="el2", name="B", kind="fixed", value="2"),
        ElementNode(id="el3", name="C", kind="fixed", value="3"),
    ]
    task.element_nodes = els
    node = OutlineNode(
        key="1.1",
        sequence="1.1",
        title="节",
        element_ids=["el1", "el2"],
        point_items=[
            {"id": "p0", "text": "a", "element_id": "el1"},
            {
                "id": "p1",
                "text": "b（待补充：B）",
                "element_id": "el2",
                "needs_supplement": False,
            },
            {"id": "p2", "text": "c", "element_id": "el3"},
        ],
    )
    task.outline = OutlineTree(chapters=[node])
    normalize_chapter_point_items_on_node(task, node)
    session = ChatSession(id="s3", task_id=wid)
    from report_writing.chat.writing_supplement_check import run_supplement_check, supplement_check_card

    reply, items, _reg = run_supplement_check(home, session, task, ["1.1"], invoke=None)
    assert "处还空着" not in reply
    assert items == []
    assert supplement_check_card(task, ["1.1"], items=items, home=home) is None


def test_element_ids_union_includes_point_item_links(tmp_path: Path):
    from report_writing.domain.elements import resolve_elements_for_chapter
    from report_writing.domain.models import ChapterRequirement

    home = tmp_path
    els = [
        ElementNode(id="el1", name="A", kind="fixed", value="v1"),
        ElementNode(id="el2", name="B", kind="fixed", value="v2"),
        ElementNode(id="el3", name="C", kind="fixed", value="v3"),
    ]
    node = OutlineNode(
        key="1.1",
        sequence="1.1",
        title="节",
        element_ids=["el1", "el2"],
        point_items=[
            {"id": "p0", "text": "a", "element_id": "el1"},
            {"id": "p1", "text": "b", "element_id": "el2"},
            {"id": "p2", "text": "c", "element_id": "el3"},
        ],
    )
    outline = OutlineTree(chapters=[node])
    req = ChapterRequirement(key="1.1", title="节", element_ids=["el1", "el2"])
    resolve_elements_for_chapter(req, els, None, outline=outline, home=home, task_id="t1")
    assert len(req.element_results) == 3
    assert {r["id"] for r in req.element_results} == {"el1", "el2", "el3"}


def test_run_supplement_check_no_false_count_three_linked_points(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wThree"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    els = [
        ElementNode(id="el1", name="A", kind="fixed", value="1"),
        ElementNode(id="el2", name="B", kind="fixed", value="2"),
        ElementNode(id="el3", name="C", kind="fixed", value="3"),
    ]
    task.element_nodes = els
    node = OutlineNode(
        key="1.1",
        sequence="1.1",
        title="节",
        element_ids=["el1", "el2"],
        point_items=[
            {"id": "p0", "text": "a", "element_id": "el1"},
            {
                "id": "p1",
                "text": "b（待补充：B）",
                "element_id": "el2",
                "needs_supplement": False,
            },
            {"id": "p2", "text": "c", "element_id": "el3"},
        ],
    )
    task.outline = OutlineTree(chapters=[node])
    normalize_chapter_point_items_on_node(task, node)
    session = ChatSession(id="s3", task_id=wid)
    from report_writing.chat.writing_supplement_check import run_supplement_check, supplement_check_card

    reply, items, _reg = run_supplement_check(home, session, task, ["1.1"], invoke=None)
    assert "处还空着" not in reply
    assert items == []
    assert supplement_check_card(task, ["1.1"], items=items, home=home) is None


def test_element_ids_union_includes_point_item_links(tmp_path: Path):
    from report_writing.domain.elements import resolve_elements_for_chapter
    from report_writing.domain.models import ChapterRequirement

    home = tmp_path
    els = [
        ElementNode(id="el1", name="A", kind="fixed", value="v1"),
        ElementNode(id="el2", name="B", kind="fixed", value="v2"),
        ElementNode(id="el3", name="C", kind="fixed", value="v3"),
    ]
    node = OutlineNode(
        key="1.1",
        sequence="1.1",
        title="节",
        element_ids=["el1", "el2"],
        point_items=[
            {"id": "p0", "text": "a", "element_id": "el1"},
            {"id": "p1", "text": "b", "element_id": "el2"},
            {"id": "p2", "text": "c", "element_id": "el3"},
        ],
    )
    outline = OutlineTree(chapters=[node])
    req = ChapterRequirement(key="1.1", title="节", element_ids=["el1", "el2"])
    resolve_elements_for_chapter(req, els, None, outline=outline, home=home, task_id="t1")
    assert len(req.element_results) == 3
    assert {r["id"] for r in req.element_results} == {"el1", "el2", "el3"}
