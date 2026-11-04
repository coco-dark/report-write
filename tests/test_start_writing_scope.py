"""开写范围：文档窗勾选 chapter_keys 不得写全篇 pending。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from report_writing.chat.actions import _action_start_writing
from report_writing.controller import ensure_task
from report_writing.domain.models import ChapterRequirement, ChapterStatus, ChatSession, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


def _task_with_chapters(home: Path, wid: str) -> WritingTask:
    path = home / "tasks" / wid
    ensure_task(path)
    task = TaskStore(path).load()
    task.id = wid
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="1.1", sequence="1.1", title="A"),
            OutlineNode(key="1.2", sequence="1.2", title="B"),
            OutlineNode(key="2.1", sequence="2.1", title="C"),
        ]
    )
    task.chapters = [
        ChapterRequirement(key="1.1", title="A", status=ChapterStatus.PENDING),
        ChapterRequirement(key="1.2", title="B", status=ChapterStatus.PENDING),
        ChapterRequirement(key="2.1", title="C", status=ChapterStatus.PENDING),
    ]
    TaskStore(path).save(task)
    return task


def test_start_writing_respects_single_chapter_keys(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    wid = "wScope"
    task = _task_with_chapters(home, wid)
    task.outline_confirmed = True
    TaskStore(home / "tasks" / wid).save(task)
    session = ChatSession(id="s_scope", task_id=wid, phase="writing")
    session.freeform = {
        "write_chapter_keys": ["1.1"],
        "outline_confirmed": True,
        "meta_confirmed": True,
    }

    captured: list[list[str]] = []

    def _fake_run(_home, _session, task, keys, *, emit=None):
        captured.append(list(keys))
        return task, []

    with patch("report_writing.chat.writing._run_writing_keys", side_effect=_fake_run):
        with patch(
            "report_writing.chat.materials_pack_gate.materials_pack_gate_satisfied",
            return_value=True,
        ):
            with patch(
                "report_writing.chat.writing_supplement_check.supplement_gate_needed",
                return_value=False,
            ):
                with patch(
                    "report_writing.chat.tool_capability.missing_for_tool",
                    return_value=[],
                ):
                    _action_start_writing(
                        home,
                        session,
                        chapter_keys=["1.1"],
                        payload={"_skip_supplement_check": True},
                        invoke=None,
                    )

    assert captured
    assert captured[0] == ["1.1"]
