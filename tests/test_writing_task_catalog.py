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


def test_task_store_prefers_mysql_over_stale_disk(tmp_path: Path, monkeypatch):
    path = tmp_path / "tasks" / "wmysql01"
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = "wmysql01"
    task.title = "磁盘旧稿无插槽"
    TaskStore(path).save(task)

    mysql_doc = task.to_dict()
    mysql_doc["title"] = "库里已配插槽"
    mysql_doc["element_nodes"] = [
        {
            "id": "el1",
            "name": "批复",
            "kind": "file_slot",
            "file_slots": [{"no": 1, "name": "批复文件", "query": "建设规模"}],
        }
    ]

    monkeypatch.setattr(
        "report_writing.services.writing_task_catalog.load_writing_task_document",
        lambda tid: mysql_doc if tid == "wmysql01" else None,
    )
    again = TaskStore(path).load()
    assert again.title == "库里已配插槽"
    assert again.element_nodes[0].file_slots[0].query == "建设规模"
