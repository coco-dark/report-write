"""将 data/ 下本地配置同步到 MySQL（启动或 CLI 触发）。"""

from __future__ import annotations

from pathlib import Path

from report_writing.infra.postgres import load_database_settings
from report_writing.services.blueprint_catalog import sync_blueprints_from_disk
from report_writing.services.chat_session import sync_chat_sessions_from_disk
from report_writing.services.render import sync_render_templates_from_disk
from report_writing.services.robots import sync_robots_from_disk
from report_writing.services.word_styles import sync_word_styles_from_disk
from report_writing.services.writing_task_catalog import sync_writing_tasks_from_disk


def sync_local_data(home: Path) -> dict[str, int]:
    """有库配置时，把机器人、渲染模版、蓝本、会话 upsert 入库。"""
    if load_database_settings() is None:
        return {}
    root = Path(home)
    tasks = root / "tasks"
    return {
        "robots": sync_robots_from_disk(root),
        "render_templates": sync_render_templates_from_disk(root),
        "blueprints": sync_blueprints_from_disk(tasks),
        "writing_tasks": sync_writing_tasks_from_disk(tasks),
        "chat_sessions": sync_chat_sessions_from_disk(root),
        "word_styles": sync_word_styles_from_disk(root),
    }


def sync_all_users_local_data(data_home: Path) -> dict[str, dict[str, int]]:
    """按用户工作区分别同步到 MySQL（写入各自 user_id）。"""
    from report_writing.services.users import (
        reset_current_user_id,
        set_current_user_id,
        sync_users_from_disk,
        user_workspace,
        users_root,
        _load_index,
    )

    home = Path(data_home)
    sync_users_from_disk(home)
    index = _load_index(users_root(home))
    user_ids = sorted(set(index.values()) | {"default"})
    results: dict[str, dict[str, int]] = {}
    for uid in user_ids:
        token = set_current_user_id(uid)
        try:
            results[uid] = sync_local_data(user_workspace(home, uid))
        finally:
            reset_current_user_id(token)
    return results
