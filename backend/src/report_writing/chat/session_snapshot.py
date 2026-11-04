"""会话指针（phase、brief、路径锁等），供 Agent body / turn_context。"""

from __future__ import annotations

import re

from report_writing.domain.models import ChatSession

_META_LINE_RE = re.compile(r"[｜|]")
_OUTLINE_BODY_RE = re.compile(r"(?:^|\n)\s*\d+(?:[\.．、]\d+)*\s+\S")


def looks_like_freeform_meta_line(message: str) -> bool:
    """一行「类型｜标题」，不是章节目录粘贴。"""
    text = (message or "").strip()
    if not text or not _META_LINE_RE.search(text):
        return False
    if _OUTLINE_BODY_RE.search(text):
        return False
    parts = re.split(r"[｜|]", text, maxsplit=1)
    if len(parts) < 2:
        return False
    left = (parts[0] or "").strip()
    right = (parts[1] or "").strip()
    if len(left) < 2 or len(right) < 2:
        return False
    if re.match(r"^\d", left):
        return False
    return True


def assistant_asked_for_freeform_meta(session: ChatSession) -> bool:
    prev = last_assistant_text(session, max_chars=600)
    if not prev:
        return False
    if re.search(r"类型.{0,8}[｜|].{0,8}标题", prev):
        return True
    if "类型｜标题" in prev or "类型|标题" in prev:
        return True
    if "一行" in prev and "标题" in prev and ("类型" in prev or "｜" in prev or "|" in prev):
        return True
    return False


def session_turn_snapshot(session: ChatSession, message: str = "") -> dict[str, str | bool]:
    freeform = dict(session.freeform or {})
    plan = dict(freeform.get("plan") or {})
    from report_writing.chat.session_goal import get_session_goal, session_goal_brief
    from report_writing.chat.intent_router import dialogue_recently_skipped_blueprint, user_chose_skip_blueprint
    from report_writing.chat.writing_brief import get_writing_brief

    goal = get_session_goal(session)
    brief = get_writing_brief(session)
    facts = brief.get("facts") or []
    bits: list[str] = [str(x) for x in facts[-3:] if str(x).strip()]
    constraints = str(brief.get("user_constraints") or "").strip()
    if constraints:
        bits.append(f"约束:{constraints[:120]}")
    notes = str(goal.get("notes") or "").strip()
    if notes and notes not in constraints:
        bits.append(f"备注:{notes[:80]}")
    outline_c = str(freeform.get("outline_constraints") or "").strip()
    if outline_c:
        bits.append(f"目录要求:{outline_c[:80]}")
    brief_tail = "；".join(bits)[:320] if bits else ""

    from report_writing.chat.session_documents import focus_document_brief, focus_document_key, focus_document_meta

    focus_meta = focus_document_meta(session)
    return {
        "phase": str(session.phase or ""),
        "writing_brief_tail": brief_tail,
        "task_id": str(session.task_id or ""),
        "blueprint_id": str(session.blueprint_id or ""),
        "focus_document_key": focus_document_key(session),
        "focus_document_title": str(focus_meta.get("title") or ""),
        "focus_document_brief": focus_document_brief(session),
        "plan_goal": str(plan.get("goal") or ""),
        "freeform_category": str(freeform.get("category") or ""),
        "freeform_title": str(freeform.get("title") or ""),
        "meta_confirmed": bool(freeform.get("meta_confirmed")),
        "writing_doc_named": bool(freeform.get("writing_doc_named"))
        or (
            bool(str(freeform.get("title") or "").strip())
            and bool(freeform.get("meta_confirmed"))
        ),
        "outline_pending_edit": bool(freeform.get("outline_pending_edit")),
        "outline_confirmed": bool(freeform.get("outline_confirmed")),
        "points_step": str(freeform.get("points_step") or ""),
        "skip_blueprint_path": bool(user_chose_skip_blueprint(session)),
        "dialogue_skipped_blueprint": bool(dialogue_recently_skipped_blueprint(session)),
        "writing_path": str(freeform.get("writing_path") or ""),
        "session_goal": session_goal_brief(session),
        "session_goal_project": str(goal.get("project") or ""),
        "doc_category": str(goal.get("doc_category") or ""),
        "doc_title": str(goal.get("doc_title") or ""),
    }


# 兼容旧 import 名
session_intent_snapshot = session_turn_snapshot


def last_assistant_text(session: ChatSession, *, max_chars: int = 500) -> str:
    for msg in reversed(list(session.messages or [])):
        if str(getattr(msg, "role", "") or "") != "assistant":
            continue
        text = str(getattr(msg, "text", "") or "").strip()
        if text:
            return text[:max_chars]
    return ""
