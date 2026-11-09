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


def _dedupe_basis_lines(lines: list[str], *, limit: int = _MAX_FACTS) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text[:500])
    return out[-limit:]


def content_basis_document_keys(session: ChatSession, task: Any | None = None) -> set[str]:
    """当前写作文档及其蓝本/副本登记键。内容依据只认这些车道，不串其它文档。"""
    from report_writing.chat.session_documents import (
        focus_document_key,
        focus_document_meta,
        normalize_document_registry_key,
    )

    def _add(bucket: set[str], raw: str) -> None:
        k = normalize_document_registry_key(str(raw or "").strip())
        if k:
            bucket.add(k)

    task_keys: set[str] = set()
    if task is not None:
        tid = str(getattr(task, "id", "") or "").strip()
        src = str(getattr(task, "source_blueprint_id", "") or "").strip()
        if tid:
            _add(task_keys, f"task:{tid}")
        if src:
            _add(task_keys, f"bp:{src}")

    focus_keys: set[str] = set()
    _add(focus_keys, focus_document_key(session))
    try:
        meta = focus_document_meta(session)
    except Exception:
        meta = {}
    _add(focus_keys, str(meta.get("registry_key") or ""))
    bid = str(meta.get("blueprint_id") or "").strip()
    if bid:
        _add(focus_keys, f"bp:{bid}")
    for tid in (
        str(meta.get("writing_task_id") or "").strip(),
        str(meta.get("task_id") or "").strip(),
        str(getattr(session, "task_id", "") or "").strip(),
    ):
        if tid:
            _add(focus_keys, f"task:{tid}")

    if not task_keys:
        return focus_keys
    allowed = set(task_keys)
    tid = str(getattr(task, "id", "") or "").strip()
    src = str(getattr(task, "source_blueprint_id", "") or "").strip()
    same_doc = tid and tid in {
        str(meta.get("writing_task_id") or "").strip(),
        str(meta.get("task_id") or "").strip(),
        str(getattr(session, "task_id", "") or "").strip(),
    }
    if src and src == str(meta.get("blueprint_id") or "").strip():
        same_doc = True
    if same_doc:
        allowed |= focus_keys
    return allowed


def _message_belongs_to_document(
    session: ChatSession, msg: Any, allowed: set[str]
) -> bool:
    from report_writing.chat.dialogue import _resolve_message_document_key
    from report_writing.chat.session_documents import (
        normalize_document_registry_key,
        session_documents_map,
    )

    raw = str(
        getattr(msg, "document_key", "") or getattr(msg, "document_id", "") or ""
    ).strip()
    key = normalize_document_registry_key(
        _resolve_message_document_key(session, raw)
    )
    if key:
        return key in allowed
    docs = session_documents_map(session)
    # 未盖戳且没有多文档登记：当作本份文档（单文档会话）
    return not docs


def collect_dialogue_content_basis(
    session: ChatSession, *, task: Any | None = None
) -> list[str]:
    """当前文档车道里用户说过的原文，以及本车道目标/备注。不做槽位抽取。"""
    from report_writing.chat.session_documents import (
        focus_document_key,
        normalize_document_registry_key,
    )

    allowed = content_basis_document_keys(session, task)
    focus = normalize_document_registry_key(focus_document_key(session) or "")
    use_lane_brief = not allowed or not focus or focus in allowed
    lines: list[str] = []
    if use_lane_brief:
        brief = get_writing_brief(session)
        for fact in list(brief.get("facts") or []):
            if str(fact).strip():
                lines.append(str(fact).strip())
        constraints = str(brief.get("user_constraints") or "").strip()
        if constraints:
            lines.append(constraints)
        try:
            from report_writing.chat.session_goal import get_session_goal

            goal = get_session_goal(session)
            for key in ("project", "summary", "notes", "doc_title"):
                val = str(getattr(goal, key, "") or "").strip()
                if val:
                    lines.append(val)
        except Exception:
            pass
    for msg in list(getattr(session, "messages", None) or []):
        if str(getattr(msg, "role", "") or "") != "user":
            continue
        if allowed and not _message_belongs_to_document(session, msg, allowed):
            continue
        text = str(getattr(msg, "text", "") or "").strip()
        if text:
            lines.append(text[:500])
    return _dedupe_basis_lines(lines)


def attach_dialogue_content_basis(session: ChatSession, task: Any) -> list[str]:
    """把本份文档对话原文记到写作任务，供各章写作当内容依据。不改 element_nodes。"""
    lines = collect_dialogue_content_basis(session, task=task)
    if hasattr(task, "writing_content_basis"):
        task.writing_content_basis = list(lines)
    return lines


def ingest_user_turn(
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None = None,
) -> bool:
    """每轮用户话：同步 session_goal + 写作简报（静默，不挡执行路径）。"""
    from report_writing.chat.session_goal import get_session_goal, ingest_user_message_goal

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
    if text and len(text) >= 8:
        append_brief_fact(session, text[:500])
    if changed:
        goal = get_session_goal(session)
        narrative = str(goal.get("summary") or goal.get("notes") or "").strip()
        if narrative:
            merge_brief_patch(session, {"narrative": narrative})
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
        "session_goal": goal.to_dict(),
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
    lines.append(
        "用户可用对话或点卡片，二者等价：确认大纲、生成要点、选节奏、开写、改章、导出。"
    )
    from report_writing.chat.session_documents import focus_document_brief

    fdb = focus_document_brief(session)
    if "onboarding=" in fdb:
        lines.append(f"焦点文档：{fdb}")
    return "\n".join(lines)


def brief_to_dict_for_body(session: ChatSession, *, home: Path | None = None) -> dict[str, Any]:
    return build_writing_brief_view(session, home=home)
