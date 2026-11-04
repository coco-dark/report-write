"""用户个人模型配置：MySQL 主存（writing_user_llm_config），不再写 llm.json。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from report_writing.domain.user import UserLlmConfig

_log = logging.getLogger(__name__)

_TABLE = "writing_user_llm_config"
_schema_ready = False

_DDL = """
CREATE TABLE IF NOT EXISTS writing_user_llm_config (
    user_id varchar(64) NOT NULL PRIMARY KEY,
    document json NOT NULL,
    updated_at varchar(32) NOT NULL,
    KEY writing_user_llm_updated_idx (updated_at)
)
"""


class UserLlmConfigStoreError(RuntimeError):
    """MySQL 不可用或未配置时无法读写个人模型配置。"""


def llm_config_store_available() -> bool:
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
        _log.warning("用户模型配置库不可用：%s", error)
        return False


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    connection.execute(_DDL)
    _schema_ready = True


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_user_llm_config(user_id: str) -> UserLlmConfig | None:
    """从库读取；无记录返回 None。"""
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = (user_id or "").strip()
    if not uid:
        return None
    settings = load_database_settings()
    if settings is None:
        raise UserLlmConfigStoreError("未配置 MySQL")
    with _connect(settings) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT document FROM {_TABLE} WHERE user_id = %s",
            (uid,),
        ).fetchone()
    if row is None:
        return None
    doc = row[0]
    if isinstance(doc, str):
        doc = json.loads(doc)
    if not isinstance(doc, dict):
        return UserLlmConfig()
    return UserLlmConfig.from_dict(doc)


def save_user_llm_config(user_id: str, cfg: UserLlmConfig) -> None:
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("用户 id 不合法")
    settings = load_database_settings()
    if settings is None:
        raise UserLlmConfigStoreError("未配置 MySQL")
    payload = json.dumps(cfg.to_dict(), ensure_ascii=False)
    updated = _now()
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(
            f"""
            INSERT INTO {_TABLE} (user_id, document, updated_at)
            VALUES (%s, CAST(%s AS JSON), %s) AS incoming
            ON DUPLICATE KEY UPDATE
                document = incoming.document,
                updated_at = incoming.updated_at
            """,
            (uid, payload, updated),
        )


def delete_user_llm_config(user_id: str) -> None:
    from report_writing.infra.postgres import _connect, load_database_settings

    uid = (user_id or "").strip()
    if not uid:
        return
    settings = load_database_settings()
    if settings is None:
        return
    with _connect(settings) as connection:
        _ensure_schema(connection)
        connection.execute(f"DELETE FROM {_TABLE} WHERE user_id = %s", (uid,))


def llm_config_has_data(cfg: UserLlmConfig | None) -> bool:
    if cfg is None:
        return False
    if cfg.enabled:
        return True
    if (cfg.api_key or "").strip():
        return True
    if (cfg.model_name or "").strip():
        return True
    if (cfg.intent_model_name or "").strip():
        return True
    if (cfg.base_url or "").strip():
        return True
    return False
