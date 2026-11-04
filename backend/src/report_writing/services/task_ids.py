"""任务 id 约定：蓝本 bp*，写作任务 w*（或其它非 bp 的历史 id）。"""

from __future__ import annotations


def is_blueprint_task_id(task_id: str) -> bool:
    return str(task_id or "").strip().startswith("bp")


def is_writing_task_id(task_id: str) -> bool:
    tid = str(task_id or "").strip()
    return bool(tid) and not is_blueprint_task_id(tid)
