"""蓝本子 Agent：配置向导 + 选型；章节 search/write 由总控 start_writing → supervisor。"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain.tools import tool
from pydantic import Field

from report_writing.agents.state import ChatControlState
from report_writing.agents.subagents.blueprint_wizard_impl import (
    _make_wizard_scripted_model,
    build_blueprint_wizard_subagent_tools,
)
from report_writing.agents.subagents.runner import run_domain_subagent

BLUEPRINT_SUBAGENT_INSTRUCTIONS = """# Role
你是蓝本领域子助手。

# Task
只处理蓝本选型与向导配置。不对用户编章节正文。

# Inputs
用户原话与当前向导状态。只调用已绑定 Tool；何时调用、禁忌见各 Tool description。

# Rules
- 选型：用户要找蓝本、写某类报告时，匹配或列出候选，不要代选。
- 配置：会话里已经有向导时，按用户话推进上传样板、提取/确认目录、学习分析。
- 向导还没开始：结束并把观察交给总控去开启，不要在这里代开向导。
- 用户要开写：说明由对话总控走按章流水线，不要在本子助手里检索或写正文。
- 不要把用户赶到蓝本管理页（除非卡片只作补充）。

# Output
完成后用一两句口语总结。
"""


def _dump(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


def build_blueprint_subagent_tools(state: ChatControlState) -> list:
    tools = list(build_blueprint_wizard_subagent_tools(state))

    @tool(
        "match_blueprints_for_user",
        description=(
            "何时：用户要找蓝本、写某类报告，按描述给出候选。"
            "缺了：用当前用户原话匹配即可。"
            "不要：代选；明确选用交给对话总控。不要用本工具写正文。"
        ),
    )
    def match_blueprints_for_user(
        message: Annotated[str, Field(description="可选。用户原话；空则用本轮对话。")] = "",
    ) -> str:
        from report_writing.agents.chat_tools import _finish_blueprint_match
        from report_writing.services.robots import match_blueprints_with_agent

        invoke = state.invoke or (lambda _s, _u: "")
        agent_reply, matches = match_blueprints_with_agent(
            state.home,
            message=message or state.last_user_message or "",
            robot_id=state.session.robot_id,
            invoke=invoke,
        )
        raw = _finish_blueprint_match(state, message or "", agent_reply, matches)
        return raw

    @tool(
        "list_blueprints_scoped",
        description=(
            "何时：用户要浏览蓝本库。"
            "缺了：直接列出可见蓝本即可。"
            "不要：按题材匹配（用 match_blueprints_for_user）；不要开写。"
        ),
    )
    def list_blueprints_scoped(
        query: Annotated[str, Field(description="可选。关键词；空则列出当前可见蓝本。")] = "",
    ) -> str:
        from report_writing.chat import _action_list_blueprints

        reply, cards = _action_list_blueprints(state.home, state.session, {"q": query or ""})
        state.cards = list(cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool(
        "writing_pipeline_note",
        description=(
            "何时：用户要开始按章写作。本工具只说明：检索/写正文/检查由对话总控的按章流水线执行。"
            "不要：在本子助手里检索或写正文。"
        ),
    )
    def writing_pipeline_note() -> str:
        return _dump(
            {
                "ok": True,
                "reply_hint": (
                    "蓝本配置和选型我这边可以搞定。真正按章检索、写正文、检查，"
                    "需要你在对话里说「开始写」或点开始写作，总控会走 supervisor 流水线。"
                ),
            }
        )

    tools.extend([match_blueprints_for_user, list_blueprints_scoped, writing_pipeline_note])
    return tools


def run_blueprint_subagent(
    model: BaseChatModel,
    state: ChatControlState,
    user_request: str,
) -> str:
    from report_writing.chat.session_state import active_blueprint_wizard

    wizard = active_blueprint_wizard(state.session) or {}
    context = (
        f"当前向导步骤：{wizard.get('step') or '未开始'}\n"
        f"蓝本 id：{wizard.get('blueprint_id') or state.session.blueprint_id or '—'}\n"
        f"会话 phase：{state.session.phase or '—'}"
    )
    # 官方：create_agent(model, tools, system_prompt).invoke(user query)
    return run_domain_subagent(
        model=model,
        state=state,
        user_request=user_request,
        tools=build_blueprint_subagent_tools(state),
        instructions=BLUEPRINT_SUBAGENT_INSTRUCTIONS,
        agent_name="blueprint_subagent",
        context=context,
        process_message="蓝本助手在处理…",
        default_reply="蓝本这边已经处理好了，你看下方面板。",
        scripted_factory=lambda st: _make_wizard_scripted_model(st, st.invoke),
    )
