"""会话写作目标：口语信号走意图模型（与 session_goal 存储/合并逻辑分离）。"""

from __future__ import annotations

from typing import Any, Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]


def is_explicit_remember_goal_message(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    from report_writing.chat.intent_context import pack_classifier_context
    from report_writing.chat.intent_llm import classify_explicit_remember_goal_llm

    ctx = pack_classifier_context(session) if session is not None else {}
    return classify_explicit_remember_goal_llm(
        message,
        dialogue_tail=list(ctx.get("dialogue_tail") or []) or None,
        invoke=invoke,
    )


def doc_meta_patch_from_message(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, str]:
    from report_writing.chat.intent_context import pack_classifier_context
    from report_writing.chat.intent_llm import classify_doc_meta_line_llm

    ctx = dict(context or {})
    if session is not None and not ctx:
        ctx = pack_classifier_context(session)
    return classify_doc_meta_line_llm(
        message,
        dialogue_tail=list(ctx.get("dialogue_tail") or []) or None,
        focus_document_brief=str(ctx.get("focus_document_brief") or ""),
        invoke=invoke,
    )


def prefer_react_over_freeform_dispatch(
    message: str,
    *,
    phase: str = "",
    invoke: InvokeFn | None = None,
) -> bool:
    from report_writing.chat.intent_llm import classify_freeform_dispatch_route_llm

    # 无 session 时仅用 phase；有上下文的调用方走 session_goal.prefer_*
    return classify_freeform_dispatch_route_llm(message, phase=phase, invoke=invoke) == "react"
