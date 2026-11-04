"""Word 样式预设：MySQL（按 user_id）+ 本地 workspace 回落。"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from report_writing.domain.word_style import WordStyleSettings, WordStyleSummary

_log = logging.getLogger(__name__)
_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_schema_ready = False

_STYLE_PARENT = "writing_word_style"
_STYLE_CHILD = "writing_word_style_document"
_STYLE_META_DDL = """
CREATE TABLE IF NOT EXISTS writing_word_style (
    id varchar(64) NOT NULL PRIMARY KEY,
    name varchar(255) NOT NULL DEFAULT '',
    label_updated varchar(64) NOT NULL DEFAULT '',
    updated_at varchar(32) NOT NULL,
    KEY writing_word_style_updated_idx (updated_at)
)
"""
_STYLE_CHILD_DDL = """
CREATE TABLE IF NOT EXISTS writing_word_style_document (
    style_id varchar(64) NOT NULL PRIMARY KEY,
    document json NOT NULL
)
"""

_DEFAULT_SEEDS: list[dict[str, Any]] = [
    {
        "id": "ws_default",
        "name": "默认报告样式",
        "desc": "宋体正文 12pt、黑体分级标题、含目录、首行缩进 2 字符。",
        "settings": WordStyleSettings().to_dict(),
    },
]


def word_styles_root(home: Path) -> Path:
    root = Path(home) / "word_styles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _style_owner_id(explicit: str | None = None) -> str:
    from report_writing.services.users import current_user_id, normalize_owner_id

    return normalize_owner_id(explicit or current_user_id())


def _owns_style(doc: dict[str, Any] | None, owner_id: str) -> bool:
    from report_writing.services.users import owns_user_resource

    if not doc:
        return False
    return owns_user_resource(owner_id, str(doc.get("user_id") or ""))


def ensure_default_word_styles(home: Path, *, owner_id: str | None = None) -> None:
    if _use_mysql():
        if _db_style_count_for_user(owner_id) == 0:
            for item in _DEFAULT_SEEDS:
                data = dict(item)
                data["updated"] = data.get("updated") or _now_label()
                _db_save_style(data, owner_id=owner_id)
        return
    root = word_styles_root(home)
    if any(root.glob("*/meta.json")):
        return
    for item in _DEFAULT_SEEDS:
        save_word_style(home, item)


def list_word_styles(home: Path) -> list[WordStyleSummary]:
    ensure_default_word_styles(home)
    if _use_mysql():
        return _db_list_styles()
    uid = _style_owner_id()
    items: list[WordStyleSummary] = []
    for meta_path in sorted(word_styles_root(home).glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if not _owns_style(meta, uid):
            continue
        items.append(_summary_from_meta(meta, meta_path.parent.name))
    return items


def get_word_style(home: Path, style_id: str) -> dict[str, Any] | None:
    ensure_default_word_styles(home)
    sid = _require_id(style_id)
    if _use_mysql():
        found = _db_get_style(sid)
        if found is not None:
            return found
        local = _file_get_style(home, sid)
        if local is not None:
            _db_save_style(local)
            return _db_get_style(sid)
        return None
    return _file_get_style(home, sid)


def save_word_style(home: Path, payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    sid = str(data.get("id") or "").strip() or f"ws{uuid.uuid4().hex[:10]}"
    sid = _require_id(sid)
    data["id"] = sid
    data["updated"] = data.get("updated") or _now_label()
    settings = WordStyleSettings.from_dict(data.get("settings") if isinstance(data.get("settings"), dict) else {})
    data["settings"] = settings.to_dict()
    _persist_style(home, data)
    return get_word_style(home, sid) or data


def delete_word_style(home: Path, style_id: str) -> None:
    sid = _require_id(style_id)
    if _use_mysql():
        if _db_get_style(sid) is None:
            raise KeyError(style_id)
        if not _db_delete_style(sid):
            raise KeyError(style_id)
        folder = word_styles_root(home) / sid
        if folder.exists():
            import shutil

            shutil.rmtree(folder)
        return
    import shutil

    folder = word_styles_root(home) / sid
    if not folder.exists():
        raise KeyError(style_id)
    shutil.rmtree(folder)


def resolve_word_style(home: Path, style_id: str = "") -> WordStyleSettings:
    ensure_default_word_styles(home)
    sid = (style_id or "").strip()
    if sid:
        found = get_word_style(home, sid)
        if found is None:
            raise KeyError(sid)
        return WordStyleSettings.from_dict(found.get("settings"))
    found = get_word_style(home, "ws_default")
    if found is not None:
        return WordStyleSettings.from_dict(found.get("settings"))
    return WordStyleSettings()


def sync_word_styles_from_disk(home: Path) -> int:
    if not _use_mysql():
        return 0
    saved = 0
    for meta_path in word_styles_root(home).glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        sid = str(meta.get("id") or meta_path.parent.name)
        full = _file_get_style(home, sid)
        if full is None:
            continue
        try:
            existing = _db_get_style(sid)
            disk_ts = str(full.get("updated") or "")
            db_ts = str(existing.get("updated") or "") if existing else ""
            if existing is None or disk_ts >= db_ts:
                _db_save_style(full)
                saved += 1
        except Exception:
            continue
    return saved


def _persist_style(home: Path, data: dict[str, Any]) -> None:
    if _use_mysql():
        _db_save_style(data)
        return
    _file_save_style(home, data)


def _file_get_style(home: Path, style_id: str) -> dict[str, Any] | None:
    folder = word_styles_root(home) / style_id
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        return None
    settings_path = folder / "settings.json"
    if settings_path.is_file():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings = meta.get("settings") or {}
    meta["id"] = style_id
    meta["settings"] = WordStyleSettings.from_dict(settings if isinstance(settings, dict) else {}).to_dict()
    return meta


def _file_save_style(home: Path, data: dict[str, Any]) -> None:
    sid = _require_id(str(data.get("id") or ""))
    settings = WordStyleSettings.from_dict(data.get("settings") if isinstance(data.get("settings"), dict) else {})
    folder = word_styles_root(home) / sid
    folder.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": sid,
        "name": str(data.get("name") or "未命名样式"),
        "desc": str(data.get("desc") or ""),
        "updated": str(data.get("updated") or _now_label()),
        "user_id": str(data.get("user_id") or _style_owner_id()),
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "settings.json").write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _summary_from_meta(meta: dict[str, Any], fallback_id: str = "") -> WordStyleSummary:
    return WordStyleSummary(
        id=str(meta.get("id") or fallback_id),
        name=str(meta.get("name") or ""),
        desc=str(meta.get("desc") or ""),
        updated=str(meta.get("updated") or ""),
    )


def _ensure_style_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    from report_writing.infra.json_tables import migrate_legacy_document_column, table_columns

    connection.execute(_STYLE_CHILD_DDL)
    cols = table_columns(connection, _STYLE_PARENT)
    if not cols:
        connection.execute(_STYLE_META_DDL)
        _schema_ready = True
        return
    if "name" not in cols:
        for stmt in (
            "ADD COLUMN name varchar(255) NOT NULL DEFAULT ''",
            "ADD COLUMN label_updated varchar(64) NOT NULL DEFAULT ''",
        ):
            try:
                connection.execute(f"ALTER TABLE {_STYLE_PARENT} {stmt}")
            except Exception:
                pass

    def _on_migrate(parent_id: str, doc: dict, updated_at: str) -> None:
        sid = str(doc.get("id") or parent_id)
        connection.execute(
            f"""
            INSERT INTO {_STYLE_PARENT} (id, name, label_updated, updated_at)
            VALUES (%s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                name = incoming.name,
                label_updated = incoming.label_updated,
                updated_at = incoming.updated_at
            """,
            (
                sid,
                str(doc.get("name") or sid),
                str(doc.get("updated") or ""),
                updated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )

    migrate_legacy_document_column(connection, _STYLE_PARENT, _STYLE_CHILD, "style_id", on_row=_on_migrate)
    from report_writing.infra.json_tables import drop_legacy_document_column

    drop_legacy_document_column(connection, _STYLE_PARENT)
    _schema_ready = True


def _use_mysql() -> bool:
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
            _ensure_style_schema(connection)
        return True
    except Exception as error:
        _log.warning("Word 样式改用本地目录存储：%s", error)
        return False


def _db_style_count_for_user(owner_id: str | None = None) -> int:
    return len(_db_list_styles(owner_id=owner_id))


def _db_list_styles(owner_id: str | None = None) -> list[WordStyleSummary]:
    from report_writing.infra.json_tables import fetch_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = _style_owner_id(owner_id)
    settings = load_database_settings()
    if settings is None:
        return []
    items: list[WordStyleSummary] = []
    with _connect(settings) as connection:
        _ensure_style_schema(connection)
        rows = connection.execute(
            f"SELECT id FROM {_STYLE_PARENT} ORDER BY updated_at DESC LIMIT 500"
        ).fetchall()
        for row in rows or []:
            sid = str(row[0])
            doc = fetch_document_child(connection, _STYLE_CHILD, "style_id", sid)
            if doc is None or not _owns_style(doc, uid):
                continue
            items.append(_summary_from_meta(doc, sid))
    return items


def _db_get_style(style_id: str) -> dict[str, Any] | None:
    from report_writing.infra.json_tables import fetch_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return None
    uid = _style_owner_id()
    with _connect(settings) as connection:
        _ensure_style_schema(connection)
        doc = fetch_document_child(connection, _STYLE_CHILD, "style_id", style_id)
        if doc is None:
            return None
        if not _owns_style(doc, uid):
            return None
        settings = doc.get("settings") if isinstance(doc.get("settings"), dict) else {}
        doc["settings"] = WordStyleSettings.from_dict(settings).to_dict()
        return doc


def _db_save_style(data: dict[str, Any], *, owner_id: str | None = None) -> None:
    from report_writing.infra.json_tables import upsert_document_child
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        raise RuntimeError("未配置数据库")
    sid = _require_id(str(data.get("id") or ""))
    payload = dict(data)
    payload["id"] = sid
    payload["user_id"] = _style_owner_id(
        owner_id if owner_id is not None else str(payload.get("user_id") or "")
    )
    payload["updated"] = str(payload.get("updated") or _now_label())
    payload["settings"] = WordStyleSettings.from_dict(
        payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    ).to_dict()
    updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect(settings) as connection:
        _ensure_style_schema(connection)
        connection.execute(
            f"""
            INSERT INTO {_STYLE_PARENT} (id, name, label_updated, updated_at)
            VALUES (%s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                name = incoming.name,
                label_updated = incoming.label_updated,
                updated_at = incoming.updated_at
            """,
            (
                sid,
                str(payload.get("name") or sid),
                str(payload.get("updated") or ""),
                updated_at,
            ),
        )
        upsert_document_child(connection, _STYLE_CHILD, "style_id", sid, payload)


def _db_delete_style(style_id: str) -> bool:
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        return False
    with _connect(settings) as connection:
        _ensure_style_schema(connection)
        exists = connection.execute(
            f"SELECT 1 FROM {_STYLE_PARENT} WHERE id = %s",
            (style_id,),
        ).fetchone()
        if exists is None:
            return False
        connection.execute(f"DELETE FROM {_STYLE_CHILD} WHERE style_id = %s", (style_id,))
        connection.execute(f"DELETE FROM {_STYLE_PARENT} WHERE id = %s", (style_id,))
    return True


def _require_id(value: str) -> str:
    key = (value or "").strip()
    if not key or not _ID_SAFE.match(key):
        raise ValueError("id 仅允许字母、数字、下划线与短横线")
    return key


def _now_label() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
