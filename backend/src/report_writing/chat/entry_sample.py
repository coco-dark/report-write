"""用户自带样板：结构短语提示。口语是否开向导归总控 begin_blueprint_wizard。"""

from __future__ import annotations

from typing import Callable

InvokeFn = Callable[[str, str], str]

_OWN_SAMPLE_HINTS = (
    "我有样本",
    "我有样板",
    "我有模版",
    "我有模板",
    "按样本写",
    "按样板写",
    "依据样本",
    "依据模版",
    "上传样板",
)


def user_brings_own_sample_document(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session=None,
) -> bool:
    _ = (invoke, session)
    t = (message or "").strip()
    return bool(t) and any(k in t for k in _OWN_SAMPLE_HINTS)


def guess_report_title_from_message(message: str, *, invoke: InvokeFn | None = None) -> str:
    _ = invoke
    from report_writing.chat.session_documents import quick_title_from_write_message

    return quick_title_from_write_message(message) or ""
