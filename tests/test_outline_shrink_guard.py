"""已确认大纲不得被分析/写作短列表裁成子集；写作副本可从源蓝本恢复。"""

from __future__ import annotations

from pathlib import Path

from report_writing.controller import ensure_task, save_outline
from report_writing.controller.outline_flow import maybe_restore_outline_from_source_blueprint
from report_writing.domain.models import OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _chapters(*titles: str) -> list[OutlineNode]:
    nodes: list[OutlineNode] = []
    for i, title in enumerate(titles, start=1):
        nodes.append(
            OutlineNode(
                key=f"c{i}",
                sequence=str(i),
                title=title,
                level=1,
                points=["要点"] if i == 4 else [],
            )
        )
    return nodes


def test_save_outline_merges_short_subset_instead_of_replacing(tmp_path: Path):
    path = tmp_path / "tasks" / "wFull"
    ensure_task(path)
    full = _chapters("一", "二", "三", "四", "五", "六", "七", "八")
    save_outline(path, full)
    task = TaskStore(path).load()
    task.outline_confirmed = True
    TaskStore(path).save(task)

    short = [
        OutlineNode(key="c4", sequence="4", title="四（已分析）", level=1, points=["新要点"]),
        OutlineNode(key="c4a", sequence="4.1", title="不该单独留下", level=2),
    ]
    # c4a 不在原树：合并时追加；c1-c8 不得丢
    updated = save_outline(path, [short[0]])
    keys = [n.key for n in updated.outline.chapters]
    assert keys == [f"c{i}" for i in range(1, 9)]
    hit = next(n for n in updated.outline.chapters if n.key == "c4")
    assert hit.title == "四（已分析）"
    assert list(hit.points or []) == ["新要点"]


def test_save_outline_replace_tree_allows_explicit_delete(tmp_path: Path):
    path = tmp_path / "tasks" / "wDel"
    ensure_task(path)
    save_outline(path, _chapters("一", "二", "三", "四", "五", "六", "七", "八"))
    task = TaskStore(path).load()
    task.outline_confirmed = True
    TaskStore(path).save(task)

    kept = _chapters("一", "二")
    updated = save_outline(path, kept, replace_tree=True)
    assert [n.key for n in updated.outline.chapters] == ["c1", "c2"]


def test_restore_truncated_writing_copy_from_source_blueprint(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bp = "bpSrc"
    wid = "wCopy"
    bp_path = home / "tasks" / bp
    w_path = home / "tasks" / wid
    ensure_task(bp_path)
    ensure_task(w_path)

    full = _chapters("一", "二", "三", "四", "五", "六", "七", "八")
    bp_task = TaskStore(bp_path).load()
    bp_task.id = bp
    bp_task.outline = OutlineTree(chapters=full)
    bp_task.outline_confirmed = True
    TaskStore(bp_path).save(bp_task)

    copy = TaskStore(w_path).load()
    copy.id = wid
    copy.source_blueprint_id = bp
    copy.outline_confirmed = True
    copy.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c4", sequence="4", title="四", level=1, points=["副本要点"]),
            OutlineNode(key="c5", sequence="5", title="五", level=1),
        ]
    )
    TaskStore(w_path).save(copy)
    restored = maybe_restore_outline_from_source_blueprint(w_path, TaskStore(w_path).load())
    keys = [n.key for n in restored.outline.chapters]
    assert keys == [f"c{i}" for i in range(1, 9)]
    hit = next(n for n in restored.outline.chapters if n.key == "c4")
    assert list(hit.points or []) == ["副本要点"]


def test_restore_skips_when_copy_has_unique_keys(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bp_path = home / "tasks" / "bpA"
    w_path = home / "tasks" / "wA"
    ensure_task(bp_path)
    ensure_task(w_path)
    bp_task = TaskStore(bp_path).load()
    bp_task.id = "bpA"
    bp_task.outline = OutlineTree(chapters=_chapters("一", "二", "三", "四", "五", "六", "七", "八"))
    bp_task.outline_confirmed = True
    TaskStore(bp_path).save(bp_task)

    copy = TaskStore(w_path).load()
    copy.id = "wA"
    copy.source_blueprint_id = "bpA"
    copy.outline_confirmed = True
    copy.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c4", sequence="4", title="四", level=1),
            OutlineNode(key="ch-new-x", sequence="4.1", title="新增", level=2, parent="c4"),
        ]
    )
    TaskStore(w_path).save(copy)
    out = maybe_restore_outline_from_source_blueprint(w_path, TaskStore(w_path).load())
    assert [n.key for n in out.outline.chapters] == ["c4", "ch-new-x"]
