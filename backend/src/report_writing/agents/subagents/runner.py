"""子 Agent：官方 Subagents 模式（总控把子 Agent 当 Tool）。

https://docs.langchain.com/oss/python/langchain/multi-agent/subagents

    sub = create_agent(model, tools, system_prompt=...)
    result = sub.invoke({"messages": [{"role": "user", "content": query}]})
    # 返回给总控，不直接对用户说话

产品侧多包一层 JSON（reply_hint / cards），便于总控卡片与观察闭环。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage

from report_writing.agents.react_middleware import build_react_middleware
from report_writing.agents.state import ChatControlState
from report_writing.domain.models import MAX_AGENT_STEPS


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def subagent_final_text(messages: list[Any], *, default: str) -> str:
    """优先最近 Tool reply_hint；否则官方约定：取最后一条 AI 文本。"""
    ai_text = ""
    for message in reversed(messages or []):
        if isinstance(message, ToolMessage):
            try:
                data = json.loads(message.content or "{}")
            except json.JSONDecodeError:
                data = {}
            hint = str((data or {}).get("reply_hint") or "").strip()
            if hint:
                return hint
        elif isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            if ai_text:
                continue
            content = message.content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    else:
                        parts.append(str(getattr(item, "text", None) or item or ""))
                content = "".join(parts)
            ai_text = str(content or "").strip()
    return ai_text or default


def invoke_subagent(
    model: BaseChatModel,
    tools: list,
    system_prompt: str,
    query: str,
    name: str,
    *,
    session: Any = None,
    emit: Any = None,
    rounds: int = 6,
) -> dict[str, Any]:
    """create_agent(...).invoke({\"messages\": [user query]})。"""
    needed = max(rounds or MAX_AGENT_STEPS, 1)
    limit = needed * 4 + 4
    if session is not None:
        from report_writing.chat.agent_chain_log import log_react_round

        log_react_round(session, emit, agent=name, system_prompt=system_prompt, user_text=query)
    agent = create_agent(
        model,
        tools,
        system_prompt=system_prompt,
        name=name,
        middleware=build_react_middleware(
            session=session,
            emit=emit,
            agent=name,
            model_call_limit=limit,
        ),
    )
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": query}]},
            config={"recursion_limit": limit},
        )
    except Exception as error:
        kind = error.__class__.__name__
        if kind not in {"GraphRecursionError", "ModelCallLimitExceededError"}:
            raise
        raise RuntimeError(f"{name} 在 {limit} 步内没有结束") from error
    return result


def run_domain_subagent(
    *,
    model: BaseChatModel,
    state: ChatControlState,
    user_request: str,
    tools: list,
    instructions: str,
    agent_name: str,
    context: str = "",
    process_message: str = "正在处理…",
    default_reply: str = "已经处理好了，你看下方面板。",
    scripted_factory: Callable[[ChatControlState], BaseChatModel] | None = None,
    rounds: int = 6,
) -> str:
    """领域子 Agent：官方 create_agent 循环，结果以 Tool JSON 回总控。"""
    text = (user_request or state.last_user_message or "").strip()
    if not text:
        return _dump({"ok": False, "reply_hint": "请说明要做什么。"})

    if state.emit and process_message:
        state.emit({"type": "process", "message": process_message})

    from report_writing.chat.turn_context import ensure_control_state_context, turn_context_prompt_block

    turn_ctx = ensure_control_state_context(state, text)
    ctx_block = turn_context_prompt_block(turn_ctx)
    prompt = instructions.strip()
    extra_blocks = [b for b in (ctx_block, context.strip()) if b]
    if extra_blocks:
        prompt = f"{prompt}\n\n---\n" + "\n\n".join(extra_blocks)

    sub_model = model
    if scripted_factory and (
        os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("REPORT_WRITING_SCRIPTED_CHAT") == "1"
    ):
        sub_model = scripted_factory(state)

    result = invoke_subagent(
        sub_model,
        tools,
        prompt,
        text,
        agent_name,
        session=state.session,
        emit=state.emit,
        rounds=rounds,
    )
    hint = subagent_final_text(result.get("messages") or [], default=default_reply)
    suggested = list((state.session.freeform or {}).get("suggested_next_tools") or [])
    body: dict[str, Any] = {
        "ok": True,
        "reply_hint": hint,
        "subagent": agent_name,
    }
    if suggested:
        body["suggested_next_tools"] = suggested
    return _dump(body)
