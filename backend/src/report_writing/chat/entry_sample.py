"""用户自带样板：口语信号走意图模型（与 entry_intent / intent_llm 一致，无正则路由表）。"""

from __future__ import annotations

from typing import Callable

InvokeFn = Callable[[str, str], str]


def user_brings_own_sample_document(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session=None,
) -> bool:
    from report_writing.chat.intent_context import pack_classifier_context
    from report_writing.chat.intent_llm import classify_user_own_sample_llm

    ctx = pack_classifier_context(session) if session is not None else {}
    return classify_user_own_sample_llm(
        message,
        dialogue_tail=list(ctx.get("dialogue_tail") or []) or None,
        invoke=invoke,
    )


def guess_report_title_from_message(message: str, *, invoke: InvokeFn | None = None) -> str:
    from report_writing.chat.intent_llm import extract_write_subject_title_llm

    return extract_write_subject_title_llm(message, invoke=invoke)
