"""个人要素库：按用户工作区 JSON 存储。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from report_writing.domain.models import ElementLibraryItem, ElementNode
from report_writing.services.users import current_user_id, user_workspace


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _library_path(home: Path) -> Path:
    ws = user_workspace(home, current_user_id())
    root = ws / "element_library"
    root.mkdir(parents=True, exist_ok=True)
    return root / "items.json"


def _load_raw(home: Path) -> list[dict[str, Any]]:
    path = _library_path(home)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return list(data["items"])
    if isinstance(data, list):
        return data
    return []


def _save_raw(home: Path, items: list[dict[str, Any]]) -> None:
    path = _library_path(home)
    path.write_text(
        json.dumps({"items": items, "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_library_items(home: Path) -> list[ElementLibraryItem]:
    out: list[ElementLibraryItem] = []
    for raw in _load_raw(home):
        try:
            out.append(ElementLibraryItem.model_validate(raw))
        except Exception:
            continue
    return out


def save_library_items(
    home: Path,
    items: list[ElementLibraryItem],
    *,
    sync_tasks: bool = True,
) -> list[ElementLibraryItem]:
    normalized: list[ElementLibraryItem] = []
    for item in items:
        node = item if isinstance(item, ElementLibraryItem) else ElementLibraryItem.model_validate(item)
        if not (node.id or "").strip():
            node.id = f"lib-el-{uuid.uuid4().hex[:12]}"
        if not node.created_at:
            node.created_at = _now()
        node.updated_at = _now()
        normalized.append(node)
    _save_raw(home, [i.model_dump() for i in normalized])
    if sync_tasks:
        try:
            sync_library_items_to_tasks(home, normalized)
        except Exception:
            pass
    return normalized


def upsert_library_item(home: Path, item: ElementLibraryItem | dict[str, Any]) -> ElementLibraryItem:
    node = item if isinstance(item, ElementLibraryItem) else ElementLibraryItem.model_validate(item)
    items = list_library_items(home)
    if not (node.id or "").strip():
        node.id = f"lib-el-{uuid.uuid4().hex[:12]}"
    found = False
    merged: list[ElementLibraryItem] = []
    for existing in items:
        if existing.id == node.id:
            merged.append(node)
            found = True
        else:
            merged.append(existing)
    if not found:
        if not node.created_at:
            node.created_at = _now()
        merged.append(node)
    node.updated_at = _now()
    # 整库重写时只同步本条，避免每次 upsert 扫全库
    _save_raw(home, [i.model_dump() for i in merged])
    try:
        sync_library_items_to_tasks(home, [node])
    except Exception:
        pass
    return node


def delete_library_item(home: Path, item_id: str) -> bool:
    iid = (item_id or "").strip()
    if not iid:
        return False
    items = list_library_items(home)
    kept = [i for i in items if i.id != iid]
    for item in kept:
        item.link_ids = [x for x in (item.link_ids or []) if x != iid]
    if len(kept) == len(items):
        return False
    save_library_items(home, kept, sync_tasks=False)
    return True


def link_library_items(home: Path, source_id: str, target_id: str) -> ElementLibraryItem | None:
    sid, tid = (source_id or "").strip(), (target_id or "").strip()
    if not sid or not tid or sid == tid:
        return None
    items = list_library_items(home)
    by_id = {i.id: i for i in items}
    src = by_id.get(sid)
    tgt = by_id.get(tid)
    if src is None or tgt is None:
        return None
    if tid not in (src.link_ids or []):
        src.link_ids = list(src.link_ids or []) + [tid]
    if sid not in (tgt.link_ids or []):
        tgt.link_ids = list(tgt.link_ids or []) + [sid]
    save_library_items(home, list(by_id.values()), sync_tasks=False)
    return src


def unlink_library_items(home: Path, source_id: str, target_id: str) -> None:
    sid, tid = (source_id or "").strip(), (target_id or "").strip()
    items = list_library_items(home)
    for item in items:
        if item.id in {sid, tid}:
            item.link_ids = [x for x in (item.link_ids or []) if x not in {sid, tid}]
    save_library_items(home, items, sync_tasks=False)


def library_item_from_element(
    element: ElementNode | dict[str, Any],
    *,
    category: str = "",
    tags: list[str] | None = None,
    purpose: str = "",
) -> ElementLibraryItem:
    if isinstance(element, ElementNode):
        data = element.model_dump()
    else:
        data = dict(element or {})
    kv_raw = data.get("kv_pairs") or []
    return ElementLibraryItem(
        id=f"lib-el-{uuid.uuid4().hex[:12]}",
        name=str(data.get("name") or "").strip() or "未命名要素",
        kind=str(data.get("kind") or "kv").strip() or "kv",
        value=str(data.get("value") or "").strip(),
        kv_pairs=list(kv_raw),
        query=str(data.get("query") or "").strip(),
        prompt=str(data.get("prompt") or "").strip(),
        ref_key=str(data.get("ref_key") or "").strip(),
        ref_kind=str(data.get("ref_kind") or "element").strip() or "element",
        ref_field=str(data.get("ref_field") or "result").strip() or "result",
        material_files=[str(x).strip() for x in (data.get("material_files") or []) if str(x).strip()],
        knowledge_folder_ids=[
            str(x).strip() for x in (data.get("knowledge_folder_ids") or []) if str(x).strip()
        ],
        knowledge_scope=str(data.get("knowledge_scope") or "both").strip() or "both",
        required=bool(data.get("required", True)),
        category=(category or "").strip(),
        tags=[str(t).strip() for t in (tags or []) if str(t).strip()],
        purpose=(purpose or "").strip(),
        link_ids=[],
        created_at=_now(),
        updated_at=_now(),
    )


def element_content_fingerprint(obj: ElementLibraryItem | ElementNode | dict[str, Any]) -> str:
    """内容指纹：改名后仍能把任务克隆与库条目对上（优先 query/prompt/value，其次名称）。"""
    if isinstance(obj, (ElementLibraryItem, ElementNode)):
        data = obj.model_dump()
    else:
        data = dict(obj or {})
    kind = str(data.get("kind") or "kv").strip() or "kv"
    if kind == "rag_search":
        q = normalize_element_name(str(data.get("query") or ""))
        return f"rag|{q}" if q else f"rag-name|{normalize_element_name(str(data.get('name') or ''))}"
    if kind == "llm_generate":
        p = " ".join(str(data.get("prompt") or "").split())[:240].casefold()
        return f"llm|{p}" if p else f"llm-name|{normalize_element_name(str(data.get('name') or ''))}"
    if kind == "node_ref":
        return (
            f"ref|{str(data.get('ref_kind') or '')}|{str(data.get('ref_key') or '')}|"
            f"{str(data.get('ref_field') or '')}"
        )
    val = str(data.get("value") or "").strip()
    kv_parts: list[str] = []
    for row in data.get("kv_pairs") or []:
        if isinstance(row, dict):
            kv_parts.append(f"{row.get('key', '')}={row.get('value', '')}")
        else:
            kv_parts.append(
                f"{getattr(row, 'key', '')}={getattr(row, 'value', '')}"
            )
    kv = "|".join(kv_parts)
    body = normalize_element_name(f"{val}|{kv}")
    if body and body != "|":
        return f"kv|{body}"
    return f"kv-name|{normalize_element_name(str(data.get('name') or ''))}"


def link_task_element_to_library_item(
    home: Path,
    *,
    task_id: str,
    element_id: str,
    item: ElementLibraryItem | dict[str, Any],
) -> bool:
    """收录后回写任务要素的 library_item_id，并用库定义覆盖可同步字段。"""
    tid = (task_id or "").strip()
    eid = (element_id or "").strip()
    if not tid or not eid:
        return False
    lib = item if isinstance(item, ElementLibraryItem) else ElementLibraryItem.model_validate(item)
    from report_writing.domain.elements import save_elements
    from report_writing.services.store import TaskStore

    directory = Path(home) / "tasks" / tid
    try:
        store = TaskStore(directory)
        task = store.load()
    except Exception:
        return False
    nodes = list(task.element_nodes or [])
    hit = None
    for node in nodes:
        if str(getattr(node, "id", "") or "").strip() == eid:
            hit = node
            break
    if hit is None:
        return False
    apply_library_item_to_element(hit, lib)
    try:
        save_elements(task, nodes)
        store.save(task)
    except Exception:
        return False
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(tid)
    except Exception:
        pass
    return True


def relink_task_elements_by_content(home: Path, task_id: str = "") -> int:
    """按内容指纹给未回链的任务要素补 library_item_id，并同步库定义。"""
    lib_items = list_library_items(home)
    by_fp: dict[str, list[ElementLibraryItem]] = {}
    for item in lib_items:
        fp = element_content_fingerprint(item)
        if not fp or fp.endswith("|"):
            continue
        by_fp.setdefault(fp, []).append(item)

    tids = [task_id] if (task_id or "").strip() else _owned_task_ids(home)
    from report_writing.domain.elements import save_elements
    from report_writing.services.store import TaskStore

    updated = 0
    for tid in tids:
        tid = (tid or "").strip()
        if not tid:
            continue
        directory = Path(home) / "tasks" / tid
        try:
            store = TaskStore(directory)
            task = store.load()
        except Exception:
            continue
        nodes = list(task.element_nodes or [])
        if not nodes:
            continue
        dirty = False
        for el in nodes:
            if (getattr(el, "library_item_id", "") or "").strip():
                continue
            fp = element_content_fingerprint(el)
            cands = by_fp.get(fp) or []
            if len(cands) != 1:
                # 名称兜底：仅当库中同名唯一
                name_key = normalize_element_name(str(getattr(el, "name", "") or "")).casefold()
                if not name_key:
                    continue
                name_hits = [
                    i
                    for i in lib_items
                    if normalize_element_name(i.name).casefold() == name_key
                ]
                if len(name_hits) != 1:
                    continue
                cands = name_hits
            if apply_library_item_to_element(el, cands[0]):
                dirty = True
                updated += 1
        if not dirty:
            continue
        try:
            save_elements(task, nodes)
            store.save(task)
        except Exception:
            continue
        try:
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(tid)
        except Exception:
            pass
    return updated


def list_categories(home: Path) -> list[str]:
    cats: set[str] = set()
    for item in list_library_items(home):
        c = (item.category or "").strip()
        if c:
            cats.add(c)
    return sorted(cats)


def normalize_element_name(name: str) -> str:
    return " ".join(str(name or "").strip().split())


def normalize_tag_set(tags: list[str] | None) -> frozenset[str]:
    return frozenset(
        str(t).strip().casefold() for t in (tags or []) if str(t or "").strip()
    )


def find_library_items_by_name(home: Path, name: str) -> list[ElementLibraryItem]:
    """按要素名称精确匹配（忽略首尾空白与连续空格，大小写不敏感）。"""
    key = normalize_element_name(name).casefold()
    if not key:
        return []
    return [
        item
        for item in list_library_items(home)
        if normalize_element_name(item.name).casefold() == key
    ]


def find_library_items_by_identity(
    home: Path,
    name: str,
    *,
    category: str = "",
    tags: list[str] | None = None,
) -> list[ElementLibraryItem]:
    """按名称 + 分类 + 标签集合查重（大小写不敏感，标签顺序无关）。"""
    key = normalize_element_name(name).casefold()
    if not key:
        return []
    cat = str(category or "").strip().casefold()
    tagset = normalize_tag_set(tags)
    return [
        item
        for item in list_library_items(home)
        if normalize_element_name(item.name).casefold() == key
        and str(getattr(item, "category", "") or "").strip().casefold() == cat
        and normalize_tag_set(getattr(item, "tags", None)) == tagset
    ]


def find_category_exact(home: Path, category: str) -> str:
    """若分类已存在，返回库中原样写法；否则空串。"""
    want = str(category or "").strip()
    if not want:
        return ""
    for c in list_categories(home):
        if c.casefold() == want.casefold():
            return c
    return ""


def find_existing_tags(home: Path, tags: list[str] | None) -> list[str]:
    """返回 tags 里已在库中出现过的标签（用库中原样写法）。"""
    known = {t.casefold(): t for t in list_tags(home)}
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        t = str(raw or "").strip()
        if not t:
            continue
        hit = known.get(t.casefold())
        if hit and hit.casefold() not in seen:
            seen.add(hit.casefold())
            out.append(hit)
    return out


def apply_library_item_to_element(element: ElementNode, item: ElementLibraryItem | dict[str, Any]) -> bool:
    """把库条目定义写回任务要素（保留 id / result / library_item_id）。有变更返回 True。"""
    lib = item if isinstance(item, ElementLibraryItem) else ElementLibraryItem.model_validate(item)
    changed = False

    def _set(attr: str, value: Any) -> None:
        nonlocal changed
        if getattr(element, attr, None) != value:
            setattr(element, attr, value)
            changed = True

    def _dump_list(raw: Any) -> list[Any]:
        out: list[Any] = []
        for row in list(raw or []):
            if hasattr(row, "model_dump"):
                out.append(row.model_dump())
            elif isinstance(row, dict):
                out.append(dict(row))
            else:
                out.append(row)
        return out

    _set("name", (lib.name or "").strip() or element.name)
    _set("kind", (lib.kind or "kv").strip() or "kv")
    _set("value", lib.value or "")
    new_kv = list(lib.kv_pairs or [])
    if _dump_list(getattr(element, "kv_pairs", None)) != _dump_list(new_kv):
        element.kv_pairs = new_kv
        changed = True
    _set("query", lib.query or "")
    _set("prompt", lib.prompt or "")
    _set("ref_key", lib.ref_key or "")
    _set("ref_kind", (lib.ref_kind or "element").strip() or "element")
    _set("ref_field", (lib.ref_field or "result").strip() or "result")
    new_slots = list(getattr(lib, "file_slots", None) or [])
    if _dump_list(getattr(element, "file_slots", None)) != _dump_list(new_slots):
        element.file_slots = new_slots
        changed = True
    new_attached = list(getattr(lib, "files", None) or [])
    if _dump_list(getattr(element, "files", None)) != _dump_list(new_attached):
        element.files = new_attached
        changed = True
    new_files = [str(x).strip() for x in (lib.material_files or []) if str(x).strip()]
    if [str(x) for x in (getattr(element, "material_files", None) or [])] != new_files:
        element.material_files = new_files
        changed = True
    new_folders = [str(x).strip() for x in (lib.knowledge_folder_ids or []) if str(x).strip()]
    if [str(x) for x in (getattr(element, "knowledge_folder_ids", None) or [])] != new_folders:
        element.knowledge_folder_ids = new_folders
        changed = True
    _set("knowledge_scope", str(getattr(lib, "knowledge_scope", None) or "both").strip() or "both")
    _set("required", bool(lib.required))
    _set("category", (lib.category or "").strip())
    new_tags = [str(t).strip() for t in (lib.tags or []) if str(t).strip()]
    if [str(t) for t in (getattr(element, "tags", None) or [])] != new_tags:
        element.tags = new_tags
        changed = True
    _set("purpose", (lib.purpose or "").strip())
    lid = (lib.id or "").strip()
    if lid and (getattr(element, "library_item_id", "") or "").strip() != lid:
        element.library_item_id = lid
        changed = True
    return changed


def task_element_from_library_item(item: ElementLibraryItem | dict[str, Any]) -> ElementNode:
    """克隆个人库条目为任务内要素（新 id，保留 library_item_id）。"""
    from report_writing.domain.elements import new_element_id
    from report_writing.domain.models import ElementNode

    lib = item if isinstance(item, ElementLibraryItem) else ElementLibraryItem.model_validate(item)
    node = ElementNode(id=new_element_id(), library_item_id=lib.id, result="")
    apply_library_item_to_element(node, lib)
    return node


def _owned_task_ids(home: Path) -> list[str]:
    """当前用户工作区内可能挂有库克隆的任务 / 蓝本 id。"""
    seen: set[str] = set()
    out: list[str] = []

    def _add(tid: str) -> None:
        tid = (tid or "").strip()
        if not tid or tid in seen:
            return
        seen.add(tid)
        out.append(tid)

    tasks_root = Path(home) / "tasks"
    if tasks_root.is_dir():
        for path in tasks_root.iterdir():
            if path.is_dir():
                _add(path.name)

    try:
        from report_writing.services.users import current_user_id, normalize_owner_id
        from report_writing.services.writing_task_catalog import catalog_available as writing_catalog_available

        uid = normalize_owner_id(current_user_id())
        if writing_catalog_available():
            from report_writing.infra.postgres import _connect, load_database_settings

            settings = load_database_settings()
            if settings is not None:
                with _connect(settings) as connection:
                    rows = connection.execute(
                        "SELECT id FROM writing_task WHERE user_id = %s LIMIT 500",
                        (uid,),
                    ).fetchall()
                for row in rows or []:
                    _add(str(row[0]))
    except Exception:
        pass

    try:
        from report_writing.controller.blueprint import list_blueprints

        for summary in list_blueprints(Path(home) / "tasks", include_disabled=True) or []:
            _add(str(getattr(summary, "id", "") or ""))
    except Exception:
        pass
    return out


def sync_library_items_to_tasks(home: Path, items: list[ElementLibraryItem]) -> int:
    """将库条目定义同步到所有带 library_item_id 的任务要素。返回更新的要素个数。"""
    by_id = {
        (item.id or "").strip(): item
        for item in items
        if isinstance(item, ElementLibraryItem) and (item.id or "").strip()
    }
    if not by_id:
        return 0

    from report_writing.domain.elements import save_elements
    from report_writing.services.store import TaskStore

    updated = 0
    for tid in _owned_task_ids(home):
        directory = Path(home) / "tasks" / tid
        try:
            task = TaskStore(directory).load()
        except Exception:
            continue
        nodes = list(getattr(task, "element_nodes", None) or [])
        if not nodes:
            continue
        changed = False
        for el in nodes:
            lid = (getattr(el, "library_item_id", "") or "").strip()
            lib = by_id.get(lid)
            if lib is None:
                continue
            if apply_library_item_to_element(el, lib):
                changed = True
                updated += 1
        if not changed:
            continue
        try:
            save_elements(task, nodes)
            TaskStore(directory).save(task)
        except Exception:
            continue
        try:
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(tid)
        except Exception:
            pass
    return updated


def list_tags(home: Path) -> list[str]:
    tags: set[str] = set()
    for item in list_library_items(home):
        for t in item.tags or []:
            t = str(t).strip()
            if t:
                tags.add(t)
    return sorted(tags)


def get_library_item(home: Path, item_id: str) -> ElementLibraryItem | None:
    iid = str(item_id or "").strip()
    if not iid:
        return None
    for item in list_library_items(home):
        if item.id == iid:
            return item
    return None


def apply_element_to_library_item(element: ElementNode, item: ElementLibraryItem) -> bool:
    """把任务内要素定义写回库条目（保留 id / link_ids / created_at）。有变更返回 True。"""
    changed = False

    def _set(attr: str, value: Any) -> None:
        nonlocal changed
        if getattr(item, attr, None) != value:
            setattr(item, attr, value)
            changed = True

    def _dump_list(raw: Any) -> list[Any]:
        out: list[Any] = []
        for row in list(raw or []):
            if hasattr(row, "model_dump"):
                out.append(row.model_dump())
            elif isinstance(row, dict):
                out.append(dict(row))
            else:
                out.append(row)
        return out

    _set("name", (element.name or "").strip() or item.name)
    _set("kind", (element.kind or "kv").strip() or "kv")
    _set("value", element.value or "")
    new_kv = list(getattr(element, "kv_pairs", None) or [])
    if _dump_list(getattr(item, "kv_pairs", None)) != _dump_list(new_kv):
        item.kv_pairs = new_kv
        changed = True
    _set("query", element.query or "")
    _set("prompt", element.prompt or "")
    _set("ref_key", element.ref_key or "")
    _set("ref_kind", (element.ref_kind or "element").strip() or "element")
    _set("ref_field", (element.ref_field or "result").strip() or "result")
    new_slots = list(getattr(element, "file_slots", None) or [])
    if _dump_list(getattr(item, "file_slots", None)) != _dump_list(new_slots):
        item.file_slots = new_slots
        changed = True
    new_attached = list(getattr(element, "files", None) or [])
    if _dump_list(getattr(item, "files", None)) != _dump_list(new_attached):
        item.files = new_attached
        changed = True
    new_files = [str(x).strip() for x in (getattr(element, "material_files", None) or []) if str(x).strip()]
    if [str(x) for x in (getattr(item, "material_files", None) or [])] != new_files:
        item.material_files = new_files
        changed = True
    new_folders = [
        str(x).strip() for x in (getattr(element, "knowledge_folder_ids", None) or []) if str(x).strip()
    ]
    if [str(x) for x in (getattr(item, "knowledge_folder_ids", None) or [])] != new_folders:
        item.knowledge_folder_ids = new_folders
        changed = True
    _set(
        "knowledge_scope",
        str(getattr(element, "knowledge_scope", None) or "both").strip() or "both",
    )
    _set("required", bool(getattr(element, "required", True)))
    _set("category", (getattr(element, "category", "") or "").strip())
    new_tags = [str(t).strip() for t in (getattr(element, "tags", None) or []) if str(t).strip()]
    if [str(t) for t in (getattr(item, "tags", None) or [])] != new_tags:
        item.tags = new_tags
        changed = True
    _set("purpose", (getattr(element, "purpose", "") or "").strip())
    return changed


def sync_task_elements_to_library(home: Path, elements: list[ElementNode]) -> int:
    """任务内带 library_item_id 的要素保存后，回写个人要素库。

    经 upsert 后会同步到其他引用同一库条目的任务（与库侧编辑对称）。
    """
    if not elements:
        return 0
    by_id: dict[str, ElementLibraryItem] = {
        (it.id or "").strip(): it for it in list_library_items(home) if (it.id or "").strip()
    }
    pending: list[ElementLibraryItem] = []
    seen: set[str] = set()
    for el in elements:
        lid = str(getattr(el, "library_item_id", "") or "").strip()
        if not lid or lid in seen:
            continue
        item = by_id.get(lid)
        if item is None:
            continue
        if apply_element_to_library_item(el, item):
            pending.append(item)
            by_id[lid] = item
            seen.add(lid)
    if not pending:
        return 0
    for item in pending:
        upsert_library_item(home, item)
    return len(pending)
