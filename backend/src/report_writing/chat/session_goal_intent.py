"""会话写作目标：结构行 / 记住短语。口语记住目标归总控 remember_writing_goal。"""

from __future__ import annotations

import re
from typing import Any, Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]

_REMEMBER_RE = re.compile(r"(记住|记下来|今后按|后面都按|别忘了)")


def is_explicit_remember_goal_message(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    _ = (invoke, session)
    text = (message or "").strip()
    if not text:
        return False
    if "｜" in text or "|" in text:
        return False
    return bool(_REMEMBER_RE.search(text))


def doc_meta_patch_from_message(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, str]:
    """只认「类型｜标题」结构行；口语定名归总控 set_session_meta。"""
    _ = (invoke, session, context)
    from report_writing.chat.session_goal import _parse_meta_line_structural

    return _parse_meta_line_structural(message)
