"""无蓝本：改名不回「生成目录」；已有章时「继续」确认大纲。"""

from __future__ import annotations

from collections.abc import Mapping

from pathlib import Path

from report_writing.chat.actions import _action_submit_outline, _collect_freeform_outline
from report_writing.chat.clarify_flow import (
    build_freeform_next_clarify,
    execute_clarify_spec,
    get_pending,
    try_handle_pending_message,
)
from report_writing.chat.session_goal import get_session_goal
from report_writing.chat.session_goal_actions import run_session_goal_action
from report_writing.controller import confirm_outline, ensure_task, save_outline, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode


def _session_with_outline(tmp_path: Path, *, n: int = 3) -> ChatSession:
    tid = "w20260101130000cc"
    session = ChatSession(
        id="s-rename",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        task_id=tid,
        freeform={
            "meta_confirmed": True,
            "category": "工作汇报",
            "title": "工作汇报",
            "outline_pending_edit": True,
            "skip_blueprint": True,
        },
    )
    wdir = tmp_path / "tasks" / tid
    ensure_task(wdir)
    save_spec_meta(wdir, title="工作汇报", category="工作汇报")
    chapters = [
        OutlineNode(key=f"c{i}", sequence=str(i), title=f"第{i}章", level=1)
        for i in range(1, n + 1)
    ]
    save_outline(wdir, chapters)
    return session


def test_rename_with_existing_outline_does_not_ask_generate(tmp_path: Path):
    from report_writing.chat.session_documents import (
        session_documents_map,
        upsert_session_document,
    )

    session = _session_with_outline(tmp_path)
    upsert_session_document(
        session,
        kind="outline",
        task_id=session.task_id,
        writing_task_id=session.task_id,
        title="工作汇报",
        clear_blueprint_id=True,
    )
    reply, cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "工作汇报｜年度工作汇报"},
        invoke=None,
    )
    assert "帮我生成目录" not in reply
    assert "更新" in reply or "已" in reply
    assert session.phase == ChatPhase.FREEFORM_OUTLINE.value
    assert any(c.type == "outline_preview" for c in cards) or "章" in reply
    docs = session_documents_map(session)
    assert docs[f"task:{session.task_id}"]["title"] == "年度工作汇报"


def test_continue_with_existing_outline_confirms(tmp_path: Path):
    session = _session_with_outline(tmp_path)
    reply, _cards = _action_submit_outline(
        tmp_path, session, {}, "继续", invoke=None
    )
    assert "帮我生成目录" not in reply
    # 确认后应进入要点或可写阶段
    assert session.freeform.get("outline_confirmed") or session.phase in {
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.READY_TO_WRITE.value,
    }


def test_clarify_with_chapters_prefers_confirm(tmp_path: Path):
    session = _session_with_outline(tmp_path)
    reply, cards = build_freeform_next_clarify(
        session,
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        message="嗯",
        home=tmp_path,
    )
    assert "帮我起草目录" not in reply
    assert "确认" in reply
    opts = (cards[0].payload or {}).get("options") or []
    labels = [str(o.get("label") or "") for o in opts]
    assert any("确认" in lb for lb in labels)
    assert not any(lb == "帮我起草目录" for lb in labels)


def test_plain_title_rename_without_pipe(tmp_path: Path):
    """只说新题目、无「类型｜标题」时也应改名，不能挂「没听清」。"""
    session = _session_with_outline(tmp_path)
    reply, _cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "2026年度工作汇报"},
        invoke=None,
    )
    assert "没听清" not in reply
    assert "更新" in reply or "已" in reply
    assert get_session_goal(session).get("doc_title") == "2026年度工作汇报"
    assert get_pending(session) is None


def test_rename_requires_structured_title_not_regex(tmp_path: Path):
    """改名不再用正则剥标题：仅 message「改个名字 XXX」不得静默落盘；须传 title。"""
    session = _session_with_outline(tmp_path)
    _reply_bare, _cards_bare = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "改个名字 2026个人述职报告"},
        invoke=None,
    )
    # 整句含改名指令词 → 不当标题；无结构化 title 时不得静默改成新名
    assert get_session_goal(session).get("doc_title") != "改个名字 2026个人述职报告"
    assert get_session_goal(session).get("doc_title") != "2026个人述职报告"

    session2 = _session_with_outline(tmp_path)
    reply, _cards = run_session_goal_action(
        tmp_path,
        session2,
        "set_session_meta",
        {
            "message": "改个名字 2026个人述职报告",
            "title": "2026个人述职报告",
        },
        invoke=None,
    )
    assert "跟我说一下文档叫什么" not in reply
    assert "更新" in reply or "已" in reply
    assert get_session_goal(session2).get("doc_title") == "2026个人述职报告"


def test_dispatch_set_session_meta_needs_structured_title(tmp_path: Path):
    """调度层空 args 不再用改名正则补 title；须由上游传入结构化 title。"""
    from report_writing.chat.tool_dispatch import execute_dispatch_tool

    session = _session_with_outline(tmp_path)
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "set_session_meta", "args": {}},
        "改个名字 2026个人述职报告",
        emit=None,
        invoke=None,
    )
    assert hit is not None
    _reply, _cards, _events = hit
    # 无 title 时不能靠正则静默成功
    assert get_session_goal(session).get("doc_title") != "2026个人述职报告"

    session2 = _session_with_outline(tmp_path)
    hit2 = execute_dispatch_tool(
        tmp_path,
        session2,
        {
            "tool": "set_session_meta",
            "args": {"title": "2026个人述职报告", "message": "改个名字 2026个人述职报告"},
        },
        "改个名字 2026个人述职报告",
        emit=None,
        invoke=None,
    )
    assert hit2 is not None
    reply2, _c2, _e2 = hit2
    assert "跟我说一下文档叫什么" not in reply2
    assert get_session_goal(session2).get("doc_title") == "2026个人述职报告"


def test_explicit_title_payload_wins_over_vague_message(tmp_path: Path):
    """文档窗改名会带 title 字段：即使 message 含糊也要按 title 落盘。"""
    session = _session_with_outline(tmp_path)
    reply, _cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {
            "message": "嗯嗯",
            "title": "年度总结报告",
            "category": "工作汇报",
        },
        invoke=None,
    )
    assert "没听清" not in reply
    assert get_session_goal(session).get("doc_title") == "年度总结报告"
    assert get_pending(session) is None


def test_session_meta_clarify_accepts_plain_title_followup(tmp_path: Path):
    """澄清挂起后用户直接发具体名称，应结合上下文改名，而不是丢 pending。"""
    session = _session_with_outline(tmp_path)
    reply, cards = run_session_goal_action(
        tmp_path,
        session,
        "set_session_meta",
        {"message": "嗯嗯"},
        invoke=None,
    )
    assert cards and cards[0].type == "intent_clarify"
    assert get_pending(session) and get_pending(session)["source"] == "session_meta"

    hit = try_handle_pending_message(session, "2026年度个人述职", invoke=None)
    assert isinstance(hit, Mapping)
    assert hit.get("slots", {}).get("meta_action") == "apply"
    assert get_pending(session) is None

    reply2, _cards2, _events = execute_clarify_spec(
        tmp_path,
        session,
        hit,
        message="2026年度个人述职",
        emit=None,
        invoke=None,
    )
    assert "没听清" not in reply2
    assert get_session_goal(session).get("doc_title") == "2026年度个人述职"
