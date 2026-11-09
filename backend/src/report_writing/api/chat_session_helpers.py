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
    from report_writing.chat.cards import slim_session_progress_cards

    # 历史进度卡曾塞整章 draft_text / 完整 element_nodes，加载与对话都会被拖慢
    slimmed = slim_session_progress_cards(session)
    if persist or slimmed:
        # UI / 读后 reconcile：默认可异步刷库；裁掉膨胀后尽快写热存，后续加载才轻
        save_session(home, session, durable=False if durable is None else durable)
    view = runtime_public_view(session, home)
    return session.model_copy(update={"runtime": view})
