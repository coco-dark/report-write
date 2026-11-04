"""无蓝本阶段口语 → 意图模型；短语表仅离线兜底。"""

from __future__ import annotations

from typing import Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]

# 离线兜底短语（有 invoke 时不抢判决，由 classify_freeform_action_llm + 上下文判定）
_OUTLINE_GEN_HINTS = (
    "生成目录",
    "起草目录",
    "生成大纲",
    "起草大纲",
    "目录大纲",
    "出个目录",
    "做个目录",
    "搭个目录",
    "重新生成目录",
    "再生成目录",
    "帮我生成目录",
    "帮我起草目录",
    "自动生成目录",
    "自动生成章节目录",
    "生成章节目录",
    "自动大纲",
    "自动目录",
)
_POINTS_GEN_HINTS = (
    "生成要点",
    "写作要点",
    "生成写作信息",
    "配要点",
    "重新生成要点",
    "再生成要点",
    "帮我生成要点",
)


def lexical_freeform_intent(message: str) -> str:
    """离线短语参考；空串表示无把握。有模型时不要用本函数做主判决。"""
    t = (message or "").strip()
    if not t:
        return ""
    # 「生成目录」绝不能落到要点（离线安全网）
    if any(h in t for h in _OUTLINE_GEN_HINTS) and "要点" not in t and "写作信息" not in t:
        return "generate_outline"
    if any(h in t for h in _POINTS_GEN_HINTS):
        return "generate_points"
    if any(x in t for x in ("确认目录", "确认大纲", "目录没问题", "就这个目录")) and "生成" not in t:
        return "confirm_outline"
    return ""


def _classify_hit(
    message: str,
    *,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
    invoke: InvokeFn | None,
) -> dict:
    from report_writing.chat.intent_context import pack_classifier_context
    from report_writing.chat.intent_llm import classify_freeform_action_llm

    ctx = pack_classifier_context(session) if session is not None else {}
    return classify_freeform_action_llm(
        message,
        phase=phase or str(ctx.get("phase") or ""),
        task_id=task_id or str(ctx.get("task_id") or ""),
        dialogue_tail=list(ctx.get("dialogue_tail") or []) or None,
        focus_document_brief=str(ctx.get("focus_document_brief") or ""),
        session_documents_brief=str(ctx.get("session_documents_brief") or ""),
        session_goal_brief=str(ctx.get("session_goal_brief") or ""),
        invoke=invoke,
    )


def classify_freeform_actions(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> list[str]:
    # 有模型：上下文分类；短语表仅离线
    if invoke is None:
        lexical = lexical_freeform_intent(message)
        if lexical:
            return [lexical]
        return []
    hit = _classify_hit(
        message, phase=phase, task_id=task_id, session=session, invoke=invoke
    )
    multi = hit.get("intents")
    if isinstance(multi, list) and len(multi) > 1:
        intents = [str(i).strip() for i in multi if str(i).strip()]
        # 同轮若同时出现确认与生成，只保留确认
        if "confirm_outline" in intents:
            return ["confirm_outline"]
        return intents
    single = str(hit.get("intent") or "").strip()
    return [single] if single else []


def _classify(
    message: str,
    *,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
    invoke: InvokeFn | None,
) -> str:
    actions = classify_freeform_actions(
        message, phase=phase, task_id=task_id, session=session, invoke=invoke
    )
    return actions[0] if actions else ""


def wants_generate_outline(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    session: ChatSession | None = None,
) -> bool:
    return _classify(message, phase=phase, session=session, invoke=invoke) == "generate_outline"


def wants_outline_confirm(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    session: ChatSession | None = None,
) -> bool:
    return _classify(message, phase=phase, session=session, invoke=invoke) == "confirm_outline"


def wants_generate_points(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> bool:
    intent = _classify(
        message, phase=phase, task_id=task_id, session=session, invoke=invoke
    )
    return intent in {"generate_points", "help_draft"}


def wants_skip_points(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> bool:
    return (
        _classify(message, phase=phase, task_id=task_id, session=session, invoke=invoke)
        == "skip_points"
    )


def wants_start_writing(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> bool:
    return (
        _classify(message, phase=phase, task_id=task_id, session=session, invoke=invoke)
        == "start_writing"
    )


def wants_freeform_help_draft(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    session: ChatSession | None = None,
) -> bool:
    return _classify(message, phase=phase, session=session, invoke=invoke) == "help_draft"
