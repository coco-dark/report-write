"""对话切文档：焦点切换 + focus_document 调度。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.tool_dispatch import execute_dispatch_tool
from report_writing.chat.session_documents import (
    focus_document_key,
    set_focus_document_key,
    try_switch_focus_from_message,
    upsert_session_document,
)
from report_writing.domain.models import ChatSession


def _multi_doc_session() -> ChatSession:
    session = ChatSession(id="s_switch", blueprint_id="bpA", task_id="")
    upsert_session_document(
        session, kind="blueprint", task_id="bpA", blueprint_id="bpA", title="安全预评价-A"
    )
    upsert_session_document(
        session, kind="blueprint", task_id="bpB", blueprint_id="bpB", title="职业病危害评价"
    )
    set_focus_document_key(session, "bp:bpA")
    return session


def test_try_switch_focus_from_message_changes_focus():
    session = _multi_doc_session()
    ids = try_switch_focus_from_message(session, "切到职业病危害评价那份")
    assert ids is not None
    assert focus_document_key(session) == "bp:bpB"
    assert "职业病" in str(ids.get("title") or "")


def test_try_switch_focus_same_doc_returns_none():
    session = _multi_doc_session()
    assert try_switch_focus_from_message(session, "继续写安全预评价") is None
    assert focus_document_key(session) == "bp:bpA"


def test_focus_document_dispatch_emits_event(tmp_path):
    session = _multi_doc_session()
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "focus_document", "args": {"document_title": "职业病危害评价"}},
        "切换到职业病危害评价",
        emit=None,
        invoke=None,
    )
    assert hit is not None
    reply, cards, events = hit
    assert "切换" in reply or "已" in reply
    assert focus_document_key(session) == "bp:bpB"
    assert any(e.get("type") == "focus_document" for e in events)


def test_apply_focus_document_by_key_announces(tmp_path: Path):
    from report_writing.chat.session_documents import apply_focus_document_by_key

    session = _multi_doc_session()
    hit = apply_focus_document_by_key(session, "bp:bpB", home=tmp_path, announce=True)
    assert hit is not None
    assert focus_document_key(session) == "bp:bpB"
    assert session.messages and "切换" in str(session.messages[-1].text or "")
    assert str(session.messages[-1].document_key or "") == "bp:bpB"


def test_new_write_message_does_not_switch_to_old_doc(tmp_path):
    """再以新题写一份：禁止 focus_document 切回会话里旧文档。"""
    session = _multi_doc_session()
    upsert_session_document(
        session,
        kind="writing",
        task_id="w_new_cn",
        blueprint_id="",
        title="建设新中国",
        clear_blueprint_id=True,
    )
    set_focus_document_key(session, "task:w_new_cn")
    msg = "再以“建设新中国”为题帮我写一份文档"
    assert try_switch_focus_from_message(session, msg, title_hint="1102环境报告") is None
    hit = execute_dispatch_tool(
        tmp_path,
        session,
        {"tool": "focus_document", "args": {"document_title": "安全预评价-A"}},
        msg,
        emit=None,
        invoke=None,
    )
    assert hit is not None
    assert focus_document_key(session) == "task:w_new_cn"
    assert "建设新中国" in str(hit[0] or "")
