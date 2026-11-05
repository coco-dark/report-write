"""Agent 调用链调试：工具名、参数摘要、LLM 提示词、turn_context（过程条 + 会话 tail）。"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from report_writing.domain.models import ChatSession

EmitFn = Callable[[dict[str, Any]], None]

_MAX_ROWS = 48
_PREVIEW = 2800
_ARGS_PREVIEW = 1200


def _enabled() -> bool:
    from report_writing.infra.config_settings import chain_debug_enabled

    return chain_debug_enabled()


def _truncate(text: str, n: int = _PREVIEW) -> str:
    s = str(text or "")
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _sanitize_args(args: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    out: dict[str, Any] = {}
    for key, val in args.items():
        if key in {"text", "message", "notes", "outline_text", "payload_json", "user_request"}:
            out[key] = _truncate(str(val or ""), 500)
        else:
            out[key] = val
    return out


def _append_row(session: ChatSession, row: dict[str, Any]) -> None:
    ff = dict(session.freeform or {})
    rows: list[dict[str, Any]] = list(ff.get("agent_chain_log") or [])[-(_MAX_ROWS - 1) :]
    rows.append(row)
    ff["agent_chain_log"] = rows
    session.freeform = ff


def _emit(emit: EmitFn | None, row: dict[str, Any]) -> None:
    if emit is None or not _enabled():
        return
    summary = str(row.get("summary") or row.get("step") or "调试")
    payload = {
        "type": "chain_debug",
        "step": str(row.get("step") or ""),
        "summary": summary,
        "detail": row.get("detail") if isinstance(row.get("detail"), dict) else {},
    }
    emit(payload)


def session_invoked_tools(session: ChatSession) -> list[str]:
    """本会话 agent_chain_log 里已调用的控制 Tool 名（按时间序）。"""
    names: list[str] = []
    for row in list((getattr(session, "freeform", None) or {}).get("agent_chain_log") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("step") or "") != "tool":
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        name = str(detail.get("tool") or "").strip()
        if name:
            names.append(name)
    return names


def log_chain(
    session: ChatSession,
    emit: EmitFn | None,
    *,
    step: str,
    summary: str,
    detail: dict[str, Any] | None = None,
) -> None:
    row = {
        "step": step,
        "summary": summary,
        "detail": dict(detail or {}),
    }
    _append_row(session, row)
    _emit(emit, row)


def log_turn_context(session: ChatSession, emit: EmitFn | None, ctx: Any, *, source: str) -> None:
    if ctx is None:
        log_chain(session, emit, step="context", summary=f"{source}：无 turn_context")
        return
    snap = dict(getattr(ctx, "session_snapshot", None) or {})
    wt = dict(getattr(ctx, "writing_task", None) or {})
    tail = list(getattr(ctx, "dialogue_tail", None) or [])
    detail = {
        "source": source,
        "user_message": getattr(ctx, "user_message", ""),
        "expanded_message": getattr(ctx, "expanded_message", ""),
        "session_goal_brief": getattr(ctx, "session_goal_brief", ""),
        "session_goal": dict(getattr(ctx, "session_goal", None) or {}),
        "session_snapshot": snap,
        "writing_task": wt,
        "dialogue_tail": tail[-6:],
    }
    summary = (
        f"上下文·{source} phase={snap.get('phase') or '—'} task={snap.get('task_id') or '—'} "
        f"goal={'有' if detail['session_goal_brief'] else '无'} 对话{len(tail)}条"
    )
    log_chain(session, emit, step="context", summary=summary, detail=detail)


def log_tool_invoke(
    session: ChatSession,
    emit: EmitFn | None,
    *,
    tool: str,
    args: dict[str, Any] | None,
    source: str = "control",
    turn_context: Any = None,
) -> None:
    safe = _sanitize_args(args)
    detail: dict[str, Any] = {"tool": tool, "args": safe, "via": source}
    if turn_context is not None and hasattr(turn_context, "to_dict"):
        detail["turn_context"] = turn_context.to_dict()
    log_chain(
        session,
        emit,
        step="tool",
        summary=f"工具·{tool} ({source}) args={_truncate(json.dumps(safe, ensure_ascii=False), _ARGS_PREVIEW)}",
        detail=detail,
    )


def log_llm_call(
    session: ChatSession,
    emit: EmitFn | None,
    *,
    label: str,
    system: str,
    user: str,
) -> None:
    detail = {
        "label": label,
        "system": _truncate(system, _PREVIEW),
        "user": _truncate(user, _PREVIEW),
    }
    log_chain(
        session,
        emit,
        step="llm",
        summary=f"LLM·{label} system={len(system or '')}字 user={len(user or '')}字",
        detail=detail,
    )


def log_oral_route(
    session: ChatSession,
    emit: EmitFn | None,
    *,
    route: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """口语轮走路径：langchain_agent / clarify / ui / onboarding_dispatch 等。"""
    log_chain(
        session,
        emit,
        step="oral_route",
        summary=f"口语路径·{route}",
        detail={"route": route, **(detail or {})},
    )


def log_react_round(
    session: ChatSession,
    emit: EmitFn | None,
    *,
    agent: str,
    system_prompt: str,
    user_text: str,
) -> None:
    log_chain(
        session,
        emit,
        step="react",
        summary=f"ReAct·{agent} 开跑",
        detail={
            "agent": agent,
            "system_prompt": _truncate(system_prompt, _PREVIEW),
            "user_text": _truncate(user_text, 800),
        },
    )


def log_react_tool_result(
    session: ChatSession,
    emit: EmitFn | None,
    *,
    tool: str,
    content: str,
) -> None:
    log_chain(
        session,
        emit,
        step="tool_result",
        summary=f"工具结果·{tool or '?'}",
        detail={"tool": tool, "content_preview": _truncate(str(content or ""), 600)},
    )


def log_react_tool_calls(
    session: ChatSession,
    emit: EmitFn | None,
    *,
    agent: str,
    messages: list[Any],
) -> None:
    """事后回放（无 middleware 时）。create_agent 路径改由 ReactChainLogMiddleware 实时写。"""
    if not messages:
        return
    from langchain_core.messages import ToolMessage

    for message in messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            if isinstance(call, dict):
                name = str(call.get("name") or call.get("id") or "?")
                args = call.get("args") or call.get("arguments") or {}
            else:
                name = str(getattr(call, "name", None) or "?")
                args = getattr(call, "args", None) or {}
            log_tool_invoke(
                session,
                emit,
                tool=name,
                args=args if isinstance(args, dict) else {"raw": _truncate(str(args), 400)},
                source=f"react:{agent}",
            )
        if isinstance(message, ToolMessage):
            log_react_tool_result(
                session,
                emit,
                tool=message.name or "?",
                content=str(message.content or ""),
            )


def wrap_invoke_chain_logger(
    invoke: Callable[[str, str], str],
    *,
    session: ChatSession,
    emit: EmitFn | None,
) -> Callable[[str, str], str]:
    if not _enabled():

        def passthrough(system: str, user: str) -> str:
            return invoke(system, user)

        return passthrough

    def wrapped(system: str, user: str) -> str:
        label = "llm"
        if "章节" in system and "配置" in system:
            label = "chapter_config"
        log_llm_call(session, emit, label=label, system=system or "", user=user or "")
        return invoke(system, user)

    return wrapped
