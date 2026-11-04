"""Session Intent Loop Phase 0：只读 observe / refresh，不改执行路径。"""

from __future__ import annotations

from report_writing.chat.session_documents import (
    focus_needs_doc_name,
    patch_focus_onboarding,
    session_blueprint_already_selected,
)
from report_writing.chat.session_gaps import (
    observe_session_gaps,
    refresh_session_gaps,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_observe_vague_write_report_missing_name_and_path():
    session = ChatSession(id="s_gaps_vague", phase=ChatPhase.INTENT.value)
    gaps = observe_session_gaps(session, "帮我写一份安全报告")
    # lazy shell：无 lane 时执行层 missing 为空；观察层仍记录 declared 写意图
    assert gaps.missing == []
    from report_writing.chat.session_documents import observe_new_document_gaps

    obs = observe_new_document_gaps(session, "帮我写一份安全报告")
    assert obs.get("missing_name") is True
    assert "doc_name" not in (gaps.declared or {}) or gaps.declared.get("doc_name") != "安全报告"


def test_observe_explicit_name_only():
    session = ChatSession(id="s_gaps_name", phase=ChatPhase.INTENT.value)
    gaps = observe_session_gaps(session, "名字叫大晚上安全报告")
    assert gaps.declared.get("doc_name") == "大晚上安全报告"


def test_observe_match_intent_declared_path():
    session = ChatSession(id="s_gaps_match", phase=ChatPhase.INTENT.value)
    gaps = observe_session_gaps(session, "使用已有的蓝本帮我写一份安全报告")
    assert gaps.declared.get("writing_path") == "match"


def test_refresh_writes_snapshot():
    session = ChatSession(id="s_gaps_snap", phase=ChatPhase.INTENT.value)
    refresh_session_gaps(session, message="写个报告")
    snap = (session.freeform or {}).get("session_gaps_snapshot") or {}
    assert snap.get("phase") == ChatPhase.INTENT.value
    assert isinstance(snap.get("missing"), list)
    assert "context_line" in snap


def test_blueprint_bound_missing_aligns_with_focus_not_extra_match():
    session = ChatSession(id="s_gaps_bp", phase=ChatPhase.INTENT.value)
    session.blueprint_id = "bpTest01"
    patch_focus_onboarding(session, name_confirmed=False, entry_path="match", entry_branch="with_blueprint")
    gaps = observe_session_gaps(session, "名字叫我的文档")
    assert session_blueprint_already_selected(session)
    assert gaps.declared.get("doc_name") == "我的文档"
    suggested = [str(t.get("tool") or "") for t in gaps.suggested_tools]
    assert "match_blueprints" not in suggested


def test_blueprint_select_phase_missing_blueprint_id():
    session = ChatSession(id="s_gaps_sel", phase=ChatPhase.BLUEPRINT_SELECT.value)
    gaps = observe_session_gaps(session, "就选第一份")
    assert "blueprint_id" in gaps.missing
