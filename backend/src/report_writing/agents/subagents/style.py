"""Word 样式子 Agent：列出样式、打开对话内配置向导（导出前使用）。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from report_writing.agents.scripted import ScriptedChatModel
from report_writing.agents.state import ChatControlState
from report_writing.agents.subagents.runner import run_domain_subagent

INSTRUCTIONS = """你是「Word 导出样式」领域子助手。只通过工具操作。
导出前用户要配 Word 样式 → list_word_styles 或 begin_word_style_wizard。
未配置时导出仍可用默认样式。完成后口语总结。"""


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def build_style_subagent_tools(state: ChatControlState) -> list:
    @tool
    def list_word_styles() -> str:
        """列出当前机器人/账号下可用的 Word 样式。"""
        from report_writing.services.robots import scoped_word_styles

        items = scoped_word_styles(state.home, state.session.robot_id or None)
        if not items:
            return _dump({"ok": True, "reply_hint": "还没有自定义 Word 样式，可说「配置 Word 样式」或导出时用默认。"})
        lines = [f"- {getattr(t, 'title', None) or getattr(t, 'name', '')} ({t.id})" for t in items[:20]]
        return _dump({"ok": True, "reply_hint": "可用 Word 样式：\n" + "\n".join(lines)})

    @tool
    def begin_word_style_wizard() -> str:
        """在对话中打开 Word 样式配置向导。"""
        from report_writing.chat.resource_actions import open_template_wizard_choose

        reply, cards = open_template_wizard_choose(state.session, "word_style")
        state.cards = list(cards)
        return _dump({"ok": True, "reply_hint": reply})

    return [list_word_styles, begin_word_style_wizard]


def _style_scripted(_state: ChatControlState) -> ScriptedChatModel:
    class _S(ScriptedChatModel):
        role: str = "style_sub"

        def decide(self, messages: list) -> AIMessage:
            for message in reversed(messages):
                if isinstance(message, ToolMessage):
                    return self.finish("Word 样式已处理好。")
            text = ""
            for message in reversed(messages):
                if isinstance(message, HumanMessage):
                    text = str(message.content or "")
                    break
            if "配置" in text or "样式" in text:
                return self.call("begin_word_style_wizard", {})
            return self.call("list_word_styles", {})

    return _S()


def run_style_subagent(model: BaseChatModel, state: ChatControlState, user_request: str) -> str:
    return run_domain_subagent(
        model=model,
        state=state,
        user_request=user_request,
        tools=build_style_subagent_tools(state),
        instructions=INSTRUCTIONS,
        agent_name="style_subagent",
        process_message="Word 样式助手在处理…",
        default_reply="Word 样式这边已处理好。",
        scripted_factory=_style_scripted,
    )
