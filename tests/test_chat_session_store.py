"""聊天会话存储：文件回落与 document 解析。"""

from __future__ import annotations

from pathlib import Path

from report_writing.services.chat_session import (
    _as_dict,
    create_session,
    delete_session,
    get_session,
    list_sessions,
    save_session,
)
from report_writing.domain.models import ChatMessage, ChatPhase, ChatSession, ChatUiStateRequest


def test_file_store_roundtrip_when_database_off(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    session = create_session(tmp_path, robot_id="r1")
    session.phase = ChatPhase.BLUEPRINT_SELECT.value
    session.messages.append(ChatMessage(role="user", text="写报告"))
    save_session(tmp_path, session)

    loaded = get_session(tmp_path, session.id)
    assert loaded is not None
    # robot 不在工作区绑定时会被 sanitize 清空，属预期
    assert loaded.phase == ChatPhase.BLUEPRINT_SELECT.value
    assert loaded.messages[0].text == "写报告"
    assert (tmp_path / "chat_sessions" / f"{session.id}.json").is_file()


def test_list_sessions_returns_summaries(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    empty = create_session(tmp_path, robot_id="r1")
    save_session(tmp_path, empty)

    a = create_session(tmp_path, robot_id="r1")
    a.messages.append(ChatMessage(role="user", text="写一份安全预评价"))
    a.messages.append(ChatMessage(role="assistant", text="已为你匹配蓝本"))
    save_session(tmp_path, a)

    b = create_session(tmp_path, robot_id="r2")
    b.messages.append(ChatMessage(role="user", text="起草函件"))
    save_session(tmp_path, b)

    listed = list_sessions(tmp_path, limit=10)
    ids = {item.id for item in listed}
    assert a.id in ids
    assert b.id in ids
    assert empty.id not in ids
    hit = next(item for item in listed if item.id == a.id)
    assert "安全预评价" in hit.title
    assert hit.message_count == 2


def test_delete_session_removes_file_and_list(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    session = create_session(tmp_path, robot_id="r1")
    session.messages.append(ChatMessage(role="user", text="写一份安全预评价"))
    save_session(tmp_path, session)
    path = tmp_path / "chat_sessions" / f"{session.id}.json"
    assert path.is_file()

    assert delete_session(tmp_path, session.id) is True
    assert get_session(tmp_path, session.id) is None
    assert not path.is_file()
    assert session.id not in {item.id for item in list_sessions(tmp_path, limit=10)}
    assert delete_session(tmp_path, session.id) is False


def test_ui_state_bid_never_allows_meta_step(tmp_path: Path, monkeypatch):
    from report_writing.services.chat_session import apply_chat_ui_state

    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    session = ChatSession(
        id="s_meta_guard",
        blueprint_id="bp1",
        freeform={"blueprint_wizard": {"blueprint_id": "bp1", "step": "upload", "title": "T"}},
    )
    apply_chat_ui_state(
        session,
        ChatUiStateRequest(
            freeform_patch={
                "blueprint_wizard": {"blueprint_id": "bp1", "step": "meta", "title": "T"},
                "runtime": {
                    "workflows": {
                        "blueprint_wizard": {"blueprint_id": "bp1", "step": "meta", "title": "T"}
                    }
                },
            }
        ),
    )
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("blueprint_id") == "bp1"
    assert wiz.get("step") == "upload"


def test_ui_state_strips_stale_study_triggers(tmp_path: Path, monkeypatch):
    """前端 strip 后的 ui-state 必须清掉服务端残留 trigger，否则保存章节配置会再点燃分析。"""
    from report_writing.services.chat_session import apply_chat_ui_state

    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    session = ChatSession(
        id="s_strip_study",
        blueprint_id="bp1",
        freeform={
            "blueprint_wizard": {
                "blueprint_id": "bp1",
                "step": "study",
                "trigger_begin_study": True,
                "trigger_chapter_study": ["ch-a"],
                "chapter_study_keys": ["ch-a"],
                "study_stream_active": True,
            }
        },
    )
    apply_chat_ui_state(
        session,
        ChatUiStateRequest(
            freeform_patch={
                "blueprint_wizard": {"blueprint_id": "bp1", "step": "study"},
                "runtime": {
                    "workflows": {
                        "blueprint_wizard": {"blueprint_id": "bp1", "step": "study"}
                    }
                },
            }
        ),
    )
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("step") == "study"
    assert "trigger_begin_study" not in wiz
    assert "trigger_chapter_study" not in wiz
    assert "chapter_study_keys" not in wiz
    assert "study_stream_active" not in wiz


def test_as_dict_accepts_json_string_and_dict():
    assert _as_dict('{"id":"cs1"}')["id"] == "cs1"
    assert _as_dict({"id": "cs2"})["id"] == "cs2"
