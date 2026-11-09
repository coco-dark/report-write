"""切文档 / 切线路：焦点重绑定 writing_path 与收录 id 作用域。"""

from __future__ import annotations

from report_writing.chat.session_state import (
    WRITING_PATH_BLUEPRINT,
    WRITING_PATH_FREEFORM,
    get_writing_path,
)
from report_writing.chat.session_documents import (
    collected_blueprint_id_for_focus,
    rebind_session_to_focus_document,
    set_focus_document_key,
    upsert_session_document,
)
from report_writing.domain.models import ChatSession


def test_rebind_freeform_task_sets_writing_path():
    session = ChatSession(id="rb1", phase="freeform_outline")
    upsert_session_document(
        session,
        kind="outline",
        task_id="w_doc1",
        title="环境报告",
        clear_blueprint_id=True,
    )
    upsert_session_document(
        session,
        kind="blueprint",
        task_id="bpA",
        blueprint_id="bpA",
        title="安全预评价",
    )
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "blueprint",
        "collected_blueprint_id": "bp_old",
    }
    session.blueprint_id = "bpA"
    set_focus_document_key(session, "task:w_doc1")
    session.task_id = "w_doc1"
    rebind_session_to_focus_document(session)
    assert get_writing_path(session) == WRITING_PATH_FREEFORM
    assert collected_blueprint_id_for_focus(session) == ""


def test_rebind_blueprint_doc_sets_blueprint_path():
    session = ChatSession(id="rb2", phase="ready_to_write")
    upsert_session_document(
        session,
        kind="outline",
        task_id="w_doc1",
        title="环境报告",
        clear_blueprint_id=True,
    )
    upsert_session_document(
        session,
        kind="blueprint",
        task_id="bpA",
        blueprint_id="bpA",
        title="安全预评价",
    )
    session.freeform = {**(session.freeform or {}), "writing_path": "freeform"}
    set_focus_document_key(session, "bp:bpA")
    session.blueprint_id = "bpA"
    session.task_id = ""
    rebind_session_to_focus_document(session)
    assert get_writing_path(session) == WRITING_PATH_BLUEPRINT


def test_collected_scoped_to_focus_writing_task():
    session = ChatSession(id="rb3", task_id="w1")
    upsert_session_document(
        session,
        kind="blueprint",
        task_id="bp1",
        blueprint_id="bp1",
        writing_task_id="w1",
        title="已收录",
    )
    upsert_session_document(
        session,
        kind="outline",
        task_id="w2",
        title="第二份",
        clear_blueprint_id=True,
    )
    session.freeform = {
        **(session.freeform or {}),
        "collected_blueprint_id": "bp1",
        "writing_path": "freeform",
    }
    docs = dict((session.freeform or {}).get("session_documents") or {})
    docs["bp:bp1"] = {**docs["bp:bp1"], "collected_blueprint_id": "bp1"}
    session.freeform = {**(session.freeform or {}), "session_documents": docs}

    set_focus_document_key(session, "bp:bp1")
    session.task_id = "w1"
    assert collected_blueprint_id_for_focus(session) == "bp1"

    set_focus_document_key(session, "task:w2")
    session.task_id = "w2"
    rebind_session_to_focus_document(session)
    assert collected_blueprint_id_for_focus(session) == ""
