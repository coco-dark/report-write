"""无蓝本：用户请求托管起草（口语意图见 freeform_intent）。"""

from __future__ import annotations

from typing import Callable

InvokeFn = Callable[[str, str], str]


def wants_freeform_help_draft(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
) -> bool:
    from report_writing.chat.freeform_intent import wants_freeform_help_draft as _intent

    return _intent(message, invoke=invoke, phase=phase)
