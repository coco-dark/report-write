"""LangChain create_agent middleware：链路日志 + 模型调用上限。

不改卡片类型、不改 Tool 语义；只包一层 wrap_tool_call / ModelCallLimit。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain_core.messages import ToolMessage


class ReactChainLogMiddleware(AgentMiddleware):
    """执行期写入 agent_chain_log（step=tool / tool_result）。"""

    def __init__(self, *, session: Any, emit: Any, agent: str) -> None:
        super().__init__()
        self._session = session
        self._emit = emit
        self._agent = agent

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        call = getattr(request, "tool_call", None) or {}
        if isinstance(call, dict):
            name = str(call.get("name") or "?")
            args = call.get("args") or {}
        else:
            name = str(getattr(call, "name", None) or "?")
            args = getattr(call, "args", None) or {}
        from report_writing.chat.agent_chain_log import log_react_tool_result, log_tool_invoke

        log_tool_invoke(
            self._session,
            self._emit,
            tool=name,
            args=args if isinstance(args, dict) else {"raw": str(args)},
            source=f"react:{self._agent}",
        )
        try:
            result = handler(request)
        except ValueError as error:
            call_id = ""
            if isinstance(call, dict):
                call_id = str(call.get("id") or "")
            else:
                call_id = str(getattr(call, "id", "") or "")
            content = json.dumps(
                {"ok": False, "reply_hint": str(error), "error": str(error)},
                ensure_ascii=False,
            )
            log_react_tool_result(
                self._session,
                self._emit,
                tool=name,
                content=content,
            )
            return ToolMessage(content=content, name=name, tool_call_id=call_id)
        if isinstance(result, ToolMessage):
            log_react_tool_result(
                self._session,
                self._emit,
                tool=result.name or name,
                content=str(result.content or ""),
            )
        return result


def build_react_middleware(
    *,
    session: Any,
    emit: Any,
    agent: str,
    model_call_limit: int,
) -> list[AgentMiddleware]:
    layers: list[AgentMiddleware] = [
        ModelCallLimitMiddleware(run_limit=max(model_call_limit, 1), exit_behavior="error"),
    ]
    if session is not None:
        layers.append(ReactChainLogMiddleware(session=session, emit=emit, agent=agent))
    return layers
