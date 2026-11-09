"""单轮对话写作上下文：总控 → 子 Agent → 章节 Tool 共用，避免「只调工具不带语境」。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatSession, SessionGoal


def _turn_context_revision(session: ChatSession, home: Path | None = None) -> str:
    from report_writing.chat.session_config_revision import session_config_revision
    from report_writing.chat.session_documents import focus_document_meta

    focus = focus_document_meta(session)
    fwid = str(
        focus.get("writing_task_id") or focus.get("task_id") or session.task_id or ""
    ).strip()
    saved = ""
    if home is not None and fwid:
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, fwid)
        if task is not None:
            try:
                saved = str((task.to_dict() or {}).get("_saved_at") or "").strip()
            except Exception:
                saved = ""
    cfg = session_config_revision(session)
    return f"{session.phase}:{fwid}:{cfg}:{saved}"


@dataclass
class WritingTurnContext:
    """一次用户输入对应的可序列化会话视图。"""

    user_message: str
    user_action: str = ""
    expanded_message: str = ""
    dialogue_tail: list[dict[str, str]] = field(default_factory=list)
    session_snapshot: dict[str, Any] = field(default_factory=dict)
    session_goal: SessionGoal = field(default_factory=SessionGoal)
    session_goal_brief: str = ""
    writing_task: dict[str, Any] = field(default_factory=dict)
    focus_document: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_message": self.user_message,
            "user_action": self.user_action,
            "expanded_message": self.expanded_message,
            "dialogue_tail": self.dialogue_tail,
            "session_snapshot": self.session_snapshot,
            "session_goal": self.session_goal.to_dict(),
            "session_goal_brief": self.session_goal_brief,
            "writing_task": self.writing_task,
            "focus_document": self.focus_document,
        }


def build_writing_turn_context(
    session: ChatSession,
    message: str,
    *,
    home: Path | None = None,
    user_action: str = "",
) -> WritingTurnContext:
    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.session_snapshot import session_turn_snapshot
    from report_writing.chat.session_goal import get_session_goal, session_goal_brief

    raw = (message or "").strip()
    sid = (session.id or "").strip()
    if sid and raw:
        from report_writing.infra.redis_cache import cache_get_json, turn_context_cache_key

        rev = _turn_context_revision(session, home)
        hit = cache_get_json(turn_context_cache_key(sid, raw, revision=rev))
        if isinstance(hit, dict) and hit.get("user_message") == raw:
            return WritingTurnContext(
                user_message=str(hit.get("user_message") or ""),
                user_action=str(hit.get("user_action") or ""),
                expanded_message=str(hit.get("expanded_message") or ""),
                dialogue_tail=list(hit.get("dialogue_tail") or []),
                session_snapshot=dict(hit.get("session_snapshot") or {}),
                session_goal=SessionGoal.model_validate(hit.get("session_goal") or {}),
                session_goal_brief=str(hit.get("session_goal_brief") or ""),
                writing_task=dict(hit.get("writing_task") or {}),
                focus_document=dict(hit.get("focus_document") or {}),
            )

    expanded = raw
    goal = get_session_goal(session)
    from report_writing.chat.session_documents import focus_document_meta

    focus_doc = focus_document_meta(session)
    ctx = WritingTurnContext(
        user_message=raw,
        user_action=(user_action or "").strip(),
        expanded_message=expanded,
        dialogue_tail=format_full_dialogue(session, max_turns=8, focus_only=True),
        session_snapshot=dict(session_turn_snapshot(session)),
        session_goal=goal,
        session_goal_brief=session_goal_brief(session),
        focus_document=dict(focus_doc),
    )

    tid = (session.task_id or "").strip() or str(focus_doc.get("writing_task_id") or focus_doc.get("task_id") or "").strip()
    if not tid or home is None:
        _cache_turn_context(session, ctx, raw, home=home)
        return ctx

    from report_writing.chat.helpers import _load_task
    from report_writing.chat.chapter_resolve import pick_focus_chapter_key_extended

    task = _load_task(home, tid)
    if task is None:
        _cache_turn_context(session, ctx, raw, home=home)
        return ctx

    focus_key = pick_focus_chapter_key_extended(session, task, expanded)
    node = None
    if focus_key and task.outline is not None:
        node = next((n for n in (task.outline.chapters or []) if n.key == focus_key), None)
    ctx.writing_task = {
        "task_id": task.id,
        "category": str(task.category or "").strip(),
        "title": str(task.title or focus_doc.get("title") or "").strip(),
        "focus_chapter_key": focus_key or "",
        "focus_chapter_title": (
            f"{getattr(node, 'sequence', '')} {getattr(node, 'title', '')}".strip() if node else ""
        ),
        "document_key": str(focus_doc.get("registry_key") or ""),
    }
    _cache_turn_context(session, ctx, raw, home=home)
    return ctx


def _cache_turn_context(
    session: ChatSession, ctx: WritingTurnContext, raw: str, *, home: Path | None = None
) -> None:
    sid = (session.id or "").strip()
    if not sid or not raw:
        return
    from report_writing.infra.redis_cache import cache_set_json, turn_context_cache_key

    rev = _turn_context_revision(session, home)
    cache_set_json(turn_context_cache_key(sid, raw, revision=rev), ctx.to_dict(), ttl_seconds=600)


def turn_context_prompt_block(ctx: WritingTurnContext | None) -> str:
    if ctx is None:
        return ""
    lines = ["【本轮会话上下文】", f"用户说：{ctx.user_message}"]
    if ctx.expanded_message and ctx.expanded_message != ctx.user_message:
        lines.append(f"结合上文理解为：{ctx.expanded_message}")
    if ctx.user_action:
        lines.append(f"界面操作：{ctx.user_action}")
    snap = ctx.session_snapshot
    lines.append(
        f"阶段={snap.get('phase') or '—'} "
        f"task_id={snap.get('task_id') or '—'} "
        f"蓝本={snap.get('blueprint_id') or '—'}"
    )
    fd = ctx.focus_document or {}
    if fd.get("registry_key") or fd.get("title") or snap.get("focus_document_brief"):
        lines.append(
            "当前焦点文档："
            + (
                str(snap.get("focus_document_brief") or "").strip()
                or f"{fd.get('title') or '—'}（{fd.get('registry_key') or '—'}）"
            )
        )
    if ctx.session_goal_brief:
        lines.append(f"已记住的写作目标：{ctx.session_goal_brief}")
    wb = (ctx.session_snapshot or {}).get("writing_brief_tail")
    if wb:
        lines.append(f"简报事实：{wb}")
    wt = ctx.writing_task or {}
    if wt.get("title") or wt.get("category"):
        lines.append(f"当前写作任务：{wt.get('category') or '—'}｜{wt.get('title') or '—'}")
    if wt.get("focus_chapter_title"):
        lines.append(f"写作焦点章：{wt['focus_chapter_title']}（key={wt.get('focus_chapter_key') or '—'}）")
    if ctx.dialogue_tail:
        lines.append("近期对话：")
        for item in ctx.dialogue_tail[-5:]:
            role = item.get("role") or "?"
            text = str(item.get("text") or "").strip().replace("\n", " ")[:240]
            if text:
                lines.append(f"  - {role}：{text}")
    lines.append("请结合以上语境理解 user_request；默认操作当前焦点文档，用户点名其它文档时再切换。")
    return "\n".join(lines)


def attach_turn_context(payload: dict[str, Any], ctx: WritingTurnContext | None) -> None:
    if ctx is None:
        return
    payload["turn_context"] = ctx.to_dict()


def merge_turn_context_into_agent_body(body: dict[str, Any], ctx: WritingTurnContext | None) -> None:
    if ctx is None:
        return
    body["turn_context"] = ctx.to_dict()
    if ctx.dialogue_tail and "dialogue_full" not in body:
        body["dialogue_full"] = ctx.dialogue_tail
    if ctx.session_goal_brief and "session_goal" not in body:
        body["session_goal"] = ctx.session_goal.to_dict()


def persist_turn_snapshot(session: ChatSession, ctx: WritingTurnContext | None) -> bool:
    """把本轮上下文摘要写入 session.freeform，随 save_session 落盘（非完整 turn_context 快照）。"""
    if ctx is None:
        return False
    ff = dict(session.freeform or {})
    ff["last_turn_snapshot"] = {
        "user_message": ctx.user_message,
        "expanded_message": ctx.expanded_message,
        "session_goal_brief": ctx.session_goal_brief,
        "writing_task": dict(ctx.writing_task or {}),
        "phase": str(ctx.session_snapshot.get("phase") or ""),
    }
    session.freeform = ff
    return True


def ensure_control_state_context(state: Any, message: str | None = None) -> WritingTurnContext:
    """总控 state 上缓存本轮上下文（同一句不重复构建）。"""
    msg = (message or getattr(state, "last_user_message", None) or "").strip()
    existing = getattr(state, "turn_context", None)
    if (
        existing is not None
        and isinstance(existing, WritingTurnContext)
        and existing.user_message == msg
        and (existing.user_action or "") == (getattr(state, "user_action", None) or "")
    ):
        return existing
    ctx = build_writing_turn_context(
        state.session,
        msg,
        home=getattr(state, "home", None),
        user_action=getattr(state, "user_action", None) or "",
    )
    state.turn_context = ctx
    return ctx
