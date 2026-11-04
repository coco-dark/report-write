"""前端章节编辑器草稿与口头确认对齐。"""

from __future__ import annotations

from report_writing.domain.models import ChatSession


def chapter_edit_storage_key(task_id: str, chapter_key: str) -> str:
    return f"{(task_id or '').strip()}:{(chapter_key or '').strip()}"


def merge_client_chapter_edits(session: ChatSession, edits: dict | None) -> None:
    if not edits or not isinstance(edits, dict):
        return
    ff = dict(session.freeform or {})
    stored = dict(ff.get("chapter_edits") or {})
    for key, val in edits.items():
        k = str(key or "").strip()
        if not k or not isinstance(val, dict):
            continue
        text = str(val.get("text") or "").strip()
        if not text:
            continue
        stored[k] = {"text": text, "touched": bool(val.get("touched"))}
    ff["chapter_edits"] = stored
    session.freeform = ff


def client_chapter_draft_text(session: ChatSession, task_id: str, chapter_key: str) -> str:
    """仅当用户在卡片里改过正文（touched）时采用客户端草稿。"""
    ff = dict(session.freeform or {})
    edits = ff.get("chapter_edits") or {}
    if not isinstance(edits, dict):
        return ""
    keys = (
        chapter_edit_storage_key(task_id, chapter_key),
        str(chapter_key or "").strip(),
    )
    for k in keys:
        if not k:
            continue
        item = edits.get(k)
        if not isinstance(item, dict) or not item.get("touched"):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            return text
    return ""


def clear_client_chapter_edit(session: ChatSession, task_id: str, chapter_key: str) -> None:
    ff = dict(session.freeform or {})
    edits = dict(ff.get("chapter_edits") or {})
    for k in (
        chapter_edit_storage_key(task_id, chapter_key),
        str(chapter_key or "").strip(),
    ):
        edits.pop(k, None)
    ff["chapter_edits"] = edits
    session.freeform = ff
