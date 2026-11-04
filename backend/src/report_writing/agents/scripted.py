"""离线脚本模型。它仍走 LangGraph 的 ReAct 循环，只是下一步工具调用由规则决定。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    role: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return self.role

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self.decide(list(messages))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        raise NotImplementedError

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": name,
                    "args": arguments or {},
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "tool_call",
                }
            ],
        )

    def finish(self, text: str) -> AIMessage:
        return AIMessage(content=text)


def tool_payloads(messages: list[BaseMessage], name: str) -> list[Any]:
    payloads = []
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == name:
            payloads.append(json.loads(message.content))
    return payloads


def human_payload(messages: list[BaseMessage]) -> dict[str, Any]:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
            return json.loads(content)
    return {}
