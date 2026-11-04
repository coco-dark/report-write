"""个人要素库 HTTP API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from report_writing.api.deps import task_root
from report_writing.domain.models import ElementLibraryItem

router = APIRouter(tags=["element_library"])


@router.get("/api/writing/element-library")
def get_element_library(robot_id: str | None = Query(None, description="对话机器人 id，限定个人要素范围")) -> dict:
    from report_writing.services.element_library import list_categories, list_tags
    from report_writing.services.robots import scoped_element_library_items

    home = task_root()
    items = scoped_element_library_items(home, robot_id)
    return {
        "items": [i.model_dump() for i in items],
        "categories": list_categories(home),
        "tags": list_tags(home),
    }


@router.put("/api/writing/element-library/items")
def put_element_library_items(body: dict) -> dict:
    from report_writing.services.element_library import save_library_items

    raw = body.get("items")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="需要 items 数组")
    items = [ElementLibraryItem.model_validate(x) for x in raw]
    saved = save_library_items(task_root(), items)
    return {"items": [i.model_dump() for i in saved]}


@router.post("/api/writing/element-library/items")
def post_element_library_item(body: dict) -> dict:
    from report_writing.services.element_library import upsert_library_item

    try:
        item = upsert_library_item(task_root(), body)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"item": item.model_dump()}


@router.delete("/api/writing/element-library/items/{item_id}")
def delete_element_library_item(item_id: str) -> dict:
    from report_writing.services.element_library import delete_library_item

    if not delete_library_item(task_root(), item_id):
        raise HTTPException(status_code=404, detail="要素不存在")
    return {"ok": True}


@router.post("/api/writing/element-library/link")
def post_element_library_link(body: dict) -> dict:
    from report_writing.services.element_library import link_library_items

    src = link_library_items(
        task_root(),
        str(body.get("source_id") or ""),
        str(body.get("target_id") or ""),
    )
    if src is None:
        raise HTTPException(status_code=400, detail="无法建立关联")
    return {"item": src.model_dump()}


@router.post("/api/writing/element-library/unlink")
def post_element_library_unlink(body: dict) -> dict:
    from report_writing.services.element_library import unlink_library_items

    unlink_library_items(
        task_root(),
        str(body.get("source_id") or ""),
        str(body.get("target_id") or ""),
    )
    return {"ok": True}


@router.get("/api/writing/element-library/check-name")
def get_check_element_name(
    name: str = "",
    category: str = "",
    tags: str = "",
) -> dict:
    """入库前校验要素名称+分类+标签是否已存在。tags 可用逗号分隔。"""
    from report_writing.services.element_library import (
        find_library_items_by_identity,
        normalize_element_name,
    )

    clean = normalize_element_name(name)
    if not clean:
        raise HTTPException(status_code=400, detail="要素名称不能为空")
    tag_list = [t.strip() for t in str(tags or "").split(",") if t.strip()]
    hits = find_library_items_by_identity(
        task_root(), clean, category=category, tags=tag_list
    )
    return {
        "name": clean,
        "category": str(category or "").strip(),
        "tags": tag_list,
        "exists": bool(hits),
        "items": [
            {
                "id": i.id,
                "name": i.name,
                "category": i.category,
                "tags": list(i.tags or []),
            }
            for i in hits
        ],
    }


@router.post("/api/writing/element-library/import-batch")
def post_import_batch(body: dict) -> dict:
    """从对话待确认或前端表单批量入库。名称+分类+标签重复默认拦截，force=true 时覆盖。"""
    from report_writing.chat.element_library_pending import dismiss_pending
    from report_writing.services.chat_session import get_session, save_session
    from report_writing.services.element_library import (
        find_category_exact,
        find_existing_tags,
        find_library_items_by_identity,
        library_item_from_element,
        link_task_element_to_library_item,
        normalize_element_name,
        upsert_library_item,
    )

    home = task_root()
    entries = body.get("entries")
    if not isinstance(entries, list) or not entries:
        raise HTTPException(status_code=400, detail="需要 entries")
    force = bool(body.get("force"))
    saved: list[dict] = []
    pending_ids: list[str] = []
    conflicts: list[dict] = []
    meta_hints: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        el = entry.get("element") or entry
        name = normalize_element_name(
            str(entry.get("name") or (el.get("name") if isinstance(el, dict) else "") or "")
        )
        if not name:
            raise HTTPException(status_code=400, detail="要素名称不能为空")
        category = str(entry.get("category") or "").strip()
        tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
        tags = [str(t).strip() for t in tags if str(t).strip()]
        existing_cat = find_category_exact(home, category)
        existing_tag_hits = find_existing_tags(home, tags)
        if existing_cat or existing_tag_hits:
            meta_hints.append(
                {
                    "pending_id": str(entry.get("pending_id") or ""),
                    "name": name,
                    "existing_category": existing_cat,
                    "existing_tags": existing_tag_hits,
                }
            )
        if existing_cat:
            category = existing_cat
        hits = find_library_items_by_identity(home, name, category=category, tags=tags)
        if hits and not force and not bool(entry.get("replace_existing")):
            conflicts.append(
                {
                    "pending_id": str(entry.get("pending_id") or ""),
                    "name": name,
                    "category": category,
                    "tags": tags,
                    "existing_id": hits[0].id,
                    "existing_name": hits[0].name,
                    "existing_category": hits[0].category,
                    "existing_tags": list(hits[0].tags or []),
                }
            )
            continue
        item = library_item_from_element(
            el,
            category=category,
            tags=tags,
            purpose=str(entry.get("purpose") or "").strip(),
        )
        item.name = name
        if hits and (force or bool(entry.get("replace_existing"))):
            item.id = hits[0].id
            item.created_at = hits[0].created_at
            item.link_ids = list(hits[0].link_ids or [])
        elif str(entry.get("id") or "").strip().startswith("lib-el-"):
            item.id = str(entry.get("id")).strip()
        saved_item = upsert_library_item(home, item)
        saved.append(saved_item.model_dump())
        # 回链任务要素，避免「库已改名、任务仍旧名」双份展示
        el_id = ""
        if isinstance(el, dict):
            el_id = str(el.get("id") or "").strip()
        else:
            el_id = str(getattr(el, "id", "") or "").strip()
        link_task_element_to_library_item(
            home,
            task_id=str(entry.get("task_id") or "").strip(),
            element_id=el_id,
            item=saved_item,
        )
        pid = str(entry.get("pending_id") or "").strip()
        if pid:
            pending_ids.append(pid)
    if conflicts and not saved and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "要素库已有相同名称+分类+标签的条目，请修改这三项后再收录",
                "conflicts": conflicts,
                "meta_hints": meta_hints,
            },
        )
    session_id = str(body.get("session_id") or "").strip()
    if session_id and pending_ids:
        session = get_session(home, session_id)
        if session is not None:
            dismiss_pending(session, pending_ids, home=home)
            save_session(home, session)
    return {
        "items": saved,
        "imported": len(saved),
        "conflicts": conflicts,
        "meta_hints": meta_hints,
    }


@router.post("/api/writing/element-library/dismiss-pending")
def post_dismiss_pending(body: dict) -> dict:
    from report_writing.chat.element_library_pending import dismiss_pending
    from report_writing.services.chat_session import get_session, save_session

    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="需要 session_id")
    home = task_root()
    session = get_session(home, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    ids = body.get("pending_ids")
    entries = body.get("entries") if isinstance(body.get("entries"), list) else None
    removed = dismiss_pending(
        session,
        ids if isinstance(ids, list) else None,
        home=home,
        entries=entries,
    )
    save_session(home, session)
    return {"removed": removed}
