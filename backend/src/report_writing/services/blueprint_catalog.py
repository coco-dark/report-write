"""蓝本目录：主表元数据 + writing_blueprint_document 子表 JSON。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from report_writing.domain.models import BlueprintSummary, WritingTask

_log = logging.getLogger(__name__)

# 无 MySQL 时（如 pytest REPORT_WRITING_DATABASE=file）仅用进程内存储，不写 task.json
_MEMORY_DOCS: dict[str, dict] = {}

_CATALOG_V = 1
_LIST_LIMIT = 200
_PARENT = "writing_blueprint"
_CHILD = "writing_blueprint_document"

_META_DDL = """
CREATE TABLE IF NOT EXISTS writing_blueprint (
    id varchar(128) NOT NULL PRIMARY KEY,
    user_id varchar(64) NOT NULL DEFAULT 'default',
    title varchar(512) NOT NULL DEFAULT '',
    category varchar(128) NOT NULL DEFAULT '',
    tags json,
    description text,
    enabled tinyint(1) NOT NULL DEFAULT 1,
    outline_confirmed tinyint(1) NOT NULL DEFAULT 0,
    chapter_count int NOT NULL DEFAULT 0,
    element_count int NOT NULL DEFAULT 0,
    updated_at varchar(32) NOT NULL,
    KEY writing_blueprint_updated_idx (updated_at),
    KEY writing_blueprint_user_idx (user_id)
)
"""
_CHILD_DDL = """
CREATE TABLE IF NOT EXISTS writing_blueprint_document (
    blueprint_id varchar(128) NOT NULL PRIMARY KEY,
    document json NOT NULL
)
"""
def _ensure_schema(connection) -> None:
    from report_writing.infra.json_tables import migrate_legacy_document_column, table_columns

    connection.execute(_CHILD_DDL)
    cols = table_columns(connection, _PARENT)
    if not cols:
        connection.execute(_META_DDL)
        return
    if cols and "user_id" not in cols:
        try:
            connection.execute(
                f"ALTER TABLE {_PARENT} ADD COLUMN user_id varchar(64) NOT NULL DEFAULT 'default'"
            )
        except Exception:
            pass
    if "title" not in cols:
        for stmt in (
            "ADD COLUMN title varchar(512) NOT NULL DEFAULT ''",
            "ADD COLUMN category varchar(128) NOT NULL DEFAULT ''",
            "ADD COLUMN tags json",
            "ADD COLUMN description text",
            "ADD COLUMN enabled tinyint(1) NOT NULL DEFAULT 1",
            "ADD COLUMN outline_confirmed tinyint(1) NOT NULL DEFAULT 0",
            "ADD COLUMN chapter_count int NOT NULL DEFAULT 0",
            "ADD COLUMN element_count int NOT NULL DEFAULT 0",
        ):
            try:
                connection.execute(f"ALTER TABLE {_PARENT} {stmt}")
            except Exception:
                pass

    def _on_migrate(parent_id: str, doc: dict, updated_at: str) -> None:
        if doc.get("_catalog_v"):
            meta = {
                "id": str(doc.get("id") or parent_id),
                "title": str(doc.get("title") or parent_id),
                "category": str(doc.get("category") or ""),
                "tags": list(doc.get("tags") or []),
                "description": str(doc.get("description") or ""),
                "enabled": bool(doc.get("enabled", True)),
                "outline_confirmed": bool(doc.get("outline_confirmed")),
                "chapter_count": int(doc.get("chapter_count") or 0),
                "element_count": int(doc.get("element_count") or 0),
                "updated_at": updated_at or str(doc.get("updated_at") or ""),
            }
        else:
            try:
                task = WritingTask.from_dict(doc)
            except Exception:
                return
            meta = _meta_from_task(task, updated_at)
        _upsert_meta(connection, meta)

    migrate_legacy_document_column(connection, _PARENT, _CHILD, "blueprint_id", on_row=_on_migrate)
    from report_writing.infra.json_tables import drop_legacy_document_column

    drop_legacy_document_column(connection, _PARENT)


def _memory_mode() -> bool:
    from report_writing.infra.postgres import load_database_settings

    return load_database_settings() is None


def clear_blueprint_memory_store() -> None:
    _MEMORY_DOCS.clear()


def catalog_available() -> bool:
    if _memory_mode():
        return True
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return False
    try:
        with _connect(settings) as connection:
            _ensure_schema(connection)
            connection.execute(f"SELECT 1 FROM {_PARENT} LIMIT 1")
        return True
    except Exception as error:
        _log.warning("蓝本目录不可用（需 MySQL）：%s", error)
        return False


def save_blueprint_task(task: WritingTask) -> None:
    if str(task.id or "").startswith("w"):
        return
    if _memory_mode():
        doc = _catalog_document(task)
        doc["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        _MEMORY_DOCS[str(task.id)] = doc
        try:
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(str(task.id))
        except Exception:
            pass
        return
    if not catalog_available():
        raise RuntimeError("数据库不可用，无法保存蓝本任务")
    from report_writing.infra.json_tables import upsert_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return
    updated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = _catalog_document(task)
    doc["updated_at"] = updated_at
    meta = _meta_from_task(task, updated_at)
    with _connect(settings) as connection:
        _ensure_schema(connection)
        _upsert_meta(connection, meta)
        upsert_document_child(connection, _CHILD, "blueprint_id", task.id, doc)
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(str(task.id))
    except Exception:
        pass


def delete_blueprint_task(blueprint_id: str) -> None:
    if _memory_mode():
        _MEMORY_DOCS.pop((blueprint_id or "").strip(), None)
        try:
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(blueprint_id)
        except Exception:
            pass
        return
    if not catalog_available():
        return
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(f"DELETE FROM {_CHILD} WHERE blueprint_id = %s", (blueprint_id,))
        connection.execute(f"DELETE FROM {_PARENT} WHERE id = %s", (blueprint_id,))
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(blueprint_id)
    except Exception:
        pass


def _blueprint_owner_id(explicit: str | None = None) -> str:
    from report_writing.services.users import current_user_id, normalize_owner_id

    return normalize_owner_id(explicit or current_user_id())


def list_blueprint_summaries(*, include_disabled: bool = True) -> list[BlueprintSummary]:
    if _memory_mode():
        found: list[BlueprintSummary] = []
        uid = _blueprint_owner_id()
        for bid, data in sorted(
            _MEMORY_DOCS.items(),
            key=lambda item: str(item[1].get("updated_at") or ""),
            reverse=True,
        ):
            if str(bid).startswith("w"):
                continue
            try:
                task = WritingTask.from_dict(data)
            except Exception:
                continue
            owner = (getattr(task, "user_id", "") or "").strip() or uid
            if owner != uid:
                continue
            enabled = bool(getattr(task, "enabled", True))
            if not include_disabled and not enabled:
                continue
            chapters = task.outline.chapters if task.outline else []
            mtime = str(data.get("updated_at") or "")[:16].replace("T", " ")
            found.append(
                BlueprintSummary(
                    id=str(bid),
                    title=(task.title or "").strip() or str(bid),
                    category=task.category or "",
                    tags=list(task.tags or []),
                    description=task.description or "",
                    chapter_count=len(chapters),
                    element_count=len(task.element_nodes or []),
                    outline_confirmed=bool(task.outline_confirmed),
                    enabled=enabled,
                    updated_at=mtime or "—",
                )
            )
        return found[:_LIST_LIMIT]
    if not catalog_available():
        return []
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = _blueprint_owner_id()
    settings = load_database_settings()
    if settings is None:
        return []
    try:
        with _connect(settings) as connection:
            _ensure_schema(connection)
            rows = connection.execute(
                f"""
                SELECT id, title, category, tags, description, enabled, outline_confirmed,
                       chapter_count, element_count, updated_at
                FROM {_PARENT}
                WHERE user_id = %s AND id NOT LIKE 'w%%'
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (uid, _LIST_LIMIT),
            ).fetchall()
        found: list[BlueprintSummary] = []
        for row in rows or []:
            enabled = bool(row[5])
            if not include_disabled and not enabled:
                continue
            tags_raw = row[3]
            tags = _tags_list(tags_raw)
            mtime = str(row[9] or "")[:16].replace("T", " ")
            found.append(
                BlueprintSummary(
                    id=str(row[0]),
                    title=str(row[1] or row[0]),
                    category=str(row[2] or ""),
                    tags=tags,
                    description=str(row[4] or ""),
                    chapter_count=int(row[7] or 0),
                    element_count=int(row[8] or 0),
                    outline_confirmed=bool(row[6]),
                    enabled=enabled,
                    updated_at=mtime or "—",
                )
            )
        return found
    except Exception as error:
        _log.warning("读取蓝本目录失败：%s", error)
        return []


def sync_blueprints_from_disk(tasks_root: Path) -> int:
    if not catalog_available():
        return 0
    if not tasks_root.is_dir():
        return 0
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is not None:
        try:
            with _connect(settings) as connection:
                _ensure_schema(connection)
                connection.execute(f"DELETE FROM {_CHILD} WHERE blueprint_id LIKE 'w%%'")
                connection.execute(f"DELETE FROM {_PARENT} WHERE id LIKE 'w%%'")
        except Exception:
            pass
    saved = 0
    for path in tasks_root.glob("*/task.json"):
        try:
            task = WritingTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        if str(task.id or "").startswith("w"):
            continue
        try:
            save_blueprint_task(task)
            saved += 1
        except Exception:
            continue
    return saved


def _upsert_meta(connection, meta: dict) -> None:
    connection.execute(
        f"""
        INSERT INTO {_PARENT} (
            id, user_id, title, category, tags, description, enabled, outline_confirmed,
            chapter_count, element_count, updated_at
        )
        VALUES (%s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s, %s, %s, %s) AS incoming
        ON DUPLICATE KEY UPDATE
            user_id = incoming.user_id,
            title = incoming.title,
            category = incoming.category,
            tags = incoming.tags,
            description = incoming.description,
            enabled = incoming.enabled,
            outline_confirmed = incoming.outline_confirmed,
            chapter_count = incoming.chapter_count,
            element_count = incoming.element_count,
            updated_at = incoming.updated_at
        """,
        (
            meta["id"],
            meta.get("user_id") or _blueprint_owner_id(),
            meta["title"],
            meta["category"],
            json.dumps(meta["tags"], ensure_ascii=False),
            meta["description"],
            1 if meta["enabled"] else 0,
            1 if meta["outline_confirmed"] else 0,
            meta["chapter_count"],
            meta["element_count"],
            meta["updated_at"],
        ),
    )


def _meta_from_task(task: WritingTask, updated_at: str) -> dict:
    chapters = task.outline.chapters if task.outline else []
    owner = (getattr(task, "user_id", "") or "").strip() or _blueprint_owner_id()
    return {
        "id": task.id,
        "user_id": owner,
        "title": (task.title or "").strip() or task.id,
        "category": task.category or "",
        "tags": list(task.tags or []),
        "description": task.description or "",
        "enabled": bool(getattr(task, "enabled", True)),
        "outline_confirmed": bool(task.outline_confirmed),
        "chapter_count": len(chapters),
        "element_count": len(task.element_nodes or []),
        "updated_at": updated_at,
    }


def _catalog_document(task: WritingTask) -> dict:
    """子表 document：完整 WritingTask JSON（持久化真值），兼带 _catalog_v。"""
    doc = task.to_dict()
    doc["_catalog_v"] = _CATALOG_V
    return doc


def load_blueprint_task(blueprint_id: str) -> WritingTask | None:
    """读取蓝本完整任务（MySQL 或测试用内存）；不存在时返回 None。"""
    bid = (blueprint_id or "").strip()
    if not bid or bid.startswith("w"):
        return None
    if _memory_mode():
        data = _MEMORY_DOCS.get(bid)
        if not data:
            return None
        try:
            return WritingTask.from_dict(data)
        except Exception:
            return None
    if not catalog_available():
        return None
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return None
    try:
        with _connect(settings) as connection:
            _ensure_schema(connection)
            row = connection.execute(
                f"SELECT document FROM {_CHILD} WHERE blueprint_id = %s",
                (bid,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        raw = row[0]
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            return None
        if data.get("_catalog_v") and "outline" not in data:
            return None
        return WritingTask.from_dict(data)
    except Exception as error:
        _log.debug("load_blueprint_task %s: %s", bid, error)
        return None


def normalize_blueprint_title(title: str) -> str:
    return " ".join(str(title or "").strip().split())


def find_blueprints_by_title(
    title: str,
    *,
    include_disabled: bool = True,
) -> list[BlueprintSummary]:
    """按蓝本名称精确匹配（忽略首尾空白与连续空格，大小写不敏感）。"""
    key = normalize_blueprint_title(title).casefold()
    if not key:
        return []
    return [
        item
        for item in list_blueprint_summaries(include_disabled=include_disabled)
        if normalize_blueprint_title(item.title).casefold() == key
    ]


def _tags_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]
