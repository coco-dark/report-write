"""蓝本配置向导 Tool 集（供 blueprint 子 Agent 使用）。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.tools import tool
from pydantic import Field

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

    @tool(
        "begin_blueprint_wizard",
        description=(
            "何时：向导尚未开始，用户要依据模版编写。打开填写名称步。"
            "缺了：名称可空，先开填写名称步。"
            "不要：向导已进行中重复开启；不要代写正文。"
        ),
    )
    def begin_blueprint_wizard(
        title_hint: Annotated[str, Field(description="可选。用户口述的蓝本名称，不是已定名。")] = "",
    ) -> str:
        from report_writing.chat.entry_intent import extract_wizard_title_hint
        from report_writing.chat.resource_actions import _begin_blueprint_wizard

        hint = (title_hint or "").strip() or extract_wizard_title_hint(
            state.last_user_message,
            invoke=state.invoke,
            session=state.session,
        )
        reply, cards = _begin_blueprint_wizard(state.session, title_hint=hint)
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool(
        "re_extract_outline_from_sample",
        description=(
            "何时：样板已上传，用户要识别、生成或修正章节目录。"
            "不要：尚未上传时调用；不要代替确认目录。"
        ),
    )
    def re_extract_outline_from_sample() -> str:
        reply, cards = _run_command("re_extract_outline")
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool(
        "re_upload_sample_file",
        description="何时：用户要重新上传或更换 Word/PDF 样板。回到上传步，不要在本工具里提取目录。",
    )
    def re_upload_sample_file() -> str:
        reply, cards = _run_command("re_upload_sample")
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool(
        "go_outline_confirm_step",
        description="何时：目录已抽出，用户要在拓扑图核对。这是核对，不是最终确认、也不是开始分析。",
    )
    def go_outline_confirm_step() -> str:
        reply, cards = _run_command("go_outline_confirm")
        state.cards = list(cards)
        return _tool_ok(state, reply)

    @tool(
        "go_study_analysis_step",
        description=(
            "何时：目录已确认，用户要开始分析写作要点。"
            "进入分析步；真正跑流式分析由卡片「开始分析」触发，本工具不写正文。"
        ),
    )
    def go_study_analysis_step() -> str:
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
            from report_writing.chat.clarify_llm import resolve_wizard_action
            from report_writing.chat.session_state import active_blueprint_wizard
            from report_writing.chat.entry_intent import (
                extract_wizard_title_hint,
                is_create_blueprint_intent,
            )

            wizard = active_blueprint_wizard(state.session) or {}
            if wizard:
                hit = resolve_wizard_action(text, wizard, invoke=invoke)
                if hit and hit.get("type") == "command":
                    name = str(hit.get("command") or "").strip()
                    if name in _COMMAND_TO_TOOL:
                        return self.call(_COMMAND_TO_TOOL[name], {})
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
                return self.finish(
                    "没太听懂。你可以说：重新提取目录、换样板、确认目录、开始分析，或点下方卡片按钮。"
                )

            # 向导未开始：禁止在子代理内代调 begin；只 observation 给总控 hop。
            if is_create_blueprint_intent(text, invoke=invoke, session=state.session):
                hint = extract_wizard_title_hint(
                    text, invoke=invoke, session=state.session
                )
                ff = dict(state.session.freeform or {})
                ff["suggested_next_tools"] = [
                    {
                        "tool": "begin_blueprint_wizard",
                        "args": {
                            "explicit_create": True,
                            "from_entry_path": True,
                            "title_hint": hint,
                        },
                    }
                ]
                state.session.freeform = ff
                return self.finish(
                    "向导还没开始。请总控调用 begin_blueprint_wizard(explicit_create=true)，"
                    "或让用户点选「依据模版编写」。"
                )

            return self.finish(
                "没太听懂。依据模版编写请先 begin_blueprint_wizard；"
                "向导进行中可以说：重新提取目录、换样板、确认目录、开始分析，或点卡片。"
            )

    return _WizardScripted()
