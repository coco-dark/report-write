"""同会话多文档：定名 / 选路 / 自动大纲 / 模版卡互不串台。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.intent_dispatch import execute_dispatch_tool
from report_writing.chat.intent_turn_plan import TurnPlan, apply_declared_slots
from report_writing.chat.session_documents import (
    begin_document_onboarding,
    confirm_writing_document_name,
    focus_needs_doc_name,
    focus_onboarding,
    is_writing_document_named,
    mark_focus_entry_path,
    stash_focus_preferred_entry_path,
    upsert_session_document,
)
from report_writing.controller import ensure_task, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession
from report_writing.services.store import TaskStore


def _seed_named_doc(home: Path, session: ChatSession, *, tid: str, title: str) -> None:
    path = home / "tasks" / tid
    ensure_task(path)
    save_spec_meta(path, title=title, category="报告", tags=["chat"], description="test")
    store = TaskStore(path)
    task = store.load()
    task.title = title
    store.save(task)
    session.task_id = tid
    session.phase = ChatPhase.READY_TO_WRITE.value
    confirm_writing_document_name(session, title, home=home, message=title)
    upsert_session_document(
        session,
        kind="outline",
        task_id=tid,
        writing_task_id=tid,
        title=title,
        clear_blueprint_id=True,
    )
    mark_focus_entry_path(session, "freeform")
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "freeform",
        "writing_doc_named": True,
        "title": title,
        "focus_document_key": f"task:{tid}",
    }


def test_force_new_does_not_inherit_old_named_flag(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_named", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_doc(tmp_path, session, tid="w_old", title="环境报告")
    assert is_writing_document_named(session)

    begin_document_onboarding(
        session,
        tmp_path,
        message="再写一份述职报告",
        title_hint="述职报告",
        force_new=True,
    )
    assert not is_writing_document_named(session)
    assert focus_needs_doc_name(session)
    ob = focus_onboarding(session)
    assert ob.get("name_confirmed") is not True
    assert str(ob.get("entry_path") or "") == ""
    from report_writing.chat.session_documents import focus_document_key

    new_key = focus_document_key(session)
    assert new_key and new_key != "task:w_old"
    assert str(new_key).startswith("task:w")


def test_force_new_from_bp_focus_mints_new_task_key(tmp_path: Path):
    """选用蓝本已定名后 force_new：焦点立刻离开 bp:，落到新 w*。"""
    from report_writing.chat.session_documents import focus_document_key

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_bp_new", phase=ChatPhase.READY_TO_WRITE.value)
    bid = "bp202611022054023656"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    save_spec_meta(path, title="安全报告", category="报告", tags=["chat"], description="test")
    store = TaskStore(path)
    task = store.load()
    task.title = "安全报告"
    store.save(task)
    upsert_session_document(
        session,
        kind="blueprint",
        task_id=bid,
        blueprint_id=bid,
        title="安全报告",
    )
    session.blueprint_id = bid
    session.task_id = bid
    session.freeform = {
        **(session.freeform or {}),
        "focus_document_key": f"bp:{bid}",
        "writing_doc_named": True,
        "title": "安全报告",
    }
    confirm_writing_document_name(session, "安全报告", home=tmp_path, message="安全报告")
    assert focus_document_key(session) == f"bp:{bid}"

    begin_document_onboarding(
        session,
        tmp_path,
        message="再根据我的模版帮我写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    new_key = focus_document_key(session)
    assert new_key and new_key != f"bp:{bid}"
    assert str(new_key).startswith("task:w")
    assert not is_writing_document_named(session)
    assert focus_needs_doc_name(session)


def test_force_new_keeps_declared_auto_outline_preferred(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_pref", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_doc(tmp_path, session, tid="w_old", title="环境报告")
    stash_focus_preferred_entry_path(session, "freeform", residual="自动生成大纲")

    begin_document_onboarding(
        session,
        tmp_path,
        message="再写一份述职报告，自动生成大纲",
        title_hint="述职报告",
        force_new=True,
    )
    ob = focus_onboarding(session)
    assert str(ob.get("preferred_entry_path") or "") == "freeform"
    assert str(ob.get("entry_path") or "") == ""
    assert focus_needs_doc_name(session)


def test_force_new_does_not_inherit_previous_template_path(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_no_leak", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_doc(tmp_path, session, tid="w_env", title="环境报告")
    stash_focus_preferred_entry_path(
        session, "template", residual="再根据我的模版写一份环境报告"
    )
    mark_focus_entry_path(session, "template")

    begin_document_onboarding(
        session,
        tmp_path,
        message="再给我写一份述职报告 三个章节 名字叫做lzb述职报告",
        title_hint="lzb述职报告",
        force_new=True,
    )
    ob = focus_onboarding(session)
    assert str(ob.get("preferred_entry_path") or "") != "template"
    assert "三个章节" in str((session.freeform or {}).get("outline_constraints") or "")
    assert "lzb述职报告" in str(ob.get("clarify_residual") or "") or True


def test_apply_declared_slots_does_not_lock_path_when_unnamed(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_slots", phase=ChatPhase.INTENT.value)
    _seed_named_doc(tmp_path, session, tid="w_old", title="环境报告")
    begin_document_onboarding(
        session, tmp_path, message="再写一份述职报告", title_hint="述职报告", force_new=True
    )
    plan = TurnPlan(
        confidence=0.9,
        slots={"writing_path": "freeform", "freeform_mode": "auto_outline"},
        missing=["doc_name"],
    )
    apply_declared_slots(
        session, plan, home=tmp_path, message="再写一份述职报告，自动生成大纲"
    )
    ob = focus_onboarding(session)
    assert str(ob.get("preferred_entry_path") or "") == "freeform"
    assert str(ob.get("entry_path") or "") == ""
    assert focus_needs_doc_name(session)


def test_skip_blueprint_unnamed_returns_name_card(tmp_path: Path):
    from report_writing.chat.intent_clarify import get_pending

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_skip", phase=ChatPhase.INTENT.value)
    _seed_named_doc(tmp_path, session, tid="w_old", title="环境报告")
    begin_document_onboarding(
        session, tmp_path, message="再写一份述职报告", title_hint="述职报告", force_new=True
    )
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "skip_blueprint", "args": {"path_setup_only": True, "title": "述职报告"}},
        "再写一份述职报告",
    )
    assert hit is not None
    reply, cards, _ = hit
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name" or focus_needs_doc_name(session)
    assert str((session.freeform or {}).get("title") or "") != "环境报告"
    assert not any(str(getattr(c, "type", "") or "") == "outline_preview" for c in (cards or []))
    assert "目录我先帮你搭" not in (reply or "")


def test_submit_outline_regenerate_unnamed_returns_name_card(tmp_path: Path):
    from report_writing.chat.intent_clarify import get_pending

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_out", phase=ChatPhase.INTENT.value)
    _seed_named_doc(tmp_path, session, tid="w_old", title="环境报告")
    begin_document_onboarding(
        session, tmp_path, message="再写一份述职报告", title_hint="述职报告", force_new=True
    )
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "submit_outline", "args": {"regenerate": True, "title": "述职报告"}},
        "自动生成大纲",
    )
    assert hit is not None
    _reply, cards, _ = hit
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name" or focus_needs_doc_name(session)
    assert str((session.freeform or {}).get("title") or "") != "环境报告"
    assert not any(
        str(getattr(c, "type", "") or "") == "outline_preview"
        and "环境报告" in str(getattr(c, "title", "") or "")
        for c in (cards or [])
    )


def test_after_name_auto_outline_does_not_reask_name(tmp_path: Path):
    from report_writing.chat.intent_clarify import build_entry_path_clarify, get_pending
    from unittest.mock import MagicMock

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="iso_loop", phase=ChatPhase.INTENT.value)
    _seed_named_doc(tmp_path, session, tid="w_old", title="环境报告")
    begin_document_onboarding(
        session,
        tmp_path,
        message="再写一份述职报告，自动生成大纲",
        title_hint="述职报告",
        force_new=True,
    )
    confirm_writing_document_name(
        session, "我的述职报告", home=tmp_path, message="叫做我的述职报告"
    )
    assert is_writing_document_named(session)
    assert not focus_needs_doc_name(session)
    invoke = MagicMock(return_value="1 概述\n2 工作")
    reply, cards = build_entry_path_clarify(
        session,
        message="再写一份述职报告，自动生成大纲",
        title_hint="我的述职报告",
        home=tmp_path,
        invoke=invoke,
        after_doc_name_confirm=True,
        apply_plan=False,
    )
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    assert "先给这份文档起个名字" not in (reply or "")
    sources = {
        str((getattr(c, "payload", None) or {}).get("source") or "") for c in (cards or [])
    }
    assert "doc_name" not in sources
