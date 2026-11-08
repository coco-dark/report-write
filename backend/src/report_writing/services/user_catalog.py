"""登录用户：MySQL 主存（writing_user / writing_user_token_usage）。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from report_writing.domain.user import UserProfile

_log = logging.getLogger(__name__)

_TABLE = "writing_user"
_TOKEN_TABLE = "writing_user_token_usage"
_schema_ready = False

_DDL = """
CREATE TABLE IF NOT EXISTS writing_user (
    id varchar(64) NOT NULL PRIMARY KEY,
    username varchar(32) NOT NULL,
    display_name varchar(128) NOT NULL DEFAULT '',
    password_hash varchar(256) NOT NULL DEFAULT '',
    created_at varchar(32) NOT NULL DEFAULT '',
    updated_at varchar(32) NOT NULL,
    UNIQUE KEY writing_user_username_idx (username)
)
"""

_TOKEN_DDL = """
CREATE TABLE IF NOT EXISTS writing_user_token_usage (
    user_id varchar(64) NOT NULL PRIMARY KEY,
    document json NOT NULL,
    updated_at varchar(32) NOT NULL
)
"""


class UserStoreError(RuntimeError):
    """MySQL 不可用或未配置时无法读写用户账号。"""


@dataclass
class StoredUser:
    profile: UserProfile
    password_hash: str = ""


def user_store_available() -> bool:
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
        _log.warning("用户账号库不可用：%s", error)
        return False


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    connection.execute(_DDL)
    connection.execute(_TOKEN_DDL)
    _schema_ready = True


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _row_to_stored(row) -> StoredUser | None:
    if row is None:
        return None
    uid = str(row[0] or "").strip()
    if not uid:
        return None
    return StoredUser(
        profile=UserProfile(
            id=uid,
            username=str(row[1] or "").strip(),
            display_name=str(row[2] or "").strip(),
            created_at=str(row[4] or "").strip(),
        ),
        password_hash=str(row[3] or ""),
    )


def load_user_by_id(user_id: str) -> StoredUser | None:
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = (user_id or "").strip()
    if not uid:
        return None
    settings = load_database_settings()
    if settings is None:
        raise UserStoreError("未配置 MySQL")
    with _connect(settings) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT id, username, display_name, password_hash, created_at FROM {_TABLE} WHERE id = %s",
            (uid,),
        ).fetchone()
    return _row_to_stored(row)


def load_user_by_username(username: str) -> StoredUser | None:
    from report_writing.infra.postgres import _connect, load_database_settings

    name = (username or "").strip().lower()
    if not name:
        return None
    settings = load_database_settings()
    if settings is None:
        raise UserStoreError("未配置 MySQL")
    with _connect(settings) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT id, username, display_name, password_hash, created_at FROM {_TABLE} WHERE username = %s",
            (name,),
        ).fetchone()
    return _row_to_stored(row)


def list_stored_users() -> list[StoredUser]:
    from report_writing.infra.postgres import _connect, load_database_settings

    settings = load_database_settings()
    if settings is None:
        raise UserStoreError("未配置 MySQL")
    with _connect(settings) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"SELECT id, username, display_name, password_hash, created_at FROM {_TABLE} ORDER BY username"
        ).fetchall()
    items: list[StoredUser] = []
    for row in rows or []:
        stored = _row_to_stored(row)
        if stored:
            items.append(stored)
    return items


def save_user_record(profile: UserProfile, password_hash: str) -> None:
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = (profile.id or "").strip()
    username = (profile.username or "").strip().lower()
    if not uid or not username:
        raise ValueError("用户 id / 用户名不合法")
    settings = load_database_settings()
    if settings is None:
        raise UserStoreError("未配置 MySQL")
    updated = _now()
    created = (profile.created_at or "").strip() or updated
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(
            f"""
            INSERT INTO {_TABLE} (id, username, display_name, password_hash, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s) AS incoming
            ON DUPLICATE KEY UPDATE
                username = incoming.username,
                display_name = incoming.display_name,
                password_hash = incoming.password_hash,
                created_at = IF(writing_user.created_at = '', incoming.created_at, writing_user.created_at),
                updated_at = incoming.updated_at
            """,
            (
                uid,
                username,
                (profile.display_name or username).strip(),
                password_hash or "",
                created,
                updated,
            ),
        )


def load_token_usage_doc(user_id: str) -> dict | None:
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = (user_id or "").strip()
    if not uid:
        return None
    settings = load_database_settings()
    if settings is None:
        raise UserStoreError("未配置 MySQL")
    with _connect(settings) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT document FROM {_TOKEN_TABLE} WHERE user_id = %s",
            (uid,),
        ).fetchone()
    if row is None:
        return None
    doc = row[0]
    if isinstance(doc, str):
        doc = json.loads(doc)
    return doc if isinstance(doc, dict) else {}


def save_token_usage_doc(user_id: str, document: dict) -> None:
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("用户 id 不合法")
    settings = load_database_settings()
    if settings is None:
        raise UserStoreError("未配置 MySQL")
    payload = json.dumps(document or {}, ensure_ascii=False)
    updated = _now()
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(
            f"""
            INSERT INTO {_TOKEN_TABLE} (user_id, document, updated_at)
            VALUES (%s, CAST(%s AS JSON), %s) AS incoming
            ON DUPLICATE KEY UPDATE
                document = incoming.document,
                updated_at = incoming.updated_at
            """,
            (uid, payload, updated),
        )
