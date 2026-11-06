"""定名后已声明自动大纲时，勿再挂模版 vs 自动生成大纲点选卡。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.clarify_flow import (
    build_entry_path_clarify,
    execute_clarify_spec,
    get_pending,
)
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    focus_needs_freeform_mode,
    focus_onboarding,
    is_writing_document_named,
    mark_focus_entry_path,
    mark_focus_freeform_mode,
    peek_pending_entry,
    stash_focus_preferred_entry_path,
    stash_pending_entry,
)
from report_writing.domain.models import ChatPhase, ChatSession


FULL_LINE = "名字叫做lzb的述职报告 你帮我生成大纲"


def test_name_plus_generate_outline_skips_freeform_mode_card(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ol1", phase=ChatPhase.INTENT.value)
    stash_pending_entry(session, message=FULL_LINE, title="lzb的述职报告")
    confirm_writing_document_name(
        session, "lzb的述职报告", home=tmp_path, message=FULL_LINE
    )
    reply, cards = build_entry_path_clarify(
        session,
        message=FULL_LINE,
        title_hint="lzb的述职报告",
        invoke=None,
        home=tmp_path,
        after_doc_name_confirm=True,
        apply_plan=False,
    )
    pending = get_pending(session) or {}
    assert pending.get("source") != "entry_freeform_mode"
    assert not focus_needs_freeform_mode(session)
    assert focus_onboarding(session).get("entry_path") == "freeform"
    assert "模版" not in (reply or "") or "自动生成大纲" not in (reply or "")
    # 不应再出现二选一点选卡
    clarify_cards = [c for c in cards if getattr(c, "type", "") == "intent_clarify"]
    for card in clarify_cards:
        payload = dict(getattr(card, "payload", None) or {})
        assert payload.get("source") != "entry_freeform_mode"


def test_typed_name_confirm_keeps_pending_full_line(tmp_path: Path):
    """用户只回名字时，pending_entry 仍持有首句 → 续跑应跳过模版卡。"""
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ol2", phase=ChatPhase.INTENT.value)
    stash_pending_entry(session, message=FULL_LINE, title="")
    hit = {
        "source": "doc_name",
        "candidate": {
            "kind": "doc_name",
            "name": "confirm_document_name",
            "payload": {"message": FULL_LINE, "title": ""},
        },
        "slots": {"title": "lzb的述职报告", "doc_name_action": "apply"},
        "option_id": "typed_doc_name",
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="lzb的述职报告", invoke=None
    )
    assert is_writing_document_named(session)
    pending = get_pending(session) or {}
    assert pending.get("source") != "entry_freeform_mode"
    assert not focus_needs_freeform_mode(session)
    assert focus_onboarding(session).get("entry_path") == "freeform"
    kept = peek_pending_entry(session)
    assert "生成大纲" in str(kept.get("message") or "") or focus_onboarding(session).get(
        "entry_path"
    ) == "freeform"
    assert "好，文档就叫" in (reply or "")


def test_without_blueprint_with_stashed_freeform_skips_second_card(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ol3", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "lzb的述职报告", home=tmp_path)
    stash_focus_preferred_entry_path(session, "freeform", residual=FULL_LINE)
    stash_pending_entry(session, message=FULL_LINE, title="lzb的述职报告")
    hit = {
        "source": "entry_branch",
        "option_id": "without_blueprint",
        "slots": {"entry_branch": "without_blueprint"},
        "candidate": {
            "kind": "entry_branch",
            "name": "entry_branch",
            "payload": {"message": FULL_LINE, "title": "lzb的述职报告"},
        },
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="无蓝本", invoke=None
    )
    pending = get_pending(session) or {}
    assert pending.get("source") != "entry_freeform_mode"
    assert focus_onboarding(session).get("entry_path") == "freeform"
    clarify = [c for c in cards if getattr(c, "type", "") == "intent_clarify"]
    assert not any(
        (getattr(c, "payload", None) or {}).get("source") == "entry_freeform_mode"
        for c in clarify
    )


def test_mark_freeform_auto_outline_skips_mode_gate(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ol4", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "述职", home=tmp_path)
    mark_focus_entry_path(session, "freeform")
    mark_focus_freeform_mode(session, "auto_outline")
    assert focus_onboarding(session).get("entry_path") == "freeform"
    assert not focus_needs_freeform_mode(session)
