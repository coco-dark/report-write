"""对话总控 Agent：LLM（或脚本模型）通过 Tool 推进选蓝本 / 开写 / 确认。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from report_writing.agents.react import run_react
from report_writing.agents.scripted import ScriptedChatModel, tool_payloads
from report_writing.agents.state import ChatControlState, EmitFn, InvokeFn

# 兼容旧 import
__all__ = ["ChatControlState", "run_chat_control", "build_chat_tools", "ChatControlScriptModel"]
from report_writing.domain.models import ChatCard, ChatPhase, ChatSession

_log = logging.getLogger(__name__)

CONTROL_INSTRUCTIONS = """你是「智能文档协作」对话写文档总控（角色：小墨）。
职责：根据 writing_brief、dialogue_full、session_goal、用户原话与界面 action，选择并调用 Tool 推进写作。禁止靠关键词表猜意图；禁止编造蓝本名称或工具未返回的事实。

## 0. 整段会话自主决策（开场 / 向导 / 写作中途同一套）
你是 LangChain 绑定的总控 Agent：根据 user 消息里的 writing_brief、dialogue_full、session_gaps 等上下文，**自行选择 Tool** 并读每次 Tool 返回的 observation / missing_slots 闭环。
- **确定**：本句或上下文已给出的槽位 → 立刻调对应 Tool；一句可多 Tool（改名+开写、匹配+选章、加要素+确认等）。
- **缺项**：只补还缺的（如缺章节号、缺文档名、缺写作方式、缺蓝本 id）；不要机械重走整条流水线。
- **不确定**：指代不明、写还是学未定、多文档歧义（已定名后又说「写一份关于××」是新开还是改名）、破坏性动作把握不足 → ask_intent / 澄清卡；禁止猜第一章、代选蓝本、静默开写/删除/收录/用题材改名。
- **无写文档意图**（寒暄、只看蓝本库/列表、使用说明）：禁止挂「先起名」卡、禁止 mint 空文档；**禁止** set_session_meta / ask_entry_path / 选路卡；「你好」「谢谢」等只口语回应，勿当文档名。
- **有写意图**：「写一份××」落成**工作名**即可推进（match / 向导 / 生成目录 / 开写），不要先挂定名卡；正式名称只在用户说「名字叫…」或收录/导出时确认。
- **一句多动作**：把短名、路径、生成目录、章数拆开执行；禁止把整句写入 title / set_session_meta。
  「作lzb述职报告 自动生成章节目录 三个大章节就可以」→ 工作名 lzb述职报告 + skip_blueprint + submit_outline（一级章=3）。
  「再给我写一份述职报告 三个章节 名字叫做lzb述职报告」→ 定名 lzb述职报告 + 无蓝本自动大纲三章；不要开模版向导。
- 无固定步骤顺序；缺什么补什么；禁止对用户展示 Tool 名。
- **界面点选**：user JSON 的 action 非空 = 点了按钮/卡片，本轮须调用同名 Tool（含 resolve_intent_clarify），payload 原样传入，不要另猜意图。点选与口语等价。

观察闭环：调 Tool → 读 ok / observation / missing_slots → 再决定下一跳或收尾。
硬门闩仅限：写盘必须落在当前车道；收录/导出前须正式定名。工作名即可匹配蓝本、向导、生成目录、开写。

## 1. 说话与决策原则
- 语气：口语、简短、先接住再指路；禁止工单腔。
- 真相源：writing_brief / dialogue_full / session_goal / focus_document；已说过的勿重复追问。
- 下一步提示：body.suggested_next_step 可参考；与用户原话冲突时以用户原话为准。
- 效率：主诉求可多跳完成；每跳根据 observation 校正；缺参以 missing_slots 回流。

## 2. 开新文档（同一原则的特例）
必要槽随本句意图变化，不要按「先起名再选路再写」整机重入。
有蓝本：match_blueprints → select_blueprint → 分析/选章 → start_writing。
无蓝本：begin_blueprint_wizard（模版）或 skip_blueprint → submit_outline 等。
「名字叫…」或 slots.doc_name → 正式定名；「写一份××」→ 工作名，立刻可 match/向导/写。
「我有模版 / 用我的模版」→ begin_blueprint_wizard(explicit_create=true)；**禁止** set_session_meta 把题材当成已定名。
向导 meta 卡可顺带收名称，但不是开场必经步骤。

## 3. 写作中途示例（须同样灵活）
- **改名 ≠ 起名**：「改名叫 / 名字改一下叫做 X」→ 只 set_session_meta(title=X)，回复已改名；禁止再挂「先给这份文档起个名字」澄清卡。
- 「改名叫年度总结然后开始写」→ 定名 + 开写相关 Tool（已绑蓝本则选章/开写；未绑则问写作方式）。
- 「写这一章」没说哪一章 → 追问章节，勿猜。
- 「确认这一章并停止」→ confirm_chapter(pause_after=true)；勿确认后又自动写下章。
- 「只保留前两章」→ 裁目录后 submit_outline。
- 加要素/关联/收入库：缺参就问；同名多个挂澄清点选。
- 开写前缺要点：先问是否生成；同意后再 generate_freeform_points。
- 多章未指定 writing_pace 时 start_writing 会先澄清「一次写完 / 写一章看一章」；开写前可 check_writing_supplements 查待补要素；用户说先写再说 → skip_writing_supplement_check；卡片上忽略某项 → dismiss_writing_supplement（仅取消该章关联）。

## 4. 工具调用约定
- 你只能调用已绑定的 Tool；**何时调用、参数与禁忌见各 Tool 的 description**（LangChain 工具注解），不要在脑中另备一份工具表。
- 每调一次 Tool 必读 ok / observation / missing_slots / reply_hint，再决定下一跳或口语收尾。
- 按章检索、写正文、核对引用由后台 supervisor 流水线执行，勿在对话里编造章节正文。

## 5. 蓝本库 vs 对话内「依据模版」（必守）
- **蓝本库**（bp*）：不在对话里新建完成入库或删除；请在「蓝本管理」维护。
- **对话内** begin_blueprint_wizard / blueprint_wizard：当前文档 w* 副本；收录前不入库。
- **写入蓝本库**：save_freeform_as_blueprint 或文档窗「收录到蓝本库」。
- **更新已入库**：sync_document_to_blueprint_library。
- 对话不提供删库蓝本 Tool。

## 6. 闲聊与模糊指令
- 可口头回应并追问；或选最合理的 Tool；勿仅因 phase 枚举拒绝执行。
- 已定名但仍缺写作入口（有蓝本/无蓝本）时：用户说「继续 / 好的 / 下一步」→ 只调 ask_entry_path（或等入口卡点选）；禁止再 remember_writing_goal / set_session_meta 只回「记下了」。
"""

from report_writing.chat.intent_router import user_chose_skip_blueprint


def _dump(data: dict[str, Any]) -> str:
    """统一 Tool JSON：补齐 observation，便于 ReAct 下一跳阅读。"""
    body = dict(data or {})
    if "observation" not in body or not str(body.get("observation") or "").strip():
        ok = bool(body.get("ok", True))
        hint = str(body.get("reply_hint") or "").strip()
        missing = body.get("missing_slots")
        err = str(body.get("error") or "").strip()
        parts: list[str] = []
        if missing:
            slots = missing if isinstance(missing, (list, tuple)) else [missing]
            parts.append("缺少参数: " + ", ".join(str(s) for s in slots if str(s).strip()))
            parts.append("请向用户确认后再次调用并传入结构化参数。")
        if err:
            parts.append(f"错误: {err}")
        if hint:
            parts.append(hint[:240])
        if not parts:
            parts.append("成功。" if ok else "失败，请根据上下文改调或向用户澄清。")
        body["observation"] = " ".join(parts)
    return json.dumps(body, ensure_ascii=False)


def _obs_fail(
    *,
    reply_hint: str,
    observation: str,
    missing_slots: list[str] | None = None,
    error: str = "",
    **extra: Any,
) -> str:
    body: dict[str, Any] = {
        "ok": False,
        "reply_hint": (reply_hint or "").strip(),
        "observation": (observation or "").strip(),
    }
    if missing_slots:
        body["missing_slots"] = list(missing_slots)
    if error:
        body["error"] = error
    body.update(extra)
    return _dump(body)


def _gate_writing_doc_named(state: ChatControlState, tool_name: str) -> str | None:
    """按 Tool 能力检查缺槽；工作名即可开写，正式名仅收录/导出需要。"""
    from report_writing.chat.session_documents import mark_focus_entry_path
    from report_writing.chat.tool_capability import (
        capability_observation,
        capability_reply_hint,
        missing_for_tool,
    )

    bid = str(getattr(state.session, "blueprint_id", "") or "").strip()
    if bid:
        mark_focus_entry_path(state.session, "match")

    miss = missing_for_tool(state.session, tool_name, home=state.home)
    if not miss:
        return None
    return _obs_fail(
        missing_slots=miss,
        reply_hint=capability_reply_hint(miss),
        observation=capability_observation(tool_name, miss),
    )


def _card_types(cards: list[Any] | None) -> set[str]:
    return {str(getattr(c, "type", "") or "").strip() for c in (cards or []) if c}


def _emit_process(state: ChatControlState, message: str, **extra: Any) -> None:
    from report_writing.chat.user_process import emit_user_process

    emit_user_process(state.emit, message, **extra)


def _tool_begin(state: ChatControlState, tool_name: str) -> None:
    from report_writing.chat.user_process import emit_tool_step

    state.did_invoke_tool = True
    emit_tool_step(state.emit, tool_name)


def _tool_done(state: ChatControlState, tool_name: str) -> None:
    from report_writing.chat.user_process import emit_tool_done

    emit_tool_done(state.emit, tool_name)


def _invoke_tool(state: ChatControlState, tool: Any, args: dict[str, Any], *, via: str = "control") -> str:
    from report_writing.chat.agent_chain_log import log_tool_invoke

    log_tool_invoke(
        state.session,
        state.emit,
        tool=str(getattr(tool, "name", None) or "?"),
        args=args,
        source=via,
        turn_context=getattr(state, "turn_context", None),
    )
    return tool.invoke(args)


_ENTRY_CLARIFY_SOURCES = frozenset({"entry", "entry_branch", "entry_freeform_mode"})


def _card_clarify_source(card: ChatCard) -> str:
    return str((getattr(card, "payload", None) or {}).get("source") or "").strip()


def _is_entry_clarify_card(card: ChatCard) -> bool:
    return _card_clarify_source(card) in _ENTRY_CLARIFY_SOURCES


# 同轮并行工具会互相覆盖 state.cards。选蓝本卡一旦列出，改名/定题不得把它清掉。
_KEEP_WHEN_REPLACED = frozenset({"blueprint_list", "skip_blueprint"})


def _set_cards(state: ChatControlState, cards: list[ChatCard]) -> None:
    incoming = list(cards or [])
    incoming_types = {str(getattr(card, "type", "") or "") for card in incoming}
    kept = [
        card
        for card in (state.cards or [])
        if str(getattr(card, "type", "") or "") in _KEEP_WHEN_REPLACED
        and str(getattr(card, "type", "") or "") not in incoming_types
    ]
    state.cards = kept + incoming


def _merge_cards_keep_entry_clarify(
    state: ChatControlState, cards: list[ChatCard] | None
) -> list[ChatCard]:
    """remember 等不得用空列表冲掉入口澄清卡。"""
    incoming = list(cards or [])
    if any(_is_entry_clarify_card(c) for c in incoming):
        return incoming
    existing = [c for c in (state.cards or []) if _is_entry_clarify_card(c)]
    if not existing:
        return incoming
    if not incoming:
        return existing
    return list(existing) + [c for c in incoming if not _is_entry_clarify_card(c)]


def _hang_entry_path_after_named(
    state: ChatControlState,
    *,
    message: str = "",
    title_hint: str = "",
    after_doc_name_confirm: bool = True,
) -> tuple[str, list[ChatCard]]:
    """已定名缺入口：挂有蓝本/无蓝本（或模版/自动大纲）澄清卡。"""
    from report_writing.chat.intent_clarify import build_entry_path_clarify

    intent_fn = state.intent_invoke if state.intent_invoke is not None else state.invoke
    return build_entry_path_clarify(
        state.session,
        message=(message or state.last_user_message or "").strip(),
        title_hint=(title_hint or "").strip(),
        invoke=intent_fn,
        home=state.home,
        emit=state.emit,
        after_doc_name_confirm=after_doc_name_confirm,
        apply_plan=bool((message or state.last_user_message or "").strip()),
    )


def _ask_path_after_no_blueprint_match(state: ChatControlState, message: str) -> str:
    """无对口蓝本：问清两条主路（有蓝本 / 无蓝本），禁止静默自动无蓝本。"""
    from report_writing.chat.session_documents import patch_focus_onboarding
    from report_writing.chat.write_path_after_match import reply_ask_write_path

    # 匹配未中：解开试探性 match 锁，让入口支线卡能挂出来
    patch_focus_onboarding(
        state.session, entry_path="", entry_branch="", preferred_entry_path=""
    )
    msg = (message or state.last_user_message or "").strip()
    _emit_process(state, "没找到特别对口的蓝本，想先确认你怎么开始…")
    reply, cards = reply_ask_write_path(
        state.session,
        msg,
        invoke=state.intent_invoke or state.invoke,
    )
    _set_cards(state, cards)
    hint = (reply or "").strip() or (
        "库里暂时没特别贴合的蓝本。你可以选：上传样板建蓝本、无蓝本自拟目录，或再说说要写什么。"
    )
    return _dump(
        {
            "ok": True,
            "reply_hint": hint,
            "matched": 0,
            "awaiting_path_choice": True,
            "phase": state.session.phase,
        }
    )


def _finish_blueprint_match(
    state: ChatControlState,
    message: str,
    agent_reply: str,
    matches: list,
) -> str:
    """匹配蓝本：仅用户明确点名时代选，否则出列表让用户选用。"""
    from report_writing.chat.intent_router import allows_agent_blueprint_select, pick_auto_blueprint_id, touch_session_plan
    from report_writing.chat_orchestrator import _action_select_blueprint, _blueprint_list_card, _skip_blueprint_card

    msg = message or state.last_user_message
    auto_id = ""
    if allows_agent_blueprint_select(
        message=msg,
        user_action=state.user_action or "",
        invoke=state.intent_invoke or state.invoke,
        session=state.session,
    ):
        auto_id = pick_auto_blueprint_id(msg, matches, invoke=state.intent_invoke or state.invoke)
    if auto_id:
        _emit_process(state, "已按你的描述选定蓝本，正在准备章节…")
        reply, cards = _action_select_blueprint(state.home, state.session, {"blueprint_id": auto_id})
        _set_cards(state, cards)
        touch_session_plan(
            state.session,
            goal="write_with_blueprint",
            blueprint_id=auto_id,
            auto_selected=True,
        )
        title = next((getattr(m, "title", "") for m in matches if getattr(m, "id", "") == auto_id), auto_id)
        hint = (
            f"好，我已经帮你订上「{title or auto_id}」了。"
            f"{reply.strip()}"
        ).strip()
        from report_writing.chat.session_documents import append_doc_name_clarify_if_needed, is_writing_document_named

        hint, cards = append_doc_name_clarify_if_needed(
            state.session,
            message=msg,
            reply=hint,
            cards=cards,
            invoke=state.intent_invoke or state.invoke,
        )
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": hint,
                "matched": len(matches),
                "auto_selected": True,
                "blueprint_id": auto_id,
                "phase": state.session.phase,
                "needs_doc_name": not is_writing_document_named(state.session),
            }
        )

    if matches:
        state.session.phase = ChatPhase.BLUEPRINT_SELECT.value
        cards = [_blueprint_list_card(matches), _skip_blueprint_card()]
        from report_writing.chat.action_slots import build_action_slot_clarify

        clarify_reply, clarify_cards = build_action_slot_clarify(
            state.session,
            "select_blueprint",
            {"message": message or msg},
            ["blueprint_id"],
            message=message or msg,
            blueprints=matches,
        )
        cards = list(clarify_cards or []) + cards
        _set_cards(state, cards)
        hint = (
            (agent_reply or f"我帮你挑了 {len(matches)} 份可能合适的。").rstrip()
            + " "
            + (clarify_reply or "看中哪份点「选用」，不想用蓝本就点跳过，咱也能从零写。")
        )
        from report_writing.chat.session_documents import (
            append_doc_name_clarify_if_needed,
            is_writing_document_named,
        )

        hint, cards = append_doc_name_clarify_if_needed(
            state.session,
            message=message or msg,
            reply=hint.strip(),
            cards=cards,
            invoke=state.intent_invoke or state.invoke,
        )
        _set_cards(state, cards)
        touch_session_plan(
            state.session,
            goal="pick_blueprint",
            candidates=[getattr(m, "id", "") for m in matches],
        )
        return _dump(
            {
                "ok": True,
                "reply_hint": hint,
                "matched": len(matches),
                "ids": [m.id for m in matches],
                "awaiting_user_choice": True,
                "needs_doc_name": not is_writing_document_named(state.session),
            }
        )

    return _ask_path_after_no_blueprint_match(state, message or state.last_user_message or "")


def build_chat_tools(state: ChatControlState) -> list:
    """绑定会话状态的总控工具。"""

    @tool
    def list_blueprints(q: str = "") -> str:
        """列出可用蓝本；可选关键词 q。"""
        from report_writing.chat_orchestrator import _action_list_blueprints

        _tool_begin(state, "list_blueprints")
        reply, cards = _action_list_blueprints(state.home, state.session, {"q": q or ""})
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply, "count": len(cards[0].payload.get("items") or []) if cards else 0})

    @tool
    def ask_entry_path(message: str = "", title_hint: str = "") -> str:
        """新建文档：读 gaps，只挂缺的澄清卡；已明确的要素见 suggested_next_tools（顺序非固定）。"""
        from report_writing.chat.intent_clarify import build_entry_path_clarify
        from report_writing.chat.session_documents import is_writing_document_named

        msg = (message or state.last_user_message or "").strip()
        tip = (title_hint or "").strip()
        _tool_begin(state, "ask_entry_path")
        intent_fn = state.intent_invoke
        if intent_fn is None and state.invoke is not None:
            from report_writing.chat.intent_invoke import intent_invoke_fn

            intent_fn = intent_invoke_fn(state.invoke)
        reply, cards = build_entry_path_clarify(
            state.session,
            message=msg,
            title_hint=tip,
            invoke=intent_fn if intent_fn is not None else state.invoke,
            home=state.home,
            emit=state.emit,
        )
        # 一句多意图：首句定名后的残余路径 / follow spec
        ff = dict(state.session.freeform or {})
        follow_spec = ff.pop("clarify_follow_spec", None)
        if isinstance(follow_spec, dict):
            from report_writing.chat.intent_clarify import execute_clarify_spec

            state.session.freeform = ff
            reply2, cards2, events2 = execute_clarify_spec(
                state.home,
                state.session,
                follow_spec,
                message=msg,
                emit=state.emit,
                invoke=intent_fn if intent_fn is not None else state.invoke,
            )
            state.events.extend(events2 or [])
            if reply2:
                reply = f"{reply}\n{reply2}".strip()
            cards = list(cards or []) + list(cards2 or [])
        # 无论有没有 follow spec，澄清卡都要落到本轮消息上。
        # 以前只在 follow spec 分支里 _set_cards，其余路径函数直接结束、工具返回 null，
        # 模型就只会口头说「入口卡片已经出来啦」，气泡下面没有按钮。
        _set_cards(state, cards)
        named = is_writing_document_named(state.session)
        pending_src = str(
            ((state.session.freeform or {}).get("intent_clarify") or {}).get("source") or ""
        )
        gaps = dict((state.session.freeform or {}).get("onboarding_gaps_snapshot") or {})
        suggested = list((state.session.freeform or {}).get("suggested_next_tools") or [])
        obs_parts = [
            "已执行 ask_entry_path（只挂卡/观察，未在 clarify 内静默 dispatch）。",
            f"pending_clarify={pending_src or 'none'}",
            f"onboarding_gaps={gaps}",
        ]
        if suggested:
            obs_parts.append(
                f"suggested_next_tools={suggested}；同轮按缺项调用（不必固定先定名再 match），每跳读 observation。"
            )
        elif not pending_src and named and not gaps.get("missing_path"):
            obs_parts.append("入口缺口已齐，可直接 match/skip/wizard/submit_outline。")
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": " ".join(obs_parts),
                "onboarding": gaps,
                "pending_clarify": pending_src or None,
                "suggested_next_tools": suggested,
                "needs_doc_name": bool(gaps.get("missing_name")),
                "awaiting_path_choice": pending_src
                in {
                    "doc_name",
                    "entry",
                    "entry_branch",
                    "entry_freeform_mode",
                },
            }
        )

    @tool
    def match_blueprints(message: str = "") -> str:
        """按用户需求匹配蓝本（读用途说明）；仅用户明确匹配/入口已选「先匹配」后调用。不擅自选用。"""
        from report_writing.chat.blueprint_actions import run_blueprint_action

        from report_writing.chat.session_documents import (
            is_writing_document_named,
            quick_title_from_write_message,
            stash_pending_entry,
        )

        msg = (message or state.last_user_message or "").strip()
        if not msg:
            return _obs_fail(
                missing_slots=["message"],
                reply_hint="跟我说一下要写什么，我再帮你找蓝本。",
                observation=(
                    "match_blueprints 缺少 message。"
                    "请向用户确认写作主题后再次调用并传入 message=原话或主题摘要。"
                ),
            )
        named = is_writing_document_named(state.session)
        if not named:
            from report_writing.chat.session_documents import apply_working_title

            working = quick_title_from_write_message(msg)
            if working:
                apply_working_title(
                    state.session, working, home=state.home, message=msg
                )
            stash_pending_entry(state.session, message=msg, title=working)
        _tool_begin(state, "match_blueprints")
        reply, cards = run_blueprint_action(
            state.home,
            state.session,
            "match_blueprints",
            {"message": msg},
            invoke=state.invoke,
            emit=state.emit,
        )
        _set_cards(state, cards)
        types = _card_types(cards)
        matched_n = 0
        for c in cards or []:
            if getattr(c, "type", "") == "blueprint_list":
                items = (getattr(c, "payload", None) or {}).get("items") or []
                if isinstance(items, list):
                    matched_n = len(items)
        obs_parts = [
            f"match_blueprints 完成：候选约 {matched_n} 份。",
            (
                "请用户点选或 select_blueprint(blueprint_id=…)。"
                if matched_n
                else "无对口蓝本：可 ask_entry_path / begin_blueprint_wizard(explicit_create=true) / skip_blueprint。"
            ),
        ]
        needs_name = not is_writing_document_named(state.session)
        if needs_name:
            obs_parts.append(
                "尚未正式定名：工作名可继续写；收录/导出前再确认正式名称。"
            )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "matched": matched_n,
                "observation": " ".join(obs_parts),
                "awaiting_path_choice": "intent_clarify" in types or matched_n == 0,
                "needs_doc_name": needs_name,
            }
        )

    def _invoke_subagent(run_fn, user_request: str, *, err: str) -> str:
        if state.control_model is None:
            return _dump({"ok": False, "reply_hint": err})
        text = (user_request or state.last_user_message or "").strip()
        return run_fn(state.control_model, state, text)

    @tool
    def blueprint_subagent(user_request: str = "") -> str:
        """子 Agent：蓝本（向导、选型、目录、学习步）。传入用户原话。"""
        from report_writing.agents.subagents.blueprint import run_blueprint_subagent

        return _invoke_subagent(
            run_blueprint_subagent,
            user_request,
            err="写作助手未就绪，暂时无法处理蓝本。",
        )

    @tool
    def blueprint_wizard_subagent(user_request: str = "") -> str:
        """同 blueprint_subagent（兼容旧名）。"""
        from report_writing.agents.subagents.blueprint import run_blueprint_subagent

        return _invoke_subagent(
            run_blueprint_subagent,
            user_request,
            err="写作助手未就绪，暂时无法配置蓝本。",
        )

    @tool
    def render_subagent(user_request: str = "") -> str:
        """子 Agent：渲染模版（导出 Word 前配置）。"""
        from report_writing.agents.subagents.render import run_render_subagent

        return _invoke_subagent(
            run_render_subagent,
            user_request,
            err="写作助手未就绪，暂时无法处理渲染模版。",
        )

    @tool
    def style_subagent(user_request: str = "") -> str:
        """子 Agent：Word 导出样式。"""
        from report_writing.agents.subagents.style import run_style_subagent

        return _invoke_subagent(
            run_style_subagent,
            user_request,
            err="写作助手未就绪，暂时无法处理 Word 样式。",
        )

    @tool
    def select_blueprint(blueprint_id: str = "") -> str:
        """选用蓝本：用户点「选用」或口语明确给出 blueprint_id 时调用。禁止无 id 代选。"""
        from report_writing.chat_orchestrator import _action_select_blueprint

        bid = (blueprint_id or "").strip()
        if not bid:
            return _obs_fail(
                missing_slots=["blueprint_id"],
                reply_hint="还没拿到蓝本 id，请再点一次「选用」或告诉我用哪一份。",
                observation=(
                    "select_blueprint 缺少 blueprint_id。"
                    "请从候选列表取 id 后再次调用，或让用户点选用。"
                ),
            )
        _emit_process(state, "正在选用你点的蓝本…")
        reply, cards = _action_select_blueprint(
            state.home, state.session, {"blueprint_id": bid}
        )
        _set_cards(state, cards)
        _emit_process(state, "蓝本已选好，正在准备章节列表…")
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "phase": state.session.phase,
                "blueprint_id": bid,
                "observation": (
                    f"已选用蓝本 {bid}。下一步：未分析则引导分析；"
                    "已分析则 select_write_chapters / start_writing。"
                ),
            }
        )

    @tool
    def select_write_chapters(blueprint_id: str = "", chapter_keys_json: str = "[]") -> str:
        """用户勾选要写的已分析章节后按顺序依次开写。chapter_keys_json 为章节 key 数组 JSON。"""
        from report_writing.chat_orchestrator import _action_select_write_chapters

        named_gate = _gate_writing_doc_named(state, "select_write_chapters")
        if named_gate:
            return named_gate
        try:
            keys = json.loads(chapter_keys_json or "[]")
        except json.JSONDecodeError:
            keys = []
        if not isinstance(keys, list):
            keys = []
        keys = [str(k).strip() for k in keys if str(k).strip()]
        bid = (blueprint_id or state.session.blueprint_id or "").strip()
        if not keys:
            sess_keys = [
                str(k).strip()
                for k in ((state.session.freeform or {}).get("write_chapter_keys") or [])
                if str(k).strip()
            ]
            if not sess_keys:
                return _obs_fail(
                    missing_slots=["chapter_keys"],
                    reply_hint="还没勾选要写的章节，请在文档窗口勾选后再开写。",
                    observation=(
                        "select_write_chapters 缺少 chapter_keys。"
                        "请让用户在文档窗勾选，或从对话解析章序号后传入 chapter_keys_json；"
                        "若章未分析，先引导分析再开写。"
                    ),
                    blueprint_id=bid,
                )
            keys = sess_keys
        payload = {"blueprint_id": bid, "chapter_keys": keys}
        _emit_process(state, f"好的，按勾选的 {len(keys)} 章依次开写…")
        reply, cards = _action_select_write_chapters(
            state.home, state.session, payload, emit=state.emit, invoke=state.invoke
        )
        _set_cards(state, cards)
        types = _card_types(cards)
        if types & {"analysis_gate", "chapter_picker"}:
            return _obs_fail(
                missing_slots=["analyzed_chapters"]
                if "analysis_gate" in types
                else ["chapter_keys"],
                reply_hint=reply,
                observation=(
                    "select_write_chapters 未真正开写：仍需分析或选章。"
                    "建议文档窗分析、或 write_analyzed_only / re_study_chapters 后再试。"
                ),
                count=len(keys),
            )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "count": len(keys),
                "observation": f"已按 {len(keys)} 章推进开写。可 get_writing_status 查看进度。",
            }
        )

    @tool
    def write_analyzed_only(blueprint_id: str = "", chapter_keys_json: str = "[]") -> str:
        """兼容旧入口：按勾选章节依次开写（无勾选则提示在文档窗选章）。"""
        from report_writing.chat_orchestrator import _action_write_analyzed_only

        named_gate = _gate_writing_doc_named(state, "write_analyzed_only")
        if named_gate:
            return named_gate
        try:
            keys = json.loads(chapter_keys_json or "[]")
        except json.JSONDecodeError:
            keys = []
        if not isinstance(keys, list):
            keys = []
        keys = [str(k).strip() for k in keys if str(k).strip()]
        payload = {
            "blueprint_id": (blueprint_id or state.session.blueprint_id or "").strip(),
            "chapter_keys": keys,
        }
        if keys:
            _emit_process(state, f"好的，按勾选的 {len(keys)} 章依次开写…")
        reply, cards = _action_write_analyzed_only(
            state.home, state.session, payload, emit=state.emit, invoke=state.invoke
        )
        _set_cards(state, cards)
        types = _card_types(cards)
        if types & {"analysis_gate", "chapter_picker"} and not keys:
            return _obs_fail(
                missing_slots=["chapter_keys"],
                reply_hint=reply,
                observation=(
                    "write_analyzed_only：尚无可写勾选。"
                    "请用户勾选已分析章，或先完成分析后再调用。"
                ),
            )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "task_id": state.session.task_id,
                "observation": "write_analyzed_only 已执行。可 get_writing_status 查看。",
            }
        )

    @tool
    def skip_blueprint() -> str:
        """跳过蓝本，走无蓝本大纲路径。"""
        from report_writing.chat_orchestrator import _action_skip_blueprint

        from report_writing.chat.session_documents import is_writing_document_named

        from report_writing.chat.write_path_after_match import skip_blueprint_should_reask_path

        if skip_blueprint_should_reask_path(
            state.session, state.last_user_message or ""
        ):
            from report_writing.chat.write_path_after_match import (
                abandon_active_blueprint_and_ask_path,
            )

            _tool_begin(state, "skip_blueprint")
            reply, cards = abandon_active_blueprint_and_ask_path(
                state.home,
                state.session,
                state.last_user_message or "",
                invoke=state.intent_invoke or state.invoke,
            )
            _set_cards(state, cards)
            return _dump(
                {
                    "ok": True,
                    "reply_hint": reply,
                    "observation": "已放弃当前向导/蓝本，请用户另选写作方式。",
                    "awaiting_path_choice": True,
                }
            )

        named = is_writing_document_named(state.session)
        _tool_begin(state, "skip_blueprint")
        reply, cards = _action_skip_blueprint(
            state.session,
            home=state.home,
            invoke=state.invoke,
            emit=state.emit,
        )
        _set_cards(state, cards)
        obs = (
            "已跳过蓝本。下一步：题目未清则 set_session_meta；"
            "有目录意图再 submit_outline；可写则 start_writing。"
        )
        if not named:
            obs += " 文档尚未定名，开写前须 set_session_meta(title=…)。"
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": obs,
                "needs_doc_name": not named,
            }
        )

    @tool
    def begin_blueprint_wizard(
        force_new: bool = True,
        title_hint: str = "",
        explicit_create: bool = False,
        confirmed_create: bool = False,
        from_entry_path: bool = False,
    ) -> str:
        """依据模版编写向导（仅当前文档 w*，收录前不入蓝本库）。不是 save_freeform_as_blueprint。"""
        from report_writing.chat.blueprint_actions import run_blueprint_action
        from report_writing.chat.session_documents import is_writing_document_named

        ui = (state.user_action or "").strip() == "begin_blueprint_wizard"
        if not (explicit_create or confirmed_create or from_entry_path or ui):
            return _obs_fail(
                missing_slots=["explicit_create"],
                reply_hint="要依据模版编写的话跟我说一声，或选「无蓝本 · 依据模版编写」。",
                observation=(
                    "begin_blueprint_wizard 未带 explicit_create/confirmed_create。"
                    "路径不清时先 ask_entry_path 或 match_blueprints；"
                    "用户明确要创建/上传样板后再调用并设 explicit_create=true。"
                ),
            )
        named = is_writing_document_named(state.session)
        _tool_begin(state, "begin_blueprint_wizard")
        reply, cards = run_blueprint_action(
            state.home,
            state.session,
            "begin_blueprint_wizard",
            {
                "force_new": bool(force_new),
                "title_hint": str(title_hint or "").strip(),
                "explicit_create": bool(explicit_create) or ui,
                "confirmed_create": bool(confirmed_create),
                "from_entry_path": bool(from_entry_path),
            },
            invoke=state.intent_invoke or state.invoke,
            emit=state.emit,
        )
        _set_cards(state, cards)
        obs = (
            "已进入依据模版编写向导（仅本文档，未入蓝本库）。按卡片：meta → upload → outline → study；"
            "满意后用户可在文档窗「收录到蓝本库」，已收录则可用 sync_document_to_blueprint_library。"
            "口语命令可调 blueprint_wizard。"
        )
        if not named:
            obs += " 文档尚未定名，可在向导 meta 步或 set_session_meta 补题目。"
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": obs,
                "needs_doc_name": not named,
            }
        )

    @tool
    def handoff_blueprint_page(blueprint_id: str = "", title: str = "去蓝本分析配置") -> str:
        """引导用户去蓝本页完善分析/目录。若已有「仅写已分析」选项，不得清掉。"""
        from report_writing.chat.intent_router import active_blueprint_wizard
        from report_writing.chat_orchestrator import (
            _ensure_analysis_choice_cards,
            _handoff_card,
        )
        from report_writing.domain.models import ChatPhase

        if active_blueprint_wizard(state.session):
            kept = [c for c in (state.cards or []) if getattr(c, "type", "") != "handoff"]
            _set_cards(state, kept)
            return _dump(
                {
                    "ok": False,
                    "reply_hint": (
                        "咱们正在对话里配蓝本，不用去蓝本页。"
                        "你可以说「重新提取目录」「重新上传样板」，或点下方蓝本向导卡片。"
                    ),
                }
            )

        bid = (blueprint_id or state.session.blueprint_id or "").strip()
        if bid:
            state.session.blueprint_id = bid
        state.session.phase = ChatPhase.CREATE_HANDOFF.value
        # 保留分析门闸双选项；仅在没有时补上 handoff
        kept = [
            c
            for c in (state.cards or [])
            if getattr(c, "type", "")
            in {"analysis_gate", "write_analyzed_only", "chapter_picker", "handoff"}
        ]
        if not any(getattr(c, "type", "") == "handoff" for c in kept):
            kept.append(_handoff_card(state.session, blueprint_id=bid, title=title or "去蓝本分析配置"))
        kept = _ensure_analysis_choice_cards(state.home, state.session, kept)
        _set_cards(state, kept)
        has_write = any(
            getattr(c, "type", "") in {"analysis_gate", "write_analyzed_only", "chapter_picker"}
            for c in kept
        )
        hint = (
            "蓝本还有章没分析完。你可以去蓝本页补分析，也可以先勾选已分析章节开写。"
            if has_write
            else "这本蓝本还有点没就绪，你先去蓝本页把分析配齐，弄完点「返回对话」就行。"
        )
        return _dump({"ok": True, "reply_hint": hint})

    @tool
    def resume_from_blueprint(blueprint_id: str = "", task_id: str = "") -> str:
        """从蓝本页返回对话后继续。"""
        from report_writing.chat_orchestrator import _action_resume

        payload = {}
        if blueprint_id:
            payload["blueprint_id"] = blueprint_id
        if task_id:
            payload["task_id"] = task_id
        reply, cards = _action_resume(state.home, state.session, payload)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def start_writing(writing_pace: str = "", chapter_keys_json: str = "[]") -> str:
        """开始写作。多章且未指定 writing_pace 时会先澄清 batch|step，并可能返回待补充检查卡。writing_pace=batch|step；chapter_keys_json 为勾选章节 key 数组。"""
        from report_writing.chat_orchestrator import _action_start_writing

        named_gate = _gate_writing_doc_named(state, "start_writing")
        if named_gate:
            return named_gate
        pace = (writing_pace or "").strip()
        try:
            keys = json.loads(chapter_keys_json or "[]")
        except json.JSONDecodeError:
            keys = []
        if not isinstance(keys, list):
            keys = []
        keys = [str(k).strip() for k in keys if str(k).strip()]
        if keys:
            _emit_process(state, f"好的，按勾选的 {len(keys)} 章依次开写…")
        else:
            _emit_process(state, "正在开始写作…")
        reply, cards, events = _action_start_writing(
            state.home,
            state.session,
            emit=state.emit,
            writing_pace=pace,
            chapter_keys=keys,
            invoke=state.invoke,
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        types = _card_types(cards)
        if types & {"analysis_gate", "chapter_picker", "intent_clarify"}:
            missing: list[str] = []
            if "chapter_picker" in types:
                missing.append("chapter_keys")
            if "analysis_gate" in types:
                missing.append("analyzed_chapters")
            if "intent_clarify" in types:
                missing.append("doc_name")
            return _obs_fail(
                missing_slots=missing or ["chapter_keys"],
                reply_hint=reply,
                observation=(
                    "start_writing 未真正开写。"
                    "缺章则让用户勾选或传入 chapter_keys_json；"
                    "未分析则先分析/re_study_chapters；"
                    "未定名则 set_session_meta(title=…)。"
                ),
                task_id=state.session.task_id or "",
            )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "task_id": state.session.task_id,
                "observation": (
                    f"已 start_writing（pace={pace or 'default'}）。"
                    "可 get_writing_status；章确认用 confirm_chapter。"
                ),
            }
        )

    @tool
    def get_writing_status() -> str:
        """查看当前写作任务进度与待确认章节。"""
        from report_writing.chat_orchestrator import _writing_status

        reply, cards = _writing_status(state.home, state.session)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def prepare_export_word() -> str:
        """用户要导出 Word：附上导出卡片与下载说明。"""
        from report_writing.chat.tool_capability import missing_for_tool
        from report_writing.chat.writing import _prepare_export_word

        miss = missing_for_tool(state.session, "prepare_export_word", home=state.home)
        if "doc_name" in miss:
            from report_writing.chat.intent_clarify import build_document_name_clarify

            reply, cards = build_document_name_clarify(
                state.session,
                message=state.last_user_message or "",
                home=state.home,
                emit=state.emit,
                follow_entry=False,
            )
            _set_cards(state, cards)
            return _obs_fail(
                missing_slots=["doc_name"],
                reply_hint=reply or "导出前先确认文档的正式名称。",
                observation="prepare_export_word 需要正式定名。",
            )
        reply, cards = _prepare_export_word(state.home, state.session)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def pause_writing() -> str:
        """暂停批量写作：不再自动写下章，已确认章节保留。"""
        from report_writing.chat.actions import pause_writing_session

        _session, cards, saved = pause_writing_session(state.home, state.session.id)
        _set_cards(state, cards)
        tip = "写作已暂停，不会再自动写下章。"
        if saved:
            tip += f" 已同步 {saved} 章到库。"
        return _dump({"ok": True, "reply_hint": tip})

    @tool
    def confirm_chapter(
        task_id: str = "",
        chapter_key: str = "",
        text: str = "",
        decision: str = "approve",
        comment: str = "",
        pause_after: bool = False,
    ) -> str:
        """确认单章。pause_after=true 或用户说停止/暂停时，确认后不再自动写下章。"""
        from report_writing.chat_orchestrator import _action_confirm_chapter

        tid = (task_id or state.session.task_id or "").strip()
        ck = (chapter_key or "").strip()
        ui = (state.user_action or "").strip() == "confirm_chapter"
        if not ck and not ui:
            # 允许动作层用焦点章；完全无 task 则缺参
            if not tid and not (state.session.task_id or "").strip():
                return _obs_fail(
                    missing_slots=["chapter_key", "task_id"],
                    reply_hint="还没有可确认的章节，先开写或点文档窗里的章。",
                    observation=(
                        "confirm_chapter 缺少 task_id/chapter_key。"
                        "请传入章节 key，或先 start_writing 产生待确认章。"
                    ),
                )
        if tid:
            state.session.task_id = tid
        msg = (state.last_user_message or "").strip()
        pause = bool(pause_after)
        payload = {
            "task_id": tid,
            "chapter_key": ck,
            "text": text or "",
            "decision": decision or "approve",
            "comment": comment or "",
            "pause_after": pause,
            "user_message": msg,
        }
        reply, cards, events = _action_confirm_chapter(
            state.home, state.session, payload, emit=state.emit
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": (
                    f"已确认章节（key={ck or '焦点章'}, decision={decision or 'approve'}）。"
                    f"{'已暂停，不再自动写下章。' if pause else '可继续写下章或 get_writing_status。'}"
                ),
            }
        )

    @tool
    def confirm_all_chapters(
        chapters_json: str = "[]",
        skip_empty: bool = True,
        task_id: str = "",
    ) -> str:
        """统一确认章节。chapters_json 为 [{chapter_key,text},...] JSON 字符串。"""
        from report_writing.chat_orchestrator import _action_confirm_all_chapters

        try:
            chapters = json.loads(chapters_json or "[]")
        except json.JSONDecodeError:
            chapters = []
        tid = (task_id or state.session.task_id or "").strip()
        if tid:
            state.session.task_id = tid
        payload = {
            "task_id": tid,
            "chapters": chapters if isinstance(chapters, list) else [],
            "skip_empty": bool(skip_empty),
        }
        reply, cards, events = _action_confirm_all_chapters(
            state.home, state.session, payload, emit=state.emit
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def configure_chapter(payload_json: str = "{}") -> str:
        """保存单章配置。payload_json：task_id、chapter_key、points_text、keywords_text、describes、aux_prompt、reference_text 等（由你从用户话提取）。"""
        from report_writing.chat_orchestrator import _action_configure_chapter

        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        ck = str(payload.get("chapter_key") or payload.get("chapter_key_hint") or "").strip()
        ui = (state.user_action or "").strip() == "configure_chapter"
        if not ck and not ui:
            return _obs_fail(
                missing_slots=["chapter_key"],
                reply_hint="配置章节要先说清是哪一章。",
                observation=(
                    "configure_chapter 缺少 chapter_key。"
                    "请从用户原话或 turn_context 填入章节 key 后再次调用。"
                ),
            )
        reply, cards = _action_configure_chapter(state.home, state.session, payload)
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": f"已保存章节配置（key={ck or 'UI'}）。可继续加要素或开写。",
            }
        )

    @tool
    def add_chapter_element(payload_json: str) -> str:
        """为本章生成要素草稿（用户卡片确认后写入）。须从用户原话填全：task_id、chapter_key、kind、name、category、tags、purpose；kv 填 kv_pairs 或 key+value/content；file 填 query；file_slot 填槽位；fixed 填 value；llm_generate 填 prompt；node_ref 填 ref_element_name。用户只需在卡片里确认，不要留空让用户手填。"""
        from report_writing.chat_orchestrator import _action_add_chapter_element

        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        hint = str(state.last_user_message or payload.get("hint") or payload.get("user_note") or "").strip()
        if hint and (
            not str(payload.get("chapter_key") or "").strip()
            or not str(payload.get("name") or "").strip()
            or (str(payload.get("kind") or "kv") == "kv" and not payload.get("kv_pairs"))
        ):
            from report_writing.chat.dialogue import format_full_dialogue
            from report_writing.chat.intent_chapter import (
                pick_focus_chapter_key_extended,
                resolve_chapter_key_from_message,
            )
            from report_writing.chat.intent_llm import infer_element_payload_llm
            from report_writing.chat.helpers import _load_task

            intent_llm = state.intent_invoke
            if intent_llm is None:
                from report_writing.chat.intent_invoke import intent_invoke_fn

                intent_llm = intent_invoke_fn(state.invoke)
            inferred = infer_element_payload_llm(
                hint,
                dialogue_tail=format_full_dialogue(state.session, max_turns=8),
                invoke=intent_llm,
            )
            if inferred:
                payload = {**inferred, **{k: v for k, v in payload.items() if v not in (None, "", [], {})}}
            # 原话序号覆盖模型填错的 chapter_key（避免 2.2 被写成 1.5.6）
            task = _load_task(state.home, (state.session.task_id or "").strip())
            if task is not None:
                from_msg = resolve_chapter_key_from_message(task, hint)
                if from_msg:
                    payload["chapter_key"] = from_msg
                elif not str(payload.get("chapter_key") or "").strip():
                    ck = pick_focus_chapter_key_extended(state.session, task, hint)
                    if ck:
                        payload["chapter_key"] = ck
        missing: list[str] = []
        if not str(payload.get("chapter_key") or payload.get("chapter_key_hint") or "").strip():
            missing.append("chapter_key")
        if not str(payload.get("name") or "").strip():
            missing.append("name")
        if missing:
            return _obs_fail(
                missing_slots=missing,
                reply_hint="加要素还缺章节或名称，跟我说一下加到哪一章、叫什么。",
                observation=(
                    f"add_chapter_element 缺少 {', '.join(missing)}。"
                    "请从用户原话拆出 chapter_key（或序号）与 name、kind 后再次调用；"
                    "勿留空让用户手填卡片。"
                ),
            )
        reply, cards = _action_add_chapter_element(state.home, state.session, payload)
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": (
                    "已生成要素草稿卡片，等用户确认。"
                    "若用户其实要「收入要素库」，应改调 collect_to_element_library。"
                ),
            }
        )

    @tool
    def associate_elements(payload_json: str = "") -> str:
        """打开「关联要素」会话操作：把已有要素勾到某章。payload 含 task_id、chapter_key。"""
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        hint = str(state.last_user_message or payload.get("hint") or "").strip()
        if not str(payload.get("chapter_key") or payload.get("chapter_key_hint") or "").strip():
            # 允许分类器/动作层从 hint 解析；完全空则缺参
            if not hint and not (state.session.task_id or "").strip():
                return _obs_fail(
                    missing_slots=["chapter_key"],
                    reply_hint="关联要素要先选定章节。",
                    observation=(
                        "associate_elements 缺少 chapter_key。"
                        "请传入章节 key 或让用户点名章节后再调。"
                    ),
                )
        result = apply_writing_control_classification(
            state.home,
            state.session,
            hint,
            {"tool": "associate_elements", "payload": payload},
            emit=state.emit,
        )
        if not result:
            return _obs_fail(
                missing_slots=["chapter_key"],
                reply_hint="无法打开关联要素，请先选定章节。",
                observation=(
                    "associate_elements 失败：无可用章节上下文。"
                    "请先绑定写作任务并传入 chapter_key，或改调 open_element_library。"
                ),
            )
        reply, cards, _ = result
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": "已打开关联要素面板。用户勾选确认后即完成关联。",
            }
        )

    @tool
    def dissociate_chapter_element(payload_json: str = "") -> str:
        """取消某章与要素的关联（不删要素节点、不删要素库）。payload 可含 chapter_key、element_id、element_name。"""
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        hint = str(state.last_user_message or payload.get("hint") or "").strip()
        result = apply_writing_control_classification(
            state.home,
            state.session,
            hint,
            {"tool": "dissociate_chapter_element", "payload": payload},
            emit=state.emit,
        )
        if not result:
            return _obs_fail(
                reply_hint="请说明要取消哪一章、哪个要素的关联。",
                observation="dissociate_chapter_element 需要 task 上下文与要素名/章节。",
            )
        reply, cards, _ = result
        _set_cards(state, cards)
        obs = (
            "已挂起要素/章节选项，等用户点选。"
            if cards
            else "已取消关联；要素仍保留在本文档。"
        )
        return _dump({"ok": True, "reply_hint": reply, "observation": obs})

    @tool
    def remove_document_element(payload_json: str = "") -> str:
        """从当前写作文档删除要素（去掉各章关联与节点）；绝不删除个人要素库条目。"""
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        hint = str(state.last_user_message or payload.get("hint") or "").strip()
        result = apply_writing_control_classification(
            state.home,
            state.session,
            hint,
            {"tool": "remove_document_element", "payload": payload},
            emit=state.emit,
        )
        if not result:
            return _obs_fail(
                reply_hint="请说明要删哪个要素。",
                observation="remove_document_element 需要写作任务与要素名；同名多个时会挂澄清卡。",
            )
        reply, cards, _ = result
        _set_cards(state, cards)
        obs = (
            "同名要素有多个，已挂选项请用户确认。"
            if cards
            else "已从本文档删除要素；要素库未动。"
        )
        return _dump({"ok": True, "reply_hint": reply, "observation": obs})

    @tool
    def associate_element_knowledge(payload_json: str = "") -> str:
        """已下线：知识库入口移除；请改用要素类型「文件」上传并手动解析。"""
        return _dump(
            {
                "ok": False,
                "reply_hint": "知识库已下线。请把要素类型设为「文件」，上传文件后点击「解析」，解析完成后写作才能读取内容。",
            }
        )

    @tool
    def open_element_library() -> str:
        """查看要素库（侧栏跳转卡片）。"""
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        result = apply_writing_control_classification(
            state.home,
            state.session,
            "查看要素",
            {"tool": "open_element_library", "payload": {}},
            emit=state.emit,
        )
        if not result:
            return _dump({"ok": False, "reply_hint": "无法打开要素库。"})
        reply, cards, _ = result
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def open_blueprint_library() -> str:
        """查看蓝本库（浏览/管理页；不是选型 match）。"""
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        result = apply_writing_control_classification(
            state.home,
            state.session,
            "查看蓝本库",
            {"tool": "open_blueprint_library", "payload": {}},
            emit=state.emit,
        )
        if not result:
            return _dump({"ok": False, "reply_hint": "无法打开蓝本库。"})
        reply, cards, _ = result
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def open_knowledge() -> str:
        """打开资料库（放资料包；兼容旧称「知识库」）。"""
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        result = apply_writing_control_classification(
            state.home,
            state.session,
            "查看资料库",
            {"tool": "open_knowledge", "payload": {}},
            emit=state.emit,
        )
        if not result:
            return _dump(
                {
                    "ok": False,
                    "reply_hint": "资料库是放资料包的地方。请打开侧栏「资料库」管理资料包；要素「文件」可上传并手动解析。",
                }
            )
        reply, cards, _ = result
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def retry_chapter(payload_json: str) -> str:
        """按最新配置重试单章写作。payload_json 至少含 task_id、chapter_key；改写要求可写 aux_prompt / user_turn。"""
        from report_writing.chat_orchestrator import _action_retry_chapter
        from report_writing.chat.session_documents import focus_needs_entry_path

        if (state.user_action or "").strip() == "retry_search_chapter":
            return _dump(
                {
                    "ok": False,
                    "reply_hint": "你点的是「仅重试检索」，我不会写正文。请再点一次「仅重试检索」。",
                }
            )
        # UI 点选重试可带 user_action；口语触发时若仍缺入口则硬拦
        oral_retry = not (state.user_action or "").strip()
        if oral_retry and focus_needs_entry_path(state.session):
            from report_writing.chat.intent_clarify import build_entry_path_clarify

            reply, cards = build_entry_path_clarify(
                state.session,
                message=state.last_user_message or "",
                invoke=state.invoke,
                home=state.home,
                emit=state.emit,
                after_doc_name_confirm=True,
                apply_plan=False,
            )
            _set_cards(state, cards)
            return _obs_fail(
                missing_slots=["writing_path"],
                reply_hint=reply or "请先选择写作方式（有蓝本 / 无蓝本），再重试章节。",
                observation=(
                    "retry_chapter 被拦：焦点仍缺文档入口 writing_path。"
                    "先 ask_entry_path，再 retry_chapter。"
                ),
                task_id=state.session.task_id or "",
            )
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        reply, cards, events = _action_retry_chapter(
            state.home,
            state.session,
            payload,
            emit=state.emit,
            invoke=state.invoke,
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def retry_search_chapter(payload_json: str) -> str:
        """按最新配置仅重试单章检索（不写正文），便于调整关键词。"""
        from report_writing.chat_orchestrator import _action_retry_search_chapter

        if (state.user_action or "").strip() not in {"", "retry_search_chapter"}:
            return _dump(
                {
                    "ok": False,
                    "reply_hint": "仅检索需点「仅重试检索」按钮，不要与「保存并重试本章」混用。",
                }
            )
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        reply, cards, events = _action_retry_search_chapter(
            state.home, state.session, payload, emit=state.emit
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def set_session_meta(message: str = "", category: str = "", title: str = "") -> str:
        """记录/改名文档类型与标题（与 UI action 同名）。改名或定名必须传结构化 title（从用户话拆出的新名）；勿把「改个名字」整句塞进 title。"""
        from report_writing.chat.helpers import is_vague_freeform_meta
        from report_writing.chat.session_goal_actions import run_session_goal_action

        _tool_begin(state, "set_session_meta")
        from report_writing.chat.session_documents import (
            focus_has_active_writing_context,
            message_wants_own_template,
            silent_rename_blocked_topic,
            title_is_write_subject_not_doc_name,
            write_subject_on_active_doc_should_clarify,
        )

        msg = (message or "").strip() or (state.last_user_message or "").strip()
        from report_writing.chat.session_documents import (
            message_is_chitchat_only,
            title_rejects_as_document_name,
        )

        if message_is_chitchat_only(msg):
            hint = (
                "不客气～有需要写文档随时叫我。"
                if msg.strip().startswith(("谢", "感谢"))
                else "你好呀～想写什么直接说就行；有蓝本可以说用哪个，没有也可以说不用蓝本，咱们边聊边做。"
            )
            return _dump(
                {
                    "ok": True,
                    "reply_hint": hint,
                    "observation": (
                        "用户本句仅为寒暄/致谢，无写文档意图。"
                        "禁止 set_session_meta、ask_entry_path、挂选路/起名卡。"
                        "本 observation 已含回复，直接以此收尾，勿再调 Tool。"
                    ),
                }
            )
        cat = (category or "").strip()
        tip = (title or "").strip()
        has_pipe = bool(msg) and ("｜" in msg or "|" in msg)
        from report_writing.chat.session_documents import message_requires_structured_rename_title

        if not (title or "").strip() and msg and message_requires_structured_rename_title(msg):
            return _dump(
                {
                    "ok": False,
                    "missing_slots": ["title"],
                    "reply_hint": "改名的话跟我说新名字就行，例如：改名叫 2026个人述职报告。",
                    "observation": (
                        "set_session_meta 改名须结构化参数 title=新名，message=原话可选。"
                        "禁止仅传 message 让后端从话里猜标题；请再次调用并传入 title。"
                    ),
                }
            )
        from report_writing.chat.session_documents import (
            sanitize_candidate_document_title,
            title_contains_action_clause,
        )

        cleaned_tip = sanitize_candidate_document_title(tip, message=msg)
        if cleaned_tip:
            tip = cleaned_tip
        elif tip and (
            is_vague_freeform_meta(tip, field=True) or title_contains_action_clause(tip)
        ):
            tip = ""
        # 「写一份安全报告 用我的模版」：题材不是文档名，禁止当 title 静默定名
        subject_blocked = bool(tip and title_is_write_subject_not_doc_name(msg, tip))
        if subject_blocked:
            tip = ""
        # 已定名 + 写题材句：禁止当改名；挂 新开/改名/继续
        blocked_topic = silent_rename_blocked_topic(
            state.session, msg, title=tip or title or ""
        )
        if blocked_topic or (
            subject_blocked and silent_rename_blocked_topic(state.session, msg, title="")
        ) or (
            focus_has_active_writing_context(state.session)
            and msg
            and write_subject_on_active_doc_should_clarify(
                state.session, msg, title_hint=tip or msg, invoke=state.invoke
            )
        ):
            from report_writing.chat.intent_clarify import (
                build_document_scope_clarify,
                pending_document_scope_reply,
            )
            from report_writing.chat.session_documents import is_writing_document_named

            if is_writing_document_named(state.session) or focus_has_active_writing_context(
                state.session
            ):
                topic = blocked_topic or tip or ""
                pending_hit = pending_document_scope_reply(state.session)
                if pending_hit is not None:
                    reply2, cards2 = pending_hit
                else:
                    reply2, cards2 = build_document_scope_clarify(
                        state.session, message=msg, topic_hint=topic
                    )
                _set_cards(state, cards2)
                return _dump(
                    {
                        "ok": False,
                        "missing_slots": ["document_scope"],
                        "reply_hint": reply2,
                        "observation": (
                            "当前文档已定名，本句像新写作题材而非明确改名。"
                            "已挂澄清：请用户确认新开一份 / 改当前文档名 / 继续当前这份。"
                            "禁止再调用 set_session_meta 用题材改名；等用户点选后再继续。"
                        ),
                    }
                )
        if not tip and not cat and not has_pipe:
            # 仅 message：允许干净短题目；写作/模版整句禁止升格为 title
            promote_ok = (
                bool(msg)
                and not is_vague_freeform_meta(msg, field=True)
                and not title_contains_action_clause(msg)
                and not message_wants_own_template(msg)
                and not title_is_write_subject_not_doc_name(msg, msg)
                and not title_rejects_as_document_name(msg, message=msg)
                and not any(
                    k in msg.replace(" ", "")
                    for k in ("写一份", "写个", "写份", "帮我写", "写一篇", "想写")
                )
            )
            if not promote_ok:
                return _dump(
                    {
                        "ok": False,
                        "missing_slots": ["title"],
                        "reply_hint": "还缺文档标题，跟我说一下叫什么名字。",
                        "observation": (
                            "set_session_meta 缺少结构化 title。"
                            "请结合用户本句与近期对话抽出文档名，再次调用："
                            "set_session_meta(title=具体短标题, message=原话)；"
                            "若一句里还有匹配蓝本/开写等意图，定名与 match 可同轮或按 observation 缺项先后执行。"
                            "禁止假设用户未说的标题，禁止把「改个名字」「是的」等确认语当 title。"
                            "禁止把「写一份×× / 用我的模版」的题材当 title。"
                        ),
                    }
                )
            tip = msg
        if title_rejects_as_document_name(tip, message=msg):
            return _dump(
                {
                    "ok": False,
                    "missing_slots": ["title"],
                    "reply_hint": "这句还不像文档题目～想写啥直接说题材就行，不必先定名。",
                    "observation": (
                        "title 不能是寒暄或空泛确认语。"
                        "勿挂 ask_entry_path；等用户说明写文档题材后再推进。"
                    ),
                }
            )
        reply, cards = run_session_goal_action(
            state.home,
            state.session,
            "set_session_meta",
            {"message": msg, "category": cat, "title": tip},
            invoke=state.invoke,
            emit=state.emit,
        )
        _set_cards(state, cards)
        brief = tip or cat or msg
        user_msg = (msg or state.last_user_message or "").strip()
        write_like = any(
            k in user_msg.replace(" ", "")
            for k in ("写一份", "写个", "写份", "写报告", "帮我写", "开写", "写一篇", "想写")
        )
        awaiting_path = False
        needs_entry = False
        # 定名成功且缺入口：必挂选路卡（不单靠 write_like 关键字）
        if tip:
            from report_writing.chat.intent_clarify import (
                clear_pending,
                get_pending,
            )
            from report_writing.chat.session_documents import (
                focus_needs_doc_name,
                focus_needs_entry_path,
                is_writing_document_named,
            )

            pending_src = str((get_pending(state.session) or {}).get("source") or "")
            still_needs_name = focus_needs_doc_name(state.session)
            if still_needs_name:
                # 仍缺定名：保留刚挂的起名卡 pending，禁止清掉后再点选报过期
                pending_src = pending_src or "doc_name"
            elif pending_src == "doc_name":
                # 已定名：清起名 pending，准备挂入口选路
                clear_pending(state.session)
                pending_src = ""
            needs_entry = (
                not still_needs_name and focus_needs_entry_path(state.session)
            )
            if (
                needs_entry
                and pending_src not in {
                    "entry",
                    "entry_branch",
                    "entry_freeform_mode",
                }
            ):
                from report_writing.chat.session_documents import peek_pending_entry

                pending_entry = peek_pending_entry(state.session)
                clarify_residual = str(
                    (state.session.freeform or {}).get("clarify_residual_message") or ""
                ).strip()
                continuation = (
                    clarify_residual
                    or str(pending_entry.get("message") or "").strip()
                    or user_msg
                ).strip()
                if continuation == tip:
                    continuation = clarify_residual or str(
                        pending_entry.get("message") or ""
                    ).strip()
                reply2, cards2 = _hang_entry_path_after_named(
                    state,
                    message=continuation,
                    title_hint=tip,
                    after_doc_name_confirm=True,
                )
                name_ask_cards = any(
                    str((getattr(c, "payload", None) or {}).get("source") or "") == "doc_name"
                    for c in (cards2 or [])
                )
                if reply2:
                    # 仍在问起名时禁止再断言「好，文档就叫…」
                    if is_writing_document_named(state.session) and not name_ask_cards:
                        short = f"好，文档就叫「{tip}」。" if tip else (reply or "").strip()
                    else:
                        short = (reply or "").strip()
                        if short.startswith("好，文档就叫") and name_ask_cards:
                            short = ""
                    reply = f"{short}\n{reply2}".strip() if short else reply2
                if cards2:
                    _set_cards(state, list(cards or []) + list(cards2 or []))
                pending_src = str((get_pending(state.session) or {}).get("source") or "")
                needs_entry = focus_needs_entry_path(state.session) or pending_src in {
                    "entry",
                    "entry_branch",
                    "entry_freeform_mode",
                }
            awaiting_path = pending_src in {
                "entry",
                "entry_branch",
                "entry_freeform_mode",
            }
            gaps = dict((state.session.freeform or {}).get("onboarding_gaps_snapshot") or {})
            suggested = list((state.session.freeform or {}).get("suggested_next_tools") or [])
            next_hop = (
                f"定名/入口观察：pending={pending_src or '—'} gaps={gaps}。"
                + (
                    f" suggested_next_tools={suggested}，请同轮按序调用。"
                    if suggested
                    else (
                        " 用户点选澄清卡或继续 ask_entry_path。"
                        if needs_entry or awaiting_path
                        else " 若用户还有开写/目录等诉求，继续调下一 Tool；纯改名可收尾。"
                    )
                )
            )
        else:
            next_hop = "若用户还有开写/目录等诉求，继续调下一 Tool；纯改名可收尾。"
            gaps = dict((state.session.freeform or {}).get("onboarding_gaps_snapshot") or {})
            suggested = list((state.session.freeform or {}).get("suggested_next_tools") or [])
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": (
                    f"已调用 set_session_meta（title={tip or '—'}, category={cat or '—'}）。"
                    f"结果摘要：{(reply or '')[:180]}。{next_hop}"
                ),
                "title": tip,
                "category": cat,
                "brief": brief[:80],
                "onboarding": gaps,
                "suggested_next_tools": suggested,
                "needs_entry_path": bool(needs_entry or write_like and awaiting_path),
                "awaiting_path_choice": awaiting_path,
            }
        )

    @tool
    def submit_freeform_meta(message: str = "", category: str = "", title: str = "") -> str:
        """无蓝本路径：记录文档类型与标题（兼容旧工具名，等同 set_session_meta；优先传 title）。"""
        # 与 set_session_meta 同一实现，避免两套契约
        return _invoke_tool(
            state,
            set_session_meta,
            {
                "message": message or "",
                "category": category or "",
                "title": title or "",
            },
            via="submit_freeform_meta",
        )

    @tool
    def trim_outline_top_chapters(keep_count: int) -> str:
        """用户只要前 N 个顶层章时：裁切当前目录并刷新卡片（由你根据对话决定 N，勿依赖外部规则）。"""
        from report_writing.chat.freeform_outline import chapters_to_outline_text, trim_outline_directory_text
        from report_writing.chat.helpers import _load_task
        from report_writing.chat_orchestrator import _collect_freeform_outline

        n = max(1, int(keep_count or 1))
        ff = dict(state.session.freeform or {})
        outline_body = str(ff.get("outline_text") or "").strip()
        tid = (state.session.task_id or "").strip()
        if not outline_body and tid:
            task = _load_task(state.home, tid)
            if task and task.outline and task.outline.chapters:
                outline_body = chapters_to_outline_text(
                    [{"sequence": node.sequence, "title": node.title} for node in task.outline.chapters]
                )
        if not outline_body:
            return _dump({"ok": False, "reply_hint": "还没有目录，请先生成或粘贴大纲。"})
        trimmed = trim_outline_directory_text(outline_body, n)
        reply, cards = _collect_freeform_outline(
            state.home,
            state.session,
            trimmed,
            invoke=state.invoke,
        )
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply, "task_id": state.session.task_id})

    @tool
    def submit_outline(
        outline_text: str = "",
        confirm: bool = False,
        regenerate: bool = False,
        reopen_edit: bool = False,
        revision_instructions: str = "",
        chapter_keys_json: str = "[]",
    ) -> str:
        """无蓝本路径：生成/粘贴/确认目录；reopen_edit 重新打开编辑。

        regenerate 时务必把用户修改要求写入 revision_instructions（如「每章下三个小节」）。
        确认时若传 chapter_keys_json，仅给勾选章自动生成要点。
        """
        from report_writing.chat.actions import _action_submit_outline

        if regenerate:
            from report_writing.chat.session_documents import (
                apply_working_title,
                focus_needs_entry_path,
                mark_focus_entry_path,
                mark_focus_freeform_mode,
                quick_title_from_write_message,
            )

            if focus_needs_entry_path(state.session):
                mark_focus_entry_path(state.session, "freeform")
                mark_focus_freeform_mode(state.session, "auto_outline")
                working = quick_title_from_write_message(
                    state.last_user_message or revision_instructions or ""
                )
                if working:
                    apply_working_title(
                        state.session,
                        working,
                        home=state.home,
                        message=state.last_user_message or "",
                    )

        text = (outline_text or "").strip()
        rev = (revision_instructions or "").strip()
        ff = dict(state.session.freeform or {})
        has_pending = bool(ff.get("outline_pending_edit") or ff.get("outline_text"))
        has_task = bool((state.session.task_id or "").strip())
        if (
            not text
            and not confirm
            and not regenerate
            and not reopen_edit
            and not has_pending
            and not has_task
        ):
            return _obs_fail(
                missing_slots=["outline_text"],
                reply_hint="还没有目录。你可以粘贴章节，或让我帮你起草目录。",
                observation=(
                    "submit_outline 缺少 outline_text，且会话尚无待确认目录。"
                    "用户要生成目录 → 再次调用 submit_outline(regenerate=true)；"
                    "用户粘贴了「1 标题」行 → 传入 outline_text；"
                    "确认已有目录 → confirm=true；"
                    "按用户约束重生成 → regenerate=true 且 revision_instructions=原话。"
                ),
            )
        _tool_begin(state, "submit_outline")
        try:
            scoped_keys = json.loads(chapter_keys_json or "[]")
        except json.JSONDecodeError:
            scoped_keys = []
        if not isinstance(scoped_keys, list):
            scoped_keys = []
        payload = {
            "outline_text": text,
            "confirm": bool(confirm),
            "regenerate": bool(regenerate),
            "reopen_edit": bool(reopen_edit),
            "revision_instructions": rev,
            "chapter_keys": [str(k).strip() for k in scoped_keys if str(k).strip()],
        }
        # 口语原话优先作 message，便于落 outline_constraints
        oral = rev or state.last_user_message or text
        reply, cards = _action_submit_outline(
            state.home,
            state.session,
            payload,
            oral,
            invoke=state.invoke,
            emit=state.emit,
        )
        _set_cards(state, cards)
        mode = (
            "confirm"
            if confirm
            else ("regenerate" if regenerate else ("reopen" if reopen_edit else "submit"))
        )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "task_id": state.session.task_id,
                "observation": (
                    f"submit_outline 完成（mode={mode}）。"
                    "下一步可 generate_freeform_points / skip_freeform_points / start_writing。"
                ),
            }
        )

    @tool
    def submit_points(message: str = "") -> str:
        """无蓝本路径：补充要点或直接开写。"""
        from report_writing.chat.writing_control_route import reroute_submit_points_if_needed
        from report_writing.chat_orchestrator import _collect_freeform_points

        msg = message or state.last_user_message or ""
        intent_llm = state.intent_invoke
        if intent_llm is None:
            from report_writing.chat.intent_invoke import intent_invoke_fn

            intent_llm = intent_invoke_fn(state.invoke)
        rerouted = reroute_submit_points_if_needed(
            state.home,
            state.session,
            msg,
            invoke=state.invoke,
            intent_invoke=intent_llm,
        )
        if rerouted is not None:
            reply, cards = rerouted
            _set_cards(state, cards)
            return _dump(
                {
                    "ok": True,
                    "reply_hint": reply,
                    "observation": "submit_points 已改调其它写作动作，见 reply_hint。",
                }
            )
        reply, cards = _collect_freeform_points(
            state.home,
            state.session,
            msg,
            invoke=state.invoke,
            emit=state.emit,
        )
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": "已处理要点输入。可 start_writing 或继续补要点。",
            }
        )

    @tool
    def generate_freeform_points(
        task_id: str = "",
        chapter_keys_json: str = "[]",
        focus_chapter_key: str = "",
    ) -> str:
        """为当前无蓝本/写作任务生成写作要点。chapter_keys_json 非空时仅生成指定章。"""
        from report_writing.chat.actions import _action_generate_freeform_points

        tid = (task_id or state.session.task_id or "").strip()
        ff = dict(state.session.freeform or {})
        if not tid and not ff.get("outline_confirmed") and not ff.get("outline_pending_edit"):
            return _obs_fail(
                missing_slots=["task_id"],
                reply_hint="还没有目录任务，先生成或确认大纲再配要点。",
                observation=(
                    "generate_freeform_points 缺少 task_id / 已确认大纲。"
                    "请先 submit_outline（生成或确认）后再调用本 Tool。"
                ),
            )
        _tool_begin(state, "generate_freeform_points")
        try:
            keys = json.loads(chapter_keys_json or "[]")
        except json.JSONDecodeError:
            keys = []
        if not isinstance(keys, list):
            keys = []
        payload = {
            "task_id": tid,
            "chapter_keys": [str(k).strip() for k in keys if str(k).strip()],
            "focus_chapter_key": str(focus_chapter_key or "").strip(),
        }
        reply, cards = _action_generate_freeform_points(
            state.home,
            state.session,
            payload,
            invoke=state.invoke,
            emit=state.emit,
        )
        _tool_done(state, "generate_freeform_points")
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": (
                    "已生成写作要点。"
                    "可 start_writing；若用户说跳过要点则 skip_freeform_points。"
                ),
            }
        )

    @tool
    def skip_freeform_points(task_id: str = "") -> str:
        """跳过批量生成写作要点，直接进入开写前步骤。"""
        from report_writing.chat.actions import _action_skip_freeform_points

        _tool_begin(state, "skip_freeform_points")
        reply, cards = _action_skip_freeform_points(
            state.home,
            state.session,
            {"task_id": task_id or state.session.task_id or ""},
        )
        _tool_done(state, "skip_freeform_points")
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": "已跳过要点。下一步通常 start_writing（须已定名）。",
            }
        )

    @tool
    def resource_check() -> str:
        """检查当前机器人是否缺蓝本/渲染模版/Word 样式，并给出配置卡片。"""
        from report_writing.chat.resource_readiness import _action_resource_check

        _tool_begin(state, "resource_check")
        reply, cards = _action_resource_check(state.home, state.session)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def resource_choice(resource: str = "", accept: bool = False) -> str:
        """机器人缺资源时用户点「是/否」：blueprint / render_template / word_style。"""
        from report_writing.chat.resource_actions import _action_resource_choice

        _tool_begin(state, "resource_choice")
        payload = {"resource": (resource or "").strip(), "accept": bool(accept)}
        reply, cards = _action_resource_choice(
            state.home,
            state.session,
            payload,
            invoke=state.invoke,
            emit=state.emit,
        )
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def blueprint_wizard(payload_json: str = "{}") -> str:
        """当前文档的模版向导（上传/目录/学习）；仅改 w* 副本，不入库。收录用 save_freeform_as_blueprint。"""
        from report_writing.chat.intent_router import active_blueprint_wizard
        from report_writing.chat.resource_actions import _action_blueprint_wizard, _continue_active_wizard_reply
        from report_writing.chat.wizard_loop_guard import normalized_wizard_step
        from report_writing.chat.wizard_state import wizard_step_index

        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        step_in = str(payload.get("step") or "").strip()
        title_in = str(payload.get("title") or "").strip()
        wizard_cmd = str(payload.get("wizard_command") or "").strip()
        wiz = dict(active_blueprint_wizard(state.session) or {})
        bid = str(state.session.blueprint_id or wiz.get("blueprint_id") or "").strip()
        cur_si = wizard_step_index(normalized_wizard_step(str(wiz.get("step") or "")))
        upload_si = wizard_step_index("upload")
        has_active = bool(wiz) or bool(bid)
        fresh_meta = bool(wiz.get("fresh_meta")) and not str(wiz.get("blueprint_id") or "").strip()
        meta_submit = step_in == "meta" and bool(title_in) and not wizard_cmd
        if meta_submit:
            if has_active:
                from report_writing.chat.user_process import emit_user_process

                emit_user_process(state.emit, "正在处理蓝本向导…")
            else:
                _tool_begin(state, "blueprint_wizard")
            reply, cards = _action_blueprint_wizard(state.home, state.session, payload)
            _set_cards(state, cards)
            return _dump({"ok": True, "reply_hint": reply})
        # 仅拦截「空 step / 再填名称」的重复创建；带 wizard_command（学习本章等）必须放行
        reopen_meta = (
            not wizard_cmd and step_in in {"", "meta"} and not title_in
        )
        if (
            bid
            and cur_si >= upload_si
            and not fresh_meta
            and (reopen_meta or (step_in == "meta" and not wizard_cmd))
        ):
            cont = _continue_active_wizard_reply(state.home, state.session)
            if cont:
                reply, cards = cont
                _set_cards(state, cards)
                return _dump({"ok": True, "reply_hint": reply})
        if has_active:
            from report_writing.chat.user_process import emit_user_process

            emit_user_process(state.emit, "正在处理蓝本向导…")
        else:
            _tool_begin(state, "blueprint_wizard")
        reply, cards = _action_blueprint_wizard(state.home, state.session, payload)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def template_wizard(payload_json: str = "{}") -> str:
        """渲染模版 / Word 样式配置向导。"""
        from report_writing.chat.resource_actions import _action_template_wizard

        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _tool_begin(state, "template_wizard")
        reply, cards = _action_template_wizard(state.session, payload)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool
    def save_freeform_as_blueprint(
        task_id: str = "",
        dismiss: bool = False,
        title: str = "",
        category: str = "",
        tags_json: str = "[]",
    ) -> str:
        """把当前无蓝本写作任务（目录与已配要点）收录为新库蓝本。不是创建向导；收录后可用 sync_document_to_blueprint_library。"""
        from report_writing.chat.actions import _action_save_freeform_as_blueprint
        from report_writing.chat.tool_capability import missing_for_tool

        miss = missing_for_tool(
            state.session, "save_freeform_as_blueprint", home=state.home
        )
        if "doc_name" in miss and not (title or "").strip():
            from report_writing.chat.intent_clarify import build_document_name_clarify

            reply, cards = build_document_name_clarify(
                state.session,
                message=state.last_user_message or "",
                home=state.home,
                emit=state.emit,
                follow_entry=False,
            )
            _set_cards(state, cards)
            return _obs_fail(
                missing_slots=["doc_name"],
                reply_hint=reply or "收录到蓝本库前先确认文档的正式名称。",
                observation="save_freeform_as_blueprint 需要正式定名。",
            )
        _tool_begin(state, "save_freeform_as_blueprint")
        try:
            tags = json.loads(tags_json or "[]")
        except json.JSONDecodeError:
            tags = []
        if not isinstance(tags, list):
            tags = []
        payload = {
            "task_id": (task_id or state.session.task_id or "").strip(),
            "dismiss": bool(dismiss),
            "title": (title or "").strip(),
            "category": (category or "").strip(),
            "tags": [str(t).strip() for t in tags if str(t).strip()],
        }
        try:
            reply, cards = _action_save_freeform_as_blueprint(state.home, state.session, payload)
        except ValueError as error:
            return _dump({"ok": False, "reply_hint": str(error)})
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply, "task_id": payload["task_id"]})

    @tool
    def sync_document_to_blueprint_library(task_id: str = "") -> str:
        """收录之后：把当前文档副本的目录/要点/要素同步回已绑定的库蓝本（显式才写库）。"""
        from report_writing.chat.actions import _action_sync_document_to_blueprint_library

        _tool_begin(state, "sync_document_to_blueprint_library")
        payload = {"task_id": (task_id or state.session.task_id or "").strip()}
        try:
            reply, cards = _action_sync_document_to_blueprint_library(
                state.home, state.session, payload
            )
        except ValueError as error:
            return _dump({"ok": False, "reply_hint": str(error)})
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply, "task_id": payload["task_id"]})

    @tool
    def remember_writing_goal(notes: str = "") -> str:
        """记住本会话写作目标（类型、标题、项目），后续轮次自动沿用。"""
        from report_writing.chat.helpers import is_vague_freeform_meta
        from report_writing.chat.session_documents import (
            focus_needs_entry_path,
            is_writing_document_named,
        )
        from report_writing.chat.session_goal import session_goal_brief
        from report_writing.chat.session_goal_actions import run_session_goal_action

        body = (notes or state.last_user_message or "").strip()
        if not body:
            return _obs_fail(
                missing_slots=["notes"],
                reply_hint="想让我记住什么？说说类型、题目或项目就行。",
                observation=(
                    "remember_writing_goal 缺少 notes。"
                    "请传入用户要记住的摘要后再调用。"
                ),
            )
        from report_writing.chat.session_documents import message_is_chitchat_only

        if message_is_chitchat_only(body):
            hint = (
                "不客气～有需要写文档随时叫我。"
                if body.strip().startswith(("谢", "感谢"))
                else "你好呀～想写什么直接说就行，咱们边聊边收集信息，不必按固定步骤来。"
            )
            return _dump(
                {
                    "ok": True,
                    "reply_hint": hint,
                    "observation": (
                        "寒暄/致谢，无写作目标可记。"
                        "禁止挂入口选路卡；直接友好回复后结束本回合。"
                    ),
                }
            )
        _tool_begin(state, "remember_writing_goal")
        named = is_writing_document_named(state.session)
        needs_entry = focus_needs_entry_path(state.session)
        vague_continue = is_vague_freeform_meta(body)
        # 已定名+缺入口+含糊继续：禁止再只记目标，直接推进选路
        if named and needs_entry and vague_continue:
            reply2, cards2 = _hang_entry_path_after_named(
                state,
                message=body,
                after_doc_name_confirm=True,
            )
            kept = _merge_cards_keep_entry_clarify(state, cards2)
            _set_cards(state, kept)
            return _dump(
                {
                    "ok": True,
                    "reply_hint": (reply2 or "").strip()
                    or "文档名已经定好了，接下来选写作方式：有蓝本还是无蓝本？",
                    "session_goal": session_goal_brief(state.session),
                    "observation": (
                        "已定名但缺 writing_path/入口选路；本句是含糊继续，未再 remember。"
                        "下一步唯一 Tool：ask_entry_path，或等用户点选入口澄清卡；"
                        "禁止再调 remember_writing_goal / set_session_meta 只回「记下了」。"
                    ),
                    "needs_entry_path": True,
                    "awaiting_path_choice": True,
                    "missing_slots": ["writing_path"],
                }
            )
        reply, cards = run_session_goal_action(
            state.home,
            state.session,
            "remember_writing_goal",
            {"notes": body},
            invoke=state.invoke,
            emit=state.emit,
        )
        write_like = any(
            k in body
            for k in ("写一份", "写报告", "帮我写", "开写", "写一篇", "想写")
        )
        needs_entry = focus_needs_entry_path(state.session)
        if needs_entry:
            reply2, cards2 = _hang_entry_path_after_named(
                state,
                message=body,
                after_doc_name_confirm=True,
            )
            # 短确认 + 入口问句；勿宣称「后面选蓝本都会按这个来」却无卡
            brief = session_goal_brief(state.session)
            short = f"好，我记下了：{brief}。" if brief else (reply or "").strip()
            if short and "后面选蓝本" in short:
                short = short.split("。", 1)[0] + "。"
            if reply2:
                reply = f"{short}\n{reply2}".strip() if short else reply2
            elif short:
                reply = short
            cards = cards2 or cards
            kept = _merge_cards_keep_entry_clarify(state, cards)
            _set_cards(state, kept)
            return _dump(
                {
                    "ok": True,
                    "reply_hint": reply,
                    "session_goal": session_goal_brief(state.session),
                    "observation": (
                        "已记住会话写作目标，但仍缺 writing_path/入口选路。"
                        "已挂入口澄清卡；下一步唯一 Tool：ask_entry_path，"
                        "或等用户点选；勿就此结束、勿再只调 remember_writing_goal。"
                    ),
                    "needs_entry_path": True,
                    "awaiting_path_choice": True,
                    "missing_slots": ["writing_path"],
                }
            )
        kept = _merge_cards_keep_entry_clarify(state, cards)
        _set_cards(state, kept)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "session_goal": session_goal_brief(state.session),
                "observation": (
                    "已记住会话写作目标，后续可沿用 session_goal。"
                    + (
                        " 若本句还在开写且路径未选，请继续 ask_entry_path，勿就此结束。"
                        if write_like
                        else ""
                    )
                ),
                "needs_entry_path": False,
            }
        )

    @tool
    def resolve_intent_clarify(
        option_id: str = "",
        option_label: str = "",
        clarify_id: str = "",
        message: str = "",
    ) -> str:
        """用户点选了澄清卡片，或用原话回答当前 pending 选项。按 option_id 执行已选槽位。"""
        del clarify_id
        _tool_begin(state, "resolve_intent_clarify")
        payload = {
            "option_id": (option_id or "").strip(),
            "option_label": (option_label or "").strip(),
        }
        reply, cards, events = apply_resolve_intent_clarify(
            home=state.home,
            session=state.session,
            invoke=state.intent_invoke or state.invoke,
            emit=state.emit,
            payload=payload,
            message=(message or option_label or state.last_user_message or "").strip(),
        )
        state.cards.extend(cards or [])
        state.events.extend(events or [])
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "observation": "已按澄清点选执行。读 cards / reply_hint，不要再猜意图。",
            }
        )

    @tool
    def usage_guide() -> str:
        """用户要使用说明 / 怎么用本产品。"""
        from report_writing.chat.product_guide import build_usage_guide_reply

        _tool_begin(state, "usage_guide")
        reply = build_usage_guide_reply(state.invoke)
        return _dump({"ok": True, "reply_hint": reply, "observation": "已给出使用说明。"})

    tools = [
        list_blueprints,
        ask_entry_path,
        match_blueprints,
        blueprint_subagent,
        blueprint_wizard_subagent,
        render_subagent,
        style_subagent,
        select_blueprint,
        select_write_chapters,
        write_analyzed_only,
        skip_blueprint,
        begin_blueprint_wizard,
        resource_choice,
        blueprint_wizard,
        template_wizard,
        handoff_blueprint_page,
        resume_from_blueprint,
        start_writing,
        get_writing_status,
        prepare_export_word,
        confirm_chapter,
        pause_writing,
        confirm_all_chapters,
        configure_chapter,
        add_chapter_element,
        associate_elements,
        dissociate_chapter_element,
        remove_document_element,
        associate_element_knowledge,
        open_element_library,
        open_blueprint_library,
        open_knowledge,
        retry_chapter,
        retry_search_chapter,
        set_session_meta,
        submit_freeform_meta,
        trim_outline_top_chapters,
        submit_outline,
        submit_points,
        generate_freeform_points,
        skip_freeform_points,
        resource_check,
        save_freeform_as_blueprint,
        sync_document_to_blueprint_library,
        remember_writing_goal,
        resolve_intent_clarify,
        usage_guide,
    ]
    from report_writing.agents.resource_tools import build_export_resource_tools

    tools.extend(build_export_resource_tools(state))
    from report_writing.chat.intent_router import user_chose_skip_blueprint

    if user_chose_skip_blueprint(state.session):
        tools = [t for t in tools if getattr(t, "name", None) != "match_blueprints"]
    from report_writing.chat.tool_registry import apply_chat_control_tool_descriptions

    return apply_chat_control_tool_descriptions(tools)


def build_system_prompt(
    robot_prompt: str = "",
    *,
    session: ChatSession | None = None,
    home: Path | None = None,
) -> str:
    base = (robot_prompt or "").strip()
    core = CONTROL_INSTRUCTIONS
    if base:
        core = f"{base.rstrip()}\n\n---\n{core}"
    if session is not None:
        from report_writing.chat.writing_brief import writing_brief_prompt_block

        block = writing_brief_prompt_block(session, home=home)
        if block:
            core = f"{core}\n\n---\n{block}"
    return core


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
        "add_chapter_element",
        "associate_elements",
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


def _parse_agent_body(user_text: str) -> tuple[str, dict[str, Any], str]:
    try:
        body = json.loads(user_text or "{}")
    except json.JSONDecodeError:
        return "", {}, (user_text or "").strip()
    if not isinstance(body, dict):
        return "", {}, (user_text or "").strip()
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    return (
        str(body.get("action") or "").strip(),
        dict(payload or {}),
        str(body.get("message") or "").strip(),
    )


def _tool_args_for_ui_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload or {})
    if action == "select_blueprint":
        return {"blueprint_id": str(payload.get("blueprint_id") or "").strip()}
    if action == "skip_blueprint":
        return {}
    if action == "begin_blueprint_wizard":
        return {
            "force_new": bool(payload.get("force_new", True)),
            "title_hint": str(payload.get("title_hint") or payload.get("title") or "").strip(),
        }
    keys = payload.get("chapter_keys") or payload.get("write_chapter_keys") or []
    keys_json = json.dumps(keys if isinstance(keys, list) else [], ensure_ascii=False)
    if action in {"select_write_chapters", "write_analyzed_only"}:
        return {
            "blueprint_id": str(payload.get("blueprint_id") or "").strip(),
            "chapter_keys_json": keys_json,
        }
    if action == "start_writing":
        return {
            "writing_pace": str(payload.get("writing_pace") or "").strip(),
            "chapter_keys_json": keys_json,
        }
    if action == "submit_outline":
        return {
            "outline_text": str(payload.get("outline_text") or ""),
            "confirm": bool(payload.get("confirm")),
            "regenerate": bool(payload.get("regenerate")),
            "reopen_edit": bool(payload.get("reopen_edit")),
            "revision_instructions": str(
                payload.get("revision_instructions") or payload.get("message") or ""
            ),
            "chapter_keys_json": keys_json,
        }
    if action == "save_freeform_as_blueprint":
        raw_tags = payload.get("tags")
        if isinstance(raw_tags, str):
            try:
                raw_tags = json.loads(raw_tags or "[]")
            except json.JSONDecodeError:
                raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        if not isinstance(raw_tags, list):
            raw_tags = []
        return {
            "task_id": str(payload.get("task_id") or ""),
            "dismiss": bool(payload.get("dismiss")),
            "title": str(payload.get("title") or ""),
            "category": str(payload.get("category") or ""),
            "tags_json": json.dumps(
                [str(t).strip() for t in raw_tags if str(t).strip()],
                ensure_ascii=False,
            ),
        }
    if action == "sync_document_to_blueprint_library":
        return {"task_id": str(payload.get("task_id") or "")}
    if action == "generate_freeform_points":
        return {
            "task_id": str(payload.get("task_id") or ""),
            "chapter_keys_json": keys_json,
            "focus_chapter_key": str(
                payload.get("focus_chapter_key") or payload.get("chapter_key") or ""
            ).strip(),
        }
    if action == "skip_freeform_points":
        return {"task_id": str(payload.get("task_id") or "")}
    if action == "list_blueprints":
        return {"q": str(payload.get("q") or "")}
    if action == "match_blueprints":
        return {"message": str(payload.get("message") or payload.get("q") or "").strip()}
    if action == "resume":
        return {
            "blueprint_id": str(payload.get("blueprint_id") or "").strip(),
            "task_id": str(payload.get("task_id") or "").strip(),
        }
    if action == "confirm_chapter":
        return {
            "task_id": str(payload.get("task_id") or "").strip(),
            "chapter_key": str(payload.get("chapter_key") or "").strip(),
            "text": str(payload.get("text") or ""),
            "decision": str(payload.get("decision") or "approve"),
            "comment": str(payload.get("comment") or ""),
            "pause_after": bool(payload.get("pause_after")),
        }
    if action == "confirm_all_chapters":
        return {
            "chapters_json": json.dumps(
                payload.get("chapters") if isinstance(payload.get("chapters"), list) else [],
                ensure_ascii=False,
            ),
            "skip_empty": bool(payload.get("skip_empty", True)),
            "task_id": str(payload.get("task_id") or "").strip(),
        }
    if action == "resource_choice":
        return {
            "resource": str(payload.get("resource") or "").strip(),
            "accept": bool(payload.get("accept")),
        }
    if action in {"blueprint_wizard", "template_wizard"}:
        return {"payload_json": json.dumps(payload, ensure_ascii=False)}
    if action == "pause_writing":
        return {}
    if action in {
        "configure_chapter",
        "add_chapter_element",
        "associate_elements",
        "associate_element_knowledge",
        "retry_chapter",
        "retry_search_chapter",
    }:
        return {"payload_json": json.dumps(payload, ensure_ascii=False)}
    if action in {"open_element_library", "open_blueprint_library", "open_knowledge"}:
        return {}
    if action == "resource_check":
        return {}
    if action == "set_session_meta":
        return {
            "message": str(payload.get("message") or "").strip(),
            "category": str(payload.get("category") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
        }
    if action == "remember_writing_goal":
        return {"notes": str(payload.get("notes") or payload.get("message") or "").strip()}
    if action == "resolve_intent_clarify":
        return {
            "option_id": str(payload.get("option_id") or "").strip(),
            "option_label": str(payload.get("option_label") or "").strip(),
            "clarify_id": str(payload.get("clarify_id") or "").strip(),
        }
    return {}


def _try_ui_action_shortcut(state: ChatControlState, user_text: str) -> str | None:
    """界面按钮：只调对应工具一次，避免 LLM 同轮再调 handoff 盖掉选用结果。"""
    action = (state.user_action or "").strip()
    payload: dict[str, Any] = {}
    if action:
        _, payload, _ = _parse_agent_body(user_text)
    else:
        action, payload, _ = _parse_agent_body(user_text)
    if action not in _UI_SHORTCUT_ACTIONS:
        return None
    tool_name = _ui_action_tool_name(action)
    for tool in build_chat_tools(state):
        if getattr(tool, "name", None) != tool_name:
            continue
        raw = _invoke_tool(state, tool, _tool_args_for_ui_action(action, payload), via="ui_action")
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
        return str((data or {}).get("reply_hint") or "").strip() or None
    return None


def apply_resolve_intent_clarify(
    *,
    home: Path,
    session: ChatSession,
    invoke: InvokeFn | None,
    emit: EmitFn | None,
    payload: dict[str, Any],
    message: str,
) -> tuple[str, list[Any], list[dict]]:
    """执行澄清点选（卡片按钮或 Agent Tool）。"""
    from report_writing.chat.intent_clarify import (
        execute_clarify_spec,
        resolve_clarify,
        try_handle_pending_message,
    )

    option_id = str((payload or {}).get("option_id") or "").strip()
    option_label = str((payload or {}).get("option_label") or "").strip()
    hit = None
    if option_id:
        hit = resolve_clarify(session, option_id)
    if hit is None:
        from report_writing.chat.intent_clarify import restore_pending_from_last_clarify

        if restore_pending_from_last_clarify(session) is not None and option_id:
            hit = resolve_clarify(session, option_id)
    if hit is None:
        choice = option_label or option_id or str(message or "").strip()
        hit = try_handle_pending_message(session, choice, invoke=invoke, home=home)
    else:
        choice = option_label or option_id
    if isinstance(hit, str):
        return hit, [], []
    if isinstance(hit, dict):
        reply, cards, events = execute_clarify_spec(
            home, session, hit, message=choice, emit=emit, invoke=invoke
        )
        return reply, cards, events
    from report_writing.chat.intent_clarify import rehang_last_clarify

    rehang = rehang_last_clarify(session)
    if rehang is not None:
        return rehang[0], list(rehang[1] or []), []
    return "这轮确认好像过期了，你再说一下想做什么就行。", [], []


def run_ui_action_shortcut(
    *,
    home: Path,
    session: ChatSession,
    robot: Any,
    invoke: InvokeFn,
    emit: EmitFn | None,
    action: str,
    payload: dict[str, Any],
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
                home, session, key, dict(payload or {}), emit=emit
            )
        except ValueError as error:
            return str(error), [], []
        return reply, list(cards or []), []
    if key in {
        "check_writing_supplements",
        "skip_writing_supplement_check",
        "dismiss_writing_supplement",
    }:
        from report_writing.chat.intent_dispatch import execute_dispatch_tool

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
    payload: dict[str, Any],
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
        "payload": payload or {},
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
        body["session_goal"] = goal
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
            f"用户点击了界面操作「{action}」：本轮必须调用同名 Tool 一次完成（payload 原样传入）；"
            "勿改用其它工具替代（例如保存蓝本勿用 blueprint_subagent）。"
        )
        if action == "save_freeform_as_blueprint" and home is not None:
            from report_writing.chat.freeform_blueprint import composer_save_blueprint_state

            body["composer_save_blueprint"] = composer_save_blueprint_state(home, session)
    else:
        body["hint"] = (
            "结合 writing_brief、dialogue_full、turn_context、focus_document_brief 理解用户本轮意图后再选工具；"
            "两条主路（有蓝本 / 无蓝本·模版或自动大纲）由你统一判断；文档名不够就多轮问清再落 meta。"
            "默认操作当前焦点文档；用户点名其它文档或另写一份时再切换。"
            "要点阶段若用户在加要素、记背景，用 add_chapter_element / remember_writing_goal，"
            "勿把一句操作指令 submit_points 或只回复书写节奏。"
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
                "已定名但缺写作入口：下一步唯一 Tool 是 ask_entry_path（或等用户点选入口卡）；"
                "禁止再调 remember_writing_goal / set_session_meta 只回「记下了」。"
            )
    from report_writing.chat.intent_clarify import get_pending

    pending = get_pending(session)
    if isinstance(pending, dict) and pending:
        body["pending_clarify"] = {
            "id": pending.get("id") or "",
            "source": pending.get("source") or "",
            "question": pending.get("question") or "",
            "options": list(pending.get("options") or [])[:6],
        }
        extra = (
            "有 pending 澄清卡：用户点选或口语对应选项时调用 resolve_intent_clarify"
            "（option_id / option_label）；不要另开一套意图分类。"
        )
        prev = str(body.get("routing_hint") or "").strip()
        body["routing_hint"] = f"{prev} {extra}".strip() if prev else extra
    from report_writing.chat.turn_context import build_writing_turn_context, merge_turn_context_into_agent_body
    from report_writing.chat.writing_brief import brief_to_dict_for_body

    ctx = build_writing_turn_context(session, message or "", home=home, user_action=action)
    merge_turn_context_into_agent_body(body, ctx)
    body["writing_brief"] = brief_to_dict_for_body(session, home=home)
    from report_writing.chat.next_step import infer_suggested_next_step

    body["suggested_next_step"] = infer_suggested_next_step(session, home=home, message=message or "")
    snap = (session.freeform or {}).get("session_gaps_snapshot")
    if isinstance(snap, dict) and snap.get("context_line"):
        body["session_gaps"] = snap
    suggested = list((session.freeform or {}).get("suggested_next_tools") or [])
    if not suggested and isinstance(snap, dict):
        suggested = list(snap.get("suggested_tools") or [])
    if suggested:
        body["suggested_next_tools"] = suggested
        extra = (
            "session 上有 suggested_next_tools：本轮按序调用这些 Tool 并读 observation；"
            "不要只口头复述队列，也不要另起一套计划。"
        )
        prev = str(body.get("routing_hint") or "").strip()
        body["routing_hint"] = f"{prev} {extra}".strip() if prev else extra
    return json.dumps(body, ensure_ascii=False)


def _blueprint_no_match_cards(home: Path, robot: Any) -> list:
    """无可用/无匹配蓝本：仅蓝本创建/跳过卡片。"""
    from report_writing.chat.resource_readiness import blueprint_stage_cards

    return blueprint_stage_cards(home, robot)


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
    # Tool 失败后模型根据 observation 改口：优先最终 AI 文本
    if not last_tool_ok and ai_text:
        return ai_text
    if tool_hint:
        return tool_hint
    if ai_text:
        return ai_text
    if state.cards:
        return "好的，你看下方面板，点一下就能继续。"
    return "嗯，我在听——你接着说就行。"


def _agent_user_text_for_message(state: ChatControlState, message: str) -> str:
    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.session_snapshot import session_turn_snapshot

    body: dict[str, Any] = {
        "action": "",
        "message": message or "",
        "session": {
            **session_turn_snapshot(state.session, message),
            "robot_id": state.session.robot_id or "",
        },
    }
    dialogue_full = format_full_dialogue(state.session)
    if dialogue_full:
        body["dialogue_full"] = dialogue_full
    summary = _format_recent_dialogue_summary(state.session)
    if summary:
        body["recent_dialogue"] = summary
    from report_writing.chat.session_goal import get_session_goal

    goal = get_session_goal(state.session)
    if any(str(goal.get(k) or "").strip() for k in ("summary", "doc_category", "doc_title", "project")):
        body["session_goal"] = goal
    snap = session_turn_snapshot(state.session, message)
    if snap.get("skip_blueprint_path") or snap.get("dialogue_skipped_blueprint"):
        body["hint"] = (
            "用户已走或倾向无蓝本：禁止 match_blueprints/推销蓝本；"
            "题目未清则多轮口语问清写什么/叫什么，够了再 submit_freeform_meta；"
            "勿强迫「类型｜标题」固定句式；有目录意图再 submit_outline / generate_freeform_points。"
        )
    else:
        body["hint"] = (
            "结合 dialogue_full、session_goal、phase 理解用户一句，再调用对应工具；"
            "两条主路：有蓝本 · 无蓝本（模版 / 自动大纲）。"
            "matched=0 或结果明显不相关时回到两步入口澄清，禁止用无关蓝本凑数；"
            "定名后勿擅自生成目录。"
        )
    from report_writing.chat.turn_context import ensure_control_state_context, merge_turn_context_into_agent_body

    merge_turn_context_into_agent_body(body, ensure_control_state_context(state, message))
    from report_writing.chat.next_step import infer_suggested_next_step

    body["suggested_next_step"] = infer_suggested_next_step(
        state.session, home=state.home, message=message or ""
    )
    return json.dumps(body, ensure_ascii=False)


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

        intent_llm = state.intent_invoke or state.invoke
        refresh_session_gaps(
            state.session,
            message=message,
            home=state.home,
            invoke=intent_llm,
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

    intent_llm = state.intent_invoke
    if intent_llm is None and state.invoke is not None:
        from report_writing.chat.intent_invoke import intent_invoke_fn

        intent_llm = intent_invoke_fn(state.invoke)

    state.agent_may_select_blueprint = (state.user_action or action or "").strip() == (
        "select_blueprint"
    )

    if not (state.user_action or action) and message:
        from report_writing.chat.session_documents import ensure_write_onboarding_started

        ensure_write_onboarding_started(
            state.session,
            state.home,
            message,
            invoke=intent_llm,
            emit=state.emit,
        )

    act = (state.user_action or action or "").strip()
    reply = ""
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
        except Exception:
            _log.exception("control agent failed; UI action will fall back if present")
            state.did_invoke_tool = False
            reply = ""
    if act in UI_SHORTCUT_ACTIONS and not state.did_invoke_tool:
        payload: dict[str, Any] = {}
        if user_text:
            _, payload, _ = _parse_agent_body(user_text)
        hit = run_ui_action_shortcut(
            home=state.home,
            session=state.session,
            robot=state.robot,
            invoke=intent_llm or state.invoke,
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
    return reply


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
    """离线/单测：按 UI action 或 session phase 调用总控 Tool（不解析口语）。"""

    role: str = "chat_control_script"

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
            from report_writing.chat.materials_pack_gate import MATERIALS_PACK_ACTIONS

            if action in MATERIALS_PACK_ACTIONS:
                return self.finish("")
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
            from report_writing.chat.intent_router import message_requests_blueprint_match

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
