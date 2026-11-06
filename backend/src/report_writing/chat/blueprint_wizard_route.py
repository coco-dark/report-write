"""蓝本向导执行层（口语主路径为 ReAct / blueprint_subagent）。

`apply_wizard_action` 供 Tool 与单测复用；勿再以认领路由挂回 `run_chat_control`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatActionPayload, ChatPayload, ChatSession

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

def _strip_create_only_wizard_action(action: dict[str, Any] | None) -> dict[str, Any] | None:
    """选用已有蓝本时丢掉创建向导专属命令（上传样板 / 重新提取等）。"""
    if not action:
        return None
    from report_writing.chat.wizard_state import CREATE_ONLY_WIZARD_COMMANDS, CREATE_ONLY_WIZARD_STEPS

    kind = str(action.get("type") or "").strip()
    if kind == "command":
        cmd = str(action.get("command") or "").strip()
        if cmd in CREATE_ONLY_WIZARD_COMMANDS:
            return None
        return action
    if kind == "payload":
        step = str((action.get("payload") or {}).get("step") or "").strip()
        if step in CREATE_ONLY_WIZARD_STEPS or step in {"upload_done"}:
            return None
        return action
    if kind == "chain":
        kept: list[dict[str, Any]] = []
        for sub in list(action.get("actions") or []):
            if isinstance(sub, dict):
                cleaned = _strip_create_only_wizard_action(sub)
                if cleaned:
                    kept.append(cleaned)
        if not kept:
            return None
        if len(kept) == 1:
            return kept[0]
        return {"type": "chain", "actions": kept}
    return action

_PROCESS_LABELS: dict[str, str] = {
    "re_extract_outline": "正在从样板提取目录…",
    "re_upload_sample": "正在回到上传样板步骤…",
    "go_outline_confirm": "正在打开确认目录…",
    "go_study": "正在进入分析要点步骤…",
    "re_study_chapters": "正在对指定章节重新分析要点…",
    "meta": "正在创建蓝本…",
    "upload_done": "正在进入提取目录步骤…",
    "outline_confirm": "正在确认目录…",
    "outline_done": "正在同步目录…",
    "study_done": "正在完成蓝本向导…",
}

def _merge_wizard_action_payload(
    session: ChatSession,
    wizard: dict[str, Any],
    payload: ChatPayload,
) -> dict[str, Any]:
    """合并向导 payload；空 blueprint_id 不得冲掉会话/进行中的蓝本。"""
    payload = ChatActionPayload.parse(payload)
    merged = {**wizard, **payload.as_dict()}
    pid = str(payload.get("blueprint_id") or "").strip()
    wid = str(wizard.get("blueprint_id") or "").strip()
    sid = str(session.blueprint_id or "").strip()
    if pid:
        merged["blueprint_id"] = pid
    elif wid:
        merged["blueprint_id"] = wid
    elif sid:
        merged["blueprint_id"] = sid
    else:
        merged.pop("blueprint_id", None)
    return merged

def apply_wizard_action(
    home: Path,
    session: ChatSession,
    action: dict[str, Any],
    *,
    message: str = "",
    emit: EmitFn | None = None,
    invoke: InvokeFn | None = None,
) -> tuple[str, list[Any], list[dict[str, Any]]]:
    from report_writing.chat.helpers import _emit_process
    from report_writing.chat.intent_router import active_blueprint_wizard
    from report_writing.chat.resource_actions import _action_blueprint_wizard, execute_wizard_command

    kind = str(action.get("type") or "").strip()
    if kind == "chain":
        from report_writing.chat.intent_action_chain import execute_action_chain

        subs = list(action.get("actions") or [])
        if any(
            isinstance(sub, dict) and str(sub.get("command") or "") == "abandon_wizard" for sub in subs
        ):
            return apply_wizard_action(
                home,
                session,
                {"type": "command", "command": "abandon_wizard"},
                message=message,
                emit=emit,
                invoke=invoke,
            )
        return execute_action_chain(
            subs,
            lambda sub: apply_wizard_action(
                home, session, sub, message=message, emit=emit, invoke=invoke
            ),
        )

    if kind == "command":
        command = str(action.get("command") or "").strip()
        if command == "abandon_wizard":
            from report_writing.chat.write_path_after_match import abandon_active_blueprint_and_ask_path

            _emit_process(emit, "正在退出当前蓝本…")
            reply, cards = abandon_active_blueprint_and_ask_path(
                home, session, message, invoke=invoke
            )
            return reply, cards, []
        _emit_process(emit, _PROCESS_LABELS.get(command, "正在处理蓝本向导…"))
        try:
            reply, cards = execute_wizard_command(
                home,
                session,
                command,
                study_message=str(action.get("study_message") or message or ""),
                chapter_sequences=list(action.get("chapter_sequences") or []),
                study_scope=str(action.get("study_scope") or ""),
            )
        except ValueError as error:
            return str(error), [], []
        return reply, cards, []

    wizard = dict(active_blueprint_wizard(session) or {})
    payload = _merge_wizard_action_payload(session, wizard, dict(action.get("payload") or {}))
    step = str(payload.get("step") or "").strip()
    _emit_process(emit, _PROCESS_LABELS.get(step, "正在处理蓝本向导…"))
    try:
        reply, cards = _action_blueprint_wizard(home, session, payload)
    except ValueError as error:
        return str(error), [], []
    return reply, cards, []

