"""持久化策略：MySQL 为主，Redis 为热缓存，本地 JSON 为开发回落。"""

from __future__ import annotations

from pathlib import Path


def database_mode() -> str:
    """file | mysql | auto（有 JDBC 配置且库可达则用 mysql）。见 config/application.yml。"""
    from report_writing.infra.config_settings import database_mode as _mode

    return _mode()


def mysql_primary() -> bool:
    if database_mode() == "file":
        return False
    from report_writing.services.chat_session import _use_mysql

    return bool(_use_mysql())


def writing_task_mysql() -> bool:
    if database_mode() == "file":
        return False
    from report_writing.services.writing_task_catalog import catalog_available

    return catalog_available()
