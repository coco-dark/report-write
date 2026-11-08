"""入口结构提示：短语/结构兜底。口语选 skip / match / 向导归总控，不再整句 LLM 路由。"""

from __future__ import annotations

from typing import Any, Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]

_SKIP_HINTS = (
    "跳过蓝本",
    "不用蓝本",
    "不要蓝本",
    "无蓝本",
    "自己定大纲",
    "直接写",
    "直接开始写",
    "不用这个蓝本",
    "不要当前这份",
    "先不用蓝本",
)
_CREATE_HINTS = (
    "模版",
    "模板",
    "参考模版",
    "参考模板",
    "按模版",
    "按模板",
    "我有模版",
    "我有模板",
    "我有样本",
    "我有样板",
    "按样本写",
    "按样板写",
    "依据样本",
    "依据模版",
    "上传样板",
    "创建蓝本",
)
_MATCH_HINTS = (
    "看看有哪些蓝本",
    "有哪些蓝本",
    "匹配蓝本",
    "选蓝本",
    "列蓝本",
    "蓝本库",
    "用已有蓝本",
)


def _text(message: str) -> str:
    return (message or "").strip()


def is_skip_blueprint_intent(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    _ = (invoke, session)
    t = _text(message)
    return bool(t) and any(k in t for k in _SKIP_HINTS)


def user_wants_path_correction_to_freeform(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    return is_skip_blueprint_intent(message, invoke=invoke, session=session)


def is_create_blueprint_intent(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    _ = (invoke, session)
    t = _text(message)
    if not t or is_skip_blueprint_intent(t):
        return False
    return any(k in t for k in _CREATE_HINTS)


def is_save_freeform_blueprint_intent(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    _ = (invoke, session)
    t = _text(message)
    return bool(t) and any(
        k in t for k in ("保存为蓝本", "收录到蓝本", "存成蓝本", "收入蓝本库")
    )


def extract_wizard_title_hint(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> str:
    _ = (invoke, session)
    from report_writing.chat.session_documents import quick_title_from_write_message

    if not is_create_blueprint_intent(message):
        return ""
    return quick_title_from_write_message(message) or ""


def message_explicitly_requests_blueprint(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    if is_skip_blueprint_intent(message, invoke=invoke, session=session):
        return False
    t = _text(message)
    return bool(t) and any(k in t for k in _MATCH_HINTS)


def message_requests_blueprint_match(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    """离线脚本/结构提示：是否像要找蓝本。生产口语由总控选 match_blueprints。"""
    if is_skip_blueprint_intent(message, invoke=invoke, session=session):
        return False
    if is_create_blueprint_intent(message, invoke=invoke, session=session):
        return False
    t = _text(message)
    if message_explicitly_requests_blueprint(t):
        return True
    return bool(t) and any(k in t for k in ("写一份", "写报告", "帮我写", "开写"))


def allows_agent_blueprint_select(
    *,
    message: str,
    user_action: str,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    """仅 UI 点选允许代选；口语模糊写报告不得代选。"""
    _ = (invoke, session)
    action = (user_action or "").strip()
    if action == "select_blueprint":
        return True
    return False


def pick_auto_blueprint_id(
    message: str,
    matches: list,
    *,
    invoke: InvokeFn | None = None,
) -> str:
    """禁止口语代选蓝本；总控在用户明确选用后再调 select_blueprint。"""
    _ = (message, matches, invoke)
    return ""


def infer_freeform_meta_from_messages(
    session: ChatSession,
    extra: str = "",
    *,
    invoke=None,
) -> tuple[str, str] | None:
    from report_writing.chat.session_goal import (
        freeform_meta_from_session,
        infer_freeform_meta_from_dialogue,
    )

    hit = freeform_meta_from_session(session)
    if hit:
        return hit
    if invoke is not None:
        return infer_freeform_meta_from_dialogue(session, invoke, extra=extra)
    return None
