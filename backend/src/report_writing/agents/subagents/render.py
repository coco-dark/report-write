"""渲染模版子 Agent：列出模版、打开对话内配置向导（导出前使用）。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from report_writing.agents.scripted import ScriptedChatModel
from report_writing.agents.state import ChatControlState
from report_writing.agents.subagents.runner import run_domain_subagent

INSTRUCTIONS = """你是「渲染模版（HTML 导出）」领域子助手。只通过工具操作。
导出 Word 前用户要选/配渲染模版 → list_render_templates 或 begin_render_template_wizard。
开写阶段不要强推渲染模版。完成后口语总结。"""


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def build_render_subagent_tools(state: ChatControlState) -> list:
    @tool
    def list_render_templates() -> str:
        """列出当前机器人/账号下可用的渲染模版。"""
        from report_writing.services.robots import scoped_render_templates

        items = scoped_render_templates(state.home, state.session.robot_id or None)
        if not items:
            return _dump({"ok": True, "reply_hint": "还没有渲染模版，可以说「配置渲染模版」打开向导。"})
        lines = [f"- {getattr(t, 'title', None) or getattr(t, 'id', '')} ({t.id})" for t in items[:20]]
        return _dump({"ok": True, "reply_hint": "可用渲染模版：\n" + "\n".join(lines)})

    @tool
    def begin_render_template_wizard() -> str:
        """在对话中打开渲染模版配置向导（选用已有或新建）。"""
        from report_writing.chat.resource_actions import open_template_wizard_choose

        reply, cards = open_template_wizard_choose(state.session, "render_template")
        state.cards = list(cards)
        return _dump({"ok": True, "reply_hint": reply})

    return [list_render_templates, begin_render_template_wizard]


def _render_scripted(_state: ChatControlState) -> ScriptedChatModel:
    class _R(ScriptedChatModel):
        role: str = "render_sub"

        def decide(self, messages: list) -> AIMessage:
            for message in reversed(messages):
                if isinstance(message, ToolMessage):
                    return self.finish("渲染模版已处理好。")
            text = ""
            for message in reversed(messages):
                if isinstance(message, HumanMessage):
                    text = str(message.content or "")
                    break
            if "配置" in text or "新建" in text or "选" in text:
                return self.call("begin_render_template_wizard", {})
            return self.call("list_render_templates", {})

    return _R()


def run_render_subagent(model: BaseChatModel, state: ChatControlState, user_request: str) -> str:
    return run_domain_subagent(
        model=model,
        state=state,
        user_request=user_request,
        tools=build_render_subagent_tools(state),
        instructions=INSTRUCTIONS,
        agent_name="render_subagent",
        process_message="渲染模版助手在处理…",
        default_reply="渲染模版这边已处理好。",
        scripted_factory=_render_scripted,
    )
