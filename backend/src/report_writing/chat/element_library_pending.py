"""对话中新增的要素：暂存待用户确认是否收入个人要素库。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatCard, ChatSession, ElementNode, WritingTask


def list_pending(session: ChatSession) -> list[dict[str, Any]]:
    raw = (session.freeform or {}).get("element_library_pending")
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw if isinstance(x, dict)]


def queue_element_pending(
    session: ChatSession,
    element: ElementNode,
    *,
    task_id: str,
    chapter_key: str,
    chapter_title: str = "",
    category: str = "",
    tags: list[str] | None = None,
    purpose: str = "",
) -> dict[str, Any]:
    """任务内新建要素后入队（去重：同 task + element id）。"""
    if (getattr(element, "library_item_id", "") or "").strip():
        return {}
    clean_tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if not clean_tags:
        clean_tags = [str(t).strip() for t in (getattr(element, "tags", None) or []) if str(t).strip()]
    cat = str(category or "").strip() or str(getattr(element, "category", "") or "").strip()
    pur = str(purpose or "").strip() or str(getattr(element, "purpose", "") or "").strip()
    entry = {
        "pending_id": f"pel-{uuid.uuid4().hex[:10]}",
        "task_id": (task_id or "").strip(),
        "chapter_key": (chapter_key or "").strip(),
        "chapter_title": (chapter_title or "").strip(),
        "element": element.model_dump() if hasattr(element, "model_dump") else dict(element),
        "category": cat,
        "tags": clean_tags,
        "purpose": pur,
    }
    ff = dict(session.freeform or {})
    pending = list_pending(session)
    eid = str((entry["element"] or {}).get("id") or "").strip()
    pending = [
        p
        for p in pending
        if not (p.get("task_id") == entry["task_id"] and (p.get("element") or {}).get("id") == eid)
    ]
    pending.append(entry)
    ff["element_library_pending"] = pending[-40:]
    session.freeform = ff
    return entry


def queue_new_elements_after_configure(
    session: ChatSession,
    task: WritingTask,
    before_ids: set[str],
    *,
    chapter_key: str,
    chapter_title: str = "",
) -> int:
    """保存章节配置后，为新增且非库克隆的要素入队。"""
    n = 0
    for element in task.element_nodes or []:
        eid = str(getattr(element, "id", "") or "").strip()
        if not eid or eid in before_ids:
            continue
        if (getattr(element, "library_item_id", "") or "").strip():
            continue
        queue_element_pending(
            session,
            element,
            task_id=task.id,
            chapter_key=chapter_key,
            chapter_title=chapter_title,
            category=str(getattr(element, "category", "") or "").strip(),
            tags=list(getattr(element, "tags", None) or []),
            purpose=str(getattr(element, "purpose", "") or "").strip(),
        )
        n += 1
    return n


def _persist_pending_meta_to_tasks(
    home: Path,
    rows: list[dict[str, Any]],
    *,
    overlays: dict[str, dict[str, Any]] | None = None,
) -> int:
    """把待办上的分类/标签/作用写回任务要素，避免暂不收录后元数据丢失。"""
    from report_writing.domain.elements import save_elements
    from report_writing.services.store import TaskStore

    overlays = overlays or {}
    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        tid = str(row.get("task_id") or "").strip()
        if not tid:
            continue
        by_task.setdefault(tid, []).append(row)
    updated = 0
    for tid, task_rows in by_task.items():
        path = home / "tasks" / tid
        if not path.is_dir():
            continue
        try:
            store = TaskStore(path)
            task = store.load()
        except Exception:
            continue
        nodes = list(task.element_nodes or [])
        by_id = {str(getattr(n, "id", "") or "").strip(): n for n in nodes}
        dirty = False
        for row in task_rows:
            eid = str((row.get("element") or {}).get("id") or "").strip()
            node = by_id.get(eid)
            if node is None:
                continue
            overlay = overlays.get(str(row.get("pending_id") or "").strip()) or {}
            cat = str(overlay.get("category") if "category" in overlay else row.get("category") or "").strip()
            tags_raw = overlay.get("tags") if "tags" in overlay else row.get("tags")
            tags = [str(t).strip() for t in (tags_raw or []) if str(t).strip()]
            pur = str(overlay.get("purpose") if "purpose" in overlay else row.get("purpose") or "").strip()
            if cat or tags or pur:
                node.category = cat
                node.tags = tags
                node.purpose = pur
                dirty = True
                updated += 1
        if dirty:
            save_elements(task, nodes)
            store.save(task)
    return updated


def dismiss_pending(
    session: ChatSession,
    pending_ids: list[str] | None = None,
    *,
    home: Path | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> int:
    """从待办移除；若提供 home，会先把分类/标签/作用写回任务要素。"""
    ff = dict(session.freeform or {})
    pending = list_pending(session)
    if not pending_ids:
        removing = list(pending)
        overlays = {
            str(e.get("pending_id") or "").strip(): e
            for e in (entries or [])
            if isinstance(e, dict) and str(e.get("pending_id") or "").strip()
        }
        if home is not None and removing:
            _persist_pending_meta_to_tasks(home, removing, overlays=overlays)
        ff["element_library_pending"] = []
        session.freeform = ff
        return len(removing)
    ids = {str(x).strip() for x in pending_ids if str(x).strip()}
    removing = [p for p in pending if str(p.get("pending_id") or "") in ids]
    kept = [p for p in pending if str(p.get("pending_id") or "") not in ids]
    overlays = {
        str(e.get("pending_id") or "").strip(): e
        for e in (entries or [])
        if isinstance(e, dict) and str(e.get("pending_id") or "").strip()
    }
    if home is not None and removing:
        _persist_pending_meta_to_tasks(home, removing, overlays=overlays)
    removed = len(pending) - len(kept)
    ff["element_library_pending"] = kept
    session.freeform = ff
    return removed


def pending_library_card(
    session: ChatSession,
    *,
    task_id: str = "",
    blueprint_id: str = "",
    home: Path | None = None,
) -> ChatCard | None:
    from report_writing.chat.session_documents import filter_pending_by_task, focus_document_ids

    pending = list_pending(session)
    tid = (task_id or "").strip()
    bid = (blueprint_id or "").strip()
    if not tid and not bid:
        focus_tid, focus_bid = focus_document_ids(session)
        tid, bid = focus_tid, focus_bid
    # 有焦点文档时只展示该文档相关待办，避免在 B 收入时带上 A 的要素
    if tid or bid:
        pending = filter_pending_by_task(pending, tid, blueprint_id=bid)
    # 已回链个人库的不再展示
    pending = [
        p
        for p in pending
        if not str((p.get("element") or {}).get("library_item_id") or "").strip()
    ]
    # 个人库已有同名 = 已收录，待办里隐藏
    if home is not None and pending:
        try:
            from report_writing.services.element_library import (
                list_library_items,
                normalize_element_name,
            )

            existing = {
                normalize_element_name(str(getattr(i, "name", "") or "")).casefold()
                for i in list_library_items(home)
                if normalize_element_name(str(getattr(i, "name", "") or ""))
            }
            if existing:
                pending = [
                    p
                    for p in pending
                    if normalize_element_name(
                        str((p.get("element") or {}).get("name") or "")
                    ).casefold()
                    not in existing
                ]
        except Exception:
            pass
    if not pending:
        return None
    return ChatCard(
        type="element_library_pending",
        title="对话里新增的要素",
        payload={
            "items": pending,
            "count": len(pending),
            "filter_task_id": tid,
            "filter_blueprint_id": bid,
            "hint": (
                "勾选要收入的要素；已入库的不展示。"
                "分类/标签/作用可选，新建时已填的会自动带入。"
                "暂不收录会从待办移除，章节上的要素配置会保留。"
            ),
        },
    )


def merge_cards_with_pending(
    session: ChatSession,
    cards: list[ChatCard],
    *,
    task_id: str = "",
    home: Path | None = None,
) -> list[ChatCard]:
    extra = pending_library_card(session, task_id=task_id, home=home)
    if extra is None:
        return list(cards or [])
    out = list(cards or [])
    if any(c.type == "element_library_pending" for c in out):
        return out
    out.append(extra)
    return out
