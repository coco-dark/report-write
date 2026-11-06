"""库内蓝本未匹配后的路径澄清辅助。

`reply_ask_write_path` / `abandon_active_blueprint_and_ask_path` 供 Tool 与 clarify 复用；
口语主路径勿再以认领路由挂回 `run_chat_control`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatPhase, ChatSession

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

_AWAIT_KEY = "awaiting_path_after_no_match"

def get_pending_no_match_context(session: ChatSession) -> dict[str, Any]:
    raw = dict((session.freeform or {}).get(_AWAIT_KEY) or {})
    return {
        "topic": str(raw.get("topic") or "").strip(),
        "asked": bool(raw.get("asked")),
    }

def set_pending_no_match(session: ChatSession, topic: str) -> None:
    ff = dict(session.freeform or {})
    ff[_AWAIT_KEY] = {"topic": (topic or "").strip(), "asked": True}
    session.freeform = ff
    session.phase = ChatPhase.BLUEPRINT_SELECT.value

def clear_pending_no_match(session: ChatSession) -> None:
    ff = dict(session.freeform or {})
    ff.pop(_AWAIT_KEY, None)
    session.freeform = ff

def is_awaiting_path_after_no_match(session: ChatSession) -> bool:
    return bool(get_pending_no_match_context(session).get("asked"))

def _fallback_no_match_question(topic: str) -> str:
    lead = f"关于「{topic[:48]}」" if topic else "关于你要写的内容"
    return (
        f"{lead}，库里暂时没特别对口的蓝本。你更想怎么开始？"
        "可以说有样板建蓝本、先帮我起草大纲，或跳过蓝本自己定题目。"
    )

def build_no_match_path_clarify(
    session: ChatSession,
    *,
    message: str = "",
    topic: str = "",
    invoke: InvokeFn | None = None,
) -> tuple[str, list[Any]]:
    """无匹配后的入口澄清：两步（有蓝本 / 无蓝本）。"""
    from report_writing.chat.cards import _skip_blueprint_card
    from report_writing.chat.clarify_flow import begin_clarify, build_entry_branch_clarify
    from report_writing.domain.models import ChatCard

    tip = (topic or message or "").strip()
    question = ""
    if invoke is not None:
        from report_writing.chat.classifier_context import pack_classifier_context
        from report_writing.chat.clarify_llm import classify_unclear_chat_llm

        ctx = pack_classifier_context(session)
        judged = classify_unclear_chat_llm(
            tip or "库里没有对口蓝本，接下来怎么写？",
            phase=str(session.phase or "") or str(ctx.get("phase") or ""),
            blueprint_id="",
            wizard_step="",
            dialogue_tail=list(ctx.get("dialogue_tail") or []),
            focus_document_brief=str(ctx.get("focus_document_brief") or ""),
            session_documents_brief=str(ctx.get("session_documents_brief") or ""),
            session_goal_brief=str(ctx.get("session_goal_brief") or ""),
            invoke=invoke,
        ) or {}
        question = str(judged.get("question") or "").strip()
    if not question:
        question = _fallback_no_match_question(tip)
    reply, cards = build_entry_branch_clarify(
        session,
        message=tip,
        title_hint=tip,
    )
    if question and question not in reply:
        reply = f"{question}\n{reply}".strip()
    # 保留「跳过蓝本」快捷按钮
    extra: list[ChatCard] = [_skip_blueprint_card()]
    return reply, list(cards or []) + extra

def skip_blueprint_should_reask_path(session: ChatSession, message: str = "") -> bool:
    """库内已选蓝本时 skip 先问路径；对话内创建向导或用户已说无蓝本/没模版则直接 skip。"""
    from report_writing.chat.session_documents import message_wants_skip_to_freeform

    if message_wants_skip_to_freeform(message):
        return False
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    bp = str(getattr(session, "blueprint_id", "") or "").strip()
    wiz_bp = str(wiz.get("blueprint_id") or bp or "").strip()
    writing = False
    try:
        from report_writing.services.task_ids import is_writing_task_id

        writing = bool(wiz_bp) and is_writing_task_id(wiz_bp)
    except Exception:
        writing = str(wiz_bp).startswith("w")
    step = str(wiz.get("step") or "").strip()
    creating = step in {"meta", "upload", "outline", "outline_confirm", "upload_done"}
    if creating and (writing or not wiz_bp):
        return False
    if not bp and not wiz:
        return False
    return bool(bp) or bool(wiz)


def abandon_active_blueprint_and_ask_path(
    home: Path,
    session: ChatSession,
    message: str = "",
    *,
    invoke: InvokeFn | None = None,
) -> tuple[str, list[Any]]:
    """解绑当前蓝本、退出向导，询问：另选库内蓝本 / 无蓝本直写 / 重新建蓝本。"""
    from report_writing.chat.actions import _clear_blueprint_wizard_state
    from report_writing.chat.session_state import (
        WRITING_PATH_BLUEPRINT,
        touch_session_plan,
    )

    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    shown = str(wizard.get("title") or "").strip()
    _clear_blueprint_wizard_state(session)
    session.blueprint_id = ""
    from report_writing.chat.session_documents import patch_focus_onboarding

    patch_focus_onboarding(
        session,
        entry_path="",
        entry_branch="",
        preferred_entry_path="",
        freeform_mode="",
    )
    ff = dict(session.freeform or {})
    if ff.get("writing_path") == WRITING_PATH_BLUEPRINT:
        ff.pop("writing_path", None)
    plan = dict(ff.get("plan") or {})
    plan.pop("auto_selected", None)
    plan.pop("blueprint_id", None)
    ff["plan"] = plan
    session.freeform = ff
    touch_session_plan(session, goal="await_path_after_abandon")
    topic = shown or (message or "").strip()
    set_pending_no_match(session, topic)
    return build_no_match_path_clarify(
        session, message=message, topic=topic, invoke=invoke
    )

def reply_ask_write_path(
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None = None,
) -> tuple[str, list[Any]]:
    from report_writing.chat.session_state import touch_session_plan

    set_pending_no_match(session, message)
    touch_session_plan(session, goal="await_path_after_no_match")
    return build_no_match_path_clarify(
        session, message=message, topic=(message or "").strip(), invoke=invoke
    )

