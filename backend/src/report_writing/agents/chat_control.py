"""对话总控 Agent：LLM（或脚本模型）通过 Tool 推进选蓝本 / 开写 / 确认。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from report_writing.agents.chat_tools import (
    _set_cards,
    _state_clarify_invoke,
    apply_resolve_intent_clarify,
    bound_tool_invoke_args,
    build_chat_tools,
    invoke_named_tool,
)
from report_writing.agents.react import run_react
from report_writing.agents.scripted import ScriptedChatModel
from report_writing.agents.state import ChatControlState, EmitFn, InvokeFn

# 兼容旧 import
__all__ = [
    "ChatControlState",
    "run_chat_control",
    "build_chat_tools",
    "ChatControlScriptModel",
    "apply_resolve_intent_clarify",
    "invoke_named_tool",
    "_set_cards",
]
from report_writing.domain.models import ChatActionPayload, ChatPayload, ChatPhase, ChatSession

_log = logging.getLogger(__name__)

CONTROL_INSTRUCTIONS = """# Role
你是「智能文档协作」写文档总控，对用户自称小墨。

# Task
理解用户意图并调用当前已绑定的 Tool 推进写文档。不要检索、不要自己写章节正文、不要审稿。

# Inputs
用户消息为 JSON。先读焦点文档、writing_brief、dialogue_full、session_goal、action。
再读每次 Tool 返回的 ok / missing_slots / reply_hint / suggested_next_tools。

# Rules
- 何时调用、缺了怎么办、禁忌以各 Tool 的 description 为准；参数必填/可选以该参数 schema 为准。不要另写工具清单，不要对用户说出内部工具名。
- 槽位已齐就调；一句里有多步就连续调用。只补还缺的，不要重走开场流水线。
- 口语就是这场对话。必填项能从当前这句话和上文拆出来再调；拆不出、填错或返回缺槽：用 reply_hint 接着问，或挂澄清卡等用户点选/下一条补上，再调同一 Tool。不要编造 id、章节、标题，不要假装已经办成。
- 没把握（指代、写还是学、多文档、破坏性动作）就挂澄清卡。禁止猜章节、代选蓝本、静默开写/删除/收录。
- 寒暄、只看库、问怎么用：口头回应，不起名、不开空文档。
- 「写一份××」当工作名即可推进。正式名只在用户明确起名或收录/导出时确认。整句口语不要当标题。
- 用户点了按钮（action 非空）：本轮只调同名 Tool，payload 原样传入。对话里的动作指令（如「重写章节[1.1 标题]」）按该动作执行；用户直接打出同类指令与点按钮等价。
- 调错了就按该 Tool 的 description 改调正确的，不要在错误工具里代做别的事。
- 默认操作当前焦点文档。用户点名会话里另一份时，先切焦点再动手；新开一份不要切回旧文档。
- 用户明确另开/再写一份（可同时「名字叫 X」）：立刻新开，不要再口头确认「是不是两份」；禁止在旧文档上开写或改名顶替。
- 写盘落在当前文档车道。对话里的模版向导只改当前副本，收录才进蓝本库；库的维护在「蓝本管理」，对话不能删库蓝本。
- 章检索、正文、核对引用由后台流水线做，对话里不要编造章节正文。

# Output
需要办事：调用 Tool。需要说话：短、先接住再指路。已问过的不要再问。
"""

def build_system_prompt(
    robot_prompt: str = "",
    *,
    session: ChatSession | None = None,
    home: Path | None = None,
) -> str:
    """角色 + 边界。动态上下文（writing_brief 等）在 user JSON，不在 system 里重复。"""
    _ = (session, home)
    base = (robot_prompt or "").strip()
    if base:
        return f"{base.rstrip()}\n\n---\n{CONTROL_INSTRUCTIONS}"
    return CONTROL_INSTRUCTIONS


def _format_recent_dialogue_summary(session: ChatSession, *, max_lines: int = 8) -> str:
    """兼容旧字段：由完整对话压缩一行摘要。"""
    from report_writing.chat.dialogue import format_full_dialogue

    lines: list[str] = []
    for item in format_full_dialogue(session, max_turns=max_lines):
        prefix = "用户" if item.get("role") == "user" else "助手"
        text = str(item.get("text") or "").replace("\n", " ")
        if len(text) > 160:
            text = text[:157] + "…"
        lines.append(f"{prefix}：{text}")
    return "\n".join(lines)


_UI_ACTION_TOOL_ALIASES: dict[str, str] = {
    "resume": "resume_from_blueprint",
}

_UI_SHORTCUT_ACTIONS = frozenset(
    {
        "select_blueprint",
        "skip_blueprint",
        "begin_blueprint_wizard",
        "select_write_chapters",
        "write_analyzed_only",
        "start_writing",
        "check_writing_supplements",
        "skip_writing_supplement_check",
        "dismiss_writing_supplement",
        "submit_outline",
        "save_freeform_as_blueprint",
        "sync_document_to_blueprint_library",
        "generate_freeform_points",
        "skip_freeform_points",
        "confirm_chapter",
        "confirm_all_chapters",
        "resume",
        "list_blueprints",
        "match_blueprints",
        "resource_choice",
        "blueprint_wizard",
        "template_wizard",
        "pause_writing",
        "configure_chapter",
        "get_chapter_info",
        "generate_chapter_describes",
        "generate_chapter_boilerplate",
        "add_chapter_element",
        "associate_elements",
        "collect_to_element_library",
        "associate_element_knowledge",
        "open_element_library",
        "open_blueprint_library",
        "open_knowledge",
        "retry_chapter",
        "retry_search_chapter",
        "resource_check",
        "set_session_meta",
        "remember_writing_goal",
        "resolve_intent_clarify",
        "materials_pack_skip",
        "materials_pack_use",
        "materials_pack_create",
        "materials_pack_confirm_library",
        "materials_pack_list",
        "materials_pack_view",
        "materials_pack_unbind",
        "materials_pack_add_slot",
        "materials_pack_remove_slot",
        "materials_pack_update_slot",
        "materials_pack_rename",
        "usage_guide",
    }
)

# orchestrator 等模块只读引用
UI_SHORTCUT_ACTIONS = _UI_SHORTCUT_ACTIONS


def _ui_action_tool_name(action: str) -> str:
    key = (action or "").strip()
    return _UI_ACTION_TOOL_ALIASES.get(key, key)


def _parse_agent_body(user_text: str) -> tuple[str, ChatActionPayload, str]:
    try:
        body = json.loads(user_text or "{}")
    except json.JSONDecodeError:
        return "", ChatActionPayload(), (user_text or "").strip()
    if not isinstance(body, dict):
        return "", ChatActionPayload(), (user_text or "").strip()
    payload = ChatActionPayload.parse(body.get("payload"))
    return (
        str(body.get("action") or "").strip(),
        payload,
        str(body.get("message") or "").strip(),
    )


def _tool_args_for_ui_action(action: str, payload: ChatPayload) -> dict[str, Any]:
    return bound_tool_invoke_args(action, payload, "")


def _try_ui_action_shortcut(state: ChatControlState, user_text: str) -> str | None:
    """界面按钮：只调对应工具一次，避免 LLM 同轮再调 handoff 盖掉选用结果。"""
    action = (state.user_action or "").strip()
    payload = ChatActionPayload()
    if action:
        _, payload, _ = _parse_agent_body(user_text)
    else:
        action, payload, _ = _parse_agent_body(user_text)
    if action not in _UI_SHORTCUT_ACTIONS:
        return None
    tool_name = _ui_action_tool_name(action)
    raw = invoke_named_tool(
        state,
        tool_name,
        _tool_args_for_ui_action(action, payload),
        via="ui_action",
    )
    if raw is None:
        return None
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return str((data or {}).get("reply_hint") or "").strip() or None



def run_ui_action_shortcut(
    *,
    home: Path,
    session: ChatSession,
    robot: Any,
    invoke: InvokeFn,
    emit: EmitFn | None,
    action: str,
    payload: ChatPayload,
    message: str,
) -> tuple[str, list[Any], list[dict]] | None:
    """已知 UI action：执行同名工具（Agent 未调 Tool 时的兜底，以及资料包等未绑定 Tool）。"""
    key = (action or "").strip()
    if key not in UI_SHORTCUT_ACTIONS:
        return None
    if key == "resolve_intent_clarify":
        return apply_resolve_intent_clarify(
            home=home,
            session=session,
            invoke=invoke,
            emit=emit,
            payload=payload,
            message=message,
        )
    from report_writing.chat.materials_pack_gate import MATERIALS_PACK_ACTIONS

    if key in MATERIALS_PACK_ACTIONS:
        from report_writing.chat.materials_pack_gate import apply_materials_pack_choice

        try:
            reply, cards = apply_materials_pack_choice(
                home, session, key, payload, emit=emit
            )
        except ValueError as error:
            return str(error), [], []
        return reply, list(cards or []), []
    if key in {
        "check_writing_supplements",
        "skip_writing_supplement_check",
        "dismiss_writing_supplement",
    }:
        from report_writing.chat.tool_dispatch import execute_dispatch_tool

        hit = execute_dispatch_tool(
            home,
            session,
            {"tool": key, "args": dict(payload or {})},
            str(message or ""),
            emit=emit,
            invoke=invoke,
        )
        if hit is None:
            return f"无法执行 {key}。", [], []
        reply, cards, events = hit
        return reply, list(cards or []), list(events or [])
    if key == "usage_guide":
        from report_writing.chat.product_guide import build_usage_guide_reply

        reply = build_usage_guide_reply(invoke)
        if emit is not None and reply:
            from report_writing.chat.helpers import _emit_tokens

            _emit_tokens(emit, reply)
        return reply, [], []
    state = ChatControlState(
        home=home,
        session=session,
        robot=robot,
        invoke=invoke,
        emit=emit,
        user_action=key,
        last_user_message=(message or "").strip(),
    )
    user_text = build_agent_user_text(
        action=key,
        payload=payload,
        message=message,
        session=session,
        home=home,
    )
    # UI 按钮常把主题放在 message，而 payload 为空——补进 match 等工具参数
    if key == "match_blueprints":
        body = dict(payload or {})
        if not str(body.get("message") or body.get("q") or "").strip() and (message or "").strip():
            body["message"] = (message or "").strip()
            payload = body
            user_text = build_agent_user_text(
                action=key,
                payload=payload,
                message=message,
                session=session,
                home=home,
            )
    reply = _try_ui_action_shortcut(state, user_text)
    if reply is None:
        return None
    text = (reply or "").strip() or "好的。"
    return text, list(state.cards), list(state.events)


def build_agent_user_text(
    *,
    action: str,
    payload: ChatActionPayload | dict[str, Any] | Any,
    message: str,
    session: ChatSession,
    home: Path | None = None,
) -> str:
    """把前端消息/按钮编成总控用户输入（含会话指针，便于脚本模型与 LLM）。"""
    action = (action or "").strip()
    message = (message or "").strip()
    summary = _format_recent_dialogue_summary(session)
    from report_writing.chat.session_snapshot import session_turn_snapshot

    body: dict[str, Any] = {
        "action": action,
        "payload": (
            payload.as_dict()
            if hasattr(payload, "as_dict")
            else (payload or {})
        ),
        "message": message or ("你好" if not action else ""),
        "session": {
            **session_turn_snapshot(session, message),
            "blueprint_id": session.blueprint_id or "",
            "robot_id": session.robot_id or "",
            "blueprint_wizard_step": (
                (session.freeform or {}).get("blueprint_wizard") or {}
            ).get("step")
            or "",
        },
    }
    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.react_speed import control_dialogue_max_turns
    from report_writing.chat.session_goal import get_session_goal

    dialogue_full = format_full_dialogue(session, max_turns=control_dialogue_max_turns())
    if dialogue_full:
        body["dialogue_full"] = dialogue_full
    goal = get_session_goal(session)
    if any(str(goal.get(k) or "").strip() for k in ("summary", "doc_category", "doc_title", "project")):
        body["session_goal"] = goal.to_dict()
    from report_writing.chat.session_documents import (
        focus_document_brief,
        focus_document_meta,
        session_documents_brief,
    )

    body["focus_document"] = focus_document_meta(session)
    body["focus_document_brief"] = focus_document_brief(session)
    docs_brief = session_documents_brief(session)
    if docs_brief:
        body["session_documents_brief"] = docs_brief
    if summary:
        body["recent_dialogue"] = summary
    if action:
        body["hint"] = (
            f"用户在界面点了按钮，对话指令是「{message or action}」。"
            f"本轮必须调用 Tool「{action}」一次完成（payload 原样传入）；"
            "勿改用其它工具替代。"
        )
        if action == "save_freeform_as_blueprint" and home is not None:
            from report_writing.chat.freeform_blueprint import composer_save_blueprint_state

            body["composer_save_blueprint"] = composer_save_blueprint_state(home, session)
    else:
        body["hint"] = (
            "结合 writing_brief、dialogue_full、turn_context、focus_document_brief 理解本轮后再选 Tool；"
            "何时调用见各 Tool description。默认操作当前焦点文档；用户点名其它文档或另写一份时再切换。"
        )
        from report_writing.chat.helpers import is_vague_freeform_meta
        from report_writing.chat.session_documents import (
            focus_needs_entry_path,
            is_writing_document_named,
        )

        if (
            message
            and is_writing_document_named(session)
            and focus_needs_entry_path(session)
            and is_vague_freeform_meta(message)
        ):
            body["routing_hint"] = (
                "已定名但缺写作入口：按入口卡或 suggested_next_tools 推进，不要只口头记下。"
            )
    from report_writing.chat.clarify_flow import get_pending

    pending = get_pending(session)
    if isinstance(pending, dict) and pending:
        body["pending_clarify"] = {
            "id": pending.get("id") or "",
            "source": pending.get("source") or "",
            "question": pending.get("question") or "",
            "options": list(pending.get("options") or [])[:6],
        }
        extra = (
            "有 pending_clarify：点选或口语对应选项时按该卡处理，不要另开分类。"
        )
        prev = str(body.get("routing_hint") or "").strip()
        body["routing_hint"] = f"{prev} {extra}".strip() if prev else extra
    from report_writing.chat.turn_context import build_writing_turn_context, merge_turn_context_into_agent_body
    from report_writing.chat.writing_brief import brief_to_dict_for_body

    ctx = build_writing_turn_context(session, message or "", home=home, user_action=action)
    merge_turn_context_into_agent_body(body, ctx)
    body["writing_brief"] = brief_to_dict_for_body(session, home=home)
    snap = (session.freeform or {}).get("session_gaps_snapshot")
    if isinstance(snap, dict) and snap.get("context_line"):
        body["session_gaps"] = snap
    suggested = list((session.freeform or {}).get("suggested_next_tools") or [])
    if not suggested and isinstance(snap, dict):
        suggested = list(snap.get("suggested_tools") or [])
    if suggested:
        body["suggested_next_tools"] = suggested
        extra = (
            "session 上有 suggested_next_tools：本轮按序调用并读返回的 ok / missing_slots；"
            "不要只口头复述队列。"
        )
        prev = str(body.get("routing_hint") or "").strip()
        body["routing_hint"] = f"{prev} {extra}".strip() if prev else extra
    return json.dumps(body, ensure_ascii=False)


_FINAL_TOOL_PRIORITY = (
    "select_blueprint",
    "select_write_chapters",
    "write_analyzed_only",
    "start_writing",
    "match_blueprints",
    "skip_blueprint",
)


def _final_text(messages: list[Any], state: ChatControlState) -> str:
    tool_hints: dict[str, str] = {}
    last_tool_ok = True
    last_awaiting = False
    last_hint = ""
    for message in messages or []:
        if not isinstance(message, ToolMessage):
            continue
        name = str(getattr(message, "name", None) or "").strip()
        try:
            data = json.loads(message.content or "{}")
        except Exception:
            data = {}
        hint = str((data or {}).get("reply_hint") or "").strip()
        if name and hint:
            tool_hints[name] = hint
        last_hint = hint
        last_awaiting = bool((data or {}).get("awaiting_user_choice"))
        # 遍历到最后一条 ToolMessage 时覆盖；用于观察闭环失败后采信模型改口
        last_tool_ok = bool((data or {}).get("ok", True))
    if last_tool_ok:
        for name in _FINAL_TOOL_PRIORITY:
            if name in tool_hints:
                return tool_hints[name]
    tool_hint = ""
    ai_text = ""
    for message in reversed(messages or []):
        if isinstance(message, ToolMessage) and not tool_hint:
            try:
                data = json.loads(message.content)
            except Exception:
                data = {}
            tool_hint = str((data or {}).get("reply_hint") or "").strip()
        if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None) and not ai_text:
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
    # 待补充卡等用户点选：用工具原文，禁止模型改口成「要素还是空的」且丢掉卡片
    if last_awaiting and last_hint:
        return last_hint
    # Tool 失败后模型根据 missing_slots / reply_hint 改口：优先最终 AI 文本
    if not last_tool_ok and ai_text:
        return ai_text
    if tool_hint:
        return tool_hint
    if ai_text:
        return ai_text
    if state.cards:
        return "好的，你看下方面板，点一下就能继续。"
    return "嗯，我在听——你接着说就行。"


def _parse_user_message_from_agent_text(user_text: str) -> tuple[str, str, str]:
    action = ""
    message = ""
    phase = ""
    try:
        body = json.loads(user_text or "{}")
    except json.JSONDecodeError:
        return "", user_text or "", ""
    if not isinstance(body, dict):
        return "", user_text or "", ""
    action = str(body.get("action") or "").strip()
    message = str(body.get("message") or "").strip()
    sess = body.get("session") if isinstance(body.get("session"), dict) else {}
    phase = str(sess.get("phase") or "").strip()
    return action, message, phase



def _record_oral_route(state: ChatControlState, route: str, **extra: Any) -> None:
    from report_writing.chat.agent_chain_log import log_oral_route

    payload = {"route": route, **{k: v for k, v in extra.items() if v is not None and v != ""}}
    log_oral_route(state.session, state.emit, route=route, detail=payload)
    state.events.append({"type": "oral_route", **payload})


def run_langchain_chat_control(
    *,
    model: BaseChatModel,
    state: ChatControlState,
    user_text: str,
    system_prompt: str,
    message: str,
    action: str,
) -> str:
    """LangChain create_agent 口语主路径（见 chat/AGENT_ARCHITECTURE.md）。"""
    _record_oral_route(state, "langchain_agent", message=message[:120] if message else "")
    if not (action or state.user_action) and message:
        from report_writing.chat.session_gaps import refresh_session_gaps

        clarify_invoke = _state_clarify_invoke(state) or state.invoke
        refresh_session_gaps(
            state.session,
            message=message,
            home=state.home,
            invoke=clarify_invoke,
        )

    tools = build_chat_tools(state)
    from report_writing.chat.react_speed import control_react_rounds

    result = run_react(
        model,
        tools,
        build_system_prompt(system_prompt, session=state.session, home=state.home),
        user_text,
        "chat_control",
        rounds=control_react_rounds(),
        session=state.session,
        emit=state.emit,
    )
    messages = result.get("messages") or []
    if any(isinstance(m, ToolMessage) for m in messages):
        state.did_invoke_tool = True
    return _final_text(messages, state)


def run_chat_control(
    *,
    model: BaseChatModel,
    state: ChatControlState,
    user_text: str,
    system_prompt: str,
) -> str:
    """运行一轮对话总控：卡片/按钮作为对话交给 LangChain Agent。

    点选结果与原先同名 Tool 执行一致；Agent 未调 Tool 时才走 UI 兜底。
    """
    action, message, _phase = _parse_user_message_from_agent_text(user_text)
    state.last_user_message = message
    state.user_action = state.user_action or (action or "")
    state.control_model = model
    from report_writing.chat.turn_context import ensure_control_state_context

    ensure_control_state_context(state, message)

    clarify_invoke = _state_clarify_invoke(state)

    state.agent_may_select_blueprint = (state.user_action or action or "").strip() == (
        "select_blueprint"
    )

    if not (state.user_action or action) and message:
        from report_writing.chat.session_documents import ensure_write_onboarding_started

        ensure_write_onboarding_started(
            state.session,
            state.home,
            message,
            invoke=clarify_invoke,
            emit=state.emit,
        )

    act = (state.user_action or action or "").strip()
    reply = ""
    agent_fail_reply = ""
    # 开写/重写按钮直接跑流水线，避免 ReAct 先聊一轮导致过程条丢在 done 之后
    if act in {
        "retry_chapter",
        "retry_search_chapter",
        "start_writing",
        "write_analyzed_only",
        "skip_writing_supplement_check",
    }:
        hit_reply = _try_ui_action_shortcut(state, user_text)
        if hit_reply is not None:
            return (hit_reply or "").strip() or "好的。"
    can_agent = hasattr(model, "bind_tools")
    if can_agent:
        try:
            reply = run_langchain_chat_control(
                model=model,
                state=state,
                user_text=user_text,
                system_prompt=system_prompt,
                message=message,
                action=action or state.user_action or "",
            )
        except Exception as error:
            _log.exception("control agent failed; UI action will fall back if present")
            state.did_invoke_tool = False
            from report_writing.infra.llm_errors import user_message_for_llm_error

            agent_fail_reply = (user_message_for_llm_error(error) or "").strip()
            reply = ""
    if act in UI_SHORTCUT_ACTIONS and not state.did_invoke_tool:
        payload = ChatActionPayload()
        if user_text:
            _, payload, _ = _parse_agent_body(user_text)
        hit = run_ui_action_shortcut(
            home=state.home,
            session=state.session,
            robot=state.robot,
            invoke=clarify_invoke or state.invoke,
            emit=state.emit,
            action=act,
            payload=payload,
            message=message,
        )
        if hit is not None:
            text, cards, events = hit
            state.cards.extend(cards or [])
            state.events.extend(events or [])
            _record_oral_route(state, "ui_action_fallback", action=act)
            return (text or "").strip() or "好的。"
    return (reply or "").strip() or agent_fail_reply


def _script_user_message(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("{"):
        try:
            body = json.loads(raw)
            if isinstance(body, dict):
                return str(body.get("message") or "").strip()
        except json.JSONDecodeError:
            pass
    return raw


class ChatControlScriptModel(ScriptedChatModel):
    """离线/单测：按 UI action、suggested_next_tools 与 Tool 返回 hop。不读 leftover JSON plan。"""

    role: str = "chat_control_script"

    def _hop_suggested_next_tools(
        self, messages: list[BaseMessage], phase: str
    ) -> AIMessage | None:
        """新一轮无点选时只跟 suggested_next_tools，不读 leftover JSON plan。"""
        if str(phase or "").strip() not in _SCRIPT_FOLLOW_SUGGESTED_PHASES:
            return None
        rows = _suggested_next_tools_from_human(messages)
        if not rows:
            return None
        nxt = str(rows[0].get("tool") or "").strip()
        if not nxt:
            return None
        args = rows[0].get("args")
        if not isinstance(args, dict):
            args = {}
        return self.call(nxt, dict(args))

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        action, payload, text, phase, sess_snap = _parse_human(messages)
        msg = _script_user_message(text)

        # 工具已返回 → 观察闭环：定名/记住目标后若仍缺入口则挂选路；否则用 reply_hint 收尾
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                try:
                    data = json.loads(message.content or "{}")
                except json.JSONDecodeError:
                    data = {}
                tool_name = str(getattr(message, "name", "") or "")
                if (
                    tool_name in {"set_session_meta", "remember_writing_goal"}
                    and data.get("ok") is True
                    and (
                        data.get("needs_entry_path")
                        or data.get("awaiting_path_choice")
                        or "writing_path" in (data.get("missing_slots") or [])
                        or any(k in msg for k in ("写一份", "写报告", "开写", "就用", "选用"))
                    )
                ):
                    # 同轮已挂入口卡时可收尾；否则继续 ask_entry_path
                    if data.get("awaiting_path_choice") and str(
                        data.get("reply_hint") or ""
                    ).strip():
                        return self.finish(str(data.get("reply_hint") or "").strip())
                    return self.call("ask_entry_path", {"message": msg})
                if (
                    tool_name == "match_blueprints"
                    and data.get("ok") is True
                    and data.get("awaiting_user_choice")
                ):
                    ids = [
                        str(x).strip()
                        for x in (data.get("ids") or data.get("candidate_ids") or [])
                        if str(x).strip()
                    ]
                    if len(ids) == 1 and any(
                        k in msg for k in ("就用", "选用这份", "用这份", "用这个蓝本")
                    ):
                        return self.call("select_blueprint", {"blueprint_id": ids[0]})
                    if not ids and any(
                        k in msg
                        for k in ("模版", "模板", "样板", "上传样板", "创建蓝本", "依据模版")
                    ):
                        return self.call(
                            "begin_blueprint_wizard",
                            {
                                "force_new": True,
                                "explicit_create": True,
                                "title_hint": "",
                            },
                        )
                nxt = ""
                args: dict = {}
                if isinstance(data.get("suggested_next_tools"), list):
                    steps = [
                        s
                        for s in data.get("suggested_next_tools") or []
                        if isinstance(s, dict) and str(s.get("tool") or "").strip()
                    ]
                    if steps:
                        nxt = str(steps[0].get("tool") or "").strip()
                        args = dict(steps[0].get("args") or {})
                if nxt and nxt != tool_name:
                    return self.call(nxt, args)
                if data.get("ok") is False and "doc_name" in (data.get("missing_slots") or []):
                    from report_writing.chat.session_documents import quick_title_from_write_message

                    tip = quick_title_from_write_message(msg) or ""
                    return self.call(
                        "ask_entry_path",
                        {"message": msg, "title_hint": tip},
                    )
                hint = str(data.get("reply_hint") or "").strip()
                return self.finish(hint or "好的。")
            if isinstance(message, HumanMessage):
                break

        if action:
            return self.call(
                _ui_action_tool_name(action),
                _tool_args_for_ui_action(action, payload) or {},
            )

        if not action and msg:
            for message in reversed(messages):
                if isinstance(message, HumanMessage):
                    raw = (
                        message.content
                        if isinstance(message.content, str)
                        else json.dumps(message.content, ensure_ascii=False)
                    )
                    try:
                        body = json.loads(raw) if str(raw).lstrip().startswith("{") else {}
                    except json.JSONDecodeError:
                        body = {}
                    if isinstance(body, dict) and body.get("pending_clarify"):
                        return self.call(
                            "resolve_intent_clarify",
                            {"message": msg, "option_label": msg},
                        )
                    break

        phase = phase or str(sess_snap.get("phase") or "")

        # 离线脚本模拟 ReAct：改名优先于 phase 默认工具；传结构化 title（非生产意图正则）
        if not action and msg and any(
            k in msg for k in ("改个名字", "改名为", "重命名", "标题改成", "名字改成")
        ):
            tip = ""
            for sep in ("改个名字", "改名为", "重命名为", "重命名", "标题改成", "名字改成"):
                if sep in msg:
                    tip = msg.split(sep, 1)[-1].strip().lstrip("：:为成 \t")
                    break
            tip = tip.strip("「」《》\"'“”")[:160]
            if tip:
                return self.call("set_session_meta", {"title": tip, "message": msg})

        if not action and msg and any(
            k in msg
            for k in ("查看章节", "章节信息", "这章配置", "本章配置", "看看配置", "章节配置")
        ) and "改" not in msg[:8]:
            return self.call(
                "get_chapter_info",
                {"payload_json": json.dumps({"message": msg}, ensure_ascii=False)},
            )
        if not action and msg and "生成章节说明" in msg:
            return self.call(
                "generate_chapter_describes",
                {"payload_json": json.dumps({"message": msg}, ensure_ascii=False)},
            )

        hop = self._hop_suggested_next_tools(
            messages, phase or str(sess_snap.get("phase") or "")
        )
        if hop is not None:
            return hop

        if phase == ChatPhase.CREATE_HANDOFF.value and msg:
            # 向导口语 → blueprint_wizard / begin（等同 ReAct 选 Tool，不经认领即 return 路由）
            import re as _re

            wiz = {}
            snap_wiz = sess_snap.get("blueprint_wizard") if isinstance(sess_snap, dict) else None
            if isinstance(snap_wiz, dict):
                wiz = snap_wiz
            if any(
                k in msg
                for k in (
                    "不用这个蓝本",
                    "不用蓝本",
                    "不要蓝本",
                    "放弃向导",
                    "先不用",
                    "不要这份",
                )
            ) or ("不用" in msg and "蓝本" in msg):
                # abandon 走 apply_wizard_action（via subagent），与 UI/dispatch 同语义
                return self.call("blueprint_subagent", {"user_request": msg})
            if (
                "重新提取" in msg
                or "再来一遍" in msg
                or "提取目录" in msg
                or any(k in msg for k in ("上传好了", "上传完成", "传好了"))
            ):
                # 与 abandon 相同：走 wizard resolve → apply_wizard_action
                return self.call("blueprint_subagent", {"user_request": msg})
            quoted = _re.search(r"「([^」]+)」", msg)
            if quoted and ("创建" in msg or "蓝本" in msg or "名叫" in msg or "叫" in msg):
                return self.call(
                    "blueprint_wizard",
                    {
                        "payload_json": json.dumps(
                            {"step": "meta", "title": quoted.group(1).strip()},
                            ensure_ascii=False,
                        )
                    },
                )
            if "名叫" in msg:
                title = msg.split("名叫", 1)[-1].strip()[:40]
                if title:
                    return self.call(
                        "blueprint_wizard",
                        {
                            "payload_json": json.dumps(
                                {"step": "meta", "title": title}, ensure_ascii=False
                            )
                        },
                    )
            step = str(wiz.get("step") or "")
            if step == "meta" and len(msg) >= 2:
                return self.call(
                    "blueprint_wizard",
                    {
                        "payload_json": json.dumps(
                            {"step": "meta", "title": msg[:40]}, ensure_ascii=False
                        )
                    },
                )
            return self.call("blueprint_subagent", {"user_request": msg})
        if phase == ChatPhase.FREEFORM_ASK.value:
            return self.call("submit_freeform_meta", {"message": msg})
        if phase == ChatPhase.FREEFORM_OUTLINE.value:
            return self.call("submit_outline", {"outline_text": msg})
        if phase == ChatPhase.FREEFORM_POINTS.value:
            return self.call("submit_points", {"message": msg})
        if phase == ChatPhase.READY_TO_WRITE.value:
            return self.call("get_writing_status")
        if phase in {ChatPhase.WRITING.value, ChatPhase.CONFIRM_EXPORT.value}:
            return self.call("get_writing_status")
        if phase == ChatPhase.BLUEPRINT_SELECT.value:
            return self.call("list_blueprints", {"q": ""})

        if not action and phase in {"", ChatPhase.INTENT.value} and msg:
            from report_writing.chat.helpers import is_vague_freeform_meta

            # 已定名+缺入口+含糊继续：推进入口，禁止再 remember_writing_goal
            if (
                is_vague_freeform_meta(msg)
                and bool(sess_snap.get("writing_doc_named"))
                and not str(sess_snap.get("writing_path") or "").strip()
                and not bool(sess_snap.get("skip_blueprint_path"))
            ):
                return self.call("ask_entry_path", {"message": msg})
            # 离线脚本：跳过须先于 match；勿因「跳过蓝本」含「蓝本」二字误 match
            if any(
                k in msg
                for k in (
                    "跳过蓝本",
                    "不用蓝本",
                    "不要蓝本",
                    "无蓝本",
                    "自己定大纲",
                    "直接写",
                    "直接开始写",
                )
            ):
                return self.call("skip_blueprint")
            if ("创建" in msg and "蓝本" in msg) or "上传样板" in msg:
                tip = ""
                import re as _re

                quoted = _re.search(r"「([^」]+)」", msg)
                if quoted:
                    tip = quoted.group(1).strip()
                return self.call(
                    "begin_blueprint_wizard",
                    {
                        "force_new": True,
                        "explicit_create": True,
                        "title_hint": tip,
                    },
                )
            from report_writing.chat.session_documents import message_requests_list_blueprints

            if message_requests_list_blueprints(msg):
                return self.call("list_blueprints", {"q": ""})

            from report_writing.chat.entry_intent import message_requests_blueprint_match

            wants_write = message_requests_blueprint_match(msg) or any(
                k in msg for k in ("写报告", "写一份", "开写", "就用", "选用")
            )
            if wants_write:
                # 入口不清：ask_entry_path 按 gaps 挂卡，勿无观察地默认 match
                from report_writing.chat.session_documents import quick_title_from_write_message

                tip = quick_title_from_write_message(msg) or ""
                return self.call(
                    "ask_entry_path",
                    {"message": msg, "title_hint": tip},
                )

        return self.finish(
            "嗨，跟我说你想写什么就行。"
            if not msg
            else f"收到：{msg[:80]}。（离线脚本模式请用按钮或明确 phase）"
        )


_SCRIPT_FOLLOW_SUGGESTED_PHASES = frozenset(
    {
        "",
        ChatPhase.INTENT.value,
        ChatPhase.BLUEPRINT_SELECT.value,
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.FREEFORM_POINTS.value,
    }
)


def _suggested_next_tools_from_human(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        raw = (
            message.content
            if isinstance(message.content, str)
            else json.dumps(message.content, ensure_ascii=False)
        )
        try:
            data = json.loads(raw) if str(raw or "").lstrip().startswith("{") else {}
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        rows = data.get("suggested_next_tools")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict) and str(row.get("tool") or "").strip()]
    return []


def _parse_human(messages: list[BaseMessage]) -> tuple[str, dict[str, Any], str, str, dict[str, Any]]:
    text = ""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            raw = message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)
            text = str(raw or "").strip()
            break
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            session = data.get("session") if isinstance(data.get("session"), dict) else {}
            return (
                str(data.get("action") or "").strip(),
                dict(data.get("payload") or {}),
                str(data.get("message") or "").strip(),
                str(session.get("phase") or "").strip(),
                dict(session),
            )
    return "", {}, text, "", {}
