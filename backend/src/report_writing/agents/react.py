"""调用 LangChain create_agent，直到模型不再发起 tool_calls。

对齐官方 Agent 循环：https://docs.langchain.com/oss/python/langchain/agents
- system_prompt：角色与策略（不含工具枚举）
- tools：语义在 Tool.description
- user 消息：本轮上下文 + 用户原话
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from report_writing.domain.models import MAX_AGENT_STEPS


def run_react(
    model: BaseChatModel,
    tools: list[BaseTool],
    prompt: str,
    user_text: str,
    name: str,
    rounds: int | None = None,
    *,
    session: Any = None,
    emit: Any = None,
) -> dict[str, Any]:
    """LangChain Agent 图：model → tools → ToolMessage → … → 结束。"""
    if session is not None:
        from report_writing.chat.agent_chain_log import log_react_round, log_react_tool_calls

        log_react_round(session, emit, agent=name, system_prompt=prompt, user_text=user_text)
    from report_writing.chat.user_process import emit_user_process

    emit_user_process(emit, "写作助手正在决定调用哪些步骤…")
    agent = create_agent(model, tools, system_prompt=prompt, name=name)
    needed = max(rounds or MAX_AGENT_STEPS, 1)
    limit = needed * 4 + 4
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_text}]},
            config={"recursion_limit": limit},
        )
    except Exception as error:
        if error.__class__.__name__ != "GraphRecursionError":
            raise
        raise RuntimeError(f"{name} 在 {limit} 步内没有结束") from error
    messages = result["messages"]
    if session is not None:
        from report_writing.chat.agent_chain_log import log_react_tool_calls

        log_react_tool_calls(session, emit, agent=name, messages=messages)
    return {"messages": messages}


def tool_result(messages: list[Any], name: str) -> Any:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == name:
            return json.loads(message.content)
    raise RuntimeError(f"{name} 未提交结果")
