"""会话缺口只读观察：写入 snapshot，供 Agent user JSON 阅读。

不驱动 Tool 选择，不替代 create_agent。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatPhase, ChatSession

InvokeFn = Callable[[str, str], str] | None

# 允许的缺口名（观察层 vocabulary；与 clarify / Tool 前置槽一致）
_CANON_MISSING = (
    "doc_name",
    "writing_path",
    "freeform_mode",
    "blueprint_id",
    "chapter_key",
    "element_id",
    "pack_id",
)


@dataclass
class SessionGaps:
    phase: str = ""
    declared: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    suggested_tools: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    context_line: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "declared": dict(self.declared or {}),
            "missing": list(self.missing or []),
            "suggested_tools": list(self.suggested_tools or []),
            "blockers": list(self.blockers or []),
            "context_line": self.context_line,
        }

    def to_event(self) -> dict[str, Any]:
        body = self.to_dict()
        return {"type": "session_gaps", **body}


def _dedupe_missing(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for key in items:
        k = (key or "").strip()
        if not k or k not in _CANON_MISSING or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def _build_context_line(
    *,
    phase: str,
    missing: list[str],
    blockers: list[str],
    declared: dict[str, Any],
) -> str:
    miss = ",".join(missing) if missing else "—"
    block = ",".join(blockers) if blockers else "—"
    decl_bits = []
    for k in ("doc_name", "writing_path", "freeform_mode", "blueprint_id"):
        v = str((declared or {}).get(k) or "").strip()
        if v:
            decl_bits.append(f"{k}={v[:40]}")
    decl = "; ".join(decl_bits) if decl_bits else "—"
    return f"phase={phase or '—'}; 规则仍缺={miss}; 门闩={block}; 本句已声明={decl}"


def _intent_missing_from_focus(session: ChatSession) -> list[str]:
    """与 focus_needs_* 一致，避免观察层比执行层更「贪」。"""
    from report_writing.chat.session_documents import onboarding_lane_active

    if not onboarding_lane_active(session):
        return []
    # 定名/选路不再作为会话级 missing；由 Tool 能力检查按需追问
    return []


def _declared_from_message(session: ChatSession, message: str, *, invoke=None) -> dict[str, Any]:
    from report_writing.chat.session_documents import (
        explicit_doc_title_from_message,
        message_declares_document_title,
        preferred_entry_path_resolved,
        user_wants_existing_blueprint_match,
    )

    msg = (message or "").strip()
    declared: dict[str, Any] = {}
    title = message_declares_document_title(msg, "") or explicit_doc_title_from_message(msg)
    if title:
        declared["doc_name"] = title
    else:
        from report_writing.chat.session_documents import (
            focus_working_title,
            quick_title_from_write_message,
        )

        working = focus_working_title(session) or quick_title_from_write_message(msg)
        if working:
            declared["title_working"] = working
    ep = preferred_entry_path_resolved(session, msg, invoke=invoke)
    if ep:
        declared["writing_path"] = ep
    elif user_wants_existing_blueprint_match(msg, session=session, invoke=invoke):
        declared["writing_path"] = "match"
    return declared


def _blockers_for_session(session: ChatSession) -> list[str]:
    from report_writing.chat.session_documents import (
        focus_needs_doc_name,
        is_writing_document_named,
    )

    blockers: list[str] = []
    from report_writing.chat.session_documents import focus_working_title

    if focus_needs_doc_name(session) and not focus_working_title(session):
        blockers.append("awaiting_doc_name_confirm")
    elif not is_writing_document_named(session):
        blockers.append("formal_name_unconfirmed")
    return blockers


def observe_intent_gaps(
    session: ChatSession,
    message: str = "",
    *,
    home: Path | None = None,
    invoke: InvokeFn = None,
) -> SessionGaps:
    del home
    from report_writing.chat.session_documents import (
        observe_new_document_gaps,
        suggest_next_tools_from_gaps,
    )

    msg = (message or "").strip()
    declared = _declared_from_message(session, msg, invoke=invoke)
    obs = observe_new_document_gaps(session, msg, invoke=invoke)
    missing = _intent_missing_from_focus(session)
    suggested = suggest_next_tools_from_gaps(
        obs,
        message=msg,
        title=str(declared.get("doc_name") or (session.freeform or {}).get("title") or ""),
        session=session,
    )
    blockers = _blockers_for_session(session)
    phase = str(session.phase or ChatPhase.INTENT.value)
    ctx = _build_context_line(
        phase=phase, missing=missing, blockers=blockers, declared=declared
    )
    return SessionGaps(
        phase=phase,
        declared=declared,
        missing=missing,
        suggested_tools=suggested,
        blockers=blockers,
        context_line=ctx,
    )


def observe_blueprint_select_gaps(
    session: ChatSession,
    message: str = "",
    *,
    home: Path | None = None,
    invoke: InvokeFn = None,
) -> SessionGaps:
    del home, invoke
    phase = str(session.phase or ChatPhase.BLUEPRINT_SELECT.value)
    declared = _declared_from_message(session, message)
    missing: list[str] = []
    if not str(getattr(session, "blueprint_id", "") or "").strip():
        missing.append("blueprint_id")
    missing = _dedupe_missing(missing + _intent_missing_from_focus(session))
    blockers = _blockers_for_session(session)
    ctx = _build_context_line(
        phase=phase, missing=missing, blockers=blockers, declared=declared
    )
    return SessionGaps(
        phase=phase,
        declared=declared,
        missing=missing,
        suggested_tools=[],
        blockers=blockers,
        context_line=ctx,
    )


def observe_handoff_gaps(
    session: ChatSession,
    message: str = "",
    *,
    home: Path | None = None,
    invoke: InvokeFn = None,
) -> SessionGaps:
    del message, home, invoke
    phase = str(session.phase or ChatPhase.CREATE_HANDOFF.value)
    declared: dict[str, Any] = {}
    missing = _dedupe_missing(_intent_missing_from_focus(session))
    blockers = _blockers_for_session(session)
    ctx = _build_context_line(
        phase=phase, missing=missing, blockers=blockers, declared=declared
    )
    return SessionGaps(
        phase=phase,
        declared=declared,
        missing=missing,
        suggested_tools=[],
        blockers=blockers,
        context_line=ctx,
    )


def observe_freeform_gaps(
    session: ChatSession,
    message: str = "",
    *,
    home: Path | None = None,
    invoke: InvokeFn = None,
) -> SessionGaps:
    del invoke
    ff = dict(session.freeform or {})
    phase = str(session.phase or ChatPhase.FREEFORM_ASK.value)
    declared = _declared_from_message(session, message)
    missing = _dedupe_missing(_intent_missing_from_focus(session))
    if phase == ChatPhase.FREEFORM_OUTLINE.value:
        has_outline = bool(str(ff.get("outline_text") or "").strip() or ff.get("outline_pending_edit"))
        if not has_outline and not ff.get("outline_confirmed"):
            if "writing_path" not in missing and not missing:
                missing.append("writing_path")
    blockers = _blockers_for_session(session)
    ctx = _build_context_line(
        phase=phase, missing=missing, blockers=blockers, declared=declared
    )
    return SessionGaps(
        phase=phase,
        declared=declared,
        missing=missing,
        suggested_tools=[],
        blockers=blockers,
        context_line=ctx,
    )


def observe_writing_gaps(
    session: ChatSession,
    message: str = "",
    *,
    home: Path | None = None,
    invoke: InvokeFn = None,
) -> SessionGaps:
    del home, invoke
    phase = str(session.phase or ChatPhase.WRITING.value)
    declared = _declared_from_message(session, message)
    missing: list[str] = []
    if not str(getattr(session, "task_id", "") or "").strip():
        missing.append("chapter_key")
    missing = _dedupe_missing(missing)
    blockers = _blockers_for_session(session)
    ctx = _build_context_line(
        phase=phase, missing=missing, blockers=blockers, declared=declared
    )
    return SessionGaps(
        phase=phase,
        declared=declared,
        missing=missing,
        suggested_tools=[],
        blockers=blockers,
        context_line=ctx,
    )


PHASE_GAP_OBSERVERS: dict[str, Callable[..., SessionGaps]] = {
    ChatPhase.INTENT.value: observe_intent_gaps,
    ChatPhase.BLUEPRINT_SELECT.value: observe_blueprint_select_gaps,
    ChatPhase.CREATE_HANDOFF.value: observe_handoff_gaps,
    ChatPhase.FREEFORM_ASK.value: observe_freeform_gaps,
    ChatPhase.FREEFORM_OUTLINE.value: observe_freeform_gaps,
    ChatPhase.FREEFORM_POINTS.value: observe_freeform_gaps,
    ChatPhase.READY_TO_WRITE.value: observe_handoff_gaps,
    ChatPhase.WRITING.value: observe_writing_gaps,
    ChatPhase.CONFIRM_EXPORT.value: observe_writing_gaps,
}


def observe_session_gaps(
    session: ChatSession,
    message: str = "",
    *,
    home: Path | None = None,
    invoke: InvokeFn = None,
) -> SessionGaps:
    phase = str(session.phase or ChatPhase.INTENT.value).strip() or ChatPhase.INTENT.value
    fn = PHASE_GAP_OBSERVERS.get(phase) or observe_intent_gaps
    return fn(session, message, home=home, invoke=invoke)


def refresh_session_gaps(
    session: ChatSession,
    gaps: SessionGaps | None = None,
    *,
    message: str = "",
    home: Path | None = None,
    invoke: InvokeFn = None,
) -> SessionGaps:
    """写入 freeform.session_gaps_snapshot（Phase 0 不驱动执行）。"""
    snap = gaps or observe_session_gaps(session, message, home=home, invoke=invoke)
    ff = dict(session.freeform or {})
    ff["session_gaps_snapshot"] = snap.to_dict()
    if snap.phase in {ChatPhase.INTENT.value, ChatPhase.BLUEPRINT_SELECT.value}:
        from report_writing.chat.session_documents import observe_new_document_gaps

        raw = observe_new_document_gaps(session, message, invoke=invoke)
        ff["onboarding_gaps_snapshot"] = dict(raw)
    session.freeform = ff
    return snap
