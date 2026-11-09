"""会话配置代数：任务在文档窗/对话中落盘后 bump，用于失效 turn_context 等热缓存。"""

from __future__ import annotations

from report_writing.domain.models import ChatSession


def touch_session_config_revision(session: ChatSession) -> None:
    ff = dict(session.freeform or {})
    ff["config_revision"] = int(ff.get("config_revision") or 0) + 1
    session.freeform = ff


def session_config_revision(session: ChatSession) -> int:
    return int((session.freeform or {}).get("config_revision") or 0)
