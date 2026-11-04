"""蓝本配置向导 Tool 集（供 blueprint 子 Agent 使用）。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from report_writing.agents.state import ChatControlState
from report_writing.agents.scripted import ScriptedChatModel

_COMMAND_TO_TOOL = {
    "re_extract_outline": "re_extract_outline_from_sample",
    "re_upload_sample": "re_upload_sample_file",
    "go_outline_confirm": "go_outline_confirm_step",
    "go_study": "go_study_analysis_step",
}


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _tool_ok(state: ChatControlState, reply: str) -> str:
    return _dump({"ok": True, "reply_hint": reply})


def build_blueprint_wizard_subagent_tools(state: ChatControlState) -> list:
    def _run_command(command: str) -> tuple[str, list]:
        from report_writing.chat.resource_actions import execute_wizard_command

        return execute_wizard_command(state.home, state.session, command)

    @tool
    def begin_blueprint_wizard(title_hint: str = "") -> str:
        """开始对话内蓝本向导（填写名称步）。title_hint 为用户口述的蓝本名称，可空。"""
        from report_writing.chat.intent_router import extract_wizard_title_hint
        from report_writing.chat.resource_actions import _begin_blueprint_wizard

        hint = (title_hint or "").strip() or extract_wizard_title_hint(
            state.last_user_message,
            invoke=state.invoke,
            session=state.session,
        )
        reply, cards = _begin_blueprint_wizard(state.session, title_hint=hint)
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool
    def re_extract_outline_from_sample() -> str:
        """从已上传的样板 Word/PDF 重新识别并生成章节目录。"""
        reply, cards = _run_command("re_extract_outline")
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool
    def re_upload_sample_file() -> str:
        """回到上传样板步骤，让用户重新上传或更换文件。"""
        reply, cards = _run_command("re_upload_sample")
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool
    def go_outline_confirm_step() -> str:
        """进入「确认目录」步骤，在拓扑图中核对章节结构。"""
        reply, cards = _run_command("go_outline_confirm")
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool
    def go_study_analysis_step() -> str:
        """进入分析要点步骤（用户再在卡片点「开始分析」跑流式任务）。"""
        reply, cards = _run_command("go_study")
        state.cards = list(cards)
        return _tool_ok(state, reply)

    return [
        begin_blueprint_wizard,
        re_extract_outline_from_sample,
        re_upload_sample_file,
        go_outline_confirm_step,
        go_study_analysis_step,
    ]


def _make_wizard_scripted_model(state: ChatControlState, invoke) -> ScriptedChatModel:
    class _WizardScripted(ScriptedChatModel):
        role: str = "blueprint_wizard_sub"

        def decide(self, messages: list) -> AIMessage:
            for message in reversed(messages):
                if isinstance(message, ToolMessage):
                    try:
                        data = json.loads(message.content or "{}")
                    except json.JSONDecodeError:
                        data = {}
                    hint = str((data or {}).get("reply_hint") or "").strip()
                    return self.finish(hint or "好的。")

            text = ""
            for message in reversed(messages):
                if isinstance(message, HumanMessage):
                    text = str(message.content or "").strip()
                    break

            from report_writing.chat.blueprint_wizard_route import apply_wizard_action
            from report_writing.chat.intent_llm import resolve_wizard_action
            from report_writing.chat.intent_router import (
                active_blueprint_wizard,
                extract_wizard_title_hint,
                is_create_blueprint_intent,
            )

            wizard = active_blueprint_wizard(state.session) or {}
            if wizard:
                hit = resolve_wizard_action(text, wizard, invoke=invoke)
                if hit:
                    reply, cards, _events = apply_wizard_action(
                        state.home,
                        state.session,
                        hit,
                        message=text,
                        invoke=invoke,
                    )
                    state.cards = list(cards)
                    return self.finish(reply or "好的。")

            if is_create_blueprint_intent(
                text, invoke=invoke, session=state.session
            ) and not wizard.get("blueprint_id"):
                return self.call(
                    "begin_blueprint_wizard",
                    {
                        "title_hint": extract_wizard_title_hint(
                            text, invoke=invoke, session=state.session
                        )
                    },
                )

            cmd = resolve_wizard_action(text, wizard, invoke=invoke)
            if cmd and cmd.get("type") == "command":
                name = str(cmd.get("command") or "").strip()
                if name in _COMMAND_TO_TOOL:
                    return self.call(_COMMAND_TO_TOOL[name], {})

            if is_create_blueprint_intent(text, invoke=invoke, session=state.session):
                return self.call(
                    "begin_blueprint_wizard",
                    {
                        "title_hint": extract_wizard_title_hint(
                            text, invoke=invoke, session=state.session
                        )
                    },
                )

            return self.finish(
                "没太听懂。你可以说：创建蓝本、重新提取目录、换样板、"
                "确认目录（或确认第×到×章）、开始分析全部、分析第×到×章，或点下方卡片按钮。"
            )

    return _WizardScripted()
