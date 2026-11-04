"""retry_chapter：失败勿报成功；章名用大纲中文标题。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from report_writing.chat.actions import (
    _action_retry_chapter,
    _retry_chapter_display_title,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _seed(home: Path, *, confirmed: bool = False) -> str:
    tid = "w_retry_title"
    path = home / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(
                key="overall_performance_evaluation",
                sequence="1.3",
                title="总体履职评价",
                points=["写履职亮点"],
            )
        ]
    )
    task.outline_confirmed = confirmed
    TaskStore(path).save(task)
    return tid


def test_retry_display_title_uses_chinese_outline():
    class _T:
        outline = OutlineTree(
            chapters=[
                OutlineNode(
                    key="overall_performance_evaluation",
                    sequence="1.3",
                    title="总体履职评价",
                )
            ]
        )
        chapters = []

    label = _retry_chapter_display_title(_T(), "overall_performance_evaluation", {})
    assert "总体履职评价" in label
    assert "_" not in label
    assert "1.3" in label


def test_retry_display_title_rejects_camelcase_slug():
    class _T:
        outline = OutlineTree(
            chapters=[
                OutlineNode(
                    key="basicprojectinformation",
                    sequence="1.1",
                    title="基本项目信息",
                )
            ]
        )
        chapters = []

    label = _retry_chapter_display_title(
        _T(),
        "basicprojectinformation",
        {"title": "basicprojectinformation", "chapter_key": "basicprojectinformation"},
    )
    assert "基本项目信息" in label
    assert "basicprojectinformation" not in label.lower()
    assert "1.1" in label


def test_retry_chapter_failure_does_not_claim_success(tmp_path: Path):
    tid = _seed(tmp_path, confirmed=True)
    session = ChatSession(
        id="s1",
        phase=ChatPhase.WRITING.value,
        task_id=tid,
        freeform={"outline_confirmed": True},
    )

    def _boom(*_a, **_k):
        raise RuntimeError("模拟写章失败")

    with patch("report_writing.services.pipeline.run_chapters", side_effect=_boom):
        reply, _cards, events = _action_retry_chapter(
            tmp_path,
            session,
            {
                "task_id": tid,
                "chapter_key": "overall_performance_evaluation",
                "title": "总体履职评价",
                "sequence": "1.3",
                "aux_prompt": "补充要求",
            },
        )
    assert "重写了" not in reply
    assert "没跑成" in reply or "失败" in reply
    assert "总体履职评价" in reply
    assert any(e.get("type") == "error" for e in events)


def test_retry_chapter_auto_confirms_outline(tmp_path: Path):
    tid = _seed(tmp_path, confirmed=False)
    session = ChatSession(
        id="s2",
        phase=ChatPhase.WRITING.value,
        task_id=tid,
        freeform={"outline_confirmed": True},
    )

    def _fake_run(path, **_kwargs):
        return TaskStore(path).load()

    with patch("report_writing.services.pipeline.run_chapters", side_effect=_fake_run):
        reply, _cards, _events = _action_retry_chapter(
            tmp_path,
            session,
            {
                "task_id": tid,
                "chapter_key": "overall_performance_evaluation",
                "aux_prompt": "按补充重写",
            },
        )
    task = TaskStore(tmp_path / "tasks" / tid).load()
    assert task.outline_confirmed is True
    assert "总体履职评价" in reply
    assert "重写了" in reply or "重跑" in reply
