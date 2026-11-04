"""子 Agent 通用 ReAct 运行器。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage

from report_writing.agents.react import run_react
from report_writing.agents.state import ChatControlState


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def subagent_final_text(messages: list[Any], *, default: str) -> str:
    """优先最近 Tool reply_hint；若子 Agent 直接 finish（如 abandon 澄清）则采信 AI 文本。"""
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

    from report_writing.chat.agent_chain_log import log_chain

    log_chain(
        state.session,
        state.emit,
        step="subagent",
        summary=f"子Agent·{agent_name}",
        detail={
            "agent": agent_name,
            "instructions_preview": instructions[:2000],
            "context_preview": context[:1200] if context else "",
            "turn_context_block": ctx_block[:2000] if ctx_block else "",
            "user_request": text[:800],
        },
    )
    result = run_react(
        sub_model,
        tools,
        prompt,
        text,
        agent_name,
        rounds=rounds,
        session=state.session,
        emit=state.emit,
    )
    hint = subagent_final_text(result.get("messages") or [], default=default_reply)
    return _dump({"ok": True, "reply_hint": hint, "subagent": agent_name})
