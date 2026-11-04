"""对话导出：不再附带 export / 渲染模版 / Word 样式卡片。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.resource_readiness import export_phase_cards
from report_writing.chat.writing import _prepare_export_word
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChapterState,
    ChapterStatus,
    ChatSession,
    OutlineNode,
    OutlineTree,
    WritingBrief,
    ChapterRequirement,
)
from report_writing.services.store import TaskStore


def _seed_writing_task(home: Path, task_id: str = "w_export") -> Path:
    path = home / "tasks" / task_id
    ensure_task(path)
    store = TaskStore(path)
    task = store.load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="第一章", points=["p"], keywords=["k"])]
    )
    task.outline_confirmed = True
    task.brief = WritingBrief(
        template_id="t",
        template_version="1",
        chapters=[ChapterRequirement(key="c1", title="第一章", points=["p"], keywords=["k"])],
    )
    task.brief_confirmed = True
    task.chapters = [
        ChapterState(key="c1", title="第一章", status=ChapterStatus.AWAITING_CONFIRM),
    ]
    store.save(task)
    return path


def test_prepare_export_word_no_export_cards(tmp_path: Path):
    _seed_writing_task(tmp_path)
    session = ChatSession(id="s1", task_id="w_export")
    reply, cards = _prepare_export_word(tmp_path, session)
    assert "下载 Word" in reply
    assert not any(c.type == "export" for c in cards)
    assert not any(c.type == "resource_choice" for c in cards)


def test_export_phase_cards_empty(tmp_path: Path):
    assert export_phase_cards(tmp_path, "w_export", None) == []
