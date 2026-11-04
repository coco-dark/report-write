"""蓝本向导 step 与 task 对齐。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.wizard_state import derive_blueprint_wizard_step, sync_blueprint_wizard_session
from report_writing.controller import confirm_outline_extract, ensure_task
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_derive_upload_when_task_not_readable_yet(tmp_path: Path):
    """仅有 blueprint_id、任务文件尚未可读时，仍应在上传步而非 meta。"""
    bid = "bp20261027120000abcd"
    step, extra = derive_blueprint_wizard_step(tmp_path, bid)
    assert step == "upload"
    assert extra.get("blueprint_id") == bid


def test_derive_wizard_steps(tmp_path: Path):
    bid = "bp_wiz_sync"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "测试蓝本"
    TaskStore(path).save(task)
    assert derive_blueprint_wizard_step(tmp_path, bid)[0] == "upload"

    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    TaskStore(path).save(task)
    assert derive_blueprint_wizard_step(tmp_path, bid)[0] == "outline_confirm"

    task.outline_confirmed = True
    task.outline.chapters[0].points = ["p"]
    TaskStore(path).save(task)
    step, extra = derive_blueprint_wizard_step(tmp_path, bid)
    assert step == "study"
    assert extra.get("study_mode") == "complete"


def test_sync_keeps_reextract_trigger_when_outline_exists(tmp_path: Path):
    """说「重新提取目录」时，即使任务里已有目录，仍应停在 outline 并保留 trigger。"""
    bid = "bp_trig2"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    TaskStore(path).save(task)

    session = ChatSession(
        id="s_trig2",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "outline_confirm",
                "trigger_outline_extract": True,
                "pending_wizard_command": "re_extract_outline",
            }
        },
    )
    sync_blueprint_wizard_session(tmp_path, session)
    wiz = session.freeform["blueprint_wizard"]
    assert wiz["step"] == "outline"
    assert wiz.get("trigger_outline_extract") is True


def test_sync_advances_after_reextract_when_trigger_cleared(tmp_path: Path):
    """重新提取完成后客户端已清 trigger：应进入确认目录并丢掉 pending 命令。"""
    bid = "bp_trig_done"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    TaskStore(path).save(task)

    session = ChatSession(
        id="s_trig_done",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "outline",
                "pending_wizard_command": "re_extract_outline",
            }
        },
    )
    sync_blueprint_wizard_session(tmp_path, session)
    wiz = session.freeform["blueprint_wizard"]
    assert wiz["step"] == "outline_confirm"
    assert "pending_wizard_command" not in wiz
    assert "trigger_outline_extract" not in wiz


def test_sync_drops_leftover_extract_trigger_when_chapters_exist(tmp_path: Path):
    """提取完成后残留 trigger 不得把步数倒回 outline。"""
    bid = "bp_trig_left"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    TaskStore(path).save(task)

    session = ChatSession(
        id="s_trig_left",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "outline_confirm",
                "trigger_outline_extract": True,
            }
        },
    )
    sync_blueprint_wizard_session(tmp_path, session)
    wiz = session.freeform["blueprint_wizard"]
    assert wiz["step"] == "outline_confirm"
    assert "trigger_outline_extract" not in wiz


def test_sync_clears_extract_trigger_when_outline_ready(tmp_path: Path):
    """无 trigger 时，有目录应对齐到 outline_confirm。"""
    bid = "bp_trig2"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    TaskStore(path).save(task)

    session = ChatSession(
        id="s_trig2",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "outline_confirm",
            }
        },
    )
    sync_blueprint_wizard_session(tmp_path, session)
    wiz = session.freeform["blueprint_wizard"]
    assert wiz["step"] == "outline_confirm"
    assert "trigger_outline_extract" not in wiz


def test_sync_keeps_extract_trigger_only_while_outline_empty(tmp_path: Path):
    bid = "bp_trig"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    TaskStore(path).save(task)

    session = ChatSession(
        id="s_trig",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "outline",
                "trigger_outline_extract": True,
            }
        },
    )
    sync_blueprint_wizard_session(tmp_path, session)
    wiz = session.freeform["blueprint_wizard"]
    assert wiz["step"] == "outline"
    assert wiz.get("trigger_outline_extract") is True


def test_sync_session_wizard(tmp_path: Path):
    bid = "bp_sess"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一")])
    task.outline_confirmed = False
    TaskStore(path).save(task)

    session = ChatSession(
        id="s1",
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )
    sync_blueprint_wizard_session(tmp_path, session)
    # 任务未确认时，允许从 study 退回 outline_confirm，避免「界面已确认、任务未确认」脱节
    assert session.freeform["blueprint_wizard"]["step"] == "outline_confirm"


def test_sync_drops_begin_study_trigger_when_already_analyzed(tmp_path: Path):
    """重进对话时，已学过的蓝本不得再带 trigger_begin_study 整本重跑。"""
    bid = "bp_studied"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="一", sequence="1", points=["已有要点"]),
            OutlineNode(key="c2", title="二", sequence="2"),
        ]
    )
    TaskStore(path).save(task)
    session = ChatSession(
        id="s_studied",
        blueprint_id=bid,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": bid,
                "step": "study",
                "trigger_begin_study": True,
                "trigger_chapter_study": ["c1", "c2"],
            }
        },
    )
    sync_blueprint_wizard_session(tmp_path, session)
    wiz = session.freeform["blueprint_wizard"]
    assert not wiz.get("trigger_begin_study")
    # 已有要点的章也保留（用户可能要再分析）；只清「开始全部学习」
    assert list(wiz.get("trigger_chapter_study") or []) == ["c1", "c2"]


def test_confirm_outline_extract_skips_studied_chapters(tmp_path: Path):
    bid = "bp_skip_studied"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="一", sequence="1", points=["已有要点"]),
            OutlineNode(key="c2", title="二", sequence="2", structure_only=True),
        ]
    )
    TaskStore(path).save(task)
    updated = confirm_outline_extract(path, continue_study=True)
    assert updated.outline_confirmed
    assert list(updated.study_chapter_keys or []) == []


def test_derive_study_mode(tmp_path: Path):
    bid = "bp_study_mode"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="一", sequence="1", points=["p1"]),
            OutlineNode(key="c2", title="二", sequence="2"),
        ]
    )
    TaskStore(path).save(task)
    _, extra = derive_blueprint_wizard_step(tmp_path, bid)
    assert extra.get("study_mode") == "continue"
    assert extra.get("study_analyzed") == 1
    assert extra.get("study_unanalyzed") == 1


def test_derive_stays_outline_confirm_without_points(tmp_path: Path):
    """目录摘录字段不算「已学习」，未确认前不应进 study/done。"""
    bid = "bp_outline_only"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline_confirmed = False
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                title="章一",
                sequence="1",
                describes="摘自样板的一段说明",
                reference_text="样板正文摘录",
            )
        ]
    )
    TaskStore(path).save(task)
    step, _ = derive_blueprint_wizard_step(tmp_path, bid)
    assert step == "outline_confirm"


def test_confirm_outline_extract_sets_outline_confirmed(tmp_path: Path):
    bid = "bp_confirm_outline"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="章一", sequence="1")])
    TaskStore(path).save(task)
    assert not task.outline_confirmed

    updated = confirm_outline_extract(path, continue_study=False)
    assert updated.outline_confirmed
    assert TaskStore(path).load().outline_confirmed


def test_finalize_wizard_cards_prefers_handler_upload_over_stale_meta(tmp_path: Path):
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.wizard_state import finalize_wizard_cards_for_response
    from report_writing.domain.models import ChatMessage, ChatSession

    bid = "bp_finalize"
    session = ChatSession(
        id="s_fin",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "upload", "title": "T"}},
        messages=[
            ChatMessage(
                role="assistant",
                text="old",
                cards=[
                    _blueprint_wizard_card(
                        blueprint_id="",
                        step="meta",
                        title="创建蓝本 · 名称",
                        extra={"title": "T"},
                    )
                ],
            )
        ],
    )
    handler = [
        _blueprint_wizard_card(
            blueprint_id=bid,
            step="upload",
            title="创建蓝本 · 上传样板",
            extra={"title": "T", "blueprint_id": bid, "step": "upload"},
        )
    ]
    wiz = dict(session.freeform["blueprint_wizard"])
    out = finalize_wizard_cards_for_response(session, handler, wiz)
    assert len(out) == 1
    assert out[0].payload.get("step") == "upload"
    assert out[0].payload.get("blueprint_id") == bid


def test_finalize_wizard_cards_floors_meta_when_blueprint_id_exists():
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.wizard_state import finalize_wizard_cards_for_response
    from report_writing.domain.models import ChatSession

    bid = "bp_floor"
    wiz = {"blueprint_id": bid, "step": "upload", "title": "T"}
    session = ChatSession(id="s_floor", blueprint_id=bid, freeform={"blueprint_wizard": wiz})
    handler = [
        _blueprint_wizard_card(
            blueprint_id=bid,
            step="meta",
            title="创建蓝本 · 名称",
            extra={"title": "T", "blueprint_id": bid, "step": "meta"},
        )
    ]
    out = finalize_wizard_cards_for_response(session, handler, wiz)
    assert len(out) == 1
    assert out[0].payload.get("step") == "upload"
    assert out[0].payload.get("blueprint_id") == bid


def test_finalize_keeps_outline_confirm_card():
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.wizard_state import finalize_wizard_cards_for_response
    from report_writing.domain.models import ChatSession

    bid = "bp_confirm_card"
    wiz = {
        "blueprint_id": bid,
        "step": "outline_confirm",
        "chapter_count": 2,
        "chapters": [{"key": "c1", "title": "一"}],
    }
    session = ChatSession(id="s_confirm", blueprint_id=bid, freeform={"blueprint_wizard": wiz})
    handler = [
        _blueprint_wizard_card(
            blueprint_id=bid,
            step="outline_confirm",
            title="创建蓝本 · 确认目录",
            extra=wiz,
        )
    ]
    out = finalize_wizard_cards_for_response(session, handler, wiz)
    assert len(out) == 1
    assert out[0].payload.get("step") == "outline_confirm"


def test_finalize_skips_synth_while_outline_extract_active():
    from report_writing.chat.wizard_state import finalize_wizard_cards_for_response
    from report_writing.domain.models import ChatSession

    bid = "bp_extract_active"
    wiz = {"blueprint_id": bid, "step": "outline", "outline_extract_active": True}
    session = ChatSession(id="s_ex", blueprint_id=bid, freeform={"blueprint_wizard": wiz})
    out = finalize_wizard_cards_for_response(session, [], wiz)
    assert out == []


def test_derive_keeps_chapter_preview_short(tmp_path: Path):
    from report_writing.chat.wizard_state import WIZARD_CHAPTER_PREVIEW_LIMIT

    bid = "bp_preview"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "大目录"
    task.sample_ready = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key=f"c{i}", title=f"章{i}", sequence=str(i))
            for i in range(1, 21)
        ]
    )
    TaskStore(path).save(task)
    step, extra = derive_blueprint_wizard_step(tmp_path, bid)
    assert step == "outline_confirm"
    assert extra.get("chapter_count") == 20
    assert len(extra.get("chapters") or []) == WIZARD_CHAPTER_PREVIEW_LIMIT
