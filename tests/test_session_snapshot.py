from report_writing.chat.session_snapshot import looks_like_freeform_meta_line, session_turn_snapshot
from report_writing.domain.models import ChatSession


def test_meta_line():
    assert looks_like_freeform_meta_line("述职报告｜2026年度")
    assert not looks_like_freeform_meta_line("1.1 背景介绍")


def test_session_snapshot_keys():
    session = ChatSession(id="s", phase="writing", task_id="t1")
    snap = session_turn_snapshot(session)
    assert snap["phase"] == "writing"
    assert snap["task_id"] == "t1"
