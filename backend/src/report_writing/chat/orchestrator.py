"""Chat orchestration helpers."""

from __future__ import annotations

import copy
import json
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from report_writing.services.chat_session import load_or_create, save_session
from report_writing.controller import (
    confirm_outline,
    ensure_task,
    outline_from_pasted_text,
    save_outline,
    save_spec_meta,
)
from report_writing.domain.models import (
    ChapterStatus,
    ChatCard,
    ChatMessage,
    ChatPhase,
    ChatRequest,
    ChatResponse,
    ChatSession,
    HumanDecision,
    OutlineNode,
    WritingTask,
)
from report_writing.services.robots import (
    DEFAULT_CHAT_PROMPT,
    get_robot,
    match_blueprints_with_agent,
    resolve_prompt,
    search_blueprints,
)
from report_writing.services.store import TaskStore

_log = logging.getLogger("report_writing.chat")

InvokeFn = Callable[[str, str], str]
EmitFn = Callable[[dict[str, Any]], None]
StreamModelFn = Callable[[str, str], Iterable[str]]


def handle_chat(
    home: Path,
    body: ChatRequest,
    *,
    invoke: InvokeFn,
    emit: EmitFn | None = None,
    stream_model: StreamModelFn | None = None,
    model: Any = None,
    chat_model: Any = None,
    clarify_model: Any = None,
) -> ChatResponse:
    """对话总控 Agent：按需调 Tool / 子流程。emit 推送 token/process；model 为总控 ReAct。"""
    from report_writing.chat.actions import _action_start_writing
    from report_writing.chat.gates import _ensure_analysis_choice_cards
    from report_writing.chat.helpers import (
        _emit,
        _emit_process,
        _emit_tokens,
        _plain_message,
        _user_action_label,
        polish_chat_reply,
    )
    import os

    from report_writing.agents.chat_control import (
        ChatControlScriptModel,
        ChatControlState,
        UI_SHORTCUT_ACTIONS,
        build_agent_user_text,
        run_chat_control,
    )

    try:
        session = load_or_create(home, body.session_id or "", robot_id=body.robot_id or "")
    except KeyError as error:
        raise ValueError(f"会话不存在：{error}") from error
    except ValueError:
        raise

    robot = get_robot(home, session.robot_id) if session.robot_id else None
    if (body.robot_id or "").strip():
        rid = body.robot_id.strip()
        if get_robot(home, rid) is not None:
            session.robot_id = rid
            robot = get_robot(home, rid)
        else:
            session.robot_id = ""
            robot = None

    raw_message = (body.message or "").strip()
    message = _plain_message(raw_message)
    action = (body.action or "").strip()
    from report_writing.domain.models import ChatActionPayload, action_payload_dict

    data = action_payload_dict(body.action_payload)
    card_key = str(data.pop("card_key", "") or "").strip()
    payload = ChatActionPayload.parse(data)
    from report_writing.services.chat_session import _mark_card_used, apply_chat_ui_state

    sync_raw = dict(getattr(body, "client_sync", None) or {})
    if sync_raw:
        from report_writing.domain.models import ChatUiStateRequest

        session = apply_chat_ui_state(session, ChatUiStateRequest.model_validate(sync_raw))
        from report_writing.chat.wizard_state import refresh_latest_wizard_card_in_session, sync_blueprint_wizard_session

        sync_blueprint_wizard_session(home, session)
        refresh_latest_wizard_card_in_session(home, session)
    from report_writing.chat.dialogue import drop_assistant_echo_user_messages

    # 新开文档刷新后，助手原文可能已被写成用户消息。先剔掉，再交给总控 Agent。
    drop_assistant_echo_user_messages(session)
    if card_key and (action or "").strip() != "blueprint_wizard":
        _mark_card_used(session, card_key)
    token_chars = {"n": 0}

    def counting_emit(event: dict[str, Any]) -> None:
        if event.get("type") == "token":
            token_chars["n"] += len(str(event.get("text") or ""))
        _emit(emit, event)

    out = counting_emit if emit is not None else None

    from report_writing.chat.agent_chain_log import wrap_invoke_chain_logger

    invoke = wrap_invoke_chain_logger(invoke, session=session, emit=out)

    _emit(
        out,
        {
            "type": "session",
            "session_id": session.id,
            "phase": session.phase,
            "robot_id": robot.id if robot else session.robot_id,
            "blueprint_wizard": dict((session.freeform or {}).get("blueprint_wizard") or {}),
        },
    )

    from report_writing.chat.session_documents import (
        focus_document_key,
        project_focus_lane_to_session,
    )

    try:
        project_focus_lane_to_session(session, home=home)
    except Exception:
        pass
    wiz_at_turn_start = dict((session.freeform or {}).get("blueprint_wizard") or {})
    focus_before = focus_document_key(session)
    if raw_message:
        session.messages.append(
            ChatMessage(
                role="user",
                text=raw_message,
                document_key=focus_before,
                document_id=focus_before,
            )
        )
        from report_writing.chat.session_goal import ingest_user_message_goal

        from report_writing.chat.writing_brief import ingest_user_turn

        ingest_user_turn(session, raw_message, invoke=invoke)
        if not (action or "").strip():
            from report_writing.chat.session_documents import apply_explicit_new_document_turn

            apply_explicit_new_document_turn(
                session, home, raw_message, emit=out
            )
    elif action:
        session.messages.append(
            ChatMessage(
                role="user",
                text=_user_action_label(action, payload),
                document_key=focus_before,
                document_id=focus_before,
            )
        )

    cards: list[ChatCard] = []
    events: list[dict[str, Any]] = []
    reply = ""
    act = (action or "").strip()
    from report_writing.infra.config_settings import llm_mode, scripted_chat

    force_scripted = bool(
        os.environ.get("PYTEST_CURRENT_TEST") or llm_mode() == "scripted" or scripted_chat()
    )
    ctrl_model = model
    if ctrl_model is None and force_scripted:
        ctrl_model = ChatControlScriptModel()
        _emit_process(out, "离线测试模式…")
    elif ctrl_model is None:
        try:
            from report_writing.infra.llm import live_control_model

            ctrl_model = live_control_model()
        except Exception as error:
            _log.warning("live_model unavailable: %s", error)
            ctrl_model = None

    if ctrl_model is None:
        reply = (
            "还没接上写作助手，暂时帮不了你。"
            "请先配置好模型，再回来跟我聊选蓝本和开写。"
        )
        cards = []
        _emit_process(out, "还没接上写作助手")
    else:
        from report_writing.chat.clarify_invoke import clarify_invoke_fn

        state = ChatControlState(
            home=home,
            session=session,
            robot=robot,
            invoke=invoke,
            clarify_invoke=clarify_invoke_fn(invoke, model=clarify_model),
            emit=out,
            user_action=action,
            control_model=ctrl_model,
            chat_model=chat_model,
            clarify_model=clarify_model,
        )
        user_text = build_agent_user_text(
            action=action,
            payload=payload.as_dict(),
            message=message,
            session=session,
            home=home,
        )
        system_prompt = resolve_prompt("chat", robot, default=DEFAULT_CHAT_PROMPT)
        try:
            from report_writing.chat.user_process import CONTROL_RECEIVED_HINT, CONTROL_WAIT_HINT

            if not act:
                _emit_process(out, CONTROL_RECEIVED_HINT)
                _emit_tokens(out, "我在看你的需求，马上安排下一步。")
                _emit_process(out, CONTROL_WAIT_HINT)
            elif act not in UI_SHORTCUT_ACTIONS:
                _emit_process(out, CONTROL_RECEIVED_HINT)
                _emit_tokens(out, "我在看你的需求，马上安排下一步。")
                _emit_process(out, CONTROL_WAIT_HINT)
            else:
                from report_writing.chat.user_process import emit_action_process

                emit_action_process(out, act, payload.as_dict())
            reply = run_chat_control(
                model=ctrl_model,
                state=state,
                user_text=user_text,
                system_prompt=system_prompt,
            )
            cards = list(state.cards)
            if act not in UI_SHORTCUT_ACTIONS:
                cards = _ensure_analysis_choice_cards(home, session, cards)
            events = list(state.events)
            # 禁止 select_blueprint + auto_start 静默开写；开写须显式 start_writing
        except Exception as error:
            _log.exception("chat control agent failed")
            from report_writing.infra.llm_errors import user_message_for_llm_error

            reply = user_message_for_llm_error(error) or (
                f"这边出了点问题：{error}。你可以再说一次，或点下方按钮重试。"
            )
            cards = []

    if not reply:
        reply = "嗯，我在听——你接着说就行。"

    user_ctx = message or (_user_action_label(action, payload) if action else "")
    reply = polish_chat_reply(
        invoke=invoke,
        model=chat_model,
        user_message=user_ctx,
        draft=reply,
        phase=str(session.phase or ""),
        action=action or "",
        session=session,
        cards=cards,
    )

    if out is not None and token_chars["n"] <= 0:
        _emit_tokens(out, reply)

    # 本回合解析后的焦点（口语切文档后可能已变）盖到本轮 user/assistant。
    # 新开文档已清掉旧焦点时，禁止回落到上一份 bp:；有新 w* 就用它。
    focus_after = focus_document_key(session)
    if not focus_after:
        tid_now = str(getattr(session, "task_id", "") or "").strip()
        if tid_now:
            try:
                from report_writing.services.task_ids import is_writing_task_id

                if is_writing_task_id(tid_now):
                    focus_after = f"task:{tid_now}"
                    from report_writing.chat.session_documents import set_focus_document_key

                    set_focus_document_key(session, focus_after)
            except Exception:
                focus_after = f"task:{tid_now}"
    if not focus_after:
        focus_after = focus_before
    if focus_after and focus_after != focus_before:
        from report_writing.chat.session_documents import (
            document_title_for_key,
            focus_document_event,
            focus_document_meta,
        )

        meta = focus_document_meta(session)
        focus_ev = focus_document_event(
            session,
            focus_after,
            task_id=str(meta.get("task_id") or meta.get("writing_task_id") or session.task_id or ""),
            blueprint_id=str(meta.get("blueprint_id") or session.blueprint_id or ""),
            title=str(meta.get("title") or document_title_for_key(session, focus_after) or ""),
        )
        events = list(events or [])
        if not any(
            str(e.get("type") or "") == "focus_document"
            and str(e.get("focus_document_key") or "") == focus_after
            for e in events
            if isinstance(e, dict)
        ):
            events.append(focus_ev)
        _emit(out, focus_ev)
    if focus_after and session.messages:
        for msg in reversed(list(session.messages)):
            if str(getattr(msg, "role", "") or "") == "user":
                msg.document_key = focus_after
                msg.document_id = focus_after
                break
    from report_writing.chat.session_documents import stamp_document_thread_messages

    stamp_document_thread_messages(session, focus_after or None)
    if (action or "").strip() != "retry_search_chapter":
        from report_writing.chat.cards import stamp_chat_card

        stamped = [stamp_chat_card(copy.deepcopy(c)) for c in (cards or [])]
        session.messages.append(
            ChatMessage(
                role="assistant",
                text=reply,
                cards=stamped,
                document_key=focus_after,
                document_id=focus_after,
            )
        )
    from report_writing.chat.session_state import user_chose_skip_blueprint
    from report_writing.chat.wizard_state import (
        CREATE_ONLY_WIZARD_STEPS,
        is_freeform_sample_wizard_mode,
        refresh_latest_wizard_card_in_session,
        sync_blueprint_wizard_session,
    )

    def _keep_early_sample_wizard_card(card) -> bool:
        """依据样本/创建早期（名称·上传·提取）卡必须保留，即使 writing_path=freeform。

        mode 常被 ui-state 空 {} 清掉；不得仅因缺 mode / 非 w* 就把「提取目录」卡剥掉。
        """
        if getattr(card, "type", "") != "blueprint_wizard":
            return True
        payload = dict(getattr(card, "payload", None) or {})
        step = str(payload.get("step") or "").strip()
        try:
            from report_writing.chat.wizard_loop_guard import normalized_wizard_step

            step = normalized_wizard_step(step)
        except Exception:
            pass
        # 名称 / 上传 / 提取 / 确认目录：一律保留（分析由文档窗承接）
        return step in CREATE_ONLY_WIZARD_STEPS or step in {
            "meta",
            "upload",
            "outline",
            "outline_confirm",
        }

    from report_writing.chat.chat_card_policy import (
        finalize_turn_cards,
        infer_sample_wizard_active,
        merge_wizard_snapshots,
    )

    wiz_before = dict(wiz_at_turn_start or {})
    handler_wiz: dict[str, Any] = {}
    for c in cards or []:
        if getattr(c, "type", "") != "blueprint_wizard":
            continue
        payload = dict(getattr(c, "payload", None) or {})
        if payload:
            handler_wiz = merge_wizard_snapshots(handler_wiz, payload)
    sample_wizard_active = is_freeform_sample_wizard_mode(wiz_before)
    freeform_path = user_chose_skip_blueprint(session)
    # 依据样本向导进行中：仍按写作任务同步，勿当纯无蓝本清掉向导
    from report_writing.chat.session_documents import (
        focus_blocks_stale_blueprint_sync,
        wizard_belongs_to_focus,
    )

    synced = None
    skip_stale_sync = False
    try:
        skip_stale_sync = focus_blocks_stale_blueprint_sync(session)
    except Exception:
        skip_stale_sync = False
    if ((not freeform_path) or sample_wizard_active) and not skip_stale_sync:
        synced = sync_blueprint_wizard_session(home, session)
    if ((not freeform_path) or sample_wizard_active) and (action or "").strip() == "blueprint_wizard":
        payload_step = str(payload.get("step") or "").strip()
        wizard = dict((synced or {}) or (session.freeform or {}).get("blueprint_wizard") or {})
        if payload_step == "upload_done":
            wizard["step"] = "outline"
            wizard.pop("trigger_outline_extract", None)
        if payload_step in {"outline_done", "outline_confirm"}:
            wizard.pop("trigger_outline_extract", None)
            wizard.pop("pending_wizard_command", None)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    from report_writing.chat.session_runtime import (
        attach_turn_study_kick,
        reconcile_chat_session,
        runtime_public_view,
    )

    reconcile_chat_session(home, session)

    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})

    def _wizard_identity(w: dict) -> str:
        return str(
            (w or {}).get("working_task_id") or (w or {}).get("blueprint_id") or ""
        ).strip()

    # 本轮已清向导（如放弃蓝本）：勿用回合开始的 wiz_before 把卡再合成回来。
    # 新开文档 fresh_meta：空 blueprint_id 不得被上一份 bp 的 id 填回去（否则名称卡会被改成上传）。
    # force_new 切焦点后，回合开始的旧 selected/study 不得合进新车道。
    focus_changed = bool(focus_after and focus_before and focus_after != focus_before)
    before_foreign = False
    try:
        before_foreign = bool(wiz_before) and not wizard_belongs_to_focus(
            session, wiz_before
        )
    except Exception:
        before_foreign = False
    new_doc_wizard = bool(wiz.get("fresh_meta")) or focus_changed or before_foreign or (
        _wizard_identity(wiz)
        and _wizard_identity(wiz_before)
        and _wizard_identity(wiz) != _wizard_identity(wiz_before)
    )
    if wiz:
        if new_doc_wizard:
            wiz_merged = merge_wizard_snapshots(wiz, handler_wiz)
        else:
            wiz_merged = merge_wizard_snapshots(wiz_before, wiz, handler_wiz)
    else:
        wiz_merged = dict(handler_wiz or {})
    try:
        from report_writing.chat.session_documents import sanitize_wizard_for_focus

        wiz_merged = sanitize_wizard_for_focus(session, wiz_merged)
        if wiz_merged:
            session.freeform = {**(session.freeform or {}), "blueprint_wizard": wiz_merged}
            wiz = dict(wiz_merged)
        elif new_doc_wizard or before_foreign or focus_changed:
            ff_san = dict(session.freeform or {})
            ff_san.pop("blueprint_wizard", None)
            session.freeform = ff_san
            wiz = {}
    except Exception:
        pass
    sample_wizard_active = (
        is_freeform_sample_wizard_mode(wiz)
        or sample_wizard_active
        or infer_sample_wizard_active(session, wiz_before)
        or infer_sample_wizard_active(session, wiz)
        or infer_sample_wizard_active(session, wiz_merged)
    )
    # 无蓝本回合禁止把「学习分析」卡追加到大纲旁；但依据样本上传卡必须保留
    # 放弃向导：回合开始有向导、结束已清空、且本轮 handler 没再下发向导卡
    wizard_cleared = bool(wiz_before) and not wiz and not handler_wiz
    if wizard_cleared:
        cards = [
            c
            for c in (cards or [])
            if str(getattr(c, "type", "") or "") != "blueprint_wizard"
        ]
    elif (not freeform_path and not user_chose_skip_blueprint(session)) or sample_wizard_active:
        cards = finalize_turn_cards(session, cards, session_wizard=wiz_merged)
    elif cards:
        cards = [c for c in cards if _keep_early_sample_wizard_card(c)]
        from report_writing.chat.cards import collapse_duplicate_turn_cards
        from report_writing.chat.chat_card_policy import ensure_wizard_chat_card_invariant

        cards = collapse_duplicate_turn_cards(cards, session=session)
        cards = ensure_wizard_chat_card_invariant(session, cards, wiz_merged)
    else:
        from report_writing.chat.chat_card_policy import ensure_wizard_chat_card_invariant

        cards = ensure_wizard_chat_card_invariant(session, cards or [], wiz_merged)
    from report_writing.chat.cards import collapse_duplicate_turn_cards

    cards = collapse_duplicate_turn_cards(list(cards or []), session=session)
    # SSE 与会话落库必须同一套卡片快照；否则 softSync 会用旧 meta 卡盖掉本轮 upload 卡
    if (action or "").strip() != "retry_search_chapter" and session.messages:
        last = session.messages[-1]
        if (getattr(last, "role", None) or "") == "assistant":
            from report_writing.chat.cards import stamp_chat_card

            last.cards = [stamp_chat_card(copy.deepcopy(c)) for c in (cards or [])]
            session.messages[-1] = last
    if not skip_stale_sync:
        refresh_latest_wizard_card_in_session(home, session)
    try:
        from report_writing.chat.session_documents import (
            drop_foreign_blueprint_wizard,
            flush_lane,
            sanitize_wizard_for_focus,
            snapshot_focus_document_state,
        )

        live_now = dict((session.freeform or {}).get("blueprint_wizard") or {})
        cleaned = sanitize_wizard_for_focus(session, live_now)
        if cleaned != live_now:
            if cleaned:
                session.freeform = {**(session.freeform or {}), "blueprint_wizard": cleaned}
            else:
                drop_foreign_blueprint_wizard(session)
        flush_lane(session)
        snapshot_focus_document_state(session)
    except Exception:
        pass
    if raw_message:
        from report_writing.chat.turn_context import build_writing_turn_context, persist_turn_snapshot

        persist_turn_snapshot(
            session,
            build_writing_turn_context(session, raw_message, home=home),
        )
    wiz_step = str(payload.get("step") or "").strip() if (action or "").strip() == "blueprint_wizard" else ""
    save_session(
        home,
        session,
        action=(action or "").strip(),
        wizard_step=wiz_step,
    )
    runtime_view = runtime_public_view(session, home)
    live_wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    done_wiz = attach_turn_study_kick(runtime_view.get("blueprint_wizard"), live_wiz)
    wf_pub = dict(runtime_view.get("workflows") or {})
    wf_pub["blueprint_wizard"] = attach_turn_study_kick(
        wf_pub.get("blueprint_wizard"), live_wiz
    )
    runtime_view = {**runtime_view, "workflows": wf_pub, "blueprint_wizard": done_wiz}

    response = ChatResponse(
        reply=reply,
        robot_id=robot.id if robot else session.robot_id,
        blueprint_ids=list(robot.blueprint_ids) if robot else [],
        render_template_ids=list(robot.render_template_ids) if robot else [],
        word_style_ids=list(getattr(robot, "word_style_ids", None) or []) if robot else [],
        session_id=session.id,
        phase=session.phase,
        cards=cards,
        task_id=session.task_id or "",
        events=events,
        runtime=runtime_view,
        blueprint_wizard=done_wiz,
    )
    _emit(
        out,
        {
            "type": "done",
            **response.to_dict(),
        },
    )
    return response


