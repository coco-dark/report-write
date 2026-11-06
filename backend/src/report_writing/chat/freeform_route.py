"""无蓝本写作执行辅助（口语主路径为 ReAct Tool）。

`_apply_single_freeform_intent` 供 clarify / 单测复用；勿再以认领路由挂回 `run_chat_control`。
无蓝本没有「依据样本学习」：学习/生成写作信息 → 生成要点；开写 → start_writing。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatPhase, ChatSession

EmitFn = Callable[[dict[str, Any]], None]

def _start_writing_from_freeform(
    home: Path,
    session: ChatSession,
    text: str,
    *,
    emit: EmitFn | None = None,
) -> tuple[str, list[Any], list[dict[str, Any]]]:
    from report_writing.chat.helpers import _emit_process
    from report_writing.chat.tool_dispatch import execute_dispatch_tool

    _emit_process(emit, "好，按无蓝本任务开写…")
    hit = execute_dispatch_tool(
        home,
        session,
        {"tool": "start_writing", "args": {}},
        text,
        emit=emit,
        invoke=None,
    )
    if hit is None:
        return "还不能开写：请先确认目录并生成（或跳过）写作要点。", [], []
    return hit

def _apply_single_freeform_intent(
    home: Path,
    session: ChatSession,
    text: str,
    intent: str,
    *,
    phase: str,
    tid: str,
    invoke: Callable[[str, str], str] | None,
    llm: Callable[[str, str], str] | None,
    emit: EmitFn | None,
) -> tuple[str, list[Any], list[dict[str, Any]]] | None:
    from report_writing.chat.helpers import _emit_process

    intent = str(intent or "").strip()
    if intent == "none":
        return None

    # 生成/重新起草目录：任意无蓝本阶段都可（含确认导出态，勿只限 freeform_outline）
    if intent == "generate_outline" or (
        intent == "help_draft" and not (session.freeform or {}).get("outline_confirmed")
    ):
        from report_writing.chat import _collect_freeform_outline

        _emit_process(emit, "正在起草章节目录…")
        reply, cards = _collect_freeform_outline(
            home,
            session,
            text,
            invoke=invoke,
            regenerate=True,
            revision_instructions=text if text not in {"generate_outline", "help_draft"} else "",
            emit=emit,
        )
        return reply, cards, []

    if intent == "confirm_outline":
        from report_writing.chat import _collect_freeform_outline

        _emit_process(emit, "正在确认目录…")
        reply, cards = _collect_freeform_outline(
            home, session, text, confirm=True, invoke=invoke, emit=emit
        )
        return reply, cards, []

    # 生成/重写要点：大纲确认后任意阶段都可对话触发（替代样本学习）
    if intent in {"generate_points", "help_draft"} and tid:
        if phase in {
            ChatPhase.FREEFORM_POINTS.value,
            ChatPhase.READY_TO_WRITE.value,
            ChatPhase.WRITING.value,
            ChatPhase.CONFIRM_EXPORT.value,
            ChatPhase.FREEFORM_OUTLINE.value,
        } or phase.startswith("freeform_"):
            from report_writing.chat import _collect_freeform_points

            _emit_process(emit, "正在生成写作要点…")
            reply, cards = _collect_freeform_points(
                home, session, text, generate=True, invoke=invoke, emit=emit
            )
            return reply, cards, []

    if intent == "skip_points":
        from report_writing.chat import _collect_freeform_points

        _emit_process(emit, "正在跳过要点配置…")
        reply, cards = _collect_freeform_points(
            home, session, text, skip=True, invoke=invoke, emit=emit
        )
        return reply, cards, []

    if intent == "start_writing" and tid:
        return _start_writing_from_freeform(home, session, text, emit=emit)
    return None
