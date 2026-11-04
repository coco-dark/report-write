"""对话写作任务（id 以 w 开头）：MySQL 主表 + document 子表存完整 WritingTask。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from report_writing.domain.models import WritingTask

_log = logging.getLogger(__name__)

_DOC_V = 1
_LIST_LIMIT = 500
_PARENT = "writing_task"
_CHILD = "writing_task_document"
_schema_ready = False

_META_DDL = """
CREATE TABLE IF NOT EXISTS writing_task (
    id varchar(128) NOT NULL PRIMARY KEY,
    user_id varchar(64) NOT NULL DEFAULT 'default',
    blueprint_id varchar(128) NOT NULL DEFAULT '',
    title varchar(512) NOT NULL DEFAULT '',
    status varchar(64) NOT NULL DEFAULT 'studying',
    updated_at varchar(32) NOT NULL,
    KEY writing_task_user_idx (user_id),
    KEY writing_task_updated_idx (updated_at),
    KEY writing_task_blueprint_idx (blueprint_id)
)
"""
_CHILD_DDL = """
CREATE TABLE IF NOT EXISTS writing_task_document (
    task_id varchar(128) NOT NULL PRIMARY KEY,
    document json NOT NULL
)
"""


def is_writing_task_id(task_id: str) -> bool:
    return str(task_id or "").strip().startswith("w")


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    from report_writing.infra.json_tables import table_columns

    connection.execute(_CHILD_DDL)
    cols = table_columns(connection, _PARENT)
    if not cols:
        connection.execute(_META_DDL)
        _schema_ready = True
        return
    if "user_id" not in cols:
        try:
            connection.execute(
                f"ALTER TABLE {_PARENT} ADD COLUMN user_id varchar(64) NOT NULL DEFAULT 'default'"
            )
        except Exception:
            pass
    for stmt in (
        "ADD COLUMN blueprint_id varchar(128) NOT NULL DEFAULT ''",
        "ADD COLUMN title varchar(512) NOT NULL DEFAULT ''",
        "ADD COLUMN status varchar(64) NOT NULL DEFAULT 'studying'",
    ):
        col = stmt.split()[2]
        if col not in cols:
            try:
                connection.execute(f"ALTER TABLE {_PARENT} {stmt}")
            except Exception:
                pass
    _schema_ready = True


def catalog_available() -> bool:
    from report_writing.infra.postgres import _connect, load_database_settings, mysql_available

    if not mysql_available():
        return False
    if _schema_ready:
        return True
    settings = load_database_settings()
    if settings is None:
        return False
    try:
        with _connect(settings) as connection:
            _ensure_schema(connection)
        return True
    except Exception as error:
        _log.warning("写作任务改用本地 task.json：%s", error)
        return False


def _owner_id(task: WritingTask) -> str:
    from report_writing.services.users import current_user_id, normalize_owner_id

    return normalize_owner_id((getattr(task, "user_id", "") or "").strip() or current_user_id())


def _meta_from_task(task: WritingTask, updated_at: str) -> dict:
    status = getattr(task.status, "value", None) or str(task.status or "studying")
    blueprint_id = str(getattr(task, "source_blueprint_id", "") or "").strip()
    if not blueprint_id:
        desc = (task.description or "").strip()
        if desc.startswith("从蓝本 "):
            parts = desc.split()
            if len(parts) >= 2:
                blueprint_id = parts[1].strip()
    return {
        "id": task.id,
        "user_id": _owner_id(task),
        "blueprint_id": blueprint_id,
        "title": (task.title or "").strip() or task.id,
        "status": status,
        "updated_at": updated_at,
    }


def _task_document(task: WritingTask, saved_at: str = "") -> dict:
    doc = task.to_dict()
    doc["_task_doc_v"] = _DOC_V
    stamp = str(saved_at or "").strip() or datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["_saved_at"] = stamp
    return doc


def save_writing_task(task: WritingTask, saved_at: str = "") -> None:
    if not is_writing_task_id(task.id):
        return
    if not catalog_available():
        return
    from report_writing.infra.json_tables import upsert_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return
    updated_at = str(saved_at or "").strip() or datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = _meta_from_task(task, updated_at)
    doc = _task_document(task, saved_at=updated_at)
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(
            f"""
            INSERT INTO {_PARENT} (id, user_id, blueprint_id, title, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                user_id = incoming.user_id,
                blueprint_id = incoming.blueprint_id,
                title = incoming.title,
                status = incoming.status,
                updated_at = incoming.updated_at
            """,
            (
                meta["id"],
                meta["user_id"],
                meta["blueprint_id"],
                meta["title"],
                meta["status"],
                meta["updated_at"],
            ),
        )
        upsert_document_child(connection, _CHILD, "task_id", task.id, doc)
    try:
        from report_writing.services.task_chapters import sync_confirmed_chapters

        sync_confirmed_chapters(task)
    except Exception as error:
        _log.debug("sync_confirmed_chapters after save: %s", error)
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(str(task.id or "").strip())
        source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
        if not source_bp:
            source_bp = str(meta.get("blueprint_id") or "").strip()
        if source_bp:
            invalidate_doc_outline_cache(source_bp)
    except Exception as error:
        _log.debug("invalidate_doc_outline_cache after writing save: %s", error)


def latest_writing_task_id_for_blueprint(blueprint_id: str) -> str:
    """返回该蓝本最近一次对话写作任务 id（无则空串）。"""
    bid = str(blueprint_id or "").strip()
    if not bid or not catalog_available():
        return ""
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return ""
    try:
        with _connect(settings) as connection:
            _ensure_schema(connection)
            row = connection.execute(
                f"""
                SELECT id FROM {_PARENT}
                WHERE blueprint_id = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (bid,),
            ).fetchone()
        return str(row[0] or "").strip() if row else ""
    except Exception as error:
        _log.debug("latest_writing_task_id_for_blueprint %s: %s", bid, error)
        return ""


def load_writing_task_document(task_id: str) -> dict | None:
    """返回 writing_task_document 原始 JSON（含 _saved_at），供 TaskStore 与磁盘比新旧。"""
    tid = (task_id or "").strip()
    if not is_writing_task_id(tid):
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
                f"SELECT document FROM {_CHILD} WHERE task_id = %s",
                (tid,),
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
        if not isinstance(data, dict):
            return None
        if not data.get("id"):
            data["id"] = tid
        return data
    except Exception as error:
        _log.debug("load_writing_task_document %s: %s", tid, error)
        return None


def load_writing_task(task_id: str) -> WritingTask | None:
    data = load_writing_task_document(task_id)
    if data is None:
        return None
    try:
        return WritingTask.from_dict(data)
    except Exception as error:
        _log.debug("load_writing_task %s: %s", task_id, error)
        return None


def clear_writing_task_document(task_id: str) -> None:
    """仅清 document 子表，保留 meta；用于磁盘已新、MySQL 写入失败时避免旧稿盖回。"""
    tid = (task_id or "").strip()
    if not is_writing_task_id(tid) or not catalog_available():
        return
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(f"DELETE FROM {_CHILD} WHERE task_id = %s", (tid,))


def delete_writing_task(task_id: str) -> None:
    if not is_writing_task_id(task_id) or not catalog_available():
        return
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(f"DELETE FROM {_CHILD} WHERE task_id = %s", (task_id,))
        connection.execute(f"DELETE FROM {_PARENT} WHERE id = %s", (task_id,))


def sync_writing_tasks_from_disk(tasks_root: Path) -> int:
    if not catalog_available() or not tasks_root.is_dir():
        return 0
    saved = 0
    for path in tasks_root.glob("w*/task.json"):
        try:
            task = WritingTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        if not is_writing_task_id(task.id):
            continue
        try:
            save_writing_task(task)
            saved += 1
        except Exception:
            continue
    return saved
