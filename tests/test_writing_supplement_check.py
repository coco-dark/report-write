"""写作节奏澄清与开写前待补充检查。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from report_writing.chat.actions import (
    _action_dismiss_writing_supplement,
    _action_retry_chapter,
    _action_start_writing,
)
from report_writing.chat.clarify_flow import get_pending
from report_writing.chat.writing import maybe_offer_writing_pace_clarify
from report_writing.chat.writing_supplement_check import (
    collect_deterministic_gaps,
    dismiss_gap,
    element_needs_supplement,
    normalize_gap_display,
    gap_resolved,
    open_gaps,
    run_supplement_check,
    supplement_gate_needed,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


def _task_with_empty_element(home: Path) -> WritingTask:
    wid = "wSupTest"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [
        ElementNode(id="el_proj", name="项目名称", kind="fixed", value=""),
    ]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1",
                title="概述",
                points=["填写项目名称"],
                element_ids=["el_proj"],
            ),
        ]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)
    return TaskStore(path).load()


def test_pace_clarify_when_two_chapters():
    session = ChatSession(id="s_pace", blueprint_id="bp1")
    hit = maybe_offer_writing_pace_clarify(
        session, ["c1", "c2"], task_id_hint="bp1"
    )
    assert hit is not None
    _reply, cards = hit
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending and pending.get("phase_hint") == "writing_pace"


def test_pace_clarify_skipped_for_single_chapter():
    session = ChatSession(id="s_one", blueprint_id="bp1")
    assert maybe_offer_writing_pace_clarify(session, ["c1"], task_id_hint="bp1") is None


def test_deterministic_gap_for_empty_element(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    gaps = collect_deterministic_gaps(task, ["c1"], home)
    assert any(g.get("element_id") == "el_proj" for g in gaps)
    assert element_needs_supplement(task.element_nodes[0], task, home)


def test_normalize_gap_display_replaces_element_id_line(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    by_id = {str(el.id): el for el in task.element_nodes}
    raw = {
        "element_id": "el_proj",
        "element_name": "已挂要素确认",
        "display_line": "已挂要素：{{el-1793765215913-eevk1uu 对应内容}}",
    }
    fixed = normalize_gap_display(raw, by_id)
    assert fixed["display_line"] == "项目名称"
    assert fixed["element_name"] == "项目名称"


def test_gap_resolved_when_element_configured(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    task.element_nodes[0].value = "某某项目"
    gap = {
        "chapter_key": "c1",
        "element_id": "el_proj",
        "point_index": 0,
        "display_line": "项目名称：{{配置要素}}",
        "status": "open",
    }
    assert gap_resolved(task, gap, home)


def test_gap_resolved_when_point_linked_element_configured(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wPtLink"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [
        ElementNode(id="el_bg", name="行业背景说明", kind="llm_generate", prompt="写背景"),
    ]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1.1",
                title="背景",
                point_items=[
                    {
                        "id": "p0",
                        "text": "行业/政策背景…（待补充：具体政策名称与文号）",
                        "slot": "具体政策名称与文号",
                        "needs_supplement": True,
                        "element_id": "el_bg",
                    },
                ],
                element_ids=["el_bg"],
            ),
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    gap = {
        "chapter_key": "c1",
        "point_index": 0,
        "slot_label": "具体政策名称与文号",
        "display_line": "行业/政策背景：{{具体政策名称与文号}}",
        "status": "open",
    }
    assert gap_resolved(task, gap, home)


def test_point_item_rows_for_chapter_uses_element_name(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wPtRows"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [
        ElementNode(id="el_bg", name="岗位信息", kind="fixed", value=""),
    ]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1.1",
                title="职责",
                point_items=[
                    {
                        "id": "p0",
                        "text": "描述岗位",
                        "element_id": "el_bg",
                    },
                ],
                element_ids=["el_bg"],
            ),
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    from report_writing.chat.writing_supplement_check import _point_item_rows_for_chapter

    rows = _point_item_rows_for_chapter(task, "c1", home)
    assert len(rows) == 1
    assert rows[0]["element_name"] == "岗位信息"
    assert rows[0]["needs_config"] is False


def test_no_gap_when_point_linked_even_if_element_incomplete(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wPtLinked"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [
        ElementNode(id="el_doc", name="年度目标责任书", kind="file", files=[]),
    ]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c12",
                sequence="1.2",
                title="年度工作目标",
                point_items=[
                    {
                        "id": "p0",
                        "text": "年度目标与任务清单（待补充：年度目标责任书）",
                        "needs_supplement": True,
                        "element_id": "el_doc",
                    },
                ],
                element_ids=["el_doc"],
            ),
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    gaps = collect_deterministic_gaps(task, ["c12"], home)
    assert not any(g.get("point_index") == 0 for g in gaps)
    assert not any(g.get("element_id") == "el_doc" for g in gaps)


def test_deterministic_gap_per_point_todo(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wPtTodo"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1",
                title="概述",
                points=[
                    "主线（待补充：主角姓名）",
                    "无占位的一条",
                ],
            ),
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    gaps = collect_deterministic_gaps(task, ["c1"], home)
    assert len(gaps) == 1
    assert gaps[0].get("point_index") == 0
    assert "主角姓名" in str(gaps[0].get("slot_label") or "")


def test_dismiss_dissociates_element_not_delete_node(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(id="s_dismiss", task_id=task.id)
    run_supplement_check(home, session, task, ["c1"], invoke=None)
    from report_writing.chat.writing_supplement_check import load_registry

    reg = load_registry(session)
    gid = next(
        g["id"]
        for g in reg.get("items") or []
        if g.get("element_id") == "el_proj"
    )
    updated = dismiss_gap(home, session, task, gid)
    node = next(n for n in updated.outline.chapters if n.key == "c1")
    assert "el_proj" not in list(node.element_ids or [])
    assert any(e.id == "el_proj" for e in updated.element_nodes or [])


def test_supplement_gate_and_skip(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(id="s_gate", task_id=task.id)
    assert supplement_gate_needed(session, task, ["c1"], home=home)
    run_supplement_check(home, session, task, ["c1"], invoke=None)
    from report_writing.chat.writing_supplement_check import load_registry, mark_supplement_skipped, supplement_batch_scope

    assert open_gaps(load_registry(session))
    mark_supplement_skipped(session, supplement_batch_scope(task.id, ["c1"]))
    assert not supplement_gate_needed(session, task, ["c1"], home=home)


def test_gate_runs_when_fresh_gaps_after_registry_checked(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(id="s_fresh", task_id=task.id)
    run_supplement_check(home, session, task, ["c1"], invoke=None)
    from report_writing.chat.writing_supplement_check import (
        load_registry,
        mark_supplement_skipped,
        supplement_batch_scope,
    )

    mark_supplement_skipped(session, supplement_batch_scope(task.id, ["c1"]))
    assert not supplement_gate_needed(session, task, ["c1"], home=home)
    # 未 skip 时 registry 标 checked 但要素仍空 → 再扫应仍要检查
    session2 = ChatSession(id="s_fresh2", task_id=task.id)
    run_supplement_check(home, session2, task, ["c1"], invoke=None)
    assert supplement_gate_needed(session2, task, ["c1"], home=home)


def test_collapse_drops_point_when_element_gap_same_target(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    from report_writing.chat.writing_supplement_check import collapse_supplement_gaps

    gaps = [
        {
            "id": "g1",
            "chapter_key": "c1",
            "point_index": 0,
            "slot_label": "项目名称",
            "display_line": "填写项目名称",
            "status": "open",
        },
        {
            "id": "g2",
            "chapter_key": "c1",
            "element_id": "el_proj",
            "element_name": "项目名称",
            "display_line": "项目名称",
            "status": "open",
        },
    ]
    out = collapse_supplement_gaps(task, gaps)
    assert len(out) == 1
    assert out[0].get("element_id") == "el_proj"


def test_no_gaps_when_point_linked_by_slot_and_element_id(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wSlotLink"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [
        ElementNode(id="el_doc", name="年度目标责任书或绩效考核表", kind="file", files=[]),
    ]
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c12",
                sequence="1.2",
                title="年度目标",
                point_items=[
                    {
                        "id": "p0",
                        "text": "说明来源（待补充：年度目标责任书或绩效考核表）",
                        "slot": "年度目标责任书或绩效考核表",
                        "needs_supplement": True,
                        "element_id": "el_doc",
                    },
                ],
                element_ids=["el_doc"],
            ),
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    gaps = collect_deterministic_gaps(task, ["c12"], home)
    assert gaps == []


def test_no_gap_from_stale_points_text_when_point_linked(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wStalePts"
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.element_nodes = [
        ElementNode(id="el_doc", name="年度目标责任书或绩效考核表", kind="file", files=[]),
    ]
    stale_line = "先说明年度目标来源（待补充：年度目标责任书或绩效考核表）"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c12",
                sequence="1.2",
                title="年度目标",
                points=[stale_line, "第二条要点"],
                point_items=[
                    {
                        "id": "p0",
                        "text": stale_line,
                        "slot": "年度目标责任书或绩效考核表",
                        "element_id": "el_doc",
                        "needs_supplement": True,
                    },
                    {"id": "p1", "text": "第二条要点"},
                ],
                element_ids=["el_doc"],
            ),
        ]
    )
    TaskStore(path).save(task)
    task = TaskStore(path).load()
    gaps = collect_deterministic_gaps(task, ["c12"], home)
    assert gaps == []


def test_gate_skipped_when_gaps_resolved_in_task(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(id="s_res", task_id=task.id)
    run_supplement_check(home, session, task, ["c1"], invoke=None)
    assert supplement_gate_needed(session, task, ["c1"], home=home)
    el = next(e for e in task.element_nodes if e.id == "el_proj")
    el.value = "已填写"
    from report_writing.services.store import TaskStore

    TaskStore(home / "tasks" / task.id).save(task)
    task = TaskStore(home / "tasks" / task.id).load()
    from report_writing.chat.writing_supplement_check import mark_resolved_gaps_in_registry

    mark_resolved_gaps_in_registry(session, task, home)
    assert not supplement_gate_needed(session, task, ["c1"], home=home)


def test_start_writing_shows_supplement_card_before_run(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(
        id="s_start",
        task_id=task.id,
        phase="ready_to_write",
        freeform={
            "write_chapter_keys": ["c1"],
            "writing_pace": "batch",
            "writing_pace_scope": f"{task.id}|c1",
            "outline_confirmed": True,
            "meta_confirmed": True,
        },
    )
    reply, cards, _events = _action_start_writing(
        home,
        session,
        writing_pace="batch",
        chapter_keys=["c1"],
    )
    assert any(getattr(c, "type", "") == "writing_supplement_check" for c in cards)
    assert "不补也可以先写" in (reply or "")


def test_retry_chapter_rewrite_mode_still_shows_supplement_card(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(id="s_retry_rewrite", task_id=task.id, phase="ready_to_write")
    with patch("report_writing.services.pipeline.run_chapters") as run_mock:
        reply, cards, events = _action_retry_chapter(
            home,
            session,
            {
                "task_id": task.id,
                "chapter_key": "c1",
                "_write_mode": "rewrite",
            },
        )
    run_mock.assert_not_called()
    assert any(getattr(c, "type", "") == "writing_supplement_check" for c in cards)


def test_retry_chapter_shows_supplement_card_before_run(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(id="s_retry_gate", task_id=task.id, phase="ready_to_write")
    with patch("report_writing.services.pipeline.run_chapters") as run_mock:
        reply, cards, events = _action_retry_chapter(
            home,
            session,
            {
                "task_id": task.id,
                "chapter_key": "c1",
                "_write_mode": "start",
            },
        )
    run_mock.assert_not_called()
    assert not events
    assert any(getattr(c, "type", "") == "writing_supplement_check" for c in cards)
    assert "不补也可以先写" in (reply or "")


def test_dismiss_action_returns_card(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    task = _task_with_empty_element(home)
    session = ChatSession(id="s_act", task_id=task.id)
    run_supplement_check(home, session, task, ["c1"], invoke=None)
    reg = session.freeform.get("writing_supplement_registry") or {}
    gid = open_gaps(reg)[0]["id"]
    reply, cards = _action_dismiss_writing_supplement(
        home, session, {"gap_id": gid, "task_id": task.id}
    )
    assert "移除" in reply
    assert cards == []
