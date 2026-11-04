"""蓝本子 Agent：配置向导 + 选型；章节 search/write 由总控 start_writing → supervisor。"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import tool

from report_writing.agents.state import ChatControlState
from report_writing.agents.subagents.blueprint_wizard_impl import (
    _make_wizard_scripted_model,
    build_blueprint_wizard_subagent_tools,
)
from report_writing.agents.subagents.runner import run_domain_subagent

BLUEPRINT_SUBAGENT_INSTRUCTIONS = """你是「蓝本」领域子助手（小总控）。只通过工具操作。
配置期：创建蓝本、上传样板、提取/确认目录、进入学习分析步 — 用 begin_* / re_* / go_* 工具。
选型：用户要写报告、找蓝本 → match_blueprints_for_user 或 list_blueprints_scoped。
分析要点流式任务在卡片里点「开始分析」；可先用 go_study_analysis_step 进入该步。
【禁止】在本子 Agent 里直接写正文或检索；开写必须由对话总控调用 start_writing，跑章由后台 supervisor（study/search/write/check）执行。
勿让用户去蓝本页（除非工具返回的卡片仅作补充）。完成后口语总结。"""


def _dump(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


def build_blueprint_subagent_tools(state: ChatControlState) -> list:
    tools = list(build_blueprint_wizard_subagent_tools(state))

    @tool
    def match_blueprints_for_user(message: str) -> str:
        """按用户描述匹配蓝本（用途说明）；仅用户明确点名或点「选用」时才自动选用。"""
        from report_writing.agents.chat_control import _finish_blueprint_match
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

    @tool
    def list_blueprints_scoped(query: str = "") -> str:
        """列出当前机器人/账号下可用蓝本，可选关键词 query。"""
        from report_writing.chat_orchestrator import _action_list_blueprints

        reply, cards = _action_list_blueprints(state.home, state.session, {"q": query or ""})
        state.cards = list(cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def writing_pipeline_note() -> str:
        """说明：章节写作不由本子 Agent 执行，应让用户通过总控 start_writing 开写。"""
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
    from report_writing.chat.intent_router import active_blueprint_wizard

    wizard = active_blueprint_wizard(state.session) or {}
    context = (
        f"当前向导步骤：{wizard.get('step') or '未开始'}\n"
        f"蓝本 id：{wizard.get('blueprint_id') or state.session.blueprint_id or '—'}\n"
        f"会话 phase：{state.session.phase or '—'}"
    )
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


def run_blueprint_wizard_subagent(model, state, user_request: str) -> str:
    """兼容旧总控工具名。"""
    return run_blueprint_subagent(model, state, user_request)
