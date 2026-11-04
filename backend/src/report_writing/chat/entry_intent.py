"""入口口语意图：统一走 intent_llm（无 intent_router 正则表）。"""

from __future__ import annotations

from typing import Any, Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]


def _entry(
    message: str,
    *,
    invoke: InvokeFn | None,
    session: ChatSession | None = None,
) -> dict[str, Any]:
    from report_writing.chat.intent_context import pack_classifier_context
    from report_writing.chat.intent_llm import classify_entry_routing_llm

    ctx = pack_classifier_context(session) if session else {}
    return classify_entry_routing_llm(
        message,
        phase=str(ctx.get("phase") or (session.phase if session else "") or ""),
        session_goal_brief=str(ctx.get("session_goal_brief") or ""),
        dialogue_tail=list(ctx.get("dialogue_tail") or []),
        focus_document_brief=str(ctx.get("focus_document_brief") or ""),
        session_documents_brief=str(ctx.get("session_documents_brief") or ""),
        invoke=invoke,
    )


def is_skip_blueprint_intent(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    hit = _entry(message, invoke=invoke, session=session)
    return hit.get("intent") in {"skip_blueprint", "path_correction"}


def user_wants_path_correction_to_freeform(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    if is_skip_blueprint_intent(message, invoke=invoke, session=session):
        return True
    hit = _entry(message, invoke=invoke, session=session)
    return hit.get("intent") == "path_correction"


def is_create_blueprint_intent(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    t = (message or "").strip()
    if t and any(k in t for k in ("模版", "模板", "参考模版", "参考模板", "按模版", "按模板", "我有模版", "我有模板")):
        return True
    return _entry(message, invoke=invoke, session=session).get("intent") == "create_blueprint"


def is_save_freeform_blueprint_intent(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    return _entry(message, invoke=invoke, session=session).get("intent") == "save_freeform_blueprint"


def extract_wizard_title_hint(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> str:
    hit = _entry(message, invoke=invoke, session=session)
    if hit.get("intent") == "create_blueprint":
        return str(hit.get("blueprint_title") or "").strip()
    return ""


def message_explicitly_requests_blueprint(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    if is_skip_blueprint_intent(message, invoke=invoke, session=session):
        return False
    hit = _entry(message, invoke=invoke, session=session)
    return hit.get("intent") in {"match_blueprints", "explicit_select"}


def message_requests_blueprint_match(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    """是否应先 match_blueprints（含泛泛写报告）。"""
    if is_skip_blueprint_intent(message, invoke=invoke, session=session):
        return False
    hit = _entry(message, invoke=invoke, session=session)
    return hit.get("intent") in {"match_blueprints", "explicit_select", "general_write"}


def allows_agent_blueprint_select(
    *,
    message: str,
    user_action: str,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    action = (user_action or "").strip()
    if action == "select_blueprint":
        return True
    if action:
        return False
    hit = _entry(message, invoke=invoke, session=session)
    if hit.get("is_vague_write"):
        return False
    if hit.get("intent") == "explicit_select" and hit.get("agent_may_select"):
        return True
    return False


def pick_auto_blueprint_id(
    message: str,
    matches: list,
    *,
    invoke: InvokeFn | None = None,
) -> str:
    from report_writing.chat.intent_llm import pick_blueprint_id_llm

    return pick_blueprint_id_llm(message, matches, invoke=invoke)


def infer_freeform_meta_from_messages(
    session: ChatSession,
    extra: str = "",
    *,
    invoke=None,
) -> tuple[str, str] | None:
    from report_writing.chat.session_goal import (
        freeform_meta_from_session,
        infer_freeform_meta_from_dialogue,
    )

    hit = freeform_meta_from_session(session)
    if hit:
        return hit
    if invoke is not None:
        return infer_freeform_meta_from_dialogue(session, invoke, extra=extra)
    return None
