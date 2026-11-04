"""会话写作简报：跨轮唯一叙事状态（70% 自由对话的数据面，30% 引导只读它）。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]

_MAX_FACTS = 24
_MAX_NARRATIVE = 4000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_writing_brief(session: ChatSession) -> dict[str, Any]:
    raw = (session.freeform or {}).get("writing_brief") or {}
    if not isinstance(raw, dict):
        raw = {}
    raw = dict(raw)
    facts = raw.get("facts")
    if not isinstance(facts, list):
        facts = []
    return {
        "narrative": str(raw.get("narrative") or "").strip(),
        "facts": [str(x).strip() for x in facts if str(x).strip()][-_MAX_FACTS:],
        "user_constraints": str(raw.get("user_constraints") or "").strip(),
        "focus": str(raw.get("focus") or "").strip(),
        "updated_at": str(raw.get("updated_at") or "").strip(),
    }


def _save_brief(session: ChatSession, brief: dict[str, Any]) -> None:
    ff = dict(session.freeform or {})
    ff["writing_brief"] = brief
    session.freeform = ff


def append_brief_fact(session: ChatSession, line: str) -> None:
    text = (line or "").strip()
    if not text:
        return
    brief = get_writing_brief(session)
    facts = list(brief.get("facts") or [])
    if text in facts:
        return
    facts.append(text[:500])
    brief["facts"] = facts[-_MAX_FACTS:]
    brief["updated_at"] = _now()
    _save_brief(session, brief)


def merge_brief_patch(session: ChatSession, patch: dict[str, Any]) -> dict[str, Any]:
    brief = get_writing_brief(session)
    for key in ("narrative", "user_constraints", "focus"):
        val = str((patch or {}).get(key) or "").strip()
        if val:
            brief[key] = val[:_MAX_NARRATIVE] if key == "narrative" else val[:800]
    extra_facts = patch.get("facts")
    if isinstance(extra_facts, list):
        for item in extra_facts:
            append_brief_fact(session, str(item))
        brief = get_writing_brief(session)
    brief["updated_at"] = _now()
    _save_brief(session, brief)
    return brief


def ingest_user_turn(
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None = None,
) -> bool:
    """每轮用户话：同步 session_goal + 写作简报（静默，不挡执行路径）。"""
    from report_writing.chat.session_goal import ingest_user_message_goal, parse_goal_patch_from_message

    text = (message or "").strip()
    if not text:
        return False
    try:
        from report_writing.chat.session_documents import (
            quick_title_from_write_message,
            silent_rename_blocked_topic,
        )

        tip = quick_title_from_write_message(text)
        if silent_rename_blocked_topic(session, text, title=tip):
            # 新开另一份文档的写句，先别写进当前焦点车道；force_new 后再 ingest。
            return False
    except Exception:
        pass
    changed = ingest_user_message_goal(session, text, invoke=invoke)
    patch = parse_goal_patch_from_message(text, invoke=invoke)
    if text and len(text) >= 8 and not patch.get("notes"):
        append_brief_fact(session, text[:500])
    if patch.get("notes") or patch.get("summary"):
        merge_brief_patch(
            session,
            {
                "narrative": patch.get("summary") or patch.get("notes") or "",
                "facts": [text[:500]] if len(text) > 8 else [],
            },
        )
        changed = True
    return changed


def build_writing_brief_view(session: ChatSession, *, home: Path | None = None) -> dict[str, Any]:
    """供 Agent / API：goal + 快照 + 任务 + 简报合并视图。"""
    from report_writing.chat.session_snapshot import session_turn_snapshot
    from report_writing.chat.session_goal import get_session_goal, session_goal_brief

    brief = get_writing_brief(session)
    snap = session_turn_snapshot(session)
    goal = get_session_goal(session)
    view: dict[str, Any] = {
        "brief": brief,
        "session_goal": goal,
        "session_goal_brief": session_goal_brief(session),
        "session": snap,
        "task_id": (session.task_id or "").strip(),
        "blueprint_id": (session.blueprint_id or "").strip(),
    }
    tid = (session.task_id or "").strip()
    if tid and home is not None:
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, tid)
        if task is not None:
            view["task"] = {
                "id": task.id,
                "title": str(task.title or ""),
                "category": str(task.category or ""),
                "phase_hint": str(session.phase or ""),
            }
    return view


def writing_brief_prompt_block(session: ChatSession, *, home: Path | None = None) -> str:
    view = build_writing_brief_view(session, home=home)
    lines = [
        "【写作简报 · 本轮必须优先遵守】",
        "用户是在连续对话里写文档，不要当成孤立单句；已记下的事实与目标优先于阶段枚举。",
        "能直接调 Tool 推进就调，不要重复索要用户已说过的背景。",
    ]
    gb = str(view.get("session_goal_brief") or "").strip()
    if gb:
        lines.append(f"目标摘要：{gb}")
    brief = view.get("brief") or {}
    facts = brief.get("facts") or []
    if facts:
        lines.append("用户已提供的事实（按时间）：")
        for f in facts[-8:]:
            lines.append(f"  - {f}")
    constraints = str(brief.get("user_constraints") or "").strip()
    if constraints:
        lines.append(f"用户约束（须持续遵守）：{constraints[:800]}")
    goal = view.get("session_goal") or {}
    notes = str(goal.get("notes") or "").strip()
    if notes and notes not in constraints and notes != gb:
        lines.append(f"会话备注：{notes[:600]}")
    # 无蓝本目录修订约束（重新生成大纲时沿用）
    ff = dict(session.freeform or {})
    outline_c = str(ff.get("outline_constraints") or "").strip()
    if outline_c and outline_c not in constraints:
        lines.append(f"目录修改要求：{outline_c[:500]}")
    narrative = str(brief.get("narrative") or "").strip()
    if narrative and narrative != gb:
        lines.append(f"叙事：{narrative[:600]}")
    focus = str(brief.get("focus") or "").strip()
    if focus:
        lines.append(f"当前焦点：{focus}")
    task = view.get("task")
    if isinstance(task, dict) and (task.get("title") or task.get("category")):
        lines.append(f"当前任务：{task.get('category') or '—'}｜{task.get('title') or '—'}")
    snap = view.get("session") or {}
    lines.append(
        "用户可用对话或点卡片，二者等价：确认大纲、生成要点、选节奏、开写、改章、导出。"
    )
    lines.append(
        "开写前若缺写作要点：先问是否要生成；用户说「好/是/生成」再 generate_freeform_points，然后 start_writing。"
    )
    from report_writing.chat.next_step import next_step_prompt_line
    from report_writing.chat.session_documents import focus_document_brief

    fdb = focus_document_brief(session)
    if "onboarding=" in fdb:
        lines.append(f"焦点文档：{fdb}")
        if "待定名" in fdb:
            lines.append("下一步：先 ask_entry_path 引导用户确认文档名称（定名卡），勿静默 set_session_meta。")
        elif "待选线路" in fdb:
            lines.append(
                "下一步：ask_entry_path — 有蓝本 / 无蓝本（无蓝本再选模版或自动大纲），"
                "勿直接说「生成目录」。"
            )

    ns = next_step_prompt_line(session, home=home)
    if ns:
        lines.append(ns)
    return "\n".join(lines)


def brief_to_dict_for_body(session: ChatSession, *, home: Path | None = None) -> dict[str, Any]:
    return build_writing_brief_view(session, home=home)
