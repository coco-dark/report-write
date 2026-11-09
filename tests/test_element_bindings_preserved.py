"""切会话 / 整树 PUT / 空 element_ids 重试不得冲掉已落盘要素。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.writing import (
    _apply_chapter_config,
    _merge_missing_elements_from_source,
)
from report_writing.controller import ensure_task, save_element_nodes, save_outline
from report_writing.domain.elements import save_elements
from report_writing.domain.models import ElementNode, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


def _el(eid: str, name: str = "") -> ElementNode:
    return ElementNode(id=eid, name=name or eid, kind="fixed", value="v")


def test_save_outline_keeps_element_ids_when_incoming_empty(tmp_path: Path):
    path = tmp_path / "tasks" / "wKeepEl"
    ensure_task(path)
    save_outline(
        path,
        [
            OutlineNode(key="c1", sequence="1", title="一", element_ids=["el-a", "el-b"]),
            OutlineNode(key="c2", sequence="2", title="二", element_ids=["el-c"]),
        ],
    )
    updated = save_outline(
        path,
        [
            OutlineNode(key="c1", sequence="1", title="一（改）", element_ids=[]),
            OutlineNode(key="c2", sequence="2", title="二", element_ids=[]),
        ],
    )
    by_key = {n.key: n for n in updated.outline.chapters}
    assert by_key["c1"].element_ids == ["el-a", "el-b"]
    assert by_key["c2"].element_ids == ["el-c"]
    assert by_key["c1"].title == "一（改）"


def test_save_outline_allows_empty_chapter_ids_when_on_points(tmp_path: Path):
    path = tmp_path / "tasks" / "wPtCh"
    ensure_task(path)
    save_outline(
        path,
        [
            OutlineNode(
                key="law",
                sequence="1.5",
                title="法规",
                element_ids=["el-a", "el-b"],
                point_items=[{"id": "p0", "text": "表", "element_id": "el-a"}],
            )
        ],
    )
    updated = save_outline(
        path,
        [
            OutlineNode(
                key="law",
                sequence="1.5",
                title="法规",
                element_ids=[],
                point_items=[{"id": "p0", "text": "表", "element_id": "el-a"}],
            )
        ],
    )
    node = updated.outline.chapters[0]
    assert node.element_ids == []


def test_save_outline_keeps_point_link_only_on_point_row(tmp_path: Path):
    path = tmp_path / "tasks" / "wPtOnly"
    ensure_task(path)
    save_outline(
        path,
        [
            OutlineNode(
                key="law",
                sequence="1.5.1",
                title="法律",
                point_items=[
                    {"id": "p0", "text": "法律名称清单", "element_id": "el-law", "element_name": "法律名称清单"}
                ],
                element_ids=[],
            )
        ],
    )
    updated = save_outline(
        path,
        [
            OutlineNode(
                key="law",
                sequence="1.5.1",
                title="法律",
                point_items=[{"id": "p0", "text": "法律名称清单"}],
                element_ids=[],
            )
        ],
    )
    node = updated.outline.chapters[0]
    assert node.element_ids == []
    assert node.point_items[0].get("element_id") == "el-law"


def test_save_outline_keeps_point_item_element_id(tmp_path: Path):
    path = tmp_path / "tasks" / "wKeepPt"
    ensure_task(path)
    seeded = TaskStore(path).load()
    seeded.element_nodes = [_el("el-scale", "规模")]
    TaskStore(path).save(seeded)
    save_outline(
        path,
        [
            OutlineNode(
                key="c1",
                sequence="1",
                title="一",
                point_items=[{"id": "p0", "text": "规模", "element_id": "el-scale"}],
                element_ids=["el-scale"],
            )
        ],
    )
    updated = save_outline(
        path,
        [
            OutlineNode(
                key="c1",
                sequence="1",
                title="一",
                point_items=[{"id": "p0", "text": "规模"}],
                element_ids=[],
            )
        ],
    )
    row = updated.outline.chapters[0].point_items[0]
    assert row.get("element_id") == "el-scale"
    # 要点关联保留后章级可分存为空
    assert updated.outline.chapters[0].element_ids == []


def test_save_outline_merges_when_incoming_adds_without_old_ids(tmp_path: Path):
    path = tmp_path / "tasks" / "wMergeEl"
    ensure_task(path)
    save_outline(
        path,
        [OutlineNode(key="c1", sequence="1", title="一", element_ids=["el-a", "el-b"])],
    )
    updated = save_outline(
        path,
        [OutlineNode(key="c1", sequence="1", title="一", element_ids=["el-c"])],
    )
    assert updated.outline.chapters[0].element_ids == ["el-a", "el-b", "el-c"]


def test_save_outline_allows_subset_unlink(tmp_path: Path):
    path = tmp_path / "tasks" / "wUnlinkEl"
    ensure_task(path)
    save_outline(
        path,
        [OutlineNode(key="c1", sequence="1", title="一", element_ids=["el-a", "el-b"])],
    )
    updated = save_outline(
        path,
        [OutlineNode(key="c1", sequence="1", title="一", element_ids=["el-b"])],
    )
    assert updated.outline.chapters[0].element_ids == ["el-b"]


def test_apply_chapter_config_empty_element_ids_does_not_wipe():
    task = WritingTask(
        id="wCfg",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c1", title="章", element_ids=["el-keep"])]
        ),
    )
    _apply_chapter_config(task, "c1", {"element_ids": [], "describes": "新说明"})
    assert task.outline.chapters[0].element_ids == ["el-keep"]
    assert task.outline.chapters[0].describes == "新说明"


def test_apply_chapter_config_new_id_merges_existing():
    task = WritingTask(
        id="wCfgAdd",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c1", title="章", element_ids=["el-keep"])]
        ),
    )
    _apply_chapter_config(task, "c1", {"element_ids": ["el-new"]})
    assert task.outline.chapters[0].element_ids == ["el-keep", "el-new"]


def test_apply_chapter_config_clear_flag_wipes():
    task = WritingTask(
        id="wClr",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c1", title="章", element_ids=["el-keep"])]
        ),
    )
    _apply_chapter_config(task, "c1", {"element_ids": [], "clear_element_ids": True})
    assert task.outline.chapters[0].element_ids == []


def test_save_elements_empty_payload_does_not_wipe():
    task = WritingTask(
        id="wEls",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", title="一", element_ids=["el-a"])]),
        element_nodes=[_el("el-a"), _el("el-b")],
    )
    save_elements(task, [])
    assert {e.id for e in task.element_nodes} == {"el-a", "el-b"}


def test_save_elements_keeps_referenced_when_payload_incomplete():
    task = WritingTask(
        id="wInc",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", title="一", element_ids=["el-a"])]),
        element_nodes=[_el("el-a"), _el("el-orphan")],
    )
    save_elements(task, [_el("el-new", "新")])
    ids = {e.id for e in task.element_nodes}
    assert "el-a" in ids
    assert "el-new" in ids
    assert "el-orphan" in ids


def test_save_element_nodes_empty_keeps_disk(tmp_path: Path):
    path = tmp_path / "tasks" / "wDiskEl"
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = "wDiskEl"
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="一", element_ids=["el-a"])])
    task.element_nodes = [_el("el-a")]
    TaskStore(path).save(task)
    save_element_nodes(path, [])
    again = TaskStore(path).load()
    assert [e.id for e in again.element_nodes] == ["el-a"]


def test_merge_missing_elements_from_blueprint_when_copy_empty():
    source = WritingTask(
        id="bp1",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c1", title="一", element_ids=["el-bp"])]
        ),
        element_nodes=[_el("el-bp", "蓝本要素")],
    )
    copy = WritingTask(
        id="w1",
        source_blueprint_id="bp1",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", title="一", element_ids=[])]),
        element_nodes=[],
    )
    assert _merge_missing_elements_from_source(copy, source) is True
    assert [e.id for e in copy.element_nodes] == ["el-bp"]
    assert copy.outline.chapters[0].element_ids == ["el-bp"]
