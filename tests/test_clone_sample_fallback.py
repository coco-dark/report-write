"""引用库蓝本后，写作副本应能复用源蓝本样本，不必重新上传。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.writing import hydrate_empty_copy_from_blueprint
from report_writing.controller import create_blueprint, ensure_task
from report_writing.controller.blueprint import (
    clear_blueprint_sample,
    resolve_sample_path,
    resolve_sample_text,
)
from report_writing.domain.models import OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


def _write_sample(path: Path, body: str = "蓝本样本正文") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04dummy")
    Path(str(path) + ".rag-parse.md").write_text(body, encoding="utf-8")
    return path


def _make_blueprint(home: Path, bid: str, sample: Path) -> WritingTask:
    path = home / "tasks" / bid
    ensure_task(path)
    create_blueprint(path, title="分析报告", category="", description="t")
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", title="背景", sequence="1", points=["p"]),
            OutlineNode(key="c2", title="概况", sequence="2"),
            OutlineNode(key="c3", title="退住原因分类结果", sequence="3"),
        ]
    )
    task.outline_confirmed = True
    task.sample_path = str(sample)
    task.sample_name = sample.name
    task.sample_chars = 12
    task.sample_ready = True
    TaskStore(path).save(task)
    return TaskStore(path).load()


def test_resolve_sample_text_falls_back_to_source_blueprint(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpSampleLib"
    sample = _write_sample(home / "uploads" / bid / "sample" / "退住原因分析报告.docx")
    _make_blueprint(home, bid, sample)

    clone = WritingTask(
        id="wCloneNoPath",
        title="文档副本",
        source_blueprint_id=bid,
        sample_path="",
        sample_ready=True,
    )
    found = resolve_sample_path(clone)
    assert found is not None
    assert found.resolve() == sample.resolve()
    assert "蓝本样本正文" in resolve_sample_text(clone)


def test_hydrate_copies_sample_onto_existing_outline_copy(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpHydrateSample"
    sample = _write_sample(home / "uploads" / bid / "sample" / "样板.docx", "hydrate样本")
    _make_blueprint(home, bid, sample)

    shell_id = "wNamedShell"
    dest = home / "tasks" / shell_id
    ensure_task(dest)
    copy = TaskStore(dest).load()
    copy.title = "已定名文档"
    copy.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概述（已改）", sequence="1")]
    )
    copy.outline_confirmed = True
    TaskStore(dest).save(copy)
    copy = TaskStore(dest).load()

    updated = hydrate_empty_copy_from_blueprint(home, copy, bid)
    assert updated.outline.chapters[0].title == "概述（已改）"
    assert updated.source_blueprint_id == bid
    assert updated.sample_path == str(sample)
    assert updated.sample_name == sample.name
    assert updated.sample_ready is True
    assert "hydrate样本" in resolve_sample_text(updated)


def test_clear_clone_sample_does_not_delete_library_file(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpKeepSample"
    sample = _write_sample(home / "uploads" / bid / "sample" / "库样本.docx", "库侧正文")
    _make_blueprint(home, bid, sample)

    clone_id = "wShareSample"
    dest = home / "tasks" / clone_id
    ensure_task(dest)
    clone = TaskStore(dest).load()
    clone.source_blueprint_id = bid
    clone.sample_path = str(sample)
    clone.sample_name = sample.name
    clone.sample_ready = True
    TaskStore(dest).save(clone)

    cleared = clear_blueprint_sample(dest)
    assert cleared.sample_path == ""
    assert cleared.sample_ready is False
    assert sample.is_file()
    assert Path(str(sample) + ".rag-parse.md").is_file()
