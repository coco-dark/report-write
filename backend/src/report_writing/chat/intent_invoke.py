"""意图相关 LLM 调用入口（与对话 invoke、总控 ReAct 分离）。"""

from __future__ import annotations

from typing import Callable

InvokeFn = Callable[[str, str], str]


def invoke_intent_llm(system: str, user: str, *, fallback: InvokeFn | None = None) -> str:
    """优先 live_intent_model；未配置独立模型时仍走 intent 通道（同对话模型 + 更小 max_tokens）。"""
    from report_writing.infra.llm import live_intent_model

    model = live_intent_model()
    if model is not None:
        from langchain_core.messages import HumanMessage, SystemMessage
        from report_writing.infra.llm_errors import is_rate_limit_error
        from report_writing.infra.llm_invoke import invoke_messages

        try:
            return invoke_messages(model, [SystemMessage(content=system), HumanMessage(content=user)])
        except Exception as error:
            if is_rate_limit_error(error) and fallback is not None:
                return fallback(system, user)
            raise
    if fallback is not None:
        return fallback(system, user)
    from report_writing.api.deps import chat_invoke

    return chat_invoke(system, user)


def intent_invoke_fn(fallback: InvokeFn | None = None) -> InvokeFn:
    """绑定 fallback 的意图 invoke（供总控 state 注入）。"""

    def _invoke(system: str, user: str) -> str:
        return invoke_intent_llm(system, user, fallback=fallback)

    return _invoke
