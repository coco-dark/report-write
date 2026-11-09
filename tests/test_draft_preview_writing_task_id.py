"""draft-preview 应尊重 writing_task_id，与 doc-outline 一致。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from report_writing.api import app
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChapterState,
    ChapterStatus,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.store import TaskStore
from report_writing.services.users import ensure_default_user, user_workspace


def _task_dir(home: Path, task_id: str) -> Path:
    ensure_default_user(home)
    return user_workspace(home) / "tasks" / task_id


def test_draft_preview_uses_requested_writing_task(tmp_path, monkeypatch):
    monkeypatch.setattr("report_writing.api.deps.data_home", lambda: tmp_path)

    bid = "bpPreviewBind"
    bp_path = _task_dir(tmp_path, bid)
    ensure_task(bp_path)
    bp = TaskStore(bp_path).load()
    bp.id = bid
    bp.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="章 A", points=["要点"]),
        ]
    )
    TaskStore(bp_path).save(bp)
    bp_snapshot = WritingTask.from_dict(bp.to_dict())

    for wid, title in (("wPrevA", "正文甲"), ("wPrevB", "正文乙")):
        path = _task_dir(tmp_path, wid)
        ensure_task(path)
        w = TaskStore(path).load()
        w.id = wid
        w.source_blueprint_id = bid
        w.title = title
        w.outline = OutlineTree(
            chapters=[OutlineNode(key="c1", sequence="1", title=title, points=["要点"])]
        )
        w.chapters = [
            ChapterState(
                key="c1",
                title=title,
                status=ChapterStatus.CONFIRMED,
                official_text=f"<p>{title}</p>",
            )
        ]
        TaskStore(path).save(w)

    def _load_blueprint_task(tid: str) -> WritingTask | None:
        if tid != bid:
            return None
        return WritingTask.from_dict(bp_snapshot.to_dict())

    def _load_writing_task(tid: str) -> WritingTask | None:
        path = _task_dir(tmp_path, tid) / "task.json"
        if not path.is_file():
            return None
        return WritingTask.from_dict(json.loads(path.read_text(encoding="utf-8")))

    monkeypatch.setattr(
        "report_writing.services.blueprint_catalog.catalog_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "report_writing.services.blueprint_catalog.load_blueprint_task",
        _load_blueprint_task,
    )
    monkeypatch.setattr(
        "report_writing.services.writing_task_catalog.load_writing_task",
        _load_writing_task,
    )

    client = TestClient(app)
    r_a = client.get(
        f"/api/writing/tasks/{bid}/draft-preview",
        params={"writing_task_id": "wPrevA", "written_only": "true"},
    )
    assert r_a.status_code == 200
    html = str(r_a.json().get("html") or "")
    assert "正文甲" in html
    assert "正文乙" not in html
