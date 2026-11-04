"""写作任务 MySQL 目录（无库时回落磁盘）。"""

from __future__ import annotations

from pathlib import Path

from report_writing.controller import ensure_task
from report_writing.domain.models import WritingTask
from report_writing.services.store import TaskStore
from report_writing.services.writing_task_catalog import is_writing_task_id, load_writing_task


def test_is_writing_task_id():
    assert is_writing_task_id("w20260101120000ab")
    assert not is_writing_task_id("bp20260101120000ab")


def test_task_store_roundtrip_local(tmp_path: Path):
    path = tmp_path / "tasks" / "wlocal01"
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = "wlocal01"
    task.title = "本地写作任务"
    TaskStore(path).save(task)
    again = TaskStore(path).load()
    assert again.title == "本地写作任务"


def test_load_writing_task_without_db(tmp_path: Path):
    assert load_writing_task("wnodb") is None
