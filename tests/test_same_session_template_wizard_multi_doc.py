"""同会话多文档 · 依据模版创建。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.orchestrator import handle_chat
from report_writing.chat.resource_actions import (
    _action_blueprint_wizard,
    _begin_blueprint_wizard,
)
from report_writing.chat.session_documents import (
    begin_document_onboarding,
    confirm_writing_document_name,
    ensure_named_freeform_document,
    focus_document_key,
    upsert_session_document,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatRequest, ChatSession
from report_writing.services.chat_session import get_session, save_session
from report_writing.services.store import TaskStore


def _seed_doc1_with_sample(home: Path, session: ChatSession, *, tid: str = "w_doc1") -> None:
    path = home / "tasks" / tid
    ensure_task(path, biz_type="002")
    store = TaskStore(path)
    task = store.load()
    task.title = "环境报告"
    task.sample_ready = True
    store.save(task)
    upsert_session_document(
        session,
        kind="outline",
        task_id=tid,
        writing_task_id=tid,
        title="环境报告",
        clear_blueprint_id=True,
    )
    session.task_id = tid
    session.freeform = {
        **(session.freeform or {}),
        "focus_document_key": f"task:{tid}",
        "writing_doc_named": True,
        "meta_confirmed": True,
        "title": "环境报告",
    }


def test_force_new_begin_wizard_preserves_new_shell_not_doc1(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="multi_tpl_1", phase=ChatPhase.INTENT.value)
    _seed_doc1_with_sample(home, session)

    begin_document_onboarding(
        session, home, message="新开一份环境报告", title_hint="环境报告", force_new=True
    )
    confirm_writing_document_name(session, "环境报告", home=home, message="新开一份环境报告")
    w2 = ensure_named_freeform_document(
        session, home, title="环境报告", confirm_name=False
    )
    assert w2 and w2 != "w_doc1"
    focus_before = focus_document_key(session)

    _begin_blueprint_wizard(
        session,
        title_hint="环境报告",
        home=home,
        force_new=True,
        user_message="依据模版编写",
    )
    assert str(session.task_id or "") == w2
    assert focus_document_key(session) == focus_before == f"task:{w2}"
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("fresh_meta") and str(wiz.get("step") or "") == "meta"
    assert str(wiz.get("working_task_id") or "") == w2


def test_meta_submit_after_force_new_yields_upload_not_continue(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="multi_tpl_2", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_doc1_with_sample(home, session)

    begin_document_onboarding(
        session, home, message="新开一份环境报告", title_hint="环境报告", force_new=True
    )
    shell = ensure_named_freeform_document(
        session, home, title="环境报告", confirm_name=False
    )
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "meta",
            "mode": "create",
            "fresh_meta": True,
            "title": "环境报告",
            "working_task_id": shell,
        },
        "plan": {"goal": "template_wizard", "step": "wizard_meta"},
    }

    reply, cards = _action_blueprint_wizard(
        home,
        session,
        {"step": "meta", "title": "环境报告"},
    )
    assert "已在进行" not in (reply or "")
    assert "上传" in (reply or "") or "样本" in (reply or "")
    wiz_cards = [c for c in cards if c.type == "blueprint_wizard"]
    assert wiz_cards and wiz_cards[0].payload.get("step") == "upload"
    bid = str(wiz_cards[0].payload.get("blueprint_id") or "")
    assert bid.startswith("w") and bid != "w_doc1"
    assert bid == shell
    assert focus_document_key(session) == f"task:{bid}"


def test_begin_with_fresh_meta_does_not_match_doc1(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="multi_tpl_3", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_doc1_with_sample(home, session)
    shell = ensure_named_freeform_document(
        session, home, title="环境报告", confirm_name=False
    )
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "meta",
            "mode": "create",
            "fresh_meta": True,
            "title": "环境报告",
            "working_task_id": shell,
        },
        "plan": {"goal": "template_wizard", "step": "wizard_meta"},
        "document_thread_since_index": 0,
    }
    session.task_id = shell

    reply, cards = _begin_blueprint_wizard(
        session,
        title_hint="环境报告",
        home=home,
        force_new=False,
        user_message="创建蓝本「环境报告」",
    )
    assert "已切换到" not in (reply or "")
    assert "已在进行" not in (reply or "") or any(
        c.type == "blueprint_wizard" and c.payload.get("step") == "upload" for c in cards
    )


def test_handle_chat_meta_action_upload_card_multi_doc(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="multi_tpl_hc", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_doc1_with_sample(home, session)
    begin_document_onboarding(
        session, home, message="新开一份环境报告", title_hint="环境报告", force_new=True
    )
    shell = ensure_named_freeform_document(
        session, home, title="环境报告", confirm_name=False
    )
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "meta",
            "mode": "create",
            "fresh_meta": True,
            "title": "环境报告",
            "working_task_id": shell,
        },
        "plan": {"goal": "template_wizard", "step": "wizard_meta"},
    }
    session.task_id = shell
    save_session(home, session)

    resp = handle_chat(
        home,
        ChatRequest(
            session_id="multi_tpl_hc",
            action="blueprint_wizard",
            action_payload={"step": "meta", "title": "环境报告"},
        ),
        invoke=lambda _s, _u: "{}",
    )
    assert "已在进行" not in (resp.reply or "")
    wiz = [c for c in (resp.cards or []) if c.type == "blueprint_wizard"]
    assert wiz and wiz[0].payload.get("step") == "upload"
    loaded = get_session(home, "multi_tpl_hc")
    assert str(loaded.task_id or "") == shell
    assert str(loaded.task_id or "") != "w_doc1"


def test_focus_switch_restores_template_wizard_snapshot(tmp_path: Path):
    from report_writing.chat.session_documents import (
        apply_focus_document_by_key,
        flush_lane,
        focus_document_key,
        snapshot_focus_document_state,
    )

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="snap1", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_doc1_with_sample(home, session)
    begin_document_onboarding(
        session, home, message="新开", title_hint="环境报告", force_new=True
    )
    shell = ensure_named_freeform_document(
        session, home, title="环境报告", confirm_name=False
    )
    from report_writing.chat.session_documents import mark_focus_entry_path

    mark_focus_entry_path(session, "template")
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "upload",
            "blueprint_id": shell,
            "title": "环境报告",
            "mode": "create",
        },
    }
    flush_lane(session)
    snapshot_focus_document_state(session)
    old_key = focus_document_key(session)
    apply_focus_document_by_key(session, "task:w_doc1", home=home, announce=False)
    apply_focus_document_by_key(session, old_key, home=home, announce=False)
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("step") or "") == "upload"
    assert str(wiz.get("blueprint_id") or "") == shell


def test_ensure_named_keeps_active_template_wizard(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="keep_wiz", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_doc1_with_sample(home, session)
    begin_document_onboarding(
        session, home, message="新开", title_hint="环境报告2", force_new=True
    )
    shell = ensure_named_freeform_document(
        session, home, title="环境报告2", confirm_name=False
    )
    from report_writing.chat.session_documents import mark_focus_entry_path

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
    ensure_named_freeform_document(session, home, title="环境报告2", confirm_name=True)
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("step") or "") == "upload"
    assert str(wiz.get("mode") or "") == "create"


def test_begin_wizard_force_new_leaves_old_bp_focus(tmp_path: Path):
    """已定名 bp 文档上开模版向导：焦点立刻落到新 w*，不回旧 bp。"""
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="focus_switch_tpl", phase=ChatPhase.READY_TO_WRITE.value)
    bid = "bp202611022054023656"
    path = home / "tasks" / bid
    ensure_task(path)
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
        "meta_confirmed": True,
        "title": "安全报告",
    }
    confirm_writing_document_name(session, "安全报告", home=home, message="名字叫我的安全报告")
    old_key = focus_document_key(session)
    assert old_key == f"bp:{bid}"
    save_session(home, session)

    captured: list[dict] = []
    resp = handle_chat(
        home,
        ChatRequest(
            session_id="focus_switch_tpl",
            action="begin_blueprint_wizard",
            message="再根据我的模版帮我写一份环境报告",
            action_payload={
                "explicit_create": True,
                "force_new": True,
                "title_hint": "环境报告",
            },
        ),
        invoke=lambda _s, _u: "{}",
        emit=lambda ev: captured.append(ev) if isinstance(ev, dict) else None,
    )
    loaded = get_session(home, "focus_switch_tpl")
    after_key = focus_document_key(loaded)
    assert after_key and after_key != old_key, after_key
    assert str(after_key).startswith("task:w"), after_key
    wiz = [c for c in (resp.cards or []) if c.type == "blueprint_wizard"]
    assert wiz and str(wiz[0].payload.get("step") or "") == "meta"
    last_user = next(
        (m for m in reversed(list(loaded.messages or [])) if m.role == "user"),
        None,
    )
    last_asst = next(
        (m for m in reversed(list(loaded.messages or [])) if m.role == "assistant"),
        None,
    )
    assert last_user and last_user.document_key == after_key
    assert last_asst and last_asst.document_key == after_key
    focus_evs = [
        e
        for e in list(resp.events or []) + captured
        if isinstance(e, dict) and e.get("type") == "focus_document"
    ]
    assert any(str(e.get("focus_document_key") or "") == after_key for e in focus_evs)


def test_unnamed_new_doc_template_returns_wizard_not_doc_name(tmp_path: Path):
    """同会话已有文档后再说「用我的模版」：必须带 blueprint_wizard，不能只回定名澄清。"""
    from report_writing.chat.clarify_flow import build_entry_path_clarify, get_pending

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="tpl_wiz_card", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_doc1_with_sample(home, session)
    begin_document_onboarding(
        session,
        home,
        message="再根据我的模版帮我写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    reply, cards = build_entry_path_clarify(
        session,
        message="再根据我的模版帮我写一份环境报告",
        title_hint="环境报告",
        home=home,
        invoke=lambda _s, _u: "{}",
    )
    wiz = [c for c in (cards or []) if getattr(c, "type", "") == "blueprint_wizard"]
    suggested = [
        str(t.get("tool") or "")
        for t in (session.freeform or {}).get("suggested_next_tools") or []
    ]
    assert not wiz
    assert "begin_blueprint_wizard" in suggested, reply
    pending = get_pending(session) or {}
    assert pending.get("source") != "doc_name"
    assert focus_document_key(session) != "task:w_doc1"


def test_user_chose_skip_false_when_focus_template_after_freeform_doc(tmp_path: Path):
    from report_writing.chat.session_state import user_chose_skip_blueprint

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="skip_tpl", phase=ChatPhase.FREEFORM_OUTLINE.value)
    _seed_doc1_with_sample(home, session)
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "freeform",
        "plan": {"goal": "skip_blueprint"},
    }
    begin_document_onboarding(
        session, home, message="新开", title_hint="环境报告2", force_new=True
    )
    from report_writing.chat.session_documents import mark_focus_entry_path

    mark_focus_entry_path(session, "template")
    assert not user_chose_skip_blueprint(session)


def test_handle_chat_begin_wizard_keeps_card_when_prev_wizard_exists(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="multi_tpl_keep_card", phase=ChatPhase.CREATE_HANDOFF.value)
    _seed_doc1_with_sample(home, session)
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "upload",
            "blueprint_id": "w_doc1",
            "mode": "create",
        },
        "writing_doc_named": True,
        "title": "环境报告",
    }
    save_session(home, session)

    resp = handle_chat(
        home,
        ChatRequest(
            session_id="multi_tpl_keep_card",
            action="begin_blueprint_wizard",
            action_payload={"explicit_create": True, "force_new": True},
        ),
        invoke=lambda _s, _u: "{}",
    )
    wiz = [c for c in (resp.cards or []) if c.type == "blueprint_wizard"]
    assert wiz, resp.reply
    assert str(wiz[0].payload.get("step") or "") in {"meta", "upload"}
    assert str(wiz[0].payload.get("mode") or "") in {"create", "freeform_sample", ""}


def test_meta_renames_unnamed_shell_no_second_document(tmp_path: Path):
    """依据模版：force_new 空壳「未命名」→ meta 定名须复用同一 w*，勿留第二个空文档。"""
    from report_writing.chat.session_documents import session_documents_map

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="tpl_unnamed_meta", phase=ChatPhase.INTENT.value)
    _begin_blueprint_wizard(
        session,
        title_hint="",
        home=home,
        force_new=True,
        user_message="依据模版编写",
    )
    shell = str(getattr(session, "task_id", "") or "").strip()
    assert shell.startswith("w")
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("working_task_id") or "") == shell

    _action_blueprint_wizard(
        home,
        session,
        {"step": "meta", "title": "我的环境报告"},
    )
    bid = str(session.task_id or "").strip()
    assert bid == shell
    docs = session_documents_map(session)
    task_keys = [k for k in docs if str(k).startswith("task:w")]
    assert len(task_keys) == 1, task_keys
    assert str(docs[task_keys[0]].get("title") or "") == "我的环境报告"

