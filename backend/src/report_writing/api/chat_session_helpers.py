"""会话 API 返回前附加 runtime 快照。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.session_runtime import reconcile_chat_session, runtime_public_view
from report_writing.domain.models import ChatSession
from report_writing.services.chat_session import save_session


def prepare_chat_session_response(
    home: Path,
    session: ChatSession,
    *,
    persist: bool = False,
    durable: bool | None = None,
) -> ChatSession:
    reconcile_chat_session(home, session)
    if persist:
        # UI / 读后 reconcile：默认可异步刷库；关键场景传 durable=True
        save_session(home, session, durable=False if durable is None else durable)
    view = runtime_public_view(session, home)
    return session.model_copy(update={"runtime": view})
