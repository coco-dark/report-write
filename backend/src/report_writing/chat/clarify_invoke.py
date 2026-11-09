"""窄 JSON 任务的 LLM 入口（配置键仍叫 `intent.max_tokens`，不是口语路由）。

口语「理解 + 调 Tool」只走总控 `create_agent`。
本模块给澄清卡、向导 command、Tool 内补槽等固定 schema；
可配独立小模型，与总控 ReAct 模型分开以省成本。
"""

from __future__ import annotations

from typing import Callable

InvokeFn = Callable[[str, str], str]


def invoke_clarify_llm(
    system: str,
    user: str,
    *,
    model=None,
    fallback: InvokeFn | None = None,
) -> str:
    """优先传入的澄清小模型；未传则 live_clarify_model；再回落 fallback / chat_invoke。"""
    from report_writing.infra.llm import live_clarify_model
    from report_writing.infra.llm_invoke import invoke_prompt

    chosen = model if model is not None else live_clarify_model()
    if chosen is not None:
        from report_writing.infra.llm_errors import is_rate_limit_error

        try:
            return invoke_prompt(chosen, system, user)
        except Exception as error:
            if is_rate_limit_error(error) and fallback is not None:
                return fallback(system, user)
            raise
    if fallback is not None:
        return fallback(system, user)
    from report_writing.api.deps import chat_invoke

    return chat_invoke(system, user)


def clarify_invoke_fn(fallback: InvokeFn | None = None, *, model=None) -> InvokeFn:
    """绑定 fallback / model 的澄清用 invoke（供总控 state 注入）。"""

    def _invoke(system: str, user: str) -> str:
        return invoke_clarify_llm(system, user, model=model, fallback=fallback)

    return _invoke
