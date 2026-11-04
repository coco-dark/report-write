"""对话写作取消/暂停：按会话 ID 标记，章节循环间检查。"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_cancelled: set[str] = set()


def clear_writing_cancel(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    with _lock:
        _cancelled.discard(sid)


def request_writing_cancel(session_id: str) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    with _lock:
        _cancelled.add(sid)


def is_writing_cancelled(session_id: str) -> bool:
    sid = (session_id or "").strip()
    if not sid:
        return False
    with _lock:
        return sid in _cancelled
