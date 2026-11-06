"""会话写作目标：窄 JSON（记住目标 / 类型｜标题）。有 invoke 时不走短语表。"""

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
    from report_writing.chat.classifier_context import pack_classifier_context
    from report_writing.chat.clarify_llm import classify_explicit_remember_goal_llm

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
    from report_writing.chat.classifier_context import pack_classifier_context
    from report_writing.chat.clarify_llm import classify_doc_meta_line_llm

    ctx = dict(context or {})
    if session is not None and not ctx:
        ctx = pack_classifier_context(session)
    return classify_doc_meta_line_llm(
        message,
        dialogue_tail=list(ctx.get("dialogue_tail") or []) or None,
        focus_document_brief=str(ctx.get("focus_document_brief") or ""),
        invoke=invoke,
    )
