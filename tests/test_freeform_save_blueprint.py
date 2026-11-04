"""无蓝本任务沉淀为蓝本。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.actions import _action_save_freeform_as_blueprint, _action_skip_blueprint
from report_writing.chat.freeform_blueprint import (
    freeform_outline_is_confirmed,
    promote_writing_task_to_blueprint,
    should_offer_save_blueprint,
)
from report_writing.chat.session_documents import focus_document_key
from report_writing.controller import confirm_outline, ensure_task, save_outline, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode
from report_writing.services.task_ids import is_blueprint_task_id


def test_promote_writing_task_to_blueprint(tmp_path: Path):
    home = tmp_path
    tasks = home / "tasks"
    tasks.mkdir(parents=True)
    wid = "w20260101120000abcd"
    wdir = tasks / wid
    ensure_task(wdir)
    save_spec_meta(wdir, title="测试工作说明", category="工作说明")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="总则", level=1, points=["要点A"]),
            OutlineNode(key="c2", sequence="2", title="范围", level=1),
        ],
    )
    confirm_outline(wdir)

    bp_id, bp = promote_writing_task_to_blueprint(
        home, wid, tags=["测试", "工作说明"]
    )
    assert is_blueprint_task_id(bp_id)
    assert (tasks / bp_id).is_dir()
    assert bp.title == "测试工作说明"
    assert bp.outline and len(bp.outline.chapters) == 2
    assert bp.outline.chapters[0].points == ["要点A"]


def test_collect_blueprint_requires_outline_confirmed(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "w20260101120000cole"
    wdir = home / "tasks" / wid
    ensure_task(wdir)
    save_spec_meta(wdir, title="未确认大纲", category="报告")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="一", level=1),
            OutlineNode(key="c2", sequence="2", title="二", level=1),
        ],
    )
    session = ChatSession(
        id="s_collect",
        task_id=wid,
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={"meta_confirmed": True, "title": "未确认大纲", "outline_pending_edit": True},
    )
    assert not freeform_outline_is_confirmed(session, home)
    # 目录生成后即可露出收录；真正入库仍要求先确认大纲
    assert should_offer_save_blueprint(session)
    reply, cards = _action_save_freeform_as_blueprint(home, session, {"task_id": wid})
    assert "确认大纲" in reply
    assert any(getattr(c, "type", "") == "outline_preview" for c in cards)


def test_auto_outline_still_offers_collect_when_meta_flag_missing(tmp_path: Path):
    """无蓝本自动大纲：焦点重绑清掉 meta_confirmed 时，有标题仍应能收录。"""
    from report_writing.chat.freeform_blueprint import composer_save_blueprint_state

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "w20260101120000auto"
    wdir = home / "tasks" / wid
    ensure_task(wdir)
    save_spec_meta(wdir, title="自动大纲文档", category="报告")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="概述", level=1),
            OutlineNode(key="c2", sequence="2", title="正文", level=1),
        ],
    )
    session = ChatSession(
        id="s_auto",
        task_id=wid,
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "title": "自动大纲文档",
            "writing_path": "freeform",
            "writing_doc_named": True,
            "meta_confirmed": False,
        },
    )
    assert should_offer_save_blueprint(session)
    state = composer_save_blueprint_state(home, session)
    assert state.get("show") is True
    assert state.get("task_id") == wid


def test_skip_tip_without_pipe_uses_provisional_title(tmp_path: Path):
    """口语题名不必强制「类型｜标题」；可作临时标题，后续多轮由大脑补全。"""
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_name_ask", blueprint_id="bpOld")
    reply, _cards = _action_skip_blueprint(
        session,
        home=home,
        title_hint="述职报告",
        extra_message="",
        invoke=None,
    )
    assert "述职报告" in reply or (session.freeform or {}).get("title") == "述职报告"
    # 有 tip 时可直接进入大纲收集，不必卡死在固定句式
    assert session.phase in {
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
    }


def test_save_blueprint_collapses_task_registry_entry(tmp_path: Path):
    """收录后 session_documents 不得同时保留 task: 与 bp: 两份同名登记。"""
    from report_writing.chat.session_documents import session_documents_map, upsert_session_document

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "w20260101120000save"
    wdir = home / "tasks" / wid
    ensure_task(wdir)
    save_spec_meta(wdir, title="个人工作汇报", category="报告")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="概述", level=1),
            OutlineNode(key="c2", sequence="2", title="正文", level=1),
        ],
    )
    confirm_outline(wdir)
    session = ChatSession(
        id="s_save_dedupe",
        task_id=wid,
        phase=ChatPhase.FREEFORM_POINTS.value,
        freeform={
            "meta_confirmed": True,
            "outline_confirmed": True,
            "title": "个人工作汇报",
            "category": "报告",
        },
    )
    upsert_session_document(
        session, kind="outline", task_id=wid, title="个人工作汇报"
    )
    assert f"task:{wid}" in session_documents_map(session)

    reply, _cards = _action_save_freeform_as_blueprint(
        home,
        session,
        {"task_id": wid, "tags": ["汇报", "个人"]},
    )
    assert "已收录" in reply
    docs = session_documents_map(session)
    assert f"task:{wid}" not in docs
    bp_keys = [k for k in docs if str(k).startswith("bp:")]
    assert len(bp_keys) == 1
    entry = docs[bp_keys[0]]
    assert entry.get("writing_task_id") == wid
    assert entry.get("title") == "个人工作汇报"
    assert focus_document_key(session) == bp_keys[0]
    assert (session.freeform or {}).get("freeform_saved_blueprint_id") == entry.get(
        "blueprint_id"
    )


def test_outline_sets_focus_document_key(tmp_path: Path):
    from report_writing.chat.actions import _collect_freeform_outline
    from unittest.mock import MagicMock

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="s_focus_outline",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "meta_confirmed": True,
            "category": "述职报告",
            "title": "2026个人述职",
        },
    )
    invoke = MagicMock(return_value="1 个人总结\n2 工作业绩\n3 不足与展望")
    _collect_freeform_outline(
        home,
        session,
        "帮我生成目录",
        invoke=invoke,
        regenerate=True,
    )
    assert session.task_id
    assert focus_document_key(session) == f"task:{session.task_id}"
