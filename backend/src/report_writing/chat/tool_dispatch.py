"""按 tool 名执行（按钮、澄清卡已选、单测）。不是「理解用户意图」。

`execute_dispatch_tool` / `execute_intent_plan` 在 tool+args **已经确定** 之后调用。
口语主路径由 Agent 选 Tool；缺参走 observation / missing_slots。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatActionPayload, ChatSession

_log = logging.getLogger(__name__)

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

# 调度层直接执行的 Tool；其余交给后续计划短路 / ReAct
DISPATCH_TOOLS = frozenset(
    {
        "add_chapter_element",
        "collect_to_element_library",
        "associate_elements",
        "dissociate_chapter_element",
        "remove_document_element",
        "associate_element_knowledge",  # stub：告知已下线
        "open_element_library",
        "open_blueprint_library",
        "open_knowledge",  # 兼容旧意图 → 资料库
        "configure_chapter",
        "select_write_chapters",
        "write_analyzed_only",
        "start_writing",
        "check_writing_supplements",
        "skip_writing_supplement_check",
        "dismiss_writing_supplement",
        "re_study_chapters",
        "generate_freeform_points",
        "skip_freeform_points",
        "submit_outline",
        "blueprint_wizard",
        "begin_blueprint_wizard",
        "match_blueprints",
        "list_blueprints",
        "select_blueprint",
        "skip_blueprint",
        "ask_intent",
        "ask_entry_path",
        "focus_document",
        "remember_writing_goal",
        "set_session_meta",
        "submit_freeform_meta",  # 与 set_session_meta 同处理；计划里勿静默丢弃
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
        # 计划词表对齐：收录/同步/确认本章可进调度（静默多意图仍只跑安全前缀）
        "save_freeform_as_blueprint",
        "sync_document_to_blueprint_library",
        "confirm_chapter",
    }
)

KEEP_WIZARD_TOOLS = frozenset(
    {
        "add_chapter_element",
        "collect_to_element_library",
        "associate_elements",
        "dissociate_chapter_element",
        "remove_document_element",
        "open_element_library",
        "open_blueprint_library",
        "open_knowledge",
        "configure_chapter",
        "select_write_chapters",
        "write_analyzed_only",
        "start_writing",
        "re_study_chapters",
        "remember_writing_goal",
        "set_session_meta",
        "submit_freeform_meta",
    }
)

def classify_intent_plan(
    home: Path | None,
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None,
    freeform_path: bool = False,
) -> list[dict[str, Any]]:
    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.intent_llm import classify_chat_control_plan_llm
    from report_writing.chat.intent_router import active_blueprint_wizard
    from report_writing.chat.react_speed import control_dialogue_max_turns
    from report_writing.chat.session_snapshot import session_turn_snapshot

    text = (message or "").strip()
    if not text or invoke is None:
        return []
    snap = session_turn_snapshot(session, text)
    wiz = dict(active_blueprint_wizard(session) or {})
    from report_writing.chat.session_documents import focus_document_brief, session_documents_brief

    return classify_chat_control_plan_llm(
        text,
        phase=str(session.phase or ""),
        task_id=str(session.task_id or ""),
        blueprint_id=str(session.blueprint_id or wiz.get("blueprint_id") or ""),
        writing_brief_tail=str(snap.get("writing_brief_tail") or ""),
        dialogue_tail=format_full_dialogue(session, max_turns=control_dialogue_max_turns()),
        wizard_step=str(wiz.get("step") or ""),
        session_documents_brief=session_documents_brief(session),
        focus_document_brief=focus_document_brief(session),
        freeform_path=freeform_path,
        invoke=invoke,
        min_steps=1,
    )

def _bound_blueprint_id(session: ChatSession) -> str:
    bid = str(session.blueprint_id or "").strip()
    if bid:
        return bid
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    return str(wiz.get("blueprint_id") or "").strip()

def _wizard_belongs_to_focus_document(session: ChatSession) -> bool:
    """会话单例上的向导是否属于当前焦点文档（防止 skip 误放弃上一份）。"""
    from report_writing.chat.intent_router import active_blueprint_wizard
    from report_writing.chat.session_documents import focus_document_key

    wiz = dict(active_blueprint_wizard(session) or {})
    working = str(wiz.get("working_task_id") or wiz.get("blueprint_id") or "").strip()
    if not working:
        return True
    w = working.replace("task:", "").replace("bp:", "")
    tid = str(getattr(session, "task_id", "") or "").replace("task:", "").strip()
    if tid and w == tid:
        return True
    bid = str(getattr(session, "blueprint_id", "") or "").replace("bp:", "").strip()
    if bid and w == bid:
        return True
    focus = str(focus_document_key(session) or "").replace("task:", "").replace("bp:", "")
    return bool(focus and w == focus)


def _wizard_still_active(session: ChatSession) -> bool:
    from report_writing.chat.intent_router import active_blueprint_wizard
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step
    from report_writing.chat.wizard_state import wizard_step_index

    wiz = dict(active_blueprint_wizard(session) or {})
    if not wiz:
        return False
    step = normalized_wizard_step(str(wiz.get("step") or ""))
    if wizard_step_index(step) < 0:
        return False
    if wizard_step_index(step) < wizard_step_index("study"):
        return True
    return str(wiz.get("study_mode") or "").strip() != "complete"

def execute_dispatch_tool(
    home: Path,
    session: ChatSession,
    step: dict[str, Any],
    message: str,
    *,
    emit: EmitFn | None = None,
    invoke: InvokeFn | None = None,
) -> tuple[str, list[Any], list[dict[str, Any]]] | None:
    name = str(step.get("tool") or "").strip()
    args = ChatActionPayload.parse(step.get("args") or step.get("payload")).as_dict()
    text = (message or "").strip()
    # 口语点名会话内已有文档 → 注入 task/blueprint，并切换焦点（写 A 时说「帮我写 B 那章」）
    # focus_document 自己切焦点，勿先 enrich 否则会误判「已是当前焦点」
    if name not in {
        "begin_blueprint_wizard",
        "ask_intent",
        "ask_entry_path",
        "match_blueprints",
        "list_blueprints",
        "focus_document",
    }:
        from report_writing.chat.session_documents import enrich_payload_with_message_document

        args = enrich_payload_with_message_document(session, text, args, switch_focus=True)
        step = {**step, "args": args}

    if name in {"select_write_chapters", "write_analyzed_only", "start_writing"}:
        from report_writing.chat.tool_capability import (
            capability_reply_hint,
            missing_for_tool,
        )
        from report_writing.chat.session_documents import (
            focus_needs_entry_branch,
            focus_needs_entry_path,
            onboarding_lane_active,
        )

        miss = missing_for_tool(session, name, home=home)
        if miss:
            if (
                focus_needs_entry_branch(session)
                or focus_needs_entry_path(session)
                or not onboarding_lane_active(session)
            ):
                from report_writing.chat.clarify_flow import build_entry_path_clarify

                tip = str(
                    args.get("title_hint")
                    or args.get("title")
                    or (session.freeform or {}).get("title")
                    or ""
                ).strip()
                reply, cards = build_entry_path_clarify(
                    session,
                    message=text,
                    title_hint=tip,
                    invoke=invoke,
                    home=home,
                    emit=emit,
                    apply_plan=False,
                )
                return reply, list(cards or []), []
            return capability_reply_hint(miss), [], []

    if name in {"select_write_chapters", "write_analyzed_only"}:
        from report_writing.chat.write_chapter_intent import try_write_chapters_from_message

        seq_raw = args.get("chapter_sequences") or args.get("sequences") or []
        key_raw = args.get("chapter_keys") or args.get("chapter_keys_json") or []
        if isinstance(key_raw, str):
            import json

            try:
                key_raw = json.loads(key_raw)
            except json.JSONDecodeError:
                key_raw = []
        hit = try_write_chapters_from_message(
            home,
            session,
            text,
            emit=emit,
            invoke=invoke,
            write_scope=str(args.get("write_scope") or args.get("scope") or ""),
            chapter_sequences=list(seq_raw) if isinstance(seq_raw, list) else None,
            chapter_key=str(args.get("chapter_key") or "").strip(),
            chapter_key_hint=str(
                args.get("chapter_key_hint") or args.get("chapter_key") or ""
            ).strip(),
            chapter_keys=[str(k).strip() for k in key_raw if str(k).strip()]
            if isinstance(key_raw, list)
            else None,
            write_analyzed_only=(name == "write_analyzed_only"),
        )
        if hit is not None:
            return hit
        from report_writing.chat.actions import _action_select_write_chapters, _action_write_analyzed_only

        bid = str(args.get("blueprint_id") or _bound_blueprint_id(session) or "").strip()
        if not bid:
            return None
        keys = key_raw if isinstance(key_raw, list) else []
        payload = {"blueprint_id": bid, "chapter_keys": keys}
        if name == "write_analyzed_only":
            reply, cards = _action_write_analyzed_only(
                home, session, payload, emit=emit, invoke=invoke
            )
        else:
            reply, cards = _action_select_write_chapters(
                home, session, payload, emit=emit, invoke=invoke
            )
        return reply, list(cards or []), []

    if name in {
        "add_chapter_element",
        "collect_to_element_library",
        "associate_elements",
        "dissociate_chapter_element",
        "remove_document_element",
        "open_element_library",
        "open_blueprint_library",
        "open_knowledge",
        "associate_element_knowledge",  # stub：告知已下线
    }:
        from report_writing.chat.writing_control_route import apply_writing_control_classification

        return apply_writing_control_classification(
            home,
            session,
            text,
            {"tool": name, "payload": args},
            emit=emit,
            expected_tool=name,
        )

    if name == "configure_chapter":
        from report_writing.chat.action_slots import build_action_slot_clarify, missing_action_slots
        from report_writing.chat.actions import _action_configure_chapter
        from report_writing.chat.helpers import _load_task
        from report_writing.chat.intent_chapter import (
            message_has_chapter_sequence,
            pick_awaiting_confirm_chapter_key,
            resolve_chapter_key_from_message,
        )
        from report_writing.chat.writing_control_route import _session_writing_task_id

        task_id = str(args.get("task_id") or _session_writing_task_id(session) or "").strip()
        chapter_key = str(args.get("chapter_key") or args.get("chapter_key_hint") or "").strip()
        task = _load_task(home, task_id) if task_id else None
        # 只认显式传入或原话序号；禁止静默绑「待确认章」或第一章
        if not chapter_key and task is not None:
            chapter_key = resolve_chapter_key_from_message(task, text)
            if not chapter_key and message_has_chapter_sequence(text):
                # 点了序号但对不上：仍 hang，勿绑 awaiting
                chapter_key = ""
        if not task_id:
            return None
        body = {**dict(args), "task_id": task_id}
        if chapter_key:
            body["chapter_key"] = chapter_key
        # 待确认章仅作为澄清首选项提示，不自动选用
        awaiting = ""
        if not chapter_key and task is not None:
            awaiting = str(pick_awaiting_confirm_chapter_key(task) or "").strip()
            if awaiting:
                body["_prefer_chapter_key"] = awaiting
        if not chapter_key:
            miss = missing_action_slots("configure_chapter", body, session=session, task=task)
            reply, cards = build_action_slot_clarify(
                session,
                "configure_chapter",
                body,
                miss or ["chapter_key"],
                task=task,
                message=text,
            )
            return reply, list(cards or []), []
        reply, cards = _action_configure_chapter(
            home, session, {**args, "task_id": task_id, "chapter_key": chapter_key}
        )
        return reply, list(cards or []), []

    if name == "start_writing":
        from report_writing.chat.actions import _action_start_writing

        if not str(_bound_blueprint_id(session) or session.task_id or "").strip():
            return None
        keys = args.get("chapter_keys") if isinstance(args.get("chapter_keys"), list) else None
        pace = str(args.get("writing_pace") or "").strip()
        reply, cards, events = _action_start_writing(
            home,
            session,
            emit=emit,
            writing_pace=pace,
            chapter_keys=keys,
            payload=ChatActionPayload.parse(args),
            invoke=invoke,
        )
        return reply, list(cards or []), list(events or [])

    if name == "check_writing_supplements":
        from report_writing.chat.actions import _action_check_writing_supplements

        reply, cards, events = _action_check_writing_supplements(
            home, session, dict(args or {}), invoke=invoke
        )
        return reply, list(cards or []), list(events or [])

    if name == "skip_writing_supplement_check":
        from report_writing.chat.actions import _action_skip_writing_supplement_check

        reply, cards, events = _action_skip_writing_supplement_check(
            home,
            session,
            dict(args or {}),
            emit=emit,
            invoke=invoke,
        )
        return reply, list(cards or []), list(events or [])

    if name == "dismiss_writing_supplement":
        from report_writing.chat.actions import _action_dismiss_writing_supplement

        reply, cards = _action_dismiss_writing_supplement(home, session, dict(args or {}))
        return reply, list(cards or []), []

    from report_writing.chat.materials_pack_gate import MATERIALS_PACK_ACTIONS

    if name in MATERIALS_PACK_ACTIONS:
        from report_writing.chat.materials_pack_gate import apply_materials_pack_choice

        reply, cards = apply_materials_pack_choice(
            home, session, name, dict(args or {}), emit=emit
        )
        return reply, list(cards or []), []

    if name == "re_study_chapters":
        from report_writing.chat.intent_router import user_chose_skip_blueprint
        from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode

        # 纯无蓝本（自动大纲）：对话「学习」= 生成写作要点
        # 依据样本支线仍走样本学习（trigger_chapter_study → 前端 study 流）
        wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
        if user_chose_skip_blueprint(session) and not is_freeform_sample_wizard_mode(wiz):
            from report_writing.chat.actions import _action_generate_freeform_points

            reply, cards = _action_generate_freeform_points(
                home, session, dict(args), invoke=invoke, emit=emit
            )
            return reply, list(cards or []), []
        from report_writing.chat.resource_actions import execute_wizard_command
        from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message

        seqs = args.get("chapter_sequences") or args.get("sequences") or []
        if not isinstance(seqs, list) or not seqs:
            seqs = parse_study_sequences_from_message(text)
        clean_seqs = [str(s).strip() for s in seqs if str(s).strip()]
        # 分类器选了学习但无序号 → 学剩余章（go_study）；不靠口语正则判「全部学习」
        if not clean_seqs:
            reply, cards = execute_wizard_command(home, session, "go_study")
            return reply, list(cards or []), []
        try:
            reply, cards = execute_wizard_command(
                home,
                session,
                "re_study_chapters",
                study_message=text,
                chapter_sequences=clean_seqs,
                study_scope=str(args.get("study_scope") or args.get("scope") or ""),
                study_method=str(args.get("study_method") or args.get("study_source") or ""),
                study_text=str(args.get("study_text") or args.get("text") or ""),
                chapter_keys=[
                    str(k).strip() for k in list(args.get("chapter_keys") or []) if str(k).strip()
                ],
                invoke=invoke,
            )
        except ValueError as error:
            msg = str(error)
            if "没认出要分析的章节" in msg:
                from report_writing.chat.action_slots import build_action_slot_clarify
                from report_writing.chat.helpers import _load_task
                from report_writing.chat.writing_control_route import _session_writing_task_id

                tid = str(_bound_blueprint_id(session) or _session_writing_task_id(session) or "").strip()
                task = _load_task(home, tid) if tid else None
                body = {
                    **dict(args),
                    "task_id": tid,
                    "chapter_sequences": clean_seqs,
                    "study_message": text,
                }
                reply, cards = build_action_slot_clarify(
                    session,
                    "re_study_chapters",
                    body,
                    ["chapter_sequences"],
                    task=task,
                    message=text,
                )
                return reply, list(cards or []), []
            raise
        return reply, list(cards or []), []

    if name == "generate_freeform_points":
        from report_writing.chat.actions import _action_generate_freeform_points
        from report_writing.chat.user_process import emit_tool_done

        reply, cards = _action_generate_freeform_points(
            home, session, dict(args), invoke=invoke, emit=emit
        )
        emit_tool_done(emit, "generate_freeform_points")
        return reply, list(cards or []), []

    if name == "skip_freeform_points":
        from report_writing.chat.actions import _action_skip_freeform_points
        from report_writing.chat.user_process import emit_tool_done

        reply, cards = _action_skip_freeform_points(home, session, dict(args))
        emit_tool_done(emit, "skip_freeform_points")
        return reply, list(cards or []), []

    if name == "submit_outline":
        from report_writing.chat.session_documents import (
            focus_needs_doc_name,
            focus_needs_entry_path,
        )

        regen = bool(args.get("regenerate"))
        if regen and focus_needs_entry_path(session):
            from report_writing.chat.session_documents import (
                apply_working_title,
                mark_focus_entry_path,
                mark_focus_freeform_mode,
                quick_title_from_write_message,
            )

            mark_focus_entry_path(session, "freeform")
            mark_focus_freeform_mode(session, "auto_outline")
            working = str(args.get("title_hint") or args.get("title") or "").strip()
            working = working or quick_title_from_write_message(text)
            if working:
                apply_working_title(session, working, home=home, message=text)
        from report_writing.chat.actions import _action_submit_outline

        reply, cards = _action_submit_outline(
            home,
            session,
            dict(args),
            text,
            invoke=invoke,
            emit=emit,
        )
        return reply, list(cards or []), []

    if name == "skip_blueprint":
        from report_writing.chat.session_documents import (
            focus_needs_doc_name,
            is_writing_document_named,
        )

        leaked_wizard = _wizard_still_active(session) or str(session.blueprint_id or "").strip()
        from report_writing.chat.write_path_after_match import skip_blueprint_should_reask_path

        if leaked_wizard and skip_blueprint_should_reask_path(session, text):
            from report_writing.chat.write_path_after_match import (
                abandon_active_blueprint_and_ask_path,
            )

            reply, cards = abandon_active_blueprint_and_ask_path(
                home, session, text, invoke=invoke
            )
            return reply, list(cards or []), []
        if leaked_wizard:
            from report_writing.chat.actions import _clear_blueprint_wizard_state

            _clear_blueprint_wizard_state(session)
            try:
                from report_writing.services.task_ids import is_writing_task_id

                bp_now = str(session.blueprint_id or "").strip()
                if bp_now and not is_writing_task_id(bp_now):
                    session.blueprint_id = ""
            except Exception:
                session.blueprint_id = ""

        if focus_needs_doc_name(session) or not is_writing_document_named(session):
            from report_writing.chat.session_documents import (
                apply_working_title,
                quick_title_from_write_message,
            )

            tip = str(args.get("title_hint") or args.get("title") or "").strip()
            working = tip or quick_title_from_write_message(text)
            if working:
                apply_working_title(session, working, home=home, message=text)
        from report_writing.chat.blueprint_actions import run_blueprint_action

        reply, cards = run_blueprint_action(
            home, session, "skip_blueprint", args, invoke=invoke, emit=emit
        )
        return reply, list(cards or []), []

    if name == "focus_document":
        from report_writing.chat.session_documents import (
            apply_matched_document_to_session,
            focus_document_action_reply,
            focus_document_key,
            match_session_document,
            message_looks_like_new_write_topic,
            message_wants_new_document_strong,
            try_switch_focus_from_message,
        )

        hint = str(
            args.get("document_title")
            or args.get("doc_title")
            or args.get("title")
            or args.get("blueprint_title")
            or ""
        ).strip()
        # 新开文档话术：禁止切到会话里旧文档（避免「建设新中国」定名后又切回「1102环境报告」）
        if message_wants_new_document_strong(text) or message_looks_like_new_write_topic(text):
            from report_writing.chat.session_documents import document_title_for_key

            cur = focus_document_key(session)
            title = document_title_for_key(session, cur) if cur else ""
            if cur:
                from report_writing.chat.session_documents import focus_document_event

                return (
                    f"好，继续在新文档「{title or '未命名'}」上；可以说分析、配置或先写哪一章。",
                    [],
                    [focus_document_event(session, cur, title=title)],
                )
            from report_writing.chat.clarify_flow import build_ask_intent_clarify

            reply, cards = build_ask_intent_clarify(
                session,
                message=text,
                invoke=invoke,
                question="要新开一份还是切到会话里已有的文档？可以说清楚一点。",
                options=None,
            )
            return reply, list(cards or []), []
        before = focus_document_key(session)
        ids = try_switch_focus_from_message(session, text, title_hint=hint, home=home)
        if not ids:
            matched = match_session_document(session, text, title_hint=hint)
            if matched:
                # 已是目标焦点：仍回报切换成功，驱动文档窗对齐
                ids = apply_matched_document_to_session(session, matched, home=home)
        if not ids:
            cur = focus_document_key(session)
            if cur and not hint and not text:
                from report_writing.chat.session_documents import document_title_for_key

                title = document_title_for_key(session, cur) or cur
                return (
                    f"当前已在「{title}」（doc_id={cur}）。要换哪一份？直接说文档名即可。",
                    [],
                    [],
                )
            from report_writing.chat.clarify_flow import build_ask_intent_clarify

            reply, cards = build_ask_intent_clarify(
                session,
                message=text,
                invoke=invoke,
                question="要切换到会话里的哪一份文档？可以说标题，或点一项。",
                options=None,
            )
            return reply, list(cards or []), []
        reply = focus_document_action_reply(ids)
        if before and before == str(ids.get("registry_key") or ""):
            reply = reply.replace("已切换到", "当前文档是", 1)
        from report_writing.chat.session_documents import focus_document_event

        events = [
            focus_document_event(
                session,
                str(ids.get("registry_key") or ""),
                task_id=str(ids.get("task_id") or ""),
                blueprint_id=str(ids.get("blueprint_id") or ""),
                title=str(ids.get("title") or ""),
            )
        ]
        return reply, [], events

    if name in {"ask_intent", "ask_entry_path"}:
        from report_writing.chat.clarify_flow import (
            build_ask_intent_clarify,
            build_entry_path_clarify,
        )

        if name == "ask_entry_path":
            tip = str(
                args.get("title_hint")
                or args.get("title")
                or args.get("blueprint_title")
                or args.get("document_title")
                or ""
            ).strip()
            reply, cards = build_entry_path_clarify(
                session,
                message=text,
                title_hint=tip,
                invoke=invoke,
                home=home,
                emit=emit,
            )
            return reply, list(cards or []), []
        question = str(args.get("question") or "").strip()
        options = args.get("options") if isinstance(args.get("options"), list) else None
        reply, cards = build_ask_intent_clarify(
            session,
            message=text,
            invoke=invoke,
            question=question,
            options=options,
            home=home,
            emit=emit,
        )
        return reply, list(cards or []), []

    if name in {"match_blueprints", "list_blueprints", "select_blueprint", "begin_blueprint_wizard"}:
        from report_writing.chat.blueprint_actions import run_blueprint_action
        from report_writing.chat.session_documents import is_writing_document_named

        payload = dict(args)
        if name == "match_blueprints" and not str(payload.get("message") or "").strip():
            payload["message"] = text
        if name == "match_blueprints":
            from report_writing.chat.session_documents import (
                explicit_doc_title_from_message,
                session_blueprint_already_selected,
                user_wants_existing_blueprint_match,
            )

            msg_gate = str(payload.get("user_message") or payload.get("message") or text or "")
            if session_blueprint_already_selected(session):
                rematch = user_wants_existing_blueprint_match(
                    msg_gate, session=session, invoke=invoke
                ) and any(
                    k in msg_gate.replace(" ", "")
                    for k in ("再找", "换一批", "重新找", "重新匹配")
                )
                if not rematch and (
                    explicit_doc_title_from_message(msg_gate)
                    or not msg_gate
                    or len(msg_gate) < 80
                ):
                    show = str((session.freeform or {}).get("title") or "").strip() or "当前文档"
                    return (
                        f"好，文档就叫「{show}」。蓝本已选好，在右侧文档窗口继续选章或开始分析就行。",
                        [],
                        [],
                    )
        # 开写相关路径：未命名则先引导定名（list 仅浏览不强制）。
        # 禁止用 title_hint 静默定名——推断标题只作建议，必须走定名澄清。
        if name in {"match_blueprints", "begin_blueprint_wizard"} and (
            not is_writing_document_named(session)
        ):
            from report_writing.chat.session_documents import (
                focus_onboarding,
                message_wants_own_template,
            )

            msg_for_gate = str(payload.get("user_message") or payload.get("message") or text or "")

            template_declared = name == "begin_blueprint_wizard" and (
                message_wants_own_template(msg_for_gate)
                or str(focus_onboarding(session).get("preferred_entry_path") or "") == "template"
                or str(focus_onboarding(session).get("entry_path") or "") == "template"
            )
            wizard_confirmed = name == "begin_blueprint_wizard" and (
                bool(payload.get("confirmed_create"))
                or bool(payload.get("explicit_create"))
                or bool(payload.get("from_entry_path"))
                or template_declared
            )
            if wizard_confirmed:
                pass
            elif name == "match_blueprints":
                from report_writing.chat.session_documents import (
                    apply_working_title,
                    mark_focus_entry_path,
                    patch_focus_onboarding,
                    stash_pending_entry,
                    quick_title_from_write_message,
                )

                mark_focus_entry_path(session, "match")
                patch_focus_onboarding(session, entry_branch="with_blueprint")
                working = quick_title_from_write_message(msg_for_gate)
                if working:
                    apply_working_title(
                        session, working, home=home, message=msg_for_gate
                    )
                stash_pending_entry(
                    session,
                    message=msg_for_gate,
                    title=working,
                )
        if name == "begin_blueprint_wizard":
            if not str(payload.get("user_message") or payload.get("message") or "").strip():
                payload["user_message"] = text
            from report_writing.chat.session_documents import (
                peek_pending_entry,
                session_documents_map,
            )

            title_hint = str(
                payload.get("title_hint")
                or payload.get("title")
                or payload.get("blueprint_title")
                or payload.get("document_title")
                or peek_pending_entry(session).get("title")
                or (session.freeform or {}).get("title")
                or ""
            ).strip()
            if title_hint and not payload.get("title_hint"):
                payload["title_hint"] = title_hint
            user_msg = str(
                payload.get("user_message") or payload.get("message") or text or ""
            ).strip()
            # 仅认 plan/澄清显式确认；勿因「已定名」自动 confirmed_create（会跳过入口三选）
            from report_writing.chat.session_documents import (
                focus_onboarding,
                message_wants_own_template,
            )

            confirmed = bool(
                payload.get("confirmed_create")
                or payload.get("from_entry_path")
                or payload.get("explicit_create")
                or message_wants_own_template(user_msg)
                or str(focus_onboarding(session).get("preferred_entry_path") or "") == "template"
                or str(focus_onboarding(session).get("entry_path") or "") == "template"
            )
            if not confirmed:
                from report_writing.chat.clarify_flow import build_entry_path_clarify

                reply, cards = build_entry_path_clarify(
                    session,
                    message=user_msg,
                    title_hint=title_hint,
                    invoke=invoke,
                    home=home,
                    emit=emit,
                )
                return reply, list(cards or []), []
            if "force_new" not in payload and (
                bool(session_documents_map(session)) or bool(title_hint)
            ):
                payload["force_new"] = True
        if name == "select_blueprint" and not str(payload.get("blueprint_id") or "").strip():
            from report_writing.chat.action_slots import build_action_slot_clarify
            from report_writing.services.robots import search_blueprints

            matches = search_blueprints(home, q=text, robot_id=session.robot_id)[:8]
            if not matches:
                matches = search_blueprints(home, q="", robot_id=session.robot_id)[:8]
            reply, cards = build_action_slot_clarify(
                session,
                "select_blueprint",
                payload,
                ["blueprint_id"],
                task=None,
                message=text,
                blueprints=matches,
            )
            return reply, list(cards or []), []
        reply, cards = run_blueprint_action(
            home, session, name, payload, invoke=invoke, emit=emit
        )
        events = []
        if name == "begin_blueprint_wizard":
            from report_writing.chat.session_documents import (
                focus_document_event,
                focus_document_key,
            )

            nk = focus_document_key(session)
            if nk:
                ev = focus_document_event(session, nk)
                events.append(ev)
                if emit:
                    emit(ev)
        return reply, list(cards or []), events

    if name == "blueprint_wizard":
        from report_writing.chat.blueprint_wizard_route import apply_wizard_action
        from report_writing.chat.intent_llm import _wizard_action_for_command

        command = str(args.get("wizard_command") or args.get("command") or "").strip()
        if command:
            mapped = _wizard_action_for_command(command, {**args, "title": args.get("title") or ""})
            action = mapped or {"type": "command", "command": command, "study_message": text}
            seqs = args.get("chapter_sequences") or []
            if isinstance(seqs, list) and seqs and action.get("type") == "command":
                action["chapter_sequences"] = [str(s).strip() for s in seqs if str(s).strip()]
        else:
            payload = dict(args)
            if not str(payload.get("step") or payload.get("title") or "").strip():
                return None
            action = {"type": "payload", "payload": payload}
        return apply_wizard_action(home, session, action, message=text, emit=emit)

    if name in {"remember_writing_goal", "set_session_meta", "submit_freeform_meta"}:
        from report_writing.chat.session_documents import (
            is_writing_document_named,
            silent_rename_blocked_topic,
            title_is_write_subject_not_doc_name,
        )
        from report_writing.chat.session_goal_actions import run_session_goal_action

        body = ChatActionPayload.parse(args).as_dict()
        # 空 message 时带上原话便于日志；title 必须由分类器/ReAct 结构化传入，不用改名正则剥
        # submit_freeform_meta → 与 set_session_meta 同路径（无蓝本定题）
        action = "set_session_meta" if name == "submit_freeform_meta" else name
        if action == "set_session_meta":
            if not str(body.get("message") or "").strip():
                body["message"] = text
            msg_for_gate = str(body.get("message") or text or "").strip()
            tip = str(body.get("title") or "").strip()
            from report_writing.chat.session_documents import message_requires_structured_rename_title

            if message_requires_structured_rename_title(msg_for_gate) and not tip:
                return (
                    "改名须带上新名字（例如：改名叫 2026个人述职报告）。",
                    [],
                    [],
                )
            # 「安全报告 用我的模版」：题材 title 禁止带入定名
            if tip and title_is_write_subject_not_doc_name(msg_for_gate, tip):
                body.pop("title", None)
                tip = ""
            blocked = silent_rename_blocked_topic(
                session, msg_for_gate, title=tip or str(body.get("title") or "")
            )
            if blocked and is_writing_document_named(session):
                from report_writing.chat.clarify_flow import build_document_scope_clarify

                reply, cards = build_document_scope_clarify(
                    session, message=msg_for_gate, topic_hint=blocked
                )
                return reply, list(cards or []), []
        elif action == "remember_writing_goal" and not str(
            body.get("notes") or body.get("message") or ""
        ).strip():
            body["notes"] = text
        reply, cards = run_session_goal_action(
            home, session, action, body, invoke=invoke, emit=emit
        )
        return reply, list(cards or []), []

    if name == "save_freeform_as_blueprint":
        from report_writing.chat.actions import _action_save_freeform_as_blueprint

        body = ChatActionPayload.parse(args).as_dict()
        if not str(body.get("task_id") or "").strip():
            body["task_id"] = str(session.task_id or "").strip()
        reply, cards = _action_save_freeform_as_blueprint(home, session, body)
        return reply, list(cards or []), []

    if name == "sync_document_to_blueprint_library":
        from report_writing.chat.actions import _action_sync_document_to_blueprint_library

        body = ChatActionPayload.parse(args).as_dict()
        if not str(body.get("task_id") or "").strip():
            body["task_id"] = str(session.task_id or "").strip()
        reply, cards = _action_sync_document_to_blueprint_library(home, session, body)
        return reply, list(cards or []), []

    if name == "confirm_chapter":
        from report_writing.chat.actions import _action_confirm_chapter

        body = ChatActionPayload.parse(args).as_dict()
        if not str(body.get("task_id") or "").strip():
            body["task_id"] = str(session.task_id or "").strip()
        reply, cards, events = _action_confirm_chapter(home, session, body, emit=emit)
        return reply, list(cards or []), list(events or [])

    return None

def _step_hung_user_gate(session: ChatSession, step_cards: list[Any] | None) -> bool:
    """本步是否已挂起需用户确认的澄清/槽位，后续步不应继续。"""
    from report_writing.chat.clarify_flow import get_pending

    if get_pending(session) is not None:
        return True
    gate_types = {
        "intent_clarify",
        "action_slots",
        "writing_pace",
        "blueprint_list",  # 匹配结果待选用
        "blueprint_pick",
        "chapter_pick",
    }
    for card in step_cards or []:
        ctype = str(getattr(card, "type", "") or "").strip()
        if ctype in gate_types:
            return True
    return False


def execute_intent_plan(
    home: Path,
    session: ChatSession,
    plan: list[dict[str, Any]],
    message: str,
    *,
    emit: EmitFn | None = None,
    invoke: InvokeFn | None = None,
    early_focus: dict[str, Any] | None = None,
) -> tuple[str, list[Any], list[dict[str, Any]]] | None:
    """按 plan 串行执行 Tool（执行层；口语主路径用 ReAct 单 Tool 观察）。"""
    text = (message or "").strip()
    steps = [s for s in (plan or []) if str(s.get("tool") or "").strip() in DISPATCH_TOOLS]
    if not steps:
        return None
    names = [str(s.get("tool") or "").strip() for s in steps]

    from report_writing.chat.helpers import _emit_process

    keep_wizard = _wizard_still_active(session) and all(n in KEEP_WIZARD_TOOLS for n in names if n)
    _emit_process(emit, "正在按你的意图调度…")
    replies: list[str] = []
    cards: list[Any] = []
    events: list[dict[str, Any]] = []
    if early_focus:
        from report_writing.chat.session_documents import focus_document_event

        events.append(
            focus_document_event(
                session,
                str(early_focus.get("registry_key") or ""),
                task_id=str(early_focus.get("task_id") or ""),
                blueprint_id=str(early_focus.get("blueprint_id") or ""),
                title=str(early_focus.get("title") or ""),
            )
        )
    write_without_blueprint = False
    offered_pace = False
    llm = invoke
    for step in steps:
        name = str(step.get("tool") or "").strip()
        if name == "start_writing":
            pace_arg = str((step.get("args") or {}).get("writing_pace") or "").strip()
            already_started = any(
                str(getattr(c, "type", "") or "") == "progress" for c in cards
            )
            if not pace_arg and (offered_pace or already_started):
                continue
        if name not in {
            "begin_blueprint_wizard",
            "ask_intent",
            "ask_entry_path",
            "match_blueprints",
            "list_blueprints",
            "focus_document",
            "set_session_meta",
            "submit_freeform_meta",
            "remember_writing_goal",
        }:
            from report_writing.chat.session_documents import enrich_payload_with_message_document

            enriched_args = enrich_payload_with_message_document(
                session, text, dict(step.get("args") or step.get("payload") or {}), switch_focus=True
            )
            step = {**step, "args": enriched_args}
        # 创建蓝本须带澄清/显式确认；勿因已定名静默补 confirmed_create
        try:
            hit = execute_dispatch_tool(home, session, step, text, emit=emit, invoke=llm)
        except ValueError as error:
            msg = str(error)
            if "请先选择蓝本" in msg:
                write_without_blueprint = True
                hit = None
            else:
                hit = (msg, [], [])
        if hit is None:
            name = str(step.get("tool") or "").strip()
            if name in {"select_write_chapters", "write_analyzed_only", "start_writing"}:
                if not str(_bound_blueprint_id(session) or session.task_id or "").strip():
                    write_without_blueprint = True
            # 前置未就绪：停止后续步，勿在缺蓝本/缺任务时继续跑开写等
            if name in {
                "select_write_chapters",
                "write_analyzed_only",
                "start_writing",
                "select_blueprint",
                "configure_chapter",
                "begin_blueprint_wizard",
                "match_blueprints",
            }:
                break
            continue
        reply, step_cards, step_events = hit
        focus_title = str((step.get("args") or {}).get("_focus_document_title") or "").strip()
        if reply and focus_title and focus_title not in reply[:48]:
            reply = f"【当前操作文档：{focus_title}】\n{reply}"
        if reply:
            replies.append(reply)
        cards.extend(step_cards or [])
        events.extend(step_events or [])
        if any(str(getattr(c, "type", "") or "") == "writing_pace" for c in (step_cards or [])):
            offered_pace = True
        # 本步已挂澄清/待用户点选：停止后续步，避免覆盖 pending 或在未就绪状态继续跑
        if _step_hung_user_gate(session, step_cards):
            break

    if write_without_blueprint and not cards:
        clarify_hit = execute_dispatch_tool(
            home,
            session,
            {"tool": "ask_intent", "args": {}},
            text,
            emit=emit,
            invoke=llm,
        )
        if clarify_hit is not None:
            reply, step_cards, step_events = clarify_hit
            if reply:
                replies.append(reply)
            cards.extend(step_cards or [])
            events.extend(step_events or [])

    if keep_wizard and _wizard_still_active(session):
        from report_writing.chat.intent_router import active_blueprint_wizard

        wiz = dict(active_blueprint_wizard(session) or {})
        if wiz.get("step") in {"study_done", "done"} and str(wiz.get("keep_wizard") or "") != "0":
            wiz["step"] = "study"
            session.freeform = {**(session.freeform or {}), "blueprint_wizard": wiz}

    if not replies and not cards:
        return None
    from report_writing.chat.cards import collapse_duplicate_turn_cards

    return "\n".join(replies).strip() or "好的。", collapse_duplicate_turn_cards(cards), events

