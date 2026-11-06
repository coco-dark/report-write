"""同会话多车道：flush/hydrate、force_new 空车道、template 不锁 freeform。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.tool_dispatch import execute_dispatch_tool
from report_writing.chat.session_documents import (
    apply_focus_document_by_key,
    begin_document_onboarding,
    confirm_writing_document_name,
    document_lane,
    empty_document_lane,
    ensure_named_freeform_document,
    flush_lane,
    focus_document_key,
    focus_document_prefers_freeform_path,
    focus_needs_doc_name,
    focus_onboarding,
    hydrate_lane,
    is_writing_document_named,
    mark_focus_entry_path,
    rebind_session_to_focus_document,
    upsert_session_document,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession
from report_writing.services.store import TaskStore


def _seed_named_doc(
    home: Path,
    session: ChatSession,
    *,
    tid: str,
    title: str,
    outline_confirmed: bool = False,
) -> None:
    path = home / "tasks" / tid
    ensure_task(path, biz_type="002")
    store = TaskStore(path)
    task = store.load()
    task.title = title
    store.save(task)
    upsert_session_document(
        session,
        kind="outline",
        task_id=tid,
        writing_task_id=tid,
        title=title,
        clear_blueprint_id=True,
    )
    session.task_id = tid
    session.phase = ChatPhase.READY_TO_WRITE.value
    session.freeform = {
        **(session.freeform or {}),
        "focus_document_key": f"task:{tid}",
        "writing_doc_named": True,
        "meta_confirmed": True,
        "title": title,
        "writing_path": "freeform",
        "outline_confirmed": outline_confirmed,
        "outline_text": "# 一、概述" if outline_confirmed else "",
    }
    confirm_writing_document_name(session, title, home=home, message=title)
    mark_focus_entry_path(session, "freeform")
    flush_lane(session)


def test_flush_hydrate_isolates_outline_confirmed(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="lanes_fh", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_doc(home, session, tid="w_doc_a", title="环境报告", outline_confirmed=True)
    key_a = focus_document_key(session)
    flush_lane(session)
    assert document_lane(session, key_a).get("outline_confirmed") is True

    begin_document_onboarding(
        session, home, message="再写一份述职报告", title_hint="述职报告", force_new=True
    )
    assert session.freeform.get("outline_confirmed") is not True
    assert str((session.freeform or {}).get("outline_text") or "") == ""
    assert str((session.freeform or {}).get("writing_path") or "") == ""
    assert not is_writing_document_named(session)
    assert focus_needs_doc_name(session)

    hydrate_lane(session, key_a)
    assert session.freeform.get("outline_confirmed") is True
    assert str(session.task_id or "") == "w_doc_a"


def test_force_new_empty_lane_does_not_auto_name_subject(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="lanes_fn", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_doc(home, session, tid="w_old", title="环境报告", outline_confirmed=True)

    begin_document_onboarding(
        session,
        home,
        message="再写一份述职报告",
        title_hint="述职报告",
        force_new=True,
    )
    assert not is_writing_document_named(session)
    ob = focus_onboarding(session)
    assert ob.get("name_confirmed") is not True
    assert str(ob.get("entry_path") or "") == ""
    assert session.freeform.get("outline_confirmed") is not True
    lane = empty_document_lane()
    assert lane.get("outline_confirmed") is False
    assert lane.get("writing_path") == ""


def test_template_rebind_does_not_lock_freeform(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="lanes_tpl", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_named_doc(home, session, tid="w_doc_a", title="环境报告", outline_confirmed=True)
    key_a = focus_document_key(session)
    flush_lane(session)

    begin_document_onboarding(
        session, home, message="新开一份环境报告", title_hint="环境报告2", force_new=True
    )
    shell = ensure_named_freeform_document(
        session, home, title="环境报告2", confirm_name=True
    )
    mark_focus_entry_path(session, "template")
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "upload",
            "blueprint_id": shell,
            "working_task_id": shell,
            "title": "环境报告2",
            "mode": "create",
        },
    }
    session.freeform.pop("writing_path", None)
    flush_lane(session)
    key_b = focus_document_key(session)
    assert key_b and key_b != key_a
    assert focus_document_prefers_freeform_path(session) is False

    rebind_session_to_focus_document(session, home=home)
    assert str((session.freeform or {}).get("writing_path") or "") != "freeform"
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("step") or "") == "upload"


def test_switch_docs_restores_template_wizard_lane(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="lanes_sw", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_named_doc(home, session, tid="w_doc_a", title="环境报告")
    key_a = focus_document_key(session)
    flush_lane(session)

    begin_document_onboarding(
        session, home, message="新开", title_hint="模版文档", force_new=True
    )
    shell = ensure_named_freeform_document(
        session, home, title="模版文档", confirm_name=True
    )
    mark_focus_entry_path(session, "template")
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "upload",
            "blueprint_id": shell,
            "working_task_id": shell,
            "mode": "create",
        },
    }
    session.freeform.pop("writing_path", None)
    flush_lane(session)
    key_b = focus_document_key(session)

    apply_focus_document_by_key(session, key_a, home=home, announce=False)
    assert str(session.task_id or "") == "w_doc_a"
    apply_focus_document_by_key(session, key_b, home=home, announce=False)
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("step") or "") == "upload"
    assert str(wiz.get("blueprint_id") or "") == shell
    assert str((session.freeform or {}).get("writing_path") or "") != "freeform"
    assert str(session.task_id or "") == shell


def test_old_outline_confirmed_does_not_allow_new_doc_write(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="lanes_gate", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_doc(home, session, tid="w_old", title="环境报告", outline_confirmed=True)

    begin_document_onboarding(
        session, home, message="再写一份述职报告", title_hint="述职报告", force_new=True
    )
    hit = execute_dispatch_tool(
        home,
        session,
        {"tool": "start_writing", "args": {}},
        "开始写作",
    )
    if hit is not None:
        reply, cards, _ = hit
        assert cards or any(
            k in (reply or "") for k in ("选", "名", "路", "目录", "题目", "蓝本")
        )
    regen = execute_dispatch_tool(
        home,
        session,
        {"tool": "submit_outline", "args": {"regenerate": True, "title": "述职报告"}},
        "生成目录",
    )
    assert regen is not None
    reply, cards, _ = regen
    assert cards or any(
        k in (reply or "") for k in ("选", "名", "路", "目录", "题目", "蓝本", "大纲")
    )
    assert session.freeform.get("outline_confirmed") is not True
