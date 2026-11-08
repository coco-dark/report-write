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
    reply, cards = _writing_status(tmp_path, session)
    assert not any(c.type == "export" for c in cards)
    assert not (reply or "").strip()


def test_refresh_status_idle_when_nothing_inflight():
    from report_writing.domain.models import ChapterState, ChapterStatus, TaskStatus, WritingTask

    task = WritingTask(
        id="w-idle",
        outline_confirmed=True,
        chapters=[
            ChapterState(key="c1", title="一", status=ChapterStatus.AWAITING_CONFIRM),
            ChapterState(key="c2", title="二", status=ChapterStatus.PENDING),
        ],
    )
    task.refresh_status()
    assert task.status == TaskStatus.AWAITING_CONFIRM

    task.chapters[0].status = ChapterStatus.CONFIRMED
    task.refresh_status()
    assert task.status == TaskStatus.IDLE

    task.chapters[1].status = ChapterStatus.WRITING
    task.refresh_status()
    assert task.status == TaskStatus.RUNNING


def test_writing_status_idle_written_chapters_not_running(tmp_path, monkeypatch):
    from report_writing.domain.models import ChapterState, ChapterStatus, WritingTask

    task = WritingTask(
        id="w2",
        chapters=[
            ChapterState(key="c1", title="第一章", status=ChapterStatus.AWAITING_CONFIRM),
            ChapterState(key="c2", title="第二章", status=ChapterStatus.PENDING),
        ],
    )
    monkeypatch.setattr(
        "report_writing.chat.helpers._load_task",
        lambda _home, _tid: task,
    )
    session = ChatSession(id="s2", task_id="w2", phase=ChatPhase.WRITING.value)
    reply, _cards = _writing_status(tmp_path, session)
    assert not (reply or "").strip()
    from report_writing.chat.writing import writing_status_facts

    facts = writing_status_facts(task)
    assert facts["awaiting"] == 1
    assert facts["inflight"] == []
