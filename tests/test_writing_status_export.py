"""查进度不应默认弹出导出卡片。"""

from __future__ import annotations

from report_writing.chat.writing import _writing_status
from report_writing.domain.models import ChatPhase, ChatSession


def test_writing_status_no_export_card_for_in_progress_task(tmp_path, monkeypatch):
    from report_writing.domain.models import ChapterState, ChapterStatus, WritingTask

    task = WritingTask(
        id="w1",
        chapters=[ChapterState(key="c1", title="第一章", status=ChapterStatus.PENDING)],
    )
    monkeypatch.setattr(
        "report_writing.chat.helpers._load_task",
        lambda _home, _tid: task,
    )
    session = ChatSession(id="s1", task_id="w1", phase=ChatPhase.WRITING.value)
    _, cards = _writing_status(tmp_path, session)
    assert not any(c.type == "export" for c in cards)
