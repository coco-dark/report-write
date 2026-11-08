"""无蓝本阶段结构短语。口语选目录/要点/开写 Tool 归总控，不再整句 LLM 动作分类。"""

from __future__ import annotations

from typing import Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]

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


def utterance_mentions_generate_outline(message: str) -> bool:
    """本句带生成目录短语（结构校验，不调分类模型）。"""
    return lexical_freeform_intent(message) == "generate_outline"


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
    if any(
        x in t
        for x in (
            "确认目录",
            "确认大纲",
            "目录没问题",
            "就这个目录",
            "大纲可以了",
            "目录可以了",
        )
    ) and "生成" not in t:
        return "confirm_outline"
    if any(x in t for x in ("跳过要点", "先不配要点", "不要要点", "跳过要点直接开写")):
        return "skip_points"
    if any(x in t for x in ("开始写作", "开写", "开始写", "写正文")):
        return "start_writing"
    return ""


def resolve_outline_mode_flags(
    message: str,
    *,
    confirm: bool = False,
    regenerate: bool = False,
    session: ChatSession | None = None,
    invoke: InvokeFn | None = None,
) -> tuple[bool, bool]:
    """补齐 submit_outline 的 confirm / regenerate。显式标志优先；收集器不再猜口语。"""
    if confirm:
        return True, False
    if regenerate:
        return False, True
    text = (message or "").strip()
    if not text:
        return False, False
    _ = invoke
    lexical = lexical_freeform_intent(text)
    if lexical == "confirm_outline":
        return True, False
    if lexical == "generate_outline":
        return False, True
    ff = dict(getattr(session, "freeform", None) or {}) if session is not None else {}
    pending = bool(ff.get("outline_pending_edit") or ff.get("outline_text"))
    if pending:
        from report_writing.chat.helpers import is_vague_freeform_meta

        if is_vague_freeform_meta(text):
            return True, False
    return False, False


def classify_freeform_actions(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> list[str]:
    _ = (invoke, phase, task_id, session)
    lexical = lexical_freeform_intent(message)
    return [lexical] if lexical else []


def _alias_has(
    message: str,
    *wanted: str,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> bool:
    actions = classify_freeform_actions(
        message, invoke=invoke, phase=phase, task_id=task_id, session=session
    )
    return any(item in wanted for item in actions)


def wants_generate_outline(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    session: ChatSession | None = None,
) -> bool:
    """测试别名：`classify_freeform_actions` 是否含 generate_outline。生产路径勿用来改道。"""
    return _alias_has(message, "generate_outline", invoke=invoke, phase=phase, session=session)


def wants_outline_confirm(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    session: ChatSession | None = None,
) -> bool:
    """测试别名：是否含 confirm_outline。生产路径勿用。"""
    return _alias_has(message, "confirm_outline", invoke=invoke, phase=phase, session=session)


def wants_generate_points(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> bool:
    """测试别名：是否含 generate_points / help_draft。生产路径勿用。"""
    return _alias_has(
        message,
        "generate_points",
        "help_draft",
        invoke=invoke,
        phase=phase,
        task_id=task_id,
        session=session,
    )


def wants_skip_points(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> bool:
    """测试别名：是否含 skip_points。生产路径勿用。"""
    return _alias_has(
        message, "skip_points", invoke=invoke, phase=phase, task_id=task_id, session=session
    )


def wants_start_writing(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    task_id: str = "",
    session: ChatSession | None = None,
) -> bool:
    """测试别名：是否含 start_writing。生产路径勿用。"""
    return _alias_has(
        message, "start_writing", invoke=invoke, phase=phase, task_id=task_id, session=session
    )


def wants_freeform_help_draft(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    phase: str = "",
    session: ChatSession | None = None,
) -> bool:
    """测试别名：是否含 help_draft。生产路径勿用。"""
    return _alias_has(message, "help_draft", invoke=invoke, phase=phase, session=session)


def suggest_tool_instead_of_submit_points_freeform(
    message: str,
    *,
    session: ChatSession | None = None,
    invoke: InvokeFn | None = None,
) -> dict | None:
    """仅粘贴目录结构时提示改调 submit_outline；其它误调由总控改选 Tool。"""
    _ = (session, invoke)
    text = (message or "").strip()
    if not text:
        return None
    from report_writing.chat.freeform_outline import looks_like_outline_directory

    if looks_like_outline_directory(text):
        return {
            "tool": "submit_outline",
            "args": {"outline_text": text},
        }
    return None
