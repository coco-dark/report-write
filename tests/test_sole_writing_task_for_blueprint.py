"""无 writing_task_id 时仅唯一写作副本才可自动合并。"""

from __future__ import annotations

from pathlib import Path

from report_writing.controller import ensure_task
from report_writing.domain.models import OutlineNode, OutlineTree
from report_writing.services.store import TaskStore
from report_writing.services.users import ensure_default_user, user_workspace
from report_writing.services.writing_task_catalog import _sole_writing_task_id_on_disk


def _task_dir(home: Path, task_id: str) -> Path:
    ensure_default_user(home)
    return user_workspace(home) / "tasks" / task_id


def test_sole_writing_on_disk_returns_id_only_when_unique(tmp_path, monkeypatch):
    monkeypatch.setattr("report_writing.api.deps.data_home", lambda: tmp_path)
    bid = "bpSole"
    w1 = "wSole1"
    w2 = "wSole2"
    for wid in (w1, w2):
        path = _task_dir(tmp_path, wid)
        ensure_task(path)
        task = TaskStore(path).load()
        task.id = wid
        task.source_blueprint_id = bid
        task.outline = OutlineTree(chapters=[OutlineNode(key="c1", sequence="1", title="章")])
        TaskStore(path).save(task)
    assert _sole_writing_task_id_on_disk(bid) == ""

    import shutil

    shutil.rmtree(_task_dir(tmp_path, w2))
    assert _sole_writing_task_id_on_disk(bid) == w1
