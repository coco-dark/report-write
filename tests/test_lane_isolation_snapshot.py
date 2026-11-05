"""同会话多文档串台：快照形状回归（flush / reconcile / force_new / 向导卡）。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.clarify_flow import (
    _start_template_wizard_now,
    build_ask_intent_clarify,
    get_pending,
)
from report_writing.chat.orchestrator import handle_chat
from report_writing.chat.session_documents import (
    begin_document_onboarding,
    confirm_writing_document_name,
    document_lane,
    flush_lane,
    focus_document_key,
    resolve_document_scope,
    session_documents_map,
    project_focus_lane_to_session,
    upsert_session_document,
)
from report_writing.chat.session_runtime import (
    WORKFLOW_BLUEPRINT,
    reconcile_chat_session,
    workflow_set,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatRequest, ChatSession
from report_writing.services.chat_session import get_session, save_session
from report_writing.services.store import TaskStore


def _study_wizard(*, bid: str, writing_tid: str, title: str, n: int = 12) -> dict:
    return {
        "mode": "selected",
        "step": "study",
        "blueprint_id": bid,
        "working_task_id": writing_tid,
        "title": title,
        "chapter_count": n,
        "chapters": [{"key": f"c{i}", "title": f"第{i}章"} for i in range(n)],
    }


def _seed_named_selected(
    home: Path,
    session: ChatSession,
    *,
    bid: str = "bp202611022054023656",
    writing_tid: str = "w202611031132464522",
    title: str = "lzb安全报告",
) -> dict:
    (home / "tasks").mkdir(parents=True, exist_ok=True)
    for tid, ttitle in ((bid, title), (writing_tid, title)):
        path = home / "tasks" / tid
        ensure_task(path)
        store = TaskStore(path)
        task = store.load()
        task.title = ttitle
        store.save(task)
    upsert_session_document(
        session,
        kind="blueprint",
        task_id=bid,
        blueprint_id=bid,
        title=title,
    )
    wizard = _study_wizard(bid=bid, writing_tid=writing_tid, title=title)
    session.blueprint_id = bid
    session.task_id = writing_tid
    session.phase = ChatPhase.READY_TO_WRITE.value
    session.freeform = {
        **(session.freeform or {}),
        "focus_document_key": f"bp:{bid}",
        "writing_path": "blueprint",
        "writing_doc_named": True,
        "meta_confirmed": True,
        "title": title,
        "blueprint_wizard": wizard,
    }
    confirm_writing_document_name(session, title, home=home, message=f"名字叫{title}")
    workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
    flush_lane(session)
    return wizard


def test_flush_lane_refuses_foreign_selected_wizard(tmp_path: Path):
    session = ChatSession(id="flush_id", phase=ChatPhase.READY_TO_WRITE.value)
    old_wiz = _seed_named_selected(tmp_path, session)
    bid = "bp202611022054023656"
    begin_document_onboarding(
        session,
        tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    new_key = focus_document_key(session)
    assert new_key and new_key != f"bp:{bid}"
    session.blueprint_id = bid
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": dict(old_wiz),
        "writing_path": "blueprint",
        "writing_doc_named": True,
        "title": "环境报告",
    }
    flush_lane(session)
    lane = document_lane(session, new_key)
    wiz = dict(lane.get("blueprint_wizard") or {})
    assert str(wiz.get("step") or "") != "study"
    assert str(wiz.get("working_task_id") or "") != "w202611031132464522"
    assert int(wiz.get("chapter_count") or 0) != 12
    old_lane = document_lane(session, f"bp:{bid}")
    assert "环境报告" not in str(old_lane.get("title") or "")
    old_wiz_kept = dict(old_lane.get("blueprint_wizard") or {})
    assert str(old_wiz_kept.get("step") or "") == "study"


def test_reconcile_skips_leftover_blueprint_sync_on_unnamed_shell(tmp_path: Path):
    session = ChatSession(id="recon_id", phase=ChatPhase.READY_TO_WRITE.value)
    old_wiz = _seed_named_selected(tmp_path, session)
    begin_document_onboarding(
        session,
        tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    session.blueprint_id = "bp202611022054023656"
    workflow_set(session, WORKFLOW_BLUEPRINT, dict(old_wiz))
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": dict(old_wiz),
    }
    reconcile_chat_session(tmp_path, session)
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(wiz.get("mode") or "") != "selected"
    assert str(wiz.get("step") or "") != "study"
    assert str(session.blueprint_id or "") in {
        "",
        str(getattr(session, "task_id", "") or ""),
    }


def test_force_new_empties_singleton_projection(tmp_path: Path):
    session = ChatSession(id="empty_proj", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_selected(tmp_path, session)
    begin_document_onboarding(
        session,
        tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    ff = dict(session.freeform or {})
    assert not ff.get("blueprint_wizard")
    assert str(ff.get("writing_path") or "") in {"", "template"}
    assert not ff.get("writing_doc_named")
    assert str(session.blueprint_id or "") == ""
    assert str(ff.get("writing_path") or "") != "blueprint"


def test_template_start_does_not_continue_selected_study(tmp_path: Path):
    session = ChatSession(id="tpl_drop", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_selected(tmp_path, session)
    begin_document_onboarding(
        session,
        tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    reply, cards = _start_template_wizard_now(
        session,
        home=tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
    )
    wiz_cards = [c for c in cards if getattr(c, "type", "") == "blueprint_wizard"]
    assert wiz_cards, reply
    payload = dict(wiz_cards[0].payload or {})
    assert str(payload.get("step") or "") == "meta"
    assert str(payload.get("mode") or "") in {"create", "freeform_sample", ""}
    assert int(payload.get("chapter_count") or 0) != 12
    live = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(live.get("working_task_id") or "") != "w202611031132464522"


def test_named_template_topic_is_force_new_not_chapter_control(tmp_path: Path):
    session = ChatSession(id="scope_tpl", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_selected(tmp_path, session)
    msg = "根据我的模版给我一份环境报告"
    scope = resolve_document_scope(session, msg)
    assert scope.get("force_new") is True
    assert scope.get("action") == "force_new"

    reply, cards = build_ask_intent_clarify(
        session,
        message=msg,
        home=tmp_path,
        invoke=lambda _s, _u: "{}",
    )
    pending = get_pending(session) or {}
    assert pending.get("source") != "control"
    wiz = [c for c in cards if getattr(c, "type", "") == "blueprint_wizard"]
    clarify = [c for c in cards if getattr(c, "type", "") == "intent_clarify"]
    assert wiz, reply
    assert str(wiz[0].payload.get("step") or "") == "meta"
    assert not any(
        "开写" in str((c.payload or {}).get("question") or "")
        or "再分析" in str((c.payload or {}).get("question") or "")
        for c in clarify
    )


def test_force_new_twice_same_turn_mints_one_shell(tmp_path: Path):
    session = ChatSession(id="one_mint", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_selected(tmp_path, session)
    begin_document_onboarding(
        session,
        tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    first = str(getattr(session, "task_id", "") or "")
    begin_document_onboarding(
        session,
        tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    second = str(getattr(session, "task_id", "") or "")
    assert first and second == first
    shells = [
        k
        for k in session_documents_map(session)
        if str(k).startswith("task:w") and str(k) != "task:w202611031132464522"
    ]
    assert len(shells) == 1


def test_snapshot_handle_chat_new_template_doc_isolated(tmp_path: Path):
    """已选用蓝本 + 已定名后「再写一份环境报告」：新 w* 空/meta，旧 bp 不改题。"""
    sid = "snap_hc_" + tmp_path.name.replace("-", "")[-12:]
    session = ChatSession(id=sid, phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_selected(tmp_path, session)
    save_session(tmp_path, session)
    old_key = f"bp:bp202611022054023656"

    resp = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=sid,
            action="begin_blueprint_wizard",
            message="根据我的模版 再写一份环境报告",
            action_payload={
                "explicit_create": True,
                "force_new": True,
                "title_hint": "环境报告",
            },
        ),
        invoke=lambda _s, _u: "{}",
    )
    loaded = get_session(tmp_path, sid)
    after_key = focus_document_key(loaded)
    assert after_key and after_key != old_key
    assert str(after_key).startswith("task:w")
    lane = document_lane(loaded, after_key)
    wiz = dict(lane.get("blueprint_wizard") or {})
    live = dict((loaded.freeform or {}).get("blueprint_wizard") or {})
    assert str(after_key) != "task:w202611031132464522"
    for snap in (wiz, live):
        assert int(snap.get("chapter_count") or 0) != 12
        assert str(snap.get("working_task_id") or "") != "w202611031132464522"
        assert str(snap.get("blueprint_id") or "") != "bp202611022054023656"
        assert str(snap.get("step") or "") in {"", "meta"}
        assert str(snap.get("mode") or "") in {"", "create", "freeform_sample"}
    cards = list(resp.cards or [])
    assert any(c.type == "blueprint_wizard" for c in cards)
    assert not (
        any(c.type == "intent_clarify" for c in cards)
        and not any(c.type == "blueprint_wizard" for c in cards)
    )
    ff = dict(loaded.freeform or {})
    assert str(ff.get("writing_path") or "") != "blueprint"
    old_lane = document_lane(loaded, old_key)
    assert "环境报告" not in str(old_lane.get("title") or "")
    shells = [
        k
        for k in session_documents_map(loaded)
        if str(k).startswith("task:w") and str(k) != "task:w202611031132464522"
    ]
    assert len(shells) == 1


def test_project_focus_lane_hydrates_not_foreign_singleton(tmp_path: Path):
    session = ChatSession(id="proj_lane", phase=ChatPhase.READY_TO_WRITE.value)
    old_wiz = _seed_named_selected(tmp_path, session)
    old_key = focus_document_key(session)
    begin_document_onboarding(
        session,
        tmp_path,
        message="根据我的模版 再写一份环境报告",
        title_hint="环境报告",
        force_new=True,
    )
    new_key = focus_document_key(session)
    assert new_key and new_key != old_key
    session.blueprint_id = "bp202611022054023656"
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": dict(old_wiz),
        "writing_path": "blueprint",
        "writing_doc_named": True,
        "title": "环境报告",
    }
    project_focus_lane_to_session(session, home=tmp_path)
    live = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert str(live.get("step") or "") != "study"
    assert str(live.get("working_task_id") or "") != "w202611031132464522"
    lane = document_lane(session, new_key)
    lane_wiz = dict(lane.get("blueprint_wizard") or {})
    assert str(lane_wiz.get("step") or "") != "study"
    old_lane = document_lane(session, old_key)
    assert str((old_lane.get("blueprint_wizard") or {}).get("step") or "") == "study"
