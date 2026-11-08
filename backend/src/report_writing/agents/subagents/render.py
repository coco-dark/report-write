"""渲染模版子 Agent：列出模版、打开对话内配置向导（导出前使用）。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.tools import tool

from report_writing.agents.scripted import ScriptedChatModel
from report_writing.agents.state import ChatControlState
from report_writing.agents.subagents.runner import run_domain_subagent

INSTRUCTIONS = """# Role
你是渲染模版（HTML 导出版式）子助手。

# Task
导出 Word 前，按用户要求列出或打开配置向导，帮他选/配渲染模版。

# Inputs
用户原话。只通过已绑定 Tool 操作；何时调用见各 Tool description。

# Rules
- 不要在开写阶段强推。
- 不要改 Word 样式（那是另一个子助手）。

# Output
完成后用一两句口语总结。
"""


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def build_render_subagent_tools(state: ChatControlState) -> list:
    @tool(
        "list_render_templates",
        description=(
            "何时：用户要看有哪些 HTML 渲染模版。"
            "缺了：直接列出即可。"
            "不要：用本工具新建或打开向导（用 begin_render_template_wizard）。"
        ),
    )
    def list_render_templates() -> str:
        from report_writing.services.robots import scoped_render_templates

        items = scoped_render_templates(state.home, state.session.robot_id or None)
        if not items:
            return _dump({"ok": True, "reply_hint": "还没有渲染模版，可以说「配置渲染模版」打开向导。"})
        lines = [f"- {getattr(t, 'title', None) or getattr(t, 'id', '')} ({t.id})" for t in items[:20]]
        return _dump({"ok": True, "reply_hint": "可用渲染模版：\n" + "\n".join(lines)})

    @tool(
        "begin_render_template_wizard",
        description=(
            "何时：用户说配置/新建/选渲染模版，打开卡片向导。"
            "缺了：打开向导卡让用户选，不要猜模版 id。"
            "不要：只想看列表时调用（用 list_render_templates）；开写阶段不要强推。"
        ),
    )
    def begin_render_template_wizard() -> str:
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
