"""总控 / 子 Agent 共享会话上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel

from report_writing.domain.models import ChatCard, ChatSession

InvokeFn = Callable[[str, str], str]
EmitFn = Callable[[dict[str, Any]], None]

@dataclass
class ChatControlState:
    home: Path
    session: ChatSession
    robot: Any = None
    invoke: InvokeFn | None = None
    intent_invoke: InvokeFn | None = None  # 窄 JSON（澄清/槽位）；不是口语路由
    emit: EmitFn | None = None
    cards: list[ChatCard] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    user_action: str = ""
    agent_may_select_blueprint: bool = False
    last_user_message: str = ""
    control_model: BaseChatModel | None = None
    chat_model: BaseChatModel | None = None  # 写作/润色/要点
    intent_model: BaseChatModel | None = None  # 窄 JSON 槽位
    turn_context: Any = None  # WritingTurnContext | None
    did_invoke_tool: bool = False
