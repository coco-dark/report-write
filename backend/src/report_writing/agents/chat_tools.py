"""总控 LangChain Tool 集（对位 ai-video-studio StudioTools）。

对话总控只绑定本模块的 Tool；蓝本/渲染/样式细操作在 subagents，这里只挂 delegate。

何时/缺了/不要写在 `@tool(..., description=)`；每个参数的必填/可选写在 `Field(description=)`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import tool
from pydantic import Field

from report_writing.agents.state import ChatControlState, EmitFn, InvokeFn
from report_writing.chat.session_state import user_chose_skip_blueprint
from report_writing.domain.models import ChatActionPayload, ChatCard, ChatPayload, ChatPhase, ChatSession


def _state_clarify_invoke(state: ChatControlState) -> InvokeFn | None:
    if state.clarify_invoke is not None:
        return state.clarify_invoke
    from report_writing.chat.clarify_invoke import clarify_invoke_fn

    if state.invoke is None and state.clarify_model is None:
        return None
    return clarify_invoke_fn(state.invoke, model=state.clarify_model)


def _dump(data: dict[str, Any]) -> str:
    """统一 Tool JSON。整段返回值即 LangChain 观察；不另写 observation / suggested_next_tool。"""
    body = dict(data or {})
    body.pop("observation", None)
    body.pop("suggested_next_tool", None)
    body.pop("suggested_args", None)
    return json.dumps(body, ensure_ascii=False)


def _obs_fail(
    *,
    reply_hint: str = "",
    missing_slots: list[str] | None = None,
    error: str = "",
    **extra: Any,
) -> str:
    slots = [str(s).strip() for s in (missing_slots or []) if str(s).strip()]
    body: dict[str, Any] = {
        "ok": False,
        # 缺槽交给总控开口，不把套话塞进 reply_hint
        "reply_hint": "" if slots else (reply_hint or "").strip(),
    }
    if slots:
        body["missing_slots"] = slots
    if error:
        body["error"] = error
    body.update(extra)
    if slots:
        body["reply_hint"] = ""
    return _dump(body)


def _dump_writing_control_result(
    state: ChatControlState,
    result: tuple[str, list, list] | None,
    *,
    expected_tool: str,
    fail_hint: str,
    fail_slots: list[str] | None = None,
) -> str:
    del expected_tool
    if not result:
        return _obs_fail(missing_slots=fail_slots, reply_hint=fail_hint)
    reply, cards, events = result
    hop = next(
        (
            e
            for e in (events or [])
            if isinstance(e, dict) and str(e.get("type") or "") == "suggested_next_tool"
        ),
        None,
    )
    if hop:
        return _obs_fail(reply_hint=reply)
    _set_cards(state, cards)
    return _dump({"ok": True, "reply_hint": reply})


def _gate_writing_doc_named(state: ChatControlState, tool_name: str) -> str | None:
    """按 Tool 能力检查缺槽；工作名即可开写，正式名仅收录/导出需要。"""
    from report_writing.chat.session_documents import (
        focus_needs_entry_branch,
        focus_needs_entry_path,
        mark_focus_entry_path,
        onboarding_lane_active,
    )
    from report_writing.chat.tool_capability import (
        WRITABLE_CHAPTER_TOOLS,
        capability_reply_hint,
        missing_for_tool,
    )

    bid = str(getattr(state.session, "blueprint_id", "") or "").strip()
    if bid:
        mark_focus_entry_path(state.session, "match")

    miss = missing_for_tool(state.session, tool_name, home=state.home)
    if not miss:
        return None
    if tool_name in WRITABLE_CHAPTER_TOOLS and (
        focus_needs_entry_branch(state.session)
        or focus_needs_entry_path(state.session)
        or not onboarding_lane_active(state.session)
    ):
        reply, cards = _hang_entry_path_after_named(
            state, message=state.last_user_message or ""
        )
        _set_cards(state, cards)
        return _obs_fail(
            missing_slots=miss,
            reply_hint=(reply or "").strip() or capability_reply_hint(miss),

        )
    return _obs_fail(
        missing_slots=miss,
        reply_hint=capability_reply_hint(miss),

    )


def _run_dispatch_switch(state: ChatControlState, name: str, args: dict[str, Any] | None = None) -> str:
    """未拆完的调度实现：经 dispatch_inner 调用，避免再绕回绑定 Tool。"""
    from report_writing.chat.tool_dispatch import dispatch_inner, execute_dispatch_tool

    with dispatch_inner():
        hit = execute_dispatch_tool(
            state.home,
            state.session,
            {"tool": name, "args": dict(args or {})},
            str(state.last_user_message or ""),
            emit=state.emit,
            invoke=_state_clarify_invoke(state) or state.invoke,
        )
    if hit is None:
        return _obs_fail(
            reply_hint="这一步暂时没法执行。",

            error="dispatch_none",
        )
    reply, cards, events = hit
    _set_cards(state, cards)
    state.events.extend(events or [])
    wait = _obs_if_supplement_gate(reply, cards)
    if wait:
        return wait
    return _dump({"ok": True, "reply_hint": reply,})


def _card_types(cards: list[Any] | None) -> set[str]:
    return {str(getattr(c, "type", "") or "").strip() for c in (cards or []) if c}


def _obs_if_supplement_gate(reply: str, cards: list[Any] | None) -> str | None:
    """待补充卡要等用户点「去配置 / 先这样写」，不要当成写作已成功继续排下一步。"""
    if "writing_supplement_check" not in _card_types(cards):
        return None
    return _dump(
        {
            "ok": False,
            "awaiting_user_choice": True,
            "missing_slots": ["writing_supplements"],
            "reply_hint": reply,
        }
    )


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
    from report_writing.chat.clarify_flow import build_entry_path_clarify

    intent_fn = _state_clarify_invoke(state) or state.invoke
    return build_entry_path_clarify(
        state.session,
        message=(message or state.last_user_message or "").strip(),
        title_hint=(title_hint or "").strip(),
        invoke=intent_fn,
        home=state.home,
        emit=state.emit,
        after_doc_name_confirm=after_doc_name_confirm,
        apply_plan=False,
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
        invoke=_state_clarify_invoke(state) or state.invoke,
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
    """匹配蓝本：只出候选，不代选。明确选用由 Agent 再调 select_blueprint。"""
    from report_writing.chat.session_state import touch_session_plan
    from report_writing.chat import _blueprint_list_card, _skip_blueprint_card

    msg = message or state.last_user_message
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
            invoke=_state_clarify_invoke(state) or state.invoke,
        )
        _set_cards(state, cards)
        touch_session_plan(
            state.session,
            goal="pick_blueprint",
            candidates=[getattr(m, "id", "") for m in matches],
        )
        ids = [str(getattr(m, "id", "") or "").strip() for m in matches]
        ids = [i for i in ids if i]
        return _dump(
            {
                "ok": True,
                "reply_hint": hint,
                "matched": len(matches),
                "ids": ids,
                "candidate_ids": ids,
                "awaiting_user_choice": True,
                "needs_doc_name": not is_writing_document_named(state.session),

            }
        )

    return _ask_path_after_no_blueprint_match(state, message or state.last_user_message or "")


_PAYLOAD_JSON_ACTIONS = frozenset(
    {
        "check_writing_supplements",
        "skip_writing_supplement_check",
        "dismiss_writing_supplement",
    }
)

# 循环绑定的 Tool：description / payload 参数说明就写在这里（与 @tool 注解同级）。
_LOOP_TOOL_META: dict[str, dict[str, str]] = {
    "check_writing_supplements": {
        "description": "何时：开写前检查要点是否已关联要素；返回待补卡。不要：用户已说跳过时仍反复检查。",
    },
    "skip_writing_supplement_check": {
        "description": "何时：开写前出现待补要素卡，用户明确「先写再说 / 跳过检查」。",
    },
    "dismiss_writing_supplement": {
        "description": "何时：用户在待补卡上忽略某项（只取消该章该项关联要求，不删要素）。缺了：请用户点卡片上的忽略，不要猜哪一项。",
        "payload": "必填。卡片 payload 原样（能确定哪一章哪一项）。",
    },
    "materials_pack_list": {
        "description": "何时：列出当前文档可用资料包。不要：直接绑定。",
    },
    "materials_pack_view": {
        "description": "何时：查看某资料包详情与槽位。缺了：先列出再请用户指定，不要猜。",
        "payload": "必填。资料包 id。",
    },
    "materials_pack_use": {
        "description": "何时：为当前文档选用/绑定资料包。缺了：列出可选资料包让用户选。不要：与新建混淆。",
        "payload": "必填。资料包 id 或卡片 payload。",
    },
    "materials_pack_create": {
        "description": "何时：新建资料包并绑定当前文档。缺了：可问一句名字，用户不说也能建。",
        "payload": "可选。name。",
    },
    "materials_pack_skip": {
        "description": "何时：用户暂不绑定资料包，继续写作。",
    },
    "materials_pack_confirm_library": {
        "description": "何时：用户确认从资料库选用某一资料包。缺了：请用户点选资料库卡片。",
        "payload": "必填。卡片所选资料包 id。",
    },
    "materials_pack_unbind": {
        "description": "何时：解除当前文档与资料包的绑定。缺了：没绑定则说明；多个时问解绑哪一个。",
        "payload": "可选。资料包 id。",
    },
    "materials_pack_add_slot": {
        "description": "何时：向资料包增加文件槽位。缺了：问槽位叫什么、要哪类文件。",
        "payload": "必填。槽位名或类型；可选资料包 id。",
    },
    "materials_pack_remove_slot": {
        "description": "何时：删除资料包槽位。缺了：问删哪个槽；不要误删整个资料包。",
        "payload": "必填。哪个槽位；可选资料包 id。",
    },
    "materials_pack_update_slot": {
        "description": "何时：更新槽位配置或已传文件。缺了：问改哪一个槽、改成什么样。",
        "payload": "必填。哪个槽位；可选新配置。",
    },
    "materials_pack_rename": {
        "description": "何时：重命名资料包。缺了：问新名字。",
        "payload": "必填。新名称；可选资料包 id。",
    },
}


def bound_tool_invoke_args(
    action: str, payload: ChatPayload | dict[str, Any], message: str = ""
) -> dict[str, Any]:
    """把 dispatch/UI payload 转成绑定 Tool 的 kwargs。"""
    payload = ChatActionPayload.parse(payload).as_dict()
    msg = (message or "").strip()
    if action == "select_blueprint":
        return {"blueprint_id": str(payload.get("blueprint_id") or "").strip()}
    if action == "skip_blueprint":
        return {
            "path_setup_only": bool(payload.get("path_setup_only")),
            "message": str(payload.get("message") or msg).strip(),
            "title_hint": str(payload.get("title_hint") or payload.get("title") or "").strip(),
        }
    if action == "begin_blueprint_wizard":
        return {
            "force_new": bool(payload.get("force_new", True)),
            "title_hint": str(payload.get("title_hint") or payload.get("title") or "").strip(),
            "explicit_create": bool(payload.get("explicit_create")),
            "confirmed_create": bool(payload.get("confirmed_create")),
            "from_entry_path": bool(payload.get("from_entry_path")),
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
                payload.get("revision_instructions") or payload.get("message") or msg
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
        return {"message": str(payload.get("message") or payload.get("q") or msg).strip()}
    if action == "ask_entry_path":
        return {
            "message": str(payload.get("message") or msg).strip(),
            "title_hint": str(payload.get("title_hint") or payload.get("title") or "").strip(),
        }
    if action == "ask_intent":
        return {
            "message": str(payload.get("message") or msg).strip(),
            "question": str(payload.get("question") or "").strip(),
        }
    if action == "focus_document":
        return {
            "document_title": str(
                payload.get("document_title")
                or payload.get("doc_title")
                or payload.get("title")
                or ""
            ).strip(),
            "message": msg,
        }
    if action == "re_study_chapters":
        return {"payload_json": json.dumps(payload, ensure_ascii=False), "message": msg}
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
    if action == "generate_chapter_boilerplate":
        return {
            "chapter_key": str(payload.get("chapter_key") or "").strip(),
            "source_text": str(
                payload.get("source_text") or payload.get("reference_text") or payload.get("text") or ""
            ).strip(),
            "task_id": str(payload.get("task_id") or "").strip(),
            "message": str(payload.get("message") or msg).strip(),
        }
    if action in {
        "configure_chapter",
        "get_chapter_info",
        "generate_chapter_describes",
        "add_chapter_element",
        "associate_elements",
        "collect_to_element_library",
        "associate_element_knowledge",
        "retry_chapter",
        "retry_search_chapter",
        "dissociate_chapter_element",
        "remove_document_element",
    }:
        return {"payload_json": json.dumps(payload, ensure_ascii=False)}
    if action in {"open_element_library", "open_blueprint_library", "open_knowledge"}:
        return {}
    if action == "resource_check":
        return {}
    if action == "set_session_meta":
        return {
            "message": str(payload.get("message") or msg).strip(),
            "category": str(payload.get("category") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
        }
    if action == "remember_writing_goal":
        return {"notes": str(payload.get("notes") or payload.get("message") or msg).strip()}
    if action == "resolve_intent_clarify":
        return {
            "option_id": str(payload.get("option_id") or "").strip(),
            "option_label": str(payload.get("option_label") or "").strip(),
            "clarify_id": str(payload.get("clarify_id") or "").strip(),
            "message": str(payload.get("message") or msg).strip(),
        }
    from report_writing.chat.materials_pack_gate import MATERIALS_PACK_ACTIONS

    if action in MATERIALS_PACK_ACTIONS or action in _PAYLOAD_JSON_ACTIONS:
        return {"payload_json": json.dumps(payload or {}, ensure_ascii=False)}
    return {}


def invoke_named_tool(
    state: ChatControlState,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    via: str = "named",
) -> str | None:
    """执行已绑定的总控 Tool（与 ReAct / 点选同一套实现）。"""
    key = (name or "").strip()
    if not key:
        return None
    for item in build_chat_tools(state):
        if getattr(item, "name", None) == key:
            return _invoke_tool(state, item, dict(args or {}), via=via)
    return None


def _bind_payload_json_tools(state: ChatControlState, names: list[str]) -> list:
    """把 dispatch / 资料包动作绑成同名 LangChain Tool（点选走 Agent）。"""
    from langchain_core.tools import StructuredTool
    from report_writing.chat.materials_pack_gate import (
        MATERIALS_PACK_ACTIONS,
        apply_materials_pack_choice,
    )

    tools = []
    for name in names:

        def _run(payload_json: str = "{}", *, _name: str = name) -> str:
            _tool_begin(state, _name)
            payload = ChatActionPayload.parse(payload_json).as_dict()
            if _name in MATERIALS_PACK_ACTIONS:
                try:
                    reply, cards = apply_materials_pack_choice(
                        state.home, state.session, _name, payload, emit=state.emit
                    )
                except ValueError as error:
                    return _dump({"ok": False, "reply_hint": str(error)})
                _set_cards(state, cards)
                return _dump(
                    {
                        "ok": True,
                        "reply_hint": reply,

                    }
                )
            from report_writing.chat.tool_dispatch import dispatch_inner, execute_dispatch_tool

            with dispatch_inner():
                hit = execute_dispatch_tool(
                    state.home,
                    state.session,
                    {"tool": _name, "args": dict(payload)},
                    str(state.last_user_message or ""),
                    emit=state.emit,
                    invoke=state.invoke,
                )
            if hit is None:
                return _dump({"ok": False, "reply_hint": f"无法执行 {_name}。"})
            reply, cards, events = hit
            _set_cards(state, cards)
            state.events.extend(events or [])
            return _dump(
                {
                    "ok": True,
                    "reply_hint": reply,

                }
            )

        from pydantic import Field, create_model

        meta = _LOOP_TOOL_META.get(name, {})
        payload_desc = str(
            meta.get("payload")
            or "JSON。按卡片 payload 原样传入；缺 id/名称就先问或先列出，不要猜。"
        )
        args_schema = create_model(
            f"{name}Args",
            payload_json=(str, Field(default="{}", description=payload_desc)),
        )
        tools.append(
            StructuredTool.from_function(
                func=_run,
                name=name,
                description=str(meta.get("description") or name),
                args_schema=args_schema,
            )
        )
    return tools


def build_chat_tools(state: ChatControlState) -> list:
    """绑定会话状态的总控工具。"""

    @tool("list_blueprints", description="何时：用户要浏览蓝本库、问「有哪些蓝本」，而不是按题材选型去写。缺了：直接列出可见蓝本即可。不要：按「写一份××」去匹配（用 match_blueprints）；寒暄、无写意图不要调。")
    def list_blueprints(q: Annotated[str, Field(description="可选。关键词；空则列出当前可见蓝本。")] = "") -> str:
        """

        何时：用户要浏览蓝本库、问「有哪些蓝本」，而不是按题材选型去写。

        必填：无。

        可选：q=关键词。

        缺了：直接列出可见蓝本即可。

        不要：按「写一份××」去匹配（用 match_blueprints）；寒暄、无写意图不要调。

        """
        from report_writing.chat import _action_list_blueprints

        _tool_begin(state, "list_blueprints")
        reply, cards = _action_list_blueprints(state.home, state.session, {"q": q or ""})
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply, "count": len(cards[0].payload.get("items") or []) if cards else 0})

    @tool("ask_entry_path", description="何时：新建或缺入口——还没选定「匹配蓝本 / 无蓝本自写 / 依据模版」。读 onboarding gaps，只挂仍缺的澄清卡。缺了：挂入口澄清卡，用对话等用户点选或下一条原话，不要猜路径。不要：静默 match 或开写；寒暄/致谢/只看库不要调；已定名但仍缺入口时，用户说「继续/好的」应调本工具，不要只 remember_writing_goal 回「记下了」。")
    def ask_entry_path(message: Annotated[str, Field(description="可选。用户原话。")] = "", title_hint: Annotated[str, Field(description="可选。工作名/标题提示。")] = "") -> str:
        """

        何时：新建或缺入口——还没选定「匹配蓝本 / 无蓝本自写 / 依据模版」。读 onboarding gaps，只挂仍缺的澄清卡。

        必填：无。读返回的 missing_slots / suggested_next_tools。

        可选：message、title_hint=用户原话/工作名提示。

        缺了：挂入口澄清卡，用对话等用户点选或下一条原话，不要猜路径。

        不要：静默 match 或开写；寒暄/致谢/只看库不要调；已定名但仍缺入口时，用户说「继续/好的」应调本工具，不要只 remember_writing_goal 回「记下了」。

        """
        from report_writing.chat.clarify_flow import build_entry_path_clarify
        from report_writing.chat.session_documents import is_writing_document_named

        msg = (message or state.last_user_message or "").strip()
        tip = (title_hint or "").strip()
        _tool_begin(state, "ask_entry_path")
        intent_fn = _state_clarify_invoke(state) or state.invoke
        reply, cards = build_entry_path_clarify(
            state.session,
            message=msg,
            title_hint=tip,
            invoke=intent_fn,
            home=state.home,
            emit=state.emit,
            apply_plan=False,
        )
        # 一句多意图：首句定名后的残余路径 / follow spec
        ff = dict(state.session.freeform or {})
        follow_spec = ff.pop("clarify_follow_spec", None)
        if isinstance(follow_spec, dict):
            from report_writing.chat.clarify_flow import execute_clarify_spec

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
        route_parts = [
            "已执行 ask_entry_path（只挂卡，未在 clarify 内静默 dispatch）。",
            f"pending_clarify={pending_src or 'none'}",
            f"onboarding_gaps={gaps}",
        ]
        if suggested:
            route_parts.append(
                f"suggested_next_tools={suggested}；同轮按缺项调用（不必固定先定名再 match）。"
            )
        elif not pending_src and named and not gaps.get("missing_path"):
            route_parts.append("入口缺口已齐，可直接 match/skip/wizard/submit_outline。")
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "routing_hint": " ".join(route_parts),
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

    @tool("match_blueprints", description="何时：用户要写某类文档、明确匹配，或入口已选「先匹配蓝本」。「写一份××」可用工作名立刻匹配。缺了：原话太空则先口头问要写什么类型，不要空跑匹配。不要：擅自 select_blueprint；无对口蓝本时不要在本工具里代开向导或 skip；寒暄、只看库、无写意图不要调。")
    def match_blueprints(message: Annotated[str, Field(description="可选。用户原话；空则用本轮对话。太空不要空跑匹配。")] = "") -> str:
        """

        何时：用户要写某类文档、明确匹配，或入口已选「先匹配蓝本」。「写一份××」可用工作名立刻匹配。

        必填：无（以用户原话为主）。

        可选：message=原话。

        缺了：原话太空则先口头问要写什么类型，不要空跑匹配。

        不要：擅自 select_blueprint；无对口蓝本时不要在本工具里代开向导或 skip；寒暄、只看库、无写意图不要调。

        """
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
                reply_hint="",
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
        ids: list[str] = []
        for c in cards or []:
            if getattr(c, "type", "") == "blueprint_list":
                items = (getattr(c, "payload", None) or {}).get("items") or []
                if isinstance(items, list):
                    matched_n = len(items)
                    ids = [
                        str(item.get("id") or "").strip()
                        for item in items
                        if isinstance(item, dict) and str(item.get("id") or "").strip()
                    ]
        needs_name = not is_writing_document_named(state.session)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "matched": matched_n,
                "ids": ids,
                "candidate_ids": ids,
                "awaiting_user_choice": matched_n > 0,
                "awaiting_path_choice": "intent_clarify" in types or matched_n == 0,
                "needs_doc_name": needs_name,
            }
        )

    def _invoke_subagent(run_fn, query: str, *, err: str) -> str:
        """

        官方：总控把子 Agent 当 Tool；子 Agent create_agent.invoke 后结果回这里。

        """
        if state.control_model is None:
            return _dump({"ok": False, "reply_hint": err})
        text = (query or state.last_user_message or "").strip()
        return run_fn(state.control_model, state, text)

    @tool("blueprint_subagent", description="何时：蓝本配置/选型较复杂，需要子助手连续处理匹配、列表、向导步。缺了：把当前用户原话原样传入即可。不要：用本工具写正文或开写；向导未开始时不要委派来「代 begin」，应直接 begin_blueprint_wizard。简单一步仍优先直接调 match/list/wizard。")
    def blueprint_subagent(user_request: Annotated[str, Field(description="可选。用户原话，推荐原样传入。")] = "") -> str:
        """

        何时：蓝本配置/选型较复杂，需要子助手连续处理匹配、列表、向导步。

        必填：无。

        可选：user_request=用户原话（推荐传入）。

        缺了：把当前用户原话原样传入即可。

        不要：用本工具写正文或开写；向导未开始时不要委派来「代 begin」，应直接 begin_blueprint_wizard。简单一步仍优先直接调 match/list/wizard。

        """
        from report_writing.agents.subagents.blueprint import run_blueprint_subagent

        return _invoke_subagent(
            run_blueprint_subagent,
            user_request,
            err="写作助手未就绪，暂时无法处理蓝本。",
        )

    @tool("blueprint_wizard_subagent", description="兼容旧名，语义同 blueprint_subagent。新调用请用 blueprint_subagent。")
    def blueprint_wizard_subagent(user_request: Annotated[str, Field(description="可选。用户原话。新调用请用 blueprint_subagent。")] = "") -> str:
        """

        兼容旧名，语义同 blueprint_subagent。新调用请用 blueprint_subagent。

        必填：无。

        可选：user_request。向导未开始时不要委派来代 begin。

        """
        from report_writing.agents.subagents.blueprint import run_blueprint_subagent

        return _invoke_subagent(
            run_blueprint_subagent,
            user_request,
            err="写作助手未就绪，暂时无法配置蓝本。",
        )

    @tool("render_subagent", description="何时：导出 Word 前要列出/配置 HTML 渲染模版。不要：开写阶段强推；改 Word 样式用 style_subagent。")
    def render_subagent(user_request: Annotated[str, Field(description="可选。用户原话。")] = "") -> str:
        """

        何时：导出 Word 前要列出/配置 HTML 渲染模版。

        必填：无。

        可选：user_request=原话。

        不要：开写阶段强推；改 Word 样式用 style_subagent。

        """
        from report_writing.agents.subagents.render import run_render_subagent

        return _invoke_subagent(
            run_render_subagent,
            user_request,
            err="写作助手未就绪，暂时无法处理渲染模版。",
        )

    @tool("style_subagent", description="何时：导出前要列出/配置 Word 样式。不要：与渲染 HTML 模版混用（那是 render_subagent）。")
    def style_subagent(user_request: Annotated[str, Field(description="可选。用户原话。")] = "") -> str:
        """

        何时：导出前要列出/配置 Word 样式。

        必填：无。

        可选：user_request=原话。

        不要：与渲染 HTML 模版混用（那是 render_subagent）。

        """
        from report_writing.agents.subagents.style import run_style_subagent

        return _invoke_subagent(
            run_style_subagent,
            user_request,
            err="写作助手未就绪，暂时无法处理 Word 样式。",
        )

    @tool("select_blueprint", description="何时：用户点了「选用」或口语明确点名某一份蓝本。缺了：列出刚才匹配到的蓝本让用户点选用，不要猜第一份。不要：低置信代选。")
    def select_blueprint(blueprint_id: Annotated[str, Field(description="必填。蓝本 id。从「选用」卡片或用户点名得到，不要猜第一份。")] = "") -> str:
        """

        何时：用户点了「选用」或口语明确点名某一份蓝本。

        必填：blueprint_id。

        可选：无。

        缺了：列出刚才匹配到的蓝本让用户点选用，不要猜第一份。

        不要：低置信代选。

        """
        from report_writing.chat import _action_select_blueprint

        bid = (blueprint_id or "").strip()
        if not bid:
            return _obs_fail(
                missing_slots=["blueprint_id"],
                reply_hint="还没拿到蓝本 id，请再点一次「选用」或告诉我用哪一份。",

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

            }
        )

    @tool("select_write_chapters", description="何时：用户要写某一章/开写正文（点名序号或「刚才分析的那几章」）。不是分析要点。空 keys 由口语解析最近分析批。缺了：请勾选或点名；未分析则说明，不要改去分析。不要：问进度（用 get_writing_status）；已勾选后点「开始写作」用 start_writing；「写已分析的全部」用 write_analyzed_only；不要调 re_study_chapters / generate_freeform_points。")
    def select_write_chapters(blueprint_id: Annotated[str, Field(description="可选。已绑蓝本时可从会话取。")] = "", chapter_keys_json: Annotated[str, Field(description="必填语义。已分析章 key 的 JSON 数组。缺了请用户勾选或点名。")] = "[]") -> str:
        """

        何时：用户要写某一章/开写正文（点名序号或「刚才分析的那几章」）。不是分析要点。

        必填：chapter_keys_json（已分析章的 key 列表）；已绑蓝本。空数组时按用户原话解析（含最近分析批）。

        可选：blueprint_id（默认可从会话取）。

        缺了：请勾选或点名；未分析则说明，不要改去分析、不要把未分析章塞进去。

        不要：问进度（用 get_writing_status）；已勾选后点「开始写作」用 start_writing；「写已分析的全部」用 write_analyzed_only；不要调 re_study_chapters / generate_freeform_points。

        """
        from report_writing.chat import _action_select_write_chapters

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
        from report_writing.chat.session_documents import focus_bound_blueprint_id

        bid = (
            blueprint_id
            or state.session.blueprint_id
            or focus_bound_blueprint_id(state.session)
            or ""
        ).strip()
        if not keys:
            from report_writing.chat.write_chapter_intent import try_write_chapters_from_message

            oral = try_write_chapters_from_message(
                state.home,
                state.session,
                state.last_user_message or "",
                emit=state.emit,
                invoke=_state_clarify_invoke(state) or state.invoke,
                chapter_keys=None,
                write_analyzed_only=False,
            )
            if oral is not None:
                reply, cards, events = oral if len(oral) == 3 else (*oral, [])
                state.events.extend(events or [])
                _set_cards(state, cards)
                return _dump({"ok": True, "reply_hint": reply})
            # oral None：多半是闲聊/问能力，不是选章；禁止再甩「还没勾选」或进度卡
            if (state.last_user_message or "").strip():
                return _obs_fail(
                    missing_slots=[],
                    reply_hint=(
                        "这句不像在指定要写的章节。写作中可看右侧目录与配置；"
                        "可以说写哪几章或「写已分析的」；问进度再说「写到哪了」。"
                        "请口头回答用户，不要调用 get_writing_status。"
                    ),
                    blueprint_id=bid,
                )
            sess_keys = [
                str(k).strip()
                for k in ((state.session.freeform or {}).get("write_chapter_keys") or [])
                if str(k).strip()
            ]
            if not sess_keys:
                return _obs_fail(
                    missing_slots=["chapter_keys"],
                    reply_hint="还没勾选要写的章节，请在文档窗口勾选后再开写。",

                    blueprint_id=bid,
                )
            keys = sess_keys
        payload = {"blueprint_id": bid, "chapter_keys": keys}
        _emit_process(state, f"好的，按勾选的 {len(keys)} 章依次开写…")
        try:
            reply, cards = _action_select_write_chapters(
                state.home, state.session, payload, emit=state.emit, invoke=state.invoke
            )
        except ValueError as error:
            return _obs_fail(reply_hint=str(error) or "现在还不能开写。")
        _set_cards(state, cards)
        types = _card_types(cards)
        if types & {"analysis_gate", "chapter_picker"}:
            return _obs_fail(
                missing_slots=["analyzed_chapters"]
                if "analysis_gate" in types
                else ["chapter_keys"],
                reply_hint=reply,

                count=len(keys),
            )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "count": len(keys),

            }
        )

    @tool("write_analyzed_only", description="何时：用户明确只要写已经分析完的章节（没点名「刚才那一批」）。缺了：没有已分析章就引导先分析，不要把未分析章硬开写。不要：指代「刚才分析的那几章」用 select_write_chapters；点名某一章用 select_write_chapters；问进度用 get_writing_status。")
    def write_analyzed_only(blueprint_id: Annotated[str, Field(description="可选。可从会话取。")] = "", chapter_keys_json: Annotated[str, Field(description="可选。指定子集；空则已分析全集。不要塞未分析章。")] = "[]") -> str:
        """

        何时：用户明确只要写已经分析完的章节（没点名「刚才那一批」）。

        必填：无（可写已分析全集）；指定子集时 chapter_keys_json。

        可选：blueprint_id。

        缺了：没有已分析章就引导先分析，不要把未分析章硬开写。

        不要：指代「刚才分析的那几章」或点名某一章用 select_write_chapters；问进度用 get_writing_status。

        """
        from report_writing.chat import _action_write_analyzed_only

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
        if not keys:
            from report_writing.chat.write_chapter_intent import try_write_chapters_from_message

            oral = try_write_chapters_from_message(
                state.home,
                state.session,
                state.last_user_message or "",
                emit=state.emit,
                invoke=state.invoke,
                write_analyzed_only=True,
            )
            if oral is not None:
                reply, cards, events = oral if len(oral) == 3 else (*oral, [])
                state.events.extend(events or [])
                _set_cards(state, cards)
                return _dump({"ok": True, "reply_hint": reply})
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

            )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "task_id": state.session.task_id,

            }
        )

    @tool("skip_blueprint", description="何时：用户说不用蓝本、自动生成章节目录，或入口已选无蓝本。缺了：只锁无蓝本路径；缺标题则继续对话起名（set_session_meta），不要编标题。作用：不抽类型标题、不生成目录。下一跳：已有标题且要自动大纲则 submit_outline（章数写进目录请求）。不要：用本工具开模版向导。")
    def skip_blueprint(
        path_setup_only: Annotated[bool, Field(description="可选。后接自动大纲时 true，只锁路径不抽标题。")] = False,
        message: Annotated[str, Field(description="可选。用户原话。")] = "",
        title_hint: Annotated[str, Field(description="可选。工作名提示；不要编标题。")] = "",
    ) -> str:
        """

        何时：用户说不用蓝本、自动生成章节目录，或入口已选无蓝本。

        必填：无。

        可选：message、title_hint；后接自动大纲时 path_setup_only=true。

        缺了：只锁无蓝本路径；缺标题则继续对话起名（set_session_meta），不要编标题。

        作用：不抽类型标题、不生成目录。

        下一跳：已有标题且要自动大纲则 submit_outline（章数写进目录请求）。

        不要：用本工具开模版向导。

        """
        from report_writing.chat import _action_skip_blueprint

        from report_writing.chat.session_documents import (
            focus_has_display_title,
            is_writing_document_named,
        )

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
                invoke=_state_clarify_invoke(state) or state.invoke,
            )
            _set_cards(state, cards)
            return _dump(
                {
                    "ok": True,
                    "reply_hint": reply,

                    "awaiting_path_choice": True,
                }
            )

        extra = (message or state.last_user_message or "").strip()
        tip = (title_hint or "").strip()
        _tool_begin(state, "skip_blueprint")
        reply, cards = _action_skip_blueprint(
            state.session,
            home=state.home,
            invoke=state.invoke,
            emit=state.emit,
            extra_message=extra,
            title_hint=tip,
            path_setup_only=bool(path_setup_only),
        )
        _set_cards(state, cards)

        named = is_writing_document_named(state.session) or focus_has_display_title(
            state.session
        )
        missing: list[str] = []
        if not named:
            missing = ["title"]
        return _dump(
            {
                "ok": True,
                "reply_hint": "" if missing else reply,
                "needs_doc_name": not named,
                "missing_slots": missing or None,
            }
        )

    @tool("begin_blueprint_wizard", description="何时：用户说「我有模版/用我的模版」（explicit_create=true），或点选/确认「依据模版编写」。缺了：先确认「依据模版编写」再调，不要把题材当已定名。作用：在当前文档 w* 上开向导（上传→目录→学习）；收录前不入蓝本库。不要：当成 save_freeform_as_blueprint。")
    def begin_blueprint_wizard(
        force_new: Annotated[bool, Field(description="可选。默认 true，在当前文档开向导。")] = True,
        title_hint: Annotated[str, Field(description="可选。名称提示，不是已定名。")] = "",
        explicit_create: Annotated[bool, Field(description="可选。用户明确说用模版时 true。")] = False,
        confirmed_create: Annotated[bool, Field(description="可选。用户已确认「依据模版编写」时 true。")] = False,
        from_entry_path: Annotated[bool, Field(description="可选。从入口卡点选时 true。")] = False,
    ) -> str:
        """

        何时：用户说「我有模版/用我的模版」（explicit_create=true），或点选/确认「依据模版编写」。

        必填：无。用户尚未确认走模版时不要设 explicit_create。

        可选：title_hint；explicit_create / confirmed_create / from_entry_path / force_new。

        缺了：先确认「依据模版编写」再调，不要把题材当已定名。

        作用：在当前文档 w* 上开向导（上传→目录→学习）；收录前不入蓝本库。

        不要：当成 save_freeform_as_blueprint。

        """
        from report_writing.chat.blueprint_actions import run_blueprint_action
        from report_writing.chat.session_documents import is_writing_document_named

        ui = (state.user_action or "").strip() == "begin_blueprint_wizard"
        from report_writing.chat.session_documents import (
            focus_onboarding,
            message_wants_own_template,
            peek_pending_entry,
        )

        if not (title_hint or "").strip():
            title_hint = str(peek_pending_entry(state.session).get("title") or "").strip()
        user_msg = (state.last_user_message or "").strip()
        ob = focus_onboarding(state.session)
        inferred = (
            message_wants_own_template(user_msg)
            or str(ob.get("preferred_entry_path") or "") == "template"
            or str(ob.get("entry_path") or "") == "template"
        )
        if inferred:
            explicit_create = True
            confirmed_create = True
        if not (explicit_create or confirmed_create or from_entry_path or ui):
            return _obs_fail(
                missing_slots=["explicit_create"],
                reply_hint="要依据模版编写的话跟我说一声，或选「无蓝本 · 依据模版编写」。",

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
            invoke=_state_clarify_invoke(state) or state.invoke,
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

                "needs_doc_name": not named,
            }
        )

    @tool("handoff_blueprint_page", description="何时：必须去蓝本页做对话里做不了的深度配置。不要：能用对话 Tool 解决时跳转；若已有「仅写已分析」选项，不得清掉。")
    def handoff_blueprint_page(blueprint_id: Annotated[str, Field(description="可选。")] = "", title: Annotated[str, Field(description="可选。跳转说明。")] = "去蓝本分析配置") -> str:
        """

        何时：必须去蓝本页做对话里做不了的深度配置。

        必填：无。

        可选：blueprint_id、title。

        不要：能用对话 Tool 解决时跳转；若已有「仅写已分析」选项，不得清掉。

        """
        from report_writing.chat.session_state import active_blueprint_wizard
        from report_writing.chat import (
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

    @tool("resume_from_blueprint", description="何时：用户从蓝本页返回对话，要恢复写作上下文。缺了：问从哪份蓝本/任务回来，不要猜。")
    def resume_from_blueprint(blueprint_id: Annotated[str, Field(description="与 task_id 至少填一个。")] = "", task_id: Annotated[str, Field(description="与 blueprint_id 至少填一个。缺了问从哪份回来。")] = "") -> str:
        """

        何时：用户从蓝本页返回对话，要恢复写作上下文。

        必填：blueprint_id 或 task_id 之一。

        可选：另一个 id。

        缺了：问从哪份蓝本/任务回来，不要猜。

        """
        from report_writing.chat import _action_resume

        payload = {}
        if blueprint_id:
            payload["blueprint_id"] = blueprint_id
        if task_id:
            payload["task_id"] = task_id
        reply, cards = _action_resume(state.home, state.session, payload)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("start_writing", description="何时：章节已勾选，或用户点「开始写作」/「开始写作章节[序号 标题]」。缺了：没说哪一章改走 select_write_chapters 或请勾选，不要猜第一章；多章未指定 pace 先澄清「一次写完 / 写一章看一章」。不要：口语刚点名要写哪几章时抢本工具（用 select_write_chapters）；不要分析要点；路径未定不要硬开写；「改名叫 X 然后开始写」须先定名再开写。")
    def start_writing(writing_pace: Annotated[str, Field(description="可选。batch=一次写完；step=写一章看一章。多章未指定时先问，不要猜。")] = "", chapter_keys_json: Annotated[str, Field(description="可选。章节 key 的 JSON 数组。没点名章节时不要猜第一章，用会话勾选/焦点章或先问。")] = "[]") -> str:
        """

        何时：章节已勾选，或用户点「开始写作」/「开始写作章节[序号 标题]」。

        必填：至少能确定要写哪些章（chapter_keys_json 或会话已勾选/焦点章）。

        可选：writing_pace=batch|step。

        缺了：没说哪一章改走 select_write_chapters 或请勾选，不要猜第一章；多章未指定 pace 先澄清「一次写完 / 写一章看一章」。

        不要：口语刚点名要写哪几章时抢本工具（用 select_write_chapters）；不要分析要点；路径未定不要硬开写；「改名叫 X 然后开始写」须先定名再开写。

        """
        from report_writing.chat import _action_start_writing

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
            model=state.chat_model,
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        wait = _obs_if_supplement_gate(reply, cards)
        if wait:
            return wait
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

                task_id=state.session.task_id or "",
            )
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "task_id": state.session.task_id,

            }
        )

    @tool("get_writing_status", description="何时：用户明确问写到哪了、哪章待确认、看写作进度。返回 status_facts，reply_hint 为空：对用户说话按原话组织一两句，禁止复读进度套话。缺了：用户没问进度不要调。不要：开写或改稿；闲聊/「能干什么」/「现在怎么办」口头答；「刚才分析的那几章」走 select_write_chapters；看单章配置用 get_chapter_info。")
    def get_writing_status() -> str:
        """

        何时：用户明确问写到哪了、哪章待确认、看写作进度。

        必填：无。

        可选：无。

        返回：status_facts；reply_hint 为空。对用户说话按原话组织一两句，禁止复读进度套话。

        缺了：用户没问进度不要调。

        不要：开写或改稿；闲聊/「能干什么」/「现在怎么办」口头答；「刚才分析的那几章」走 select_write_chapters；看单章配置用 get_chapter_info。

        """
        from report_writing.chat import _writing_status
        from report_writing.chat.writing import writing_status_facts
        from report_writing.chat.helpers import _load_task

        _reply, cards = _writing_status(state.home, state.session)
        _set_cards(state, cards)
        task = _load_task(state.home, state.session.task_id) if state.session.task_id else None
        facts = writing_status_facts(task)
        return _dump({"ok": True, "reply_hint": "", "status_facts": facts})

    @tool("prepare_export_word", description="何时：用户要导出 Word。缺了：先对话定名，不要未定名就导出。")
    def prepare_export_word() -> str:
        """

        何时：用户要导出 Word。

        必填：已正式定名（writing_doc_named）。

        可选：无。

        缺了：先对话定名，不要未定名就导出。

        """
        from report_writing.chat.tool_capability import missing_for_tool
        from report_writing.chat.writing import _prepare_export_word

        miss = missing_for_tool(state.session, "prepare_export_word", home=state.home)
        if "doc_name" in miss:
            from report_writing.chat.clarify_flow import build_document_name_clarify

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

            )
        reply, cards = _prepare_export_word(state.home, state.session)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("pause_writing", description="何时：用户要暂停按章写作流水线。已确认章节保留，不再自动写下章。")
    def pause_writing() -> str:
        """

        何时：用户要暂停按章写作流水线。已确认章节保留，不再自动写下章。

        必填：无。

        可选：无。

        """
        from report_writing.chat.actions import pause_writing_session

        _session, cards, saved = pause_writing_session(state.home, state.session.id)
        _set_cards(state, cards)
        tip = "写作已暂停，不会再自动写下章。"
        if saved:
            tip += f" 已同步 {saved} 章到库。"
        return _dump({"ok": True, "reply_hint": tip})

    @tool("confirm_chapter", description="何时：用户确认本章草稿。缺了：问确认的是哪一章，或请在文档窗点确认，不要猜第一章。")
    def confirm_chapter(
        task_id: Annotated[str, Field(description="可选。默认可从会话取。")] = "",
        chapter_key: Annotated[str, Field(description="必填语义。确认哪一章；UI 点选确认本章时可空。不要猜第一章。")] = "",
        text: Annotated[str, Field(description="可选。覆盖草稿正文。")] = "",
        decision: Annotated[str, Field(description="可选。默认 approve。")] = "approve",
        comment: Annotated[str, Field(description="可选。确认评语。")] = "",
        pause_after: Annotated[bool, Field(description="可选。true=确认后停止、不要自动写下章。")] = False,
    ) -> str:
        """

        何时：用户确认本章草稿。

        必填：能确定是哪一章（chapter_key，或 UI 点选确认本章）。

        可选：task_id（默认可从会话取）；text；decision；comment；pause_after=true 表示确认后停止、不要自动写下章。

        缺了：问确认的是哪一章，或请在文档窗点确认，不要猜第一章。

        """
        from report_writing.chat import _action_confirm_chapter

        tid = (task_id or state.session.task_id or "").strip()
        ck = (chapter_key or "").strip()
        ui = (state.user_action or "").strip() == "confirm_chapter"
        if not ck and not ui:
            # 允许动作层用焦点章；完全无 task 则缺参
            if not tid and not (state.session.task_id or "").strip():
                return _obs_fail(
                    missing_slots=["chapter_key", "task_id"],
                    reply_hint="还没有可确认的章节，先开写或点文档窗里的章。",

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
            state.home, state.session, payload, emit=state.emit, model=state.chat_model
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,

            }
        )

    @tool("confirm_all_chapters", description="何时：用户明确要一次确认多章草稿。通常应逐章 confirm_chapter，本工具慎用。缺了：确认范围不清就改逐章确认，不要一次全批。")
    def confirm_all_chapters(
        chapters_json: Annotated[str, Field(description="必填语义。[{chapter_key,text},...]；范围不清则改逐章确认。")] = "[]",
        skip_empty: Annotated[bool, Field(description="可选。默认 true。")] = True,
        task_id: Annotated[str, Field(description="可选。可从会话取。")] = "",
    ) -> str:
        """

        何时：用户明确要一次确认多章草稿。通常应逐章 confirm_chapter，本工具慎用。

        必填：chapters_json=[{chapter_key,text},...] 或 UI 已给出要确认的章。

        可选：无。

        缺了：确认范围不清就改逐章确认，不要一次全批。

        """
        from report_writing.chat import _action_confirm_all_chapters

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

    @tool(
        "get_chapter_info",
        description=(
            "何时：用户要查看某章配置（要点、章节说明、写作策略、参考正文、关联要素、是否已写）。"
            "不要：用本工具改配置或开写；改配置用 configure_chapter；问整篇写到哪了用 get_writing_status。"
            "缺了：问看哪一章；用户说「这章/本章」且文档窗有焦点章时可用焦点章。"
        ),
    )
    def get_chapter_info(
        payload_json: Annotated[
            str,
            Field(description="JSON。必填 chapter_key；task_id 可从会话取。可选 message 用于从原话解析章节号。"),
        ] = "{}",
    ) -> str:
        from report_writing.chat.actions import _action_get_chapter_info
        from report_writing.chat.chapter_resolve import (
            pick_focus_chapter_key_extended,
            resolve_chapter_key_from_message,
        )
        from report_writing.chat.helpers import _load_task

        payload = ChatActionPayload.parse(payload_json).as_dict()
        ck = str(payload.get("chapter_key") or payload.get("chapter_key_hint") or "").strip()
        hint = str(payload.get("message") or state.last_user_message or "").strip()
        tid = str(payload.get("task_id") or state.session.task_id or "").strip()
        ui = (state.user_action or "").strip() == "get_chapter_info"
        task = _load_task(state.home, tid) if tid else None
        if task is not None and not ck:
            ck = resolve_chapter_key_from_message(task, hint) or ""
            if not ck:
                ck = pick_focus_chapter_key_extended(state.session, task, hint) or ""
        if not ck and not ui:
            return _obs_fail(
                missing_slots=["chapter_key"],
                reply_hint="查看章节配置要先说清是哪一章，也可以先在文档窗点开那一章。",
            )
        if not ck:
            return _obs_fail(missing_slots=["chapter_key"], reply_hint="查看章节配置要先说清是哪一章。")
        payload["chapter_key"] = ck
        if tid:
            payload["task_id"] = tid
        _tool_begin(state, "get_chapter_info")
        reply, cards = _action_get_chapter_info(state.home, state.session, payload)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool(
        "configure_chapter",
        description=(
            "何时：用户口语要求改某章配置时保存。可改：标题、序号、父级、仅目录、写作策略 compose"
            "（source_only/template_fill/hybrid/auto_generate/fixed）、要点 points 或 point_items、"
            "章节说明 describes、补充提示词 aux_prompt、参考正文 reference_text、关联要素 element_ids。"
            "缺了：问配置哪一章；只填用户明确说的字段，没有的不要编、不要清空未提到的项。"
            "不要：文档窗里保存配置（窗内静默落盘）；不要用本工具查看配置（用 get_chapter_info）；"
            "不要自动生成补充提示词。"
        ),
    )
    def configure_chapter(
        payload_json: Annotated[
            str,
            Field(
                description=(
                    "JSON。必填 chapter_key。可选 title/sequence/parent/structure_only/compose/presence/"
                    "points_text 或 point_items/describes/aux_prompt/reference_text/element_ids。"
                    "compose 口语：资料驱动、模板填槽、混合、自动生成、固定内容输出。"
                    "没有的不要编。"
                )
            ),
        ] = "{}",
    ) -> str:
        from report_writing.chat import _action_configure_chapter

        payload = ChatActionPayload.parse(payload_json).as_dict()
        ck = str(payload.get("chapter_key") or payload.get("chapter_key_hint") or "").strip()
        ui = (state.user_action or "").strip() == "configure_chapter"
        if not ck and not ui:
            return _obs_fail(
                missing_slots=["chapter_key"],
                reply_hint="配置章节要先说清是哪一章。",

            )
        payload["_via_agent_tool"] = True
        reply, cards = _action_configure_chapter(
            state.home, state.session, payload, model=state.chat_model
        )
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,

            }
        )

    @tool(
        "generate_chapter_describes",
        description=(
            "何时：用户要按本章写作要点生成「章节说明」（风格/格式/排版注意，分点 1. 2.）。"
            "不要：生成补充提示词；不要覆盖用户刚口述的说明，除非用户明确说重新生成。"
            "缺了：问哪一章；没有要点时请用户先配要点或直接口述说明。"
        ),
    )
    def generate_chapter_describes(
        payload_json: Annotated[
            str,
            Field(description="JSON。必填 chapter_key；task_id 可从会话取。"),
        ] = "{}",
    ) -> str:
        from report_writing.chat.actions import _action_generate_chapter_describes
        from report_writing.chat.chapter_resolve import (
            pick_focus_chapter_key_extended,
            resolve_chapter_key_from_message,
        )
        from report_writing.chat.helpers import _load_task

        payload = ChatActionPayload.parse(payload_json).as_dict()
        ck = str(payload.get("chapter_key") or payload.get("chapter_key_hint") or "").strip()
        hint = str(payload.get("message") or state.last_user_message or "").strip()
        tid = str(payload.get("task_id") or state.session.task_id or "").strip()
        ui = (state.user_action or "").strip() == "generate_chapter_describes"
        task = _load_task(state.home, tid) if tid else None
        if task is not None and not ck:
            ck = resolve_chapter_key_from_message(task, hint) or ""
            if not ck:
                ck = pick_focus_chapter_key_extended(state.session, task, hint) or ""
        if not ck and not ui:
            return _obs_fail(
                missing_slots=["chapter_key"],
                reply_hint="生成章节说明要先说清是哪一章。",
            )
        payload["chapter_key"] = ck
        if tid:
            payload["task_id"] = tid
        _tool_begin(state, "generate_chapter_describes")
        reply, cards = _action_generate_chapter_describes(
            state.home, state.session, payload, model=state.chat_model
        )
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool(
        "generate_chapter_boilerplate",
        description=(
            "何时：用户要把某章做成套话正文（固定话术里嵌 {{槽名}}），或根据蓝本/粘贴原文生成套话。"
            "缺了：问哪一章；该章没有参考正文且用户也没贴原文时，请用户粘贴一段或先分析本章。"
            "不要：与 generate_freeform_points（写作要点）或 verbatim 整段照抄混淆。"
        ),
    )
    def generate_chapter_boilerplate(
        chapter_key: Annotated[str, Field(description="可选。哪一章；可从当前焦点或用户说的序号取。")] = "",
        source_text: Annotated[str, Field(description="可选。用户粘贴的原文；空则用该章已有样板/参考正文。")] = "",
        task_id: Annotated[str, Field(description="可选。默认可从会话取。")] = "",
        message: Annotated[str, Field(description="可选。用户原话，用于抽出粘贴的原文。")] = "",
    ) -> str:
        from report_writing.chat.actions import _action_generate_chapter_boilerplate
        from report_writing.chat.chapter_resolve import (
            pick_focus_chapter_key_extended,
            resolve_chapter_key_from_message,
        )
        from report_writing.chat.helpers import _load_task

        hint = str(message or state.last_user_message or "").strip()
        tid = (task_id or state.session.task_id or "").strip()
        ck = (chapter_key or "").strip()
        ui = (state.user_action or "").strip() == "generate_chapter_boilerplate"
        task = _load_task(state.home, tid) if tid else None
        if task is not None and not ck:
            ck = resolve_chapter_key_from_message(task, hint) or ""
            if not ck:
                ck = pick_focus_chapter_key_extended(state.session, task, hint) or ""
        if not ck and not ui:
            return _obs_fail(
                missing_slots=["chapter_key"],
                reply_hint="生成套话要先说清是哪一章，也可以先在文档窗点开那一章。",
            )
        if not ck:
            return _obs_fail(
                missing_slots=["chapter_key"],
                reply_hint="生成套话要先说清是哪一章。",
            )
        _tool_begin(state, "generate_chapter_boilerplate")
        try:
            reply, cards = _action_generate_chapter_boilerplate(
                state.home,
                state.session,
                {
                    "task_id": tid,
                    "chapter_key": ck,
                    "source_text": source_text,
                    "message": hint,
                },
                model=state.chat_model,
            )
        except ValueError as error:
            _tool_done(state, "generate_chapter_boilerplate")
            return _obs_fail(reply_hint=str(error) or "生成套话还缺原文或章节。")
        _tool_done(state, "generate_chapter_boilerplate")
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("add_chapter_element", description="何时：用户在对话里说要给某章加要素（键值/固定文案/文件/引用槽等）。缺了：从原话拆 kind/名称/哪一章；拆不出就问。不要：文档窗里配要素（窗内直接配）；不要与 collect_to_element_library 混淆。")
    def add_chapter_element(payload_json: Annotated[str, Field(description="必填 JSON。至少 kind、name；chapter_key 或当前焦点章。从原话填全，拆不出就问，不要编章。")]) -> str:
        """

        何时：用户在对话里说要给某章加要素（键值、固定文案、文件、引用槽位等）。

        必填：payload_json，至少含 kind、name；能确定 chapter_key（或当前焦点章）。

        可选：query 等；文件要素 kind=file。

        缺了：从用户原话拆 kind/名称/哪一章；拆不出就问，不要留空让用户手填、不要编造章。

        不要：文档窗里配/关联要素（窗内直接操作，不走对话）；不要与 collect_to_element_library（收入个人库）混淆。

        """
        from report_writing.chat import _action_add_chapter_element

        payload = ChatActionPayload.parse(payload_json).as_dict()
        hint = str(state.last_user_message or payload.get("hint") or payload.get("user_note") or "").strip()
        if hint and (
            not str(payload.get("chapter_key") or "").strip()
            or not str(payload.get("name") or "").strip()
            or (str(payload.get("kind") or "kv") == "kv" and not payload.get("kv_pairs"))
        ):
            from report_writing.chat.dialogue import format_full_dialogue
            from report_writing.chat.chapter_resolve import (
                pick_focus_chapter_key_extended,
                resolve_chapter_key_from_message,
            )
            from report_writing.chat.clarify_llm import infer_element_payload_llm
            from report_writing.chat.helpers import _load_task

            clarify_invoke = _state_clarify_invoke(state)
            inferred = infer_element_payload_llm(
                hint,
                dialogue_tail=format_full_dialogue(state.session, max_turns=8),
                invoke=clarify_invoke,
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

            )
        reply, cards = _action_add_chapter_element(state.home, state.session, payload)
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,

            }
        )

    @tool("associate_elements", description="何时：用户口语要求把已有要素关联到某章（打开关联面板）。缺了：问关联到哪一章。不要：文档窗点「关联要素」（窗内直接弹面板）；不要用本工具新建要素。")
    def associate_elements(payload_json: Annotated[str, Field(description="JSON。须能确定 chapter_key（或焦点章）；可选 element_id/name。缺了就问。")] = "") -> str:
        """

        何时：用户口语要求把已有要素关联到某章（打开关联面板）。

        必填：payload_json 能确定 chapter_key（或焦点章）。

        可选：element_id 或 name。

        缺了：问关联到哪一章、哪个要素，不要猜。

        不要：文档窗点「关联要素」（窗内直接弹面板，不走对话）；不要用本工具新建要素。

        """
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        payload = ChatActionPayload.parse(payload_json).as_dict()
        hint = str(state.last_user_message or payload.get("hint") or "").strip()
        if not str(payload.get("chapter_key") or payload.get("chapter_key_hint") or "").strip():
            # 允许分类器/动作层从 hint 解析；完全空则缺参
            if not hint and not (state.session.task_id or "").strip():
                return _obs_fail(
                    missing_slots=["chapter_key"],
                    reply_hint="关联要素要先选定章节。",

                )
        result = apply_writing_control_classification(
            state.home,
            state.session,
            hint,
            {"tool": "associate_elements", "payload": payload},
            emit=state.emit,
            expected_tool="associate_elements",
        )
        return _dump_writing_control_result(
            state,
            result,
            expected_tool="associate_elements",

            fail_hint="无法打开关联要素，请先选定章节。",
            fail_slots=["chapter_key"],
        )

    @tool("dissociate_chapter_element", description="何时：用户口语要求取消某章与要素的关联。缺了：同名多个先澄清；缺章或要素名则问。不要：文档窗取消勾选（窗内直接保存）；不要删除要素节点或个人要素库。")
    def dissociate_chapter_element(payload_json: Annotated[str, Field(description="JSON。必填 chapter_key，以及 element_id 或 element_name。同名多个先澄清。")] = "") -> str:
        """

        何时：用户口语要求取消某章与要素的关联。

        必填：chapter_key；element_id 或 element_name 之一。

        可选：payload_json 打包上述字段。

        缺了：同名多个先澄清；缺章或要素名则问，不要乱取消。

        不要：文档窗取消勾选（窗内直接保存）；不要删除要素节点或个人要素库。

        """
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        payload = ChatActionPayload.parse(payload_json).as_dict()
        hint = str(state.last_user_message or payload.get("hint") or "").strip()
        result = apply_writing_control_classification(
            state.home,
            state.session,
            hint,
            {"tool": "dissociate_chapter_element", "payload": payload},
            emit=state.emit,
            expected_tool="dissociate_chapter_element",
        )
        return _dump_writing_control_result(
            state,
            result,
            expected_tool="dissociate_chapter_element",
            fail_hint="请说明要取消哪一章、哪个要素的关联。",
        )

    @tool("remove_document_element", description="何时：用户口语要求从当前写作文档删除要素（含各章关联）。缺了：问删哪一个，破坏性动作没把握先澄清。不要：文档窗里删（窗内直接操作）；不要删除个人要素库条目。")
    def remove_document_element(payload_json: Annotated[str, Field(description="JSON。须能确定哪个要素（id 或名称）。破坏性动作没把握先问。")] = "") -> str:
        """

        何时：用户口语要求从当前写作文档删除要素（含各章关联）。

        必填：能确定哪个要素（id 或名称）。

        可选：payload_json。

        缺了：问删哪一个，破坏性动作没把握先澄清。

        不要：文档窗里删（窗内直接操作）；不要删除个人要素库条目。

        """
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        payload = ChatActionPayload.parse(payload_json).as_dict()
        hint = str(state.last_user_message or payload.get("hint") or "").strip()
        result = apply_writing_control_classification(
            state.home,
            state.session,
            hint,
            {"tool": "remove_document_element", "payload": payload},
            emit=state.emit,
            expected_tool="remove_document_element",
        )
        return _dump_writing_control_result(
            state,
            result,
            expected_tool="remove_document_element",
            fail_hint="请说明要删哪个要素。",
        )

    @tool("collect_to_element_library", description="何时：把文档内已有要素收入个人要素库。缺了：问收入哪一条。不要：新建章节要素；不要只打开要素库浏览。")
    def collect_to_element_library(payload_json: Annotated[str, Field(description="JSON。须能确定收入哪一条要素。")] = "") -> str:
        """

        何时：把文档内已有要素收入个人要素库。

        必填：能确定哪个要素。

        可选：payload_json。

        缺了：问收入哪一条。

        不要：新建章节要素；不要只打开要素库浏览。

        """
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        payload = ChatActionPayload.parse(payload_json).as_dict()
        hint = str(state.last_user_message or payload.get("hint") or "").strip()
        result = apply_writing_control_classification(
            state.home,
            state.session,
            hint,
            {"tool": "collect_to_element_library", "payload": payload},
            emit=state.emit,
            expected_tool="collect_to_element_library",
        )
        return _dump_writing_control_result(
            state,
            result,
            expected_tool="collect_to_element_library",

            fail_hint="无法收入要素库，请说明要收入哪个要素。",
        )

    @tool("associate_element_knowledge", description="已下线。不要调用。请引导用户用资料包或「文件」要素上传并手动解析。")
    def associate_element_knowledge(payload_json: Annotated[str, Field(description="已下线。不要调用。")] = "") -> str:
        """

        已下线。不要调用。请引导用户用资料包或「文件」要素上传并手动解析。

        必填：不要调。

        可选：无。

        """
        return _dump(
            {
                "ok": False,
                "reply_hint": "知识库已下线。请把要素类型设为「文件」，上传文件后点击「解析」，解析完成后写作才能读取内容。",
            }
        )

    @tool("open_element_library", description="何时：用户要查看/打开要素库浏览页。不要：当成给章节加要素。")
    def open_element_library() -> str:
        """

        何时：用户要查看/打开要素库浏览页。

        必填：无。

        可选：无。

        不要：当成给章节加要素。

        """
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        result = apply_writing_control_classification(
            state.home,
            state.session,
            "查看要素",
            {"tool": "open_element_library", "payload": {}},
            emit=state.emit,
            expected_tool="open_element_library",
        )
        return _dump_writing_control_result(
            state,
            result,
            expected_tool="open_element_library",

            fail_hint="无法打开要素库。",
        )

    @tool("open_blueprint_library", description="何时：用户要打开蓝本库管理页（维护库，不是对话里选型）。不要：当成 match_blueprints 选型、list_blueprints 浏览后选用、或 select_blueprint。")
    def open_blueprint_library() -> str:
        """

        何时：用户要打开蓝本库管理页（维护库，不是对话里选型）。

        必填：无。

        可选：无。

        不要：当成 match_blueprints 选型、list_blueprints 浏览后选用、或 select_blueprint。

        """
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        result = apply_writing_control_classification(
            state.home,
            state.session,
            "查看蓝本库",
            {"tool": "open_blueprint_library", "payload": {}},
            emit=state.emit,
            expected_tool="open_blueprint_library",
        )
        return _dump_writing_control_result(
            state,
            result,
            expected_tool="open_blueprint_library",

            fail_hint="无法打开蓝本库。",
        )

    @tool("open_knowledge", description="何时：用户要打开资料库（放资料包的地方；兼容旧称「知识库」）。")
    def open_knowledge() -> str:
        """

        何时：用户要打开资料库（放资料包的地方；兼容旧称「知识库」）。

        必填：无。

        可选：无。

        """
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        result = apply_writing_control_classification(
            state.home,
            state.session,
            "查看资料库",
            {"tool": "open_knowledge", "payload": {}},
            emit=state.emit,
            expected_tool="open_knowledge",
        )
        return _dump_writing_control_result(
            state,
            result,
            expected_tool="open_knowledge",

            fail_hint="资料库是放资料包的地方。请打开侧栏「资料库」管理资料包；要素「文件」可上传并手动解析。",
        )

    @tool("retry_chapter", description="何时：按最新配置重试某章（重新检索+写+检）。用户说「重写章节[序号 标题]」或点重写按钮时用。缺了：问重试哪一章。")
    def retry_chapter(payload_json: Annotated[str, Field(description="必填 JSON。至少 chapter_key；task_id 可从会话取。可选 aux_prompt/user_turn 改写要求。")]) -> str:
        """

        何时：按最新配置重试某章（重新检索+写+检）。

        必填：payload_json 至少含 chapter_key；task_id 默认可从会话取。

        可选：aux_prompt / user_turn=改写要求。

        缺了：问重试哪一章。

        """
        from report_writing.chat import _action_retry_chapter
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
            return _obs_fail(
                missing_slots=["writing_path"],
                reply_hint="请先选择写作方式（有蓝本 / 无蓝本），再重试章节。",



                task_id=state.session.task_id or "",
            )
        payload = ChatActionPayload.parse(payload_json).as_dict()
        if not payload.get("already_persisted") and not payload.get("from_doc_window"):
            payload["_via_agent_tool"] = True
        reply, cards, events = _action_retry_chapter(
            state.home,
            state.session,
            payload,
            emit=state.emit,
            invoke=state.invoke,
            model=state.chat_model,
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        wait = _obs_if_supplement_gate(reply, cards)
        if wait:
            return wait
        return _dump({"ok": True, "reply_hint": reply})

    @tool("retry_search_chapter", description="何时：只重试某章检索与证据采纳，不写正文（方便改关键词后再写）。缺了：问重试检索的是哪一章。")
    def retry_search_chapter(payload_json: Annotated[str, Field(description="必填 JSON。含章节 key。缺了问重试哪一章。")]) -> str:
        """

        何时：只重试某章检索与证据采纳，不写正文（方便改关键词后再写）。

        必填：payload_json 含章节 key。

        可选：无。

        缺了：问重试检索的是哪一章。

        """
        from report_writing.chat import _action_retry_search_chapter

        if (state.user_action or "").strip() not in {"", "retry_search_chapter"}:
            return _dump(
                {
                    "ok": False,
                    "reply_hint": "仅检索需点「仅重试检索」按钮，不要与「保存并重试本章」混用。",
                }
            )
        payload = ChatActionPayload.parse(payload_json).as_dict()
        if not payload.get("already_persisted") and not payload.get("from_doc_window"):
            payload["_via_agent_tool"] = True
        reply, cards, events = _action_retry_search_chapter(
            state.home, state.session, payload, emit=state.emit, model=state.chat_model
        )
        state.events.extend(events or [])
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("set_session_meta", description="何时：用户明确起名或改名（「名字叫 / 改名叫 X」）。缺了：继续问正式名称，不要把整句「改个名字…」或写题材、寒暄当 title。不要：调完后还挂「先起个名字」卡。说明：「写一份××」是工作名推进，不必先调本工具定名。")
    def set_session_meta(message: Annotated[str, Field(description="可选。用户原话。")] = "", category: Annotated[str, Field(description="可选。文档类型。")] = "", title: Annotated[str, Field(description="必填。从原话拆出的短文档名。不要把整句口语或题材当 title。")] = "") -> str:
        """

        何时：用户明确起名或改名（「名字叫 / 改名叫 X」）。

        必填：title=从原话拆出的短文档名。

        可选：message=原话；category。

        缺了：继续问正式名称，不要把整句「改个名字…」或写题材、寒暄当 title。

        不要：调完后还挂「先起个名字」卡。

        说明：「写一份××」是工作名推进，不必先调本工具定名。

        """
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

                }
            )
        cat = (category or "").strip()
        tip = (title or "").strip()
        has_pipe = bool(msg) and ("｜" in msg or "|" in msg)
        from report_writing.chat.session_documents import message_requires_structured_rename_title

        if not (title or "").strip() and msg and message_requires_structured_rename_title(msg):
            return _obs_fail(missing_slots=["title"])
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
            from report_writing.chat.clarify_flow import (
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
                return _obs_fail(missing_slots=["document_scope"])
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
                return _obs_fail(missing_slots=["title"])
            tip = msg
        if title_rejects_as_document_name(tip, message=msg):
            return _obs_fail(missing_slots=["title"])
        reply, cards = run_session_goal_action(
            state.home,
            state.session,
            "set_session_meta",
            {"message": msg, "category": cat, "title": tip},
            invoke=_state_clarify_invoke(state) or state.invoke,
            emit=state.emit,
            model=state.clarify_model,
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
        still_needs_name = False
        # 定名成功且缺入口：必挂选路卡（不单靠 write_like 关键字）
        if tip:
            from report_writing.chat.clarify_flow import (
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
        from report_writing.chat.freeform_intent import utterance_mentions_generate_outline
        from report_writing.chat.session_state import user_chose_skip_blueprint
        from report_writing.chat.session_documents import focus_onboarding as _focus_ob

        ob_meta = _focus_ob(state.session)
        auto_outline = (
            str(ob_meta.get("freeform_mode") or "").strip() == "auto_outline"
            or utterance_mentions_generate_outline(user_msg)
        )
        if (
            tip
            and user_chose_skip_blueprint(state.session)
            and auto_outline
            and not awaiting_path
        ):
            if not any(
                isinstance(t, dict) and str(t.get("tool") or "") == "submit_outline"
                for t in suggested
            ):
                suggested = list(suggested) + [
                    {
                        "tool": "submit_outline",
                        "args": {"regenerate": True, "message": user_msg[:500]},
                    }
                ]
            next_hop = (
                f"{next_hop} suggested_next_tools={suggested}，"
                "无蓝本自动大纲：同轮 submit_outline(regenerate=true)。"
            )
        return _dump(
            {
                "ok": True,
                "reply_hint": "" if still_needs_name else reply,
                "title": tip,
                "category": cat,
                "brief": brief[:80],
                "onboarding": gaps,
                "suggested_next_tools": suggested,
                "needs_doc_name": bool(still_needs_name),
                "missing_slots": ["title"] if still_needs_name else None,
                "needs_entry_path": bool(needs_entry or write_like and awaiting_path),
                "awaiting_path_choice": awaiting_path,
            }
        )

    @tool("submit_freeform_meta", description="何时：无蓝本路径下用户明确起名。与 set_session_meta 同实现，不要两个都调。已绑蓝本改名用 set_session_meta。缺了：继续问名称，不要把整句口语当标题。")
    def submit_freeform_meta(message: Annotated[str, Field(description="可选。原话。")] = "", category: Annotated[str, Field(description="可选。")] = "", title: Annotated[str, Field(description="必填。短文档名。不要把整句口语当标题。")] = "") -> str:
        """

        无蓝本路径定名，语义同 set_session_meta。

        必填：title=短文档名。

        可选：message、category。

        缺了：继续问名称，不要把整句口语当标题。

        """
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

    @tool("trim_outline_top_chapters", description="何时：用户只要前 N 个顶层章。缺了：问保留前几章，不要猜。注意：裁完通常再确认目录，不要直接开写。")
    def trim_outline_top_chapters(keep_count: Annotated[int, Field(description="必填。保留前 N 个顶层章。缺了问用户，不要猜。")]) -> str:
        """

        何时：用户只要前 N 个顶层章。

        必填：keep_count=N（由对话判断）。

        可选：无。

        缺了：问保留前几章，不要猜。

        注意：裁完通常再确认目录，不要直接开写。

        """
        from report_writing.chat.freeform_outline import chapters_to_outline_text, trim_outline_directory_text
        from report_writing.chat.helpers import _load_task
        from report_writing.chat import _collect_freeform_outline

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

    @tool("submit_outline", description="何时：无蓝本路径下提交、确认或再生成章节目录。缺了：问用户是粘贴目录、自动生成、还是确认当前目录；不要把「确认/生成」话术当 outline_text。不要：用本工具开模版向导或写正文。")
    def submit_outline(
        outline_text: Annotated[str, Field(description="与 confirm/regenerate 三选一。粘贴的目录正文；不要把「确认/生成」话术填进来。")] = "",
        confirm: Annotated[bool, Field(description="与 outline_text/regenerate 三选一。口语确认当前目录时 true。")] = False,
        regenerate: Annotated[bool, Field(description="与 outline_text/confirm 三选一。自动生成目录时 true。")] = False,
        reopen_edit: Annotated[bool, Field(description="可选。重开目录编辑。")] = False,
        revision_instructions: Annotated[str, Field(description="可选。自动生成时写入章数等要求。")] = "",
        chapter_keys_json: Annotated[str, Field(description="可选。确认时仅给这些章生要点。")] = "[]",
    ) -> str:
        """

        何时：无蓝本路径下提交、确认或再生成章节目录。

        必填：三选一——outline_text=粘贴目录；或 regenerate=true；或 confirm=true（口语确认）。

        可选：revision_instructions（自动生成时写入章数等要求）；reopen_edit；chapter_keys_json（确认时仅给勾选章生要点）。

        缺了：问用户是粘贴目录、自动生成、还是确认当前目录；不要把「确认/生成」话术当 outline_text。

        不要：用本工具开模版向导或写正文。

        """
        from report_writing.chat.actions import _action_submit_outline
        from report_writing.chat.freeform_intent import resolve_outline_mode_flags
        from report_writing.chat.freeform_outline import looks_like_outline_directory

        oral_probe = (revision_instructions or state.last_user_message or outline_text or "").strip()
        if not looks_like_outline_directory(oral_probe):
            confirm, regenerate = resolve_outline_mode_flags(
                oral_probe,
                confirm=confirm,
                regenerate=regenerate,
                session=state.session,
                invoke=_state_clarify_invoke(state) or state.invoke,
            )

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
        from report_writing.chat.session_documents import (
            focus_has_display_title,
            is_writing_document_named,
        )

        named = is_writing_document_named(state.session) or focus_has_display_title(
            state.session
        )
        waiting_name = str(state.session.phase or "") == ChatPhase.FREEFORM_ASK.value and not named
        clarify_ask = any(
            str(getattr(card, "type", "") or "") == "intent_clarify" for card in cards
        )
        handoff = waiting_name or clarify_ask
        return _dump(
            {
                "ok": True,
                "reply_hint": "" if handoff else reply,
                "task_id": state.session.task_id,
                "needs_doc_name": waiting_name,
                "missing_slots": (
                    ["title"] if waiting_name else (["outline"] if clarify_ask else None)
                ),
            }
        )

    @tool("submit_points", description="何时：用户在补充各章「怎么写」的要点长文（明确在谈写法/结构）。缺了：请用户把要点写出来，不要空调。不要：与 generate_freeform_points（自动生成）混淆；加要素、关联、记背景不要用本工具。")
    def submit_points(message: Annotated[str, Field(description="必填。要点正文或用户刚发的长文。空则先请用户写出来。")] = "") -> str:
        """

        何时：用户在补充各章「怎么写」的要点长文（明确在谈写法/结构）。

        必填：message=要点正文（或用户刚发的长文）。

        可选：无。

        缺了：请用户把要点写出来，不要空调。

        不要：与 generate_freeform_points（自动生成）混淆；加要素、关联、记背景不要用本工具。

        """
        from report_writing.chat import _collect_freeform_points

        msg = message or state.last_user_message or ""
        clarify_invoke = _state_clarify_invoke(state)
        from report_writing.chat.freeform_intent import suggest_tool_instead_of_submit_points_freeform

        freeform_hit = suggest_tool_instead_of_submit_points_freeform(
            msg, session=state.session, invoke=clarify_invoke
        )
        if freeform_hit is not None:
            nxt = str(freeform_hit.get("tool") or "").strip()
            args = dict(freeform_hit.get("args") or {})
            return _obs_fail(
                missing_slots=["points_text"],
                reply_hint="这句更像目录/要点操作，我按对应动作继续。",



            )
        if not str(state.session.task_id or "").strip():
            return _obs_fail(
                missing_slots=["outline"],
                reply_hint="还没有目录，先生成或确认大纲再补要点。",



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

            }
        )

    @tool("generate_freeform_points", description="何时：无蓝本任务、目录已确认，用户同意为各章生成写作要点。开写前缺要点时先问再调。缺了：没目录先走目录；没说哪几章则按当前可写章，不要擅自生成目录。不要：用户说写某一章（用 select_write_chapters）；已绑蓝本分析用 re_study_chapters；用户手写要点用 submit_points；不要把加要素/记背景当成生要点。")
    def generate_freeform_points(
        task_id: Annotated[str, Field(description="可选。默认可从会话取；没有目录任务则先走目录。")] = "",
        chapter_keys_json: Annotated[str, Field(description="可选。非空则只生成指定章。")] = "[]",
        focus_chapter_key: Annotated[str, Field(description="可选。定位用的章。")] = "",
    ) -> str:
        """

        何时：无蓝本任务、目录已确认，用户同意为各章生成写作要点。开写前缺要点时先问再调。

        必填：当前任务已有目录（task_id 默认可从会话取）。

        可选：chapter_keys_json 非空则只生成指定章；focus_chapter_key。

        缺了：没目录先走目录；没说哪几章则按当前可写章，不要擅自生成目录。

        不要：用户说写某一章（用 select_write_chapters）；已绑蓝本分析用 re_study_chapters；用户手写要点用 submit_points；不要把加要素/记背景当成生要点。

        """
        from report_writing.chat.actions import _action_generate_freeform_points
        from report_writing.chat.write_chapter_intent import reroute_if_write_not_study

        write_hit = reroute_if_write_not_study(
            state.home,
            state.session,
            str(state.last_user_message or ""),
            emit=state.emit,
            invoke=state.invoke,
        )
        if write_hit is not None:
            reply, cards, events = write_hit
            state.events.extend(events or [])
            _set_cards(state, cards)
            return _dump({"ok": True, "reply_hint": reply})

        tid = (task_id or state.session.task_id or "").strip()
        ff = dict(state.session.freeform or {})
        if not tid and not ff.get("outline_confirmed") and not ff.get("outline_pending_edit"):
            return _obs_fail(
                missing_slots=["task_id"],
                reply_hint="还没有目录任务，先生成或确认大纲再配要点。",

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
            model=state.chat_model,
        )
        _tool_done(state, "generate_freeform_points")
        _set_cards(state, cards)
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,

            }
        )

    @tool("skip_freeform_points", description="何时：用户明确跳过无蓝本要点生成，直接按已有目录开写。")
    def skip_freeform_points(task_id: Annotated[str, Field(description="可选。可从会话取。")] = "") -> str:
        """

        何时：用户明确跳过无蓝本要点生成，直接按已有目录开写。

        必填：无。

        可选：task_id。

        """
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

            }
        )

    @tool("resource_check", description="何时：需要检查导出前资源是否就绪（渲染模版、样式），并给出配置卡。不要：开写阶段强推。")
    def resource_check() -> str:
        """

        何时：需要检查导出前资源是否就绪（渲染模版、样式），并给出配置卡。

        必填：无。

        可选：无。

        不要：开写阶段强推。

        """
        from report_writing.chat.resource_readiness import _action_resource_check

        _tool_begin(state, "resource_check")
        reply, cards = _action_resource_check(state.home, state.session)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("resource_choice", description="何时：用户点了导出前资源（渲染模版 / Word 样式）二选一卡片。缺了：请用户点卡片，不要代选。")
    def resource_choice(resource: Annotated[str, Field(description="必填语义。卡片选项（渲染模版/样式）。请用户点选，不要代选。")] = "", accept: Annotated[bool, Field(description="可选。是否接受该资源。")] = False) -> str:
        """

        何时：用户点了导出前资源（渲染模版 / Word 样式）二选一卡片。

        必填：选择结果按卡片 payload 原样传入（resource / accept）。

        可选：无。

        缺了：请用户点卡片，不要代选。

        """
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

    @tool("blueprint_wizard", description="何时：驱动当前文档已开启的蓝本/模版向导一步（上传/目录/学习）。缺了：向导未开始应 begin_blueprint_wizard；步参数不够则按向导卡继续问，不要空跑。不要：向导未开始时用本工具代替 begin；指定章分析用 re_study_chapters；用户说写某一章用 select_write_chapters；收录用 save_freeform_as_blueprint。仅改 w* 副本，不入库。")
    def blueprint_wizard(payload_json: Annotated[str, Field(description="JSON。向导已开始时含 step 或 wizard_command。未开始应 begin_blueprint_wizard。")] = "{}") -> str:
        """

        何时：驱动当前文档已开启的蓝本/模版向导一步（上传/目录/学习）。

        必填：向导已开始；payload_json 含 step 或 wizard_command。

        可选：与该步相关的章节 key、study_method 等。

        缺了：向导未开始应 begin_blueprint_wizard；步参数不够则按向导卡继续问，不要空跑。

        不要：向导未开始时用本工具代替 begin；指定章分析用 re_study_chapters；用户说写某一章用 select_write_chapters；收录用 save_freeform_as_blueprint。仅改 w* 副本，不入库。

        """
        from report_writing.chat.session_state import active_blueprint_wizard
        from report_writing.chat.resource_actions import _action_blueprint_wizard, _continue_active_wizard_reply
        from report_writing.chat.wizard_loop_guard import normalized_wizard_step
        from report_writing.chat.wizard_state import wizard_step_index

        payload = ChatActionPayload.parse(payload_json).as_dict()
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
            try:
                reply, cards = _action_blueprint_wizard(state.home, state.session, payload)
            except ValueError as error:
                return _dump({"ok": False, "reply_hint": str(error)})
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
        try:
            reply, cards = _action_blueprint_wizard(state.home, state.session, payload)
        except ValueError as error:
            return _dump({"ok": False, "reply_hint": str(error)})
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("template_wizard", description="何时：渲染模版 / Word 样式配置向导（与 blueprint_wizard 不同：这是导出版式，不是蓝本样板）。缺了：打开向导卡让用户选，不要猜模版 id。")
    def template_wizard(payload_json: Annotated[str, Field(description="可选 JSON。向导步；不要猜模版 id。")] = "{}") -> str:
        """

        何时：渲染模版 / Word 样式配置向导（与 blueprint_wizard 不同：这是导出版式，不是蓝本样板）。

        必填：无。

        可选：payload_json=向导步。

        缺了：打开向导卡让用户选，不要猜模版 id。

        """
        from report_writing.chat.resource_actions import _action_template_wizard

        payload = ChatActionPayload.parse(payload_json).as_dict()
        _tool_begin(state, "template_wizard")
        reply, cards = _action_template_wizard(state.session, payload)
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("save_freeform_as_blueprint", description="何时：把当前无蓝本写作任务（目录与已配要点）收录进蓝本库。缺了：先问正式名称再收录。不要：与 begin_blueprint_wizard（依据模版编写）混淆。")
    def save_freeform_as_blueprint(
        task_id: Annotated[str, Field(description="可选。可从会话取。")] = "",
        dismiss: Annotated[bool, Field(description="可选。关掉收录卡。")] = False,
        title: Annotated[str, Field(description="可选。收录名；未正式定名应先问。")] = "",
        category: Annotated[str, Field(description="可选。")] = "",
        tags_json: Annotated[str, Field(description="可选。标签 JSON 数组。")] = "[]",
    ) -> str:
        """

        何时：把当前无蓝本写作任务（目录与已配要点）收录进蓝本库。

        必填：已正式定名。

        可选：task_id、title、category、tags_json、dismiss。

        缺了：先问正式名称再收录。

        不要：与 begin_blueprint_wizard（依据模版编写）混淆。

        """
        from report_writing.chat.actions import _action_save_freeform_as_blueprint
        from report_writing.chat.tool_capability import missing_for_tool

        miss = missing_for_tool(
            state.session, "save_freeform_as_blueprint", home=state.home
        )
        if "doc_name" in miss and not (title or "").strip():
            from report_writing.chat.clarify_flow import build_document_name_clarify

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

    @tool("sync_document_to_blueprint_library", description="何时：已收录蓝本后，把当前 w* 副本的目录/要点/要素同步回库内蓝本（须用户明确要同步）。缺了：未收录则不能同步，先说明；没把握先澄清。")
    def sync_document_to_blueprint_library(task_id: Annotated[str, Field(description="可选。默认可从当前副本取。未收录不能同步。")] = "") -> str:
        """

        何时：已收录蓝本后，把当前 w* 副本的目录/要点/要素同步回库内蓝本（须用户明确要同步）。

        必填：task 已绑定库蓝本。

        可选：task_id（默认可从当前副本取）。

        缺了：未收录则不能同步，先说明；没把握先澄清。

        """
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

    @tool("remember_writing_goal", description="何时：用户在谈岗位、项目、写作约束，需要后续各章遵守。缺了：请用户再说一句要记住的约束，不要空调。不要：寒暄时调；已定名但仍缺写作入口时不要只调本工具回「记下了」（应 ask_entry_path）。")
    def remember_writing_goal(notes: Annotated[str, Field(description="必填。约束摘要。")] = "") -> str:
        """

        何时：用户在谈岗位、项目、写作约束，需要后续各章遵守。

        必填：notes=摘要。

        可选：无。

        缺了：请用户再说一句要记住的约束，不要空调。

        不要：寒暄时调；已定名但仍缺写作入口时不要只调本工具回「记下了」（应 ask_entry_path）。

        """
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
            invoke=_state_clarify_invoke(state) or state.invoke,
            emit=state.emit,
            model=state.clarify_model,
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

                "needs_entry_path": False,
            }
        )

    @tool("resolve_intent_clarify", description="何时：用户点选了澄清卡，或用原话回答当前 pending 选项。缺了：请用户点卡片选项或用原话回答，不要猜 option_id。作用：只落槽、改 session 标志。不要：重新分类整句口语。入口卡落槽后读 suggested_next_tools，同轮再调 skip_blueprint / submit_outline / match_blueprints / begin_blueprint_wizard，禁止假定本工具已代跑那些步骤。")
    def resolve_intent_clarify(
        option_id: Annotated[str, Field(description="必填。澄清卡选项 id。不要猜。")] = "",
        option_label: Annotated[str, Field(description="可选。选项展示文案。")] = "",
        clarify_id: Annotated[str, Field(description="可选。澄清卡 id。")] = "",
        message: Annotated[str, Field(description="可选。用户原话回答。")] = "",
    ) -> str:
        """

        何时：用户点选了澄清卡，或用原话回答当前 pending 选项。

        必填：option_id。

        可选：option_label / message。

        缺了：请用户点卡片选项或用原话回答，不要猜 option_id。

        作用：只落槽、改 session 标志。

        不要：重新分类整句口语。入口卡落槽后读 suggested_next_tools，同轮再调 skip_blueprint / submit_outline / match_blueprints / begin_blueprint_wizard，禁止假定本工具已代跑那些步骤。

        """
        del clarify_id
        _tool_begin(state, "resolve_intent_clarify")
        payload = {
            "option_id": (option_id or "").strip(),
            "option_label": (option_label or "").strip(),
        }
        reply, cards, events = apply_resolve_intent_clarify(
            home=state.home,
            session=state.session,
            invoke=_state_clarify_invoke(state) or state.invoke,
            emit=state.emit,
            payload=payload,
            message=(message or option_label or state.last_user_message or "").strip(),
        )
        state.cards.extend(cards or [])
        state.events.extend(events or [])
        pending_src = str(
            ((state.session.freeform or {}).get("intent_clarify") or {}).get("source") or ""
        )
        suggested = list((state.session.freeform or {}).get("suggested_next_tools") or [])
        route_parts = [
            "已按澄清点选落槽（未在 clarify 内链式 skip/大纲/match/wizard）。",
            f"pending_clarify={pending_src or 'none'}",
        ]
        if suggested:
            route_parts.append(f"suggested_next_tools={suggested}；同轮按序调用这些 Tool。")
        else:
            route_parts.append("读 cards / reply_hint；有下一张澄清卡则等用户点选。")
        return _dump(
            {
                "ok": True,
                "reply_hint": reply,
                "routing_hint": " ".join(route_parts),
                "pending_clarify": pending_src or None,
                "suggested_next_tools": suggested,
                "awaiting_path_choice": pending_src
                in {
                    "doc_name",
                    "entry",
                    "entry_branch",
                    "entry_freeform_mode",
                },
            }
        )

    @tool("ask_intent", description="何时：没把握——指代不明、多文档歧义、写还是学未定、破坏性动作；或必填参数从对话里拆不出来。缺了：本工具就是继续对话/挂澄清卡收集参数。不要：已明确写某一章时用本工具（用 select_write_chapters）；已明确分析某一章时用 re_study_chapters；用户已说另开/再写一份并起名时追问；替总控静默选其它工具、猜第一章、代选蓝本、静默开写/删除/收录。")
    def ask_intent(message: Annotated[str, Field(description="可选。用户原话。")] = "", question: Annotated[str, Field(description="可选。要问清的那一项。")] = "") -> str:
        """

        何时：没把握——指代不明、多文档歧义、写还是学未定、破坏性动作；或必填参数从对话里拆不出来。

        必填：无。

        可选：message / question=要问的那一项。

        缺了：本工具就是继续对话/挂澄清卡收集参数。

        不要：已明确写某一章时用本工具（用 select_write_chapters）；已明确分析某一章时用 re_study_chapters；用户已说另开/再写一份并起名时追问；替总控静默选其它工具、猜第一章、代选蓝本、静默开写/删除/收录。

        """
        args: dict[str, Any] = {}
        if (message or "").strip():
            args["message"] = message.strip()
        if (question or "").strip():
            args["question"] = question.strip()
        if (message or "").strip():
            state.last_user_message = message.strip()
        return _run_dispatch_switch(state, "ask_intent", args)

    @tool("focus_document", description="何时：用户说「切换到/切到/换成」会话里已有的另一份文档。必须调用，不要只口头答应。缺了：标题对不上就挂澄清卡列出会话文档，不要猜。不要：新开一份时用本工具切回旧文档。")
    def focus_document(document_title: Annotated[str, Field(description="必填语义。点名标题。对不上就挂澄清卡，不要猜。")] = "", message: Annotated[str, Field(description="可选。用户原话。")] = "") -> str:
        """

        何时：用户说「切换到/切到/换成」会话里已有的另一份文档。必须调用，不要只口头答应。

        必填：document_title=点名标题，或 message 里能拆出标题。

        可选：message=原话。

        缺了：标题对不上就挂澄清卡列出会话文档，不要猜。

        不要：新开一份时用本工具切回旧文档。

        """
        if (message or "").strip():
            state.last_user_message = message.strip()
        return _run_dispatch_switch(
            state,
            "focus_document",
            {"document_title": (document_title or "").strip()},
        )

    @tool("re_study_chapters", description="何时：用户明确要分析/重学指定章写作要点（「分析第×章」），不是写正文。已绑蓝本。无蓝本自动大纲时改为生成要点。缺了：没点名章节就问分析哪几章，不要重学全部；方式未定时后端会追问原文/样本/自动。不要：用户说写某一章/开写（用 select_write_chapters）；无蓝本生要点用 generate_freeform_points；全部学习走 blueprint_wizard，不要空参扩成全部。")
    def re_study_chapters(payload_json: Annotated[str, Field(description="JSON。指定章用 chapter_keys 或 chapter_sequences；可含 study_scope=self|children|whole|selected、study_method=sample|source|auto。文档窗勾选传 selected+keys，不要再问下属。没点名章节不要重学全部。")] = "{}", message: Annotated[str, Field(description="可选。用户原话（如「分析第1.1章」）。")] = "") -> str:
        """

        何时：用户明确要分析/重学指定章写作要点（「分析第×章」），不是写正文。已绑蓝本。无蓝本自动大纲时改为生成要点。

        必填：能确定章节（payload_json.chapter_keys / chapter_sequences，或原话点名「第×章」）；用户明确「全部再分析」才可不指定。

        可选：message；study_scope=self|children|whole|selected（文档窗勾选用 selected，按勾选的章学，不要再问下属）；study_method=sample|source|auto。口语点名且该章有下属时才问范围。

        缺了：没点名章节就问分析哪几章，不要重学全部；方式未定时后端会追问原文/样本/自动。

        不要：用户说写某一章/开写（用 select_write_chapters）；无蓝本生要点用 generate_freeform_points；全部学习走 blueprint_wizard，不要空参扩成全部。

        """
        if (message or "").strip():
            state.last_user_message = message.strip()
        return _run_dispatch_switch(
            state,
            "re_study_chapters",
            ChatActionPayload.parse(payload_json).as_dict(),
        )

    @tool("usage_guide", description="何时：用户要使用说明、问怎么用本产品。不要：当成开写或选蓝本。")
    def usage_guide() -> str:
        """

        何时：用户要使用说明、问怎么用本产品。

        必填：无。

        可选：无。

        不要：当成开写或选蓝本。

        """
        from report_writing.chat.product_guide import build_usage_guide_reply

        _tool_begin(state, "usage_guide")
        reply = build_usage_guide_reply(state.invoke)
        return _dump({"ok": True, "reply_hint": reply,})

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
        get_chapter_info,
        generate_chapter_describes,
        generate_chapter_boilerplate,
        add_chapter_element,
        associate_elements,
        dissociate_chapter_element,
        remove_document_element,
        collect_to_element_library,
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
        ask_intent,
        focus_document,
        re_study_chapters,
        usage_guide,
    ]
    from report_writing.agents.resource_tools import build_export_resource_tools

    tools.extend(build_export_resource_tools(state))
    from report_writing.chat.materials_pack_gate import MATERIALS_PACK_ACTIONS

    tools.extend(
        _bind_payload_json_tools(
            state,
            sorted(MATERIALS_PACK_ACTIONS | _PAYLOAD_JSON_ACTIONS),
        )
    )
    from report_writing.chat.session_state import user_chose_skip_blueprint

    if user_chose_skip_blueprint(state.session):
        tools = [t for t in tools if getattr(t, "name", None) != "match_blueprints"]
    return tools


def apply_resolve_intent_clarify(
    *,
    home: Path,
    session: ChatSession,
    invoke: InvokeFn | None,
    emit: EmitFn | None,
    payload: ChatPayload,
    message: str,
) -> tuple[str, list[Any], list[dict]]:
    """执行澄清点选（卡片按钮或 Agent Tool）。"""
    from report_writing.chat.clarify_flow import (
        execute_clarify_spec,
        resolve_clarify,
        try_handle_pending_message,
    )

    payload = ChatActionPayload.parse(payload)
    option_id = str(payload.get("option_id") or "").strip()
    option_label = str(payload.get("option_label") or "").strip()
    hit = None
    if option_id:
        hit = resolve_clarify(session, option_id)
    if hit is None:
        from report_writing.chat.clarify_flow import restore_pending_from_last_clarify

        if restore_pending_from_last_clarify(session) is not None and option_id:
            hit = resolve_clarify(session, option_id)
    if hit is None:
        choice = option_label or option_id or str(message or "").strip()
        hit = try_handle_pending_message(session, choice, invoke=invoke, home=home)
    else:
        choice = option_label or option_id
    if isinstance(hit, str):
        return hit, [], []
    if isinstance(hit, Mapping):
        reply, cards, events = execute_clarify_spec(
            home, session, hit, message=choice, emit=emit, invoke=invoke
        )
        return reply, cards, events
    from report_writing.chat.clarify_flow import rehang_last_clarify

    rehang = rehang_last_clarify(session)
    if rehang is not None:
        return rehang[0], list(rehang[1] or []), []
    return "这轮确认好像过期了，你再说一下想做什么就行。", [], []
