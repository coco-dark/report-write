"""窄 JSON 分类器共用上下文：近期对话 + 焦点/登记文档。"""

from __future__ import annotations

from typing import Any

from report_writing.domain.models import ChatSession


def pack_classifier_context(
    session: ChatSession | None,
    *,
    max_turns: int = 8,
) -> dict[str, Any]:
    """给 classify_*_llm 的标准上下文包。"""
    if session is None:
        return {
            "phase": "",
            "task_id": "",
            "blueprint_id": "",
            "session_goal_brief": "",
            "focus_document_brief": "",
            "session_documents_brief": "",
            "dialogue_tail": [],
        }
    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.session_documents import (
        focus_document_brief,
        session_documents_brief,
    )
    from report_writing.chat.session_goal import session_goal_brief

    return {
        "phase": str(session.phase or "").strip(),
        "task_id": str(session.task_id or "").strip(),
        "blueprint_id": str(session.blueprint_id or "").strip(),
        "session_goal_brief": session_goal_brief(session),
        "focus_document_brief": focus_document_brief(session),
        "session_documents_brief": session_documents_brief(session),
        "dialogue_tail": format_full_dialogue(
            session, max_turns=max_turns, focus_only=True
        ),
    }


def format_dialogue_lines(
    dialogue_tail: list[dict[str, str]] | None,
    *,
    limit: int = 8,
    max_chars: int = 360,
) -> list[str]:
    lines: list[str] = []
    for item in (dialogue_tail or [])[-limit:]:
        role = str(item.get("role") or "?")
        line = str(item.get("text") or "").strip().replace("\n", " ")[:max_chars]
        if not line:
            continue
        doc = str(item.get("document_key") or item.get("document_id") or "").strip()
        if doc and f"[{doc}" not in line and f"doc_id={doc}" not in line:
            line = f"[doc_id={doc}] {line}"
        lines.append(f"{role}: {line}")
    return lines


def classifier_context_user_block(
    *,
    text: str,
    ctx: dict[str, Any] | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    """拼进分类器 user 提示：阶段/焦点/文档列表/近期对话/本轮。"""
    pack = dict(ctx or {})
    parts = [
        f"阶段：{(pack.get('phase') or '—')}",
        f"task_id：{(pack.get('task_id') or '—')}",
        f"blueprint_id：{(pack.get('blueprint_id') or '—')}",
        f"当前焦点文档：{(pack.get('focus_document_brief') or '—') or '—'}",
        f"已记住目标：{str(pack.get('session_goal_brief') or '—')[:400]}",
    ]
    docs = str(pack.get("session_documents_brief") or "").strip()
    parts.append("本会话已登记文档：\n" + (docs if docs else "（尚无其它文档登记）"))
    for line in extra_lines or []:
        if line:
            parts.append(line)
    dlg = format_dialogue_lines(list(pack.get("dialogue_tail") or []))
    parts.append("近期对话：\n" + ("\n".join(dlg) if dlg else "（无）"))
    parts.append(f"用户本轮：{(text or '')[:2000]}")
    return "\n".join(parts)
