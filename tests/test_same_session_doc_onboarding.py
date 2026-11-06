"""同会话新开文档：定名 → 选线路 → 多意图（模版）。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.clarify_flow import (
    build_entry_path_clarify,
    execute_clarify_spec,
    get_pending,
    try_handle_pending_message,
)
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    focus_needs_doc_name,
    focus_needs_entry_path,
    focus_onboarding,
    is_writing_document_named,
    upsert_session_document,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_second_doc_in_session_requires_name_card_first():
    session = ChatSession(id="multi1", phase=ChatPhase.FREEFORM_OUTLINE.value)
    confirm_writing_document_name(session, "没有bug的述职报告", message="写述职")
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "freeform",
        "meta_confirmed": True,
        "writing_doc_named": True,
    }
    upsert_session_document(
        session,
        kind="outline",
        task_id="w1",
        title="没有bug的述职报告",
        clear_blueprint_id=True,
    )
    session.task_id = "w1"

    reply, cards = build_entry_path_clarify(
        session, message="再帮我写一份环境报告", invoke=None
    )
    pending = get_pending(session) or {}
    assert pending.get("source") in {
        "entry_branch",
        "doc_name",
        "document_scope",
        "",
    } or is_writing_document_named(session) or cards or "环境报告" in (reply or "") or "入口" in (
        reply or ""
    )


def test_force_new_then_name_then_entry_pending(tmp_path: Path):
    from report_writing.chat.clarify_flow import build_document_name_clarify

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="multi2", phase=ChatPhase.INTENT.value)
    build_document_name_clarify(
        session, message="再写一份环境报告", title_hint="环境报告"
    )

    def _invoke(system: str, user: str) -> str:
        return json.dumps(
            {
                "decision": "apply_title",
                "option_id": "",
                "title": "没有bug的环境报告",
                "question": "",
                "confidence": 0.9,
            }
        )

    hit = try_handle_pending_message(session, "没有bug的环境报告", invoke=_invoke)
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="没有bug的环境报告"
    )
    assert is_writing_document_named(session)
    assert get_pending(session) and get_pending(session).get("source") == "entry_branch"
    assert not focus_needs_doc_name(session)
    assert focus_needs_entry_path(session)
    assert "第二步" in reply or "有蓝本" in reply or "第一步" in reply


def test_template_phrase_stashes_preferred_path():
    session = ChatSession(id="multi3", phase=ChatPhase.INTENT.value)

    def _scope(_s: str, _u: str) -> str:
        return json.dumps(
            {"action": "force_new", "title": "环境报告", "confidence": 0.9}
        )

    def _entry(_s: str, _u: str) -> str:
        if "入口意图" in _s or "create_blueprint" in _s:
            return json.dumps(
                {"intent": "create_blueprint", "confidence": 0.9, "blueprint_title": ""}
            )
        return json.dumps({"has_explicit_name": False, "title": "", "residual": "", "confidence": 0.5})

    def _invoke(system: str, user: str) -> str:
        if "文档作用域" in system:
            return _scope(system, user)
        if "入口意图" in system or "入口" in system:
            return _entry(system, user)
        return json.dumps({"title": "环境报告", "confidence": 0.8})

    build_entry_path_clarify(
        session,
        message="没有bug的环境报告，我有模版参考写",
        invoke=_invoke,
    )
    ob = focus_onboarding(session)
    assert ob.get("preferred_entry_path") == "template" or get_pending(session).get("source") in {
        "doc_name",
        "entry_branch",
        "entry_freeform_mode",
    }


def test_rename_keeps_entry_path_freeform_on_registry(tmp_path: Path):
    from report_writing.chat.clarify_flow import build_entry_path_clarify
    from report_writing.chat.session_documents import (
        focus_document_key,
        mark_focus_entry_branch,
        mark_focus_entry_path,
        mark_focus_freeform_mode,
    )

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ob_keep", phase=ChatPhase.INTENT.value)
    from report_writing.chat.session_documents import ensure_named_freeform_document

    ensure_named_freeform_document(session, tmp_path, title="述职报告", confirm_name=True)
    mark_focus_entry_branch(session, "without_blueprint")
    mark_focus_entry_path(session, "freeform")
    mark_focus_freeform_mode(session, "auto_outline")
    key = focus_document_key(session)
    assert key
    confirm_writing_document_name(
        session, "我的述职报告", home=tmp_path, message="叫做我的述职报告"
    )
    ob = focus_onboarding(session)
    assert str(ob.get("entry_path") or "") == "freeform"
    assert str(ob.get("entry_branch") or "") == "without_blueprint"
    reply, cards = build_entry_path_clarify(
        session,
        message="",
        title_hint="我的述职报告",
        home=tmp_path,
        after_doc_name_confirm=True,
        apply_plan=False,
    )
    assert "有蓝本" not in (reply or "") or "自动" in (reply or "") or not any(
        str((c.payload or {}).get("source") or "") == "entry_branch" for c in (cards or [])
    )

