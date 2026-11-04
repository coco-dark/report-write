"""写作 clone 子集大纲缺 key 时，confirm 从蓝本补节点再落正文。"""

from __future__ import annotations

from pathlib import Path

from report_writing.controller import ensure_task
from report_writing.domain.models import HumanDecision, OutlineNode, OutlineTree
from report_writing.services.pipeline import confirm_chapter, ensure_chapter_state
from report_writing.services.store import TaskStore


def test_ensure_chapter_state_appends_missing_key_from_blueprint_home(tmp_path: Path):
    home = tmp_path
    bp_id = "bp_full_outline"
    w_id = "w_subset_clone"
    bp_path = home / "tasks" / bp_id
    w_path = home / "tasks" / w_id
    ensure_task(bp_path)
    ensure_task(w_path)

    bp = TaskStore(bp_path).load()
    bp.title = "全量蓝本"
    bp.outline = OutlineTree(
        chapters=[
            OutlineNode(key="intro", sequence="1", title="概述"),
            OutlineNode(key="construction_scale", sequence="2.2", title="建设规模"),
            OutlineNode(key="overview", sequence="2.3", title="工程概况"),
        ]
    )
    TaskStore(bp_path).save(bp)

    writing = TaskStore(w_path).load()
    writing.title = "全量蓝本（对话写作）"
    writing.source_blueprint_id = bp_id
    writing.outline = OutlineTree(
        chapters=[OutlineNode(key="intro", sequence="1", title="概述")]
    )
    TaskStore(w_path).save(writing)

    writing = TaskStore(w_path).load()
    state = ensure_chapter_state(writing, "construction_scale", home=home)
    assert state.key == "construction_scale"
    assert state.title == "建设规模"
    assert any(n.key == "construction_scale" for n in writing.outline.chapters)


def test_confirm_chapter_saves_body_for_key_missing_from_clone_outline(tmp_path: Path):
    home = tmp_path
    bp_id = "bp_save_target"
    w_id = "w_save_target"
    ensure_task(home / "tasks" / bp_id)
    ensure_task(home / "tasks" / w_id)

    bp = TaskStore(home / "tasks" / bp_id).load()
    bp.outline = OutlineTree(
        chapters=[
            OutlineNode(key="a", sequence="1", title="A"),
            OutlineNode(key="construction_scale", sequence="2.2", title="建设规模"),
        ]
    )
    TaskStore(home / "tasks" / bp_id).save(bp)

    writing = TaskStore(home / "tasks" / w_id).load()
    writing.source_blueprint_id = bp_id
    writing.outline = OutlineTree(chapters=[OutlineNode(key="a", sequence="1", title="A")])
    TaskStore(home / "tasks" / w_id).save(writing)

    updated = confirm_chapter(
        home / "tasks" / w_id,
        Path("unused.json"),
        Path("unused"),
        "construction_scale",
        HumanDecision.APPROVE,
        text="<p>建设规模正文</p>",
        comment="用户保存正文",
    )
    chapter = next(c for c in updated.chapters if c.key == "construction_scale")
    assert "建设规模正文" in (chapter.official_text or "")
    assert any(n.key == "construction_scale" for n in updated.outline.chapters)
