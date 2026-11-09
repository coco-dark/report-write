"""GET doc-outline 在库蓝本 bp* 上应尊重 writing_task_id 查询参数。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from report_writing.api import app
from report_writing.controller import ensure_task
from report_writing.domain.models import ElementNode, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore
from report_writing.services.users import ensure_default_user, user_workspace


def _task_dir(home: Path, task_id: str) -> Path:
    ensure_default_user(home)
    return user_workspace(home) / "tasks" / task_id


def _save_writing(home: Path, wid: str, bid: str, element_id: str, name: str) -> None:
    path = _task_dir(home, wid)
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.source_blueprint_id = bid
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="c1",
                sequence="1",
                title="章",
                element_ids=[element_id],
            )
        ]
    )
    task.element_nodes = [
        ElementNode(id=element_id, name=name, kind="fixed", value=""),
    ]
    TaskStore(path).save(task)


def test_doc_outline_merges_requested_writing_task(tmp_path, monkeypatch):
    monkeypatch.setattr("report_writing.api.deps.data_home", lambda: tmp_path)

    bid = "bpBindTest"
    bp_path = _task_dir(tmp_path, bid)
    ensure_task(bp_path)
    bp = TaskStore(bp_path).load()
    bp.id = bid
    bp.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", sequence="1", title="章", element_ids=[])]
    )
    TaskStore(bp_path).save(bp)
    bp_snapshot = WritingTask.from_dict(bp.to_dict())

    _save_writing(tmp_path, "wBindA", bid, "el-a", "副本A")
    _save_writing(tmp_path, "wBindB", bid, "el-b", "副本B")

    def _load_blueprint_task(tid: str) -> WritingTask | None:
        if tid != bid:
            return None
        return WritingTask.from_dict(bp_snapshot.to_dict())

    def _load_writing_task(tid: str) -> WritingTask | None:
        path = _task_dir(tmp_path, tid) / "task.json"
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return WritingTask.from_dict(raw)

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
        f"/api/writing/tasks/{bid}/doc-outline",
        params={"writing_task_id": "wBindA", "fresh": "1"},
    )
    assert r_a.status_code == 200
    body_a = r_a.json()
    ch_a = (body_a.get("chapters") or [])[0]
    assert ch_a.get("element_ids") == ["el-a"]
    names_a = [n.get("name") for n in body_a.get("element_nodes") or []]
    assert "副本A" in names_a
    assert "副本B" not in names_a

    r_b = client.get(
        f"/api/writing/tasks/{bid}/doc-outline",
        params={"writing_task_id": "wBindB", "fresh": "1"},
    )
    assert r_b.status_code == 200
    body_b = r_b.json()
    ch_b = (body_b.get("chapters") or [])[0]
    assert ch_b.get("element_ids") == ["el-b"]

    r_plain = client.get(f"/api/writing/tasks/{bid}/doc-outline", params={"fresh": "1"})
    assert r_plain.status_code == 200
    ch_plain = (r_plain.json().get("chapters") or [])[0]
    assert ch_plain.get("element_ids") in ([], None) or ch_plain.get("element_ids") == []
