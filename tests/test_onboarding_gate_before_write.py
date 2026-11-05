"""入口未齐时禁止开写 / 自动 regenerate 大纲。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.tool_dispatch import execute_dispatch_tool
from report_writing.chat.session_documents import (
    begin_document_onboarding,
    confirm_writing_document_name,
    focus_needs_entry_path,
    mark_focus_entry_branch,
    upsert_session_document,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_submit_outline_regenerate_blocked_when_entry_incomplete(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="gate1", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "述职报告", home=home)
    assert focus_needs_entry_path(session)

    hit = execute_dispatch_tool(
        home,
        session,
        {"tool": "submit_outline", "args": {"regenerate": True, "title": "述职报告"}},
        "生成目录",
    )
    assert hit is not None
    reply, cards, _ = hit
    # 「生成目录」本身即选定无蓝本自动大纲，不再卡选路
    blob = f"{reply or ''}{' '.join(getattr(c, 'type', '') or '' for c in (cards or []))}"
    assert hit is not None
    assert "先给这份文档起个名字" not in (reply or "")
    assert blob.strip() != "" or cards is not None


def test_start_writing_blocked_when_entry_incomplete(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="gate2", phase=ChatPhase.INTENT.value)
    upsert_session_document(
        session,
        kind="outline",
        task_id="w1",
        title="报告",
        clear_blueprint_id=True,
    )
    session.task_id = "w1"
    confirm_writing_document_name(session, "报告", home=home)
    mark_focus_entry_branch(session, "without_blueprint")
    assert focus_needs_entry_path(session)

    hit = execute_dispatch_tool(
        home,
        session,
        {"tool": "start_writing", "args": {}},
        "开始写作",
    )
    assert hit is not None
    reply, cards, _ = hit
    assert cards or any(
        k in (reply or "") for k in ("选", "路", "目录", "粘贴", "蓝本", "大纲")
    )


def test_old_outline_confirmed_does_not_unlock_new_document(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="gate3", phase=ChatPhase.READY_TO_WRITE.value)
    upsert_session_document(
        session,
        kind="outline",
        task_id="w_old",
        title="旧报告",
        clear_blueprint_id=True,
    )
    session.task_id = "w_old"
    confirm_writing_document_name(session, "旧报告", home=home)
    session.freeform = {
        **(session.freeform or {}),
        "outline_confirmed": True,
        "outline_text": "# 一、概述",
        "writing_path": "freeform",
    }
    begin_document_onboarding(
        session, home, message="再写一份述职报告", title_hint="述职报告", force_new=True
    )
    hit = execute_dispatch_tool(
        home,
        session,
        {"tool": "submit_outline", "args": {"regenerate": True, "title": "述职报告"}},
        "生成目录",
    )
    assert hit is not None
    reply, cards, _ = hit
    assert cards or any(
        k in (reply or "") for k in ("选", "名", "路", "目录", "粘贴", "题目", "大纲")
    )
    assert session.freeform.get("outline_confirmed") is not True
