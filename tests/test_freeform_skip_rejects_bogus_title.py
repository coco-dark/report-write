"""跳过蓝本时，不得把模型旁白当成类型｜标题并直接生成目录。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from report_writing.chat.actions import _action_skip_blueprint
from report_writing.chat.helpers import is_vague_freeform_meta
from report_writing.chat.session_goal import infer_freeform_meta_from_dialogue
from report_writing.domain.models import ChatMessage, ChatPhase, ChatSession


def test_bogus_llm_title_is_vague():
    assert is_vague_freeform_meta(
        "用户希望不用蓝本，直接搭目录写文档，但尚未说明具体写作题目。",
        field=True,
    )
    assert is_vague_freeform_meta("不用蓝本直接写", field=True)
    assert not is_vague_freeform_meta("2026个人述职报告", field=True)


def test_infer_dialogue_rejects_bogus_title():
    session = ChatSession(
        id="s1",
        messages=[ChatMessage(role="user", text="不用蓝本，直接搭目录写")],
    )

    def invoke(_system: str, _user: str) -> str:
        return (
            '{"doc_category":"文档","doc_title":"用户希望不用蓝本，直接搭目录写文档，'
            '但尚未说明具体写作题目。","confidence":0.9}'
        )

    assert infer_freeform_meta_from_dialogue(session, invoke) is None


def test_skip_blueprint_asks_meta_when_title_unknown(tmp_path: Path):
    session = ChatSession(
        id="s2",
        phase=ChatPhase.INTENT.value,
        messages=[ChatMessage(role="user", text="不用蓝本，直接搭目录写")],
    )
    invoke = MagicMock(
        return_value=(
            '{"doc_category":"文档","doc_title":"用户希望不用蓝本，直接搭目录写文档，'
            '但尚未说明具体写作题目。","confidence":0.9}'
        )
    )
    reply, cards = _action_skip_blueprint(session, home=tmp_path, invoke=invoke)
    assert session.phase == ChatPhase.FREEFORM_ASK.value
    assert not session.freeform.get("meta_confirmed")
    assert (
        "类型｜标题" in reply
        or "类型|标题" in reply
        or "告诉我写什么" in reply
        or "想写什么" in reply
        or "叫什么" in reply
    )
    assert not any(c.type == "outline_preview" for c in cards)


def test_skip_blueprint_does_not_collect_meta(tmp_path: Path, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("skip_blueprint must not collect freeform meta")

    monkeypatch.setattr(
        "report_writing.chat.actions._collect_freeform_meta",
        _boom,
    )
    session = ChatSession(
        id="s_no_meta",
        phase=ChatPhase.INTENT.value,
        messages=[ChatMessage(role="user", text="写一份述职报告｜2026个人述职，不用蓝本")],
    )
    reply, cards = _action_skip_blueprint(
        session,
        home=tmp_path,
        invoke=MagicMock(return_value='{"doc_category":"述职","doc_title":"2026个人述职"}'),
        extra_message="写一份述职报告｜2026个人述职，不用蓝本",
        title_hint="2026个人述职",
    )
    assert not (session.freeform or {}).get("meta_confirmed")
    assert not any(getattr(c, "type", "") == "outline_preview" for c in cards)
    assert "目录我先帮你搭" not in (reply or "")


def test_skip_blueprint_tool_suggests_set_session_meta(tmp_path: Path):
    import json

    from report_writing.agents.chat_control import ChatControlState, build_chat_tools

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_skip_tool", phase=ChatPhase.INTENT.value)
    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="不用蓝本直接写",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    data = json.loads(tools["skip_blueprint"].invoke({}))
    assert data.get("ok") is True
    assert "suggested_next_tool" not in data
    assert data.get("needs_doc_name") is True
    assert "目录我先帮你搭" not in str(data.get("reply_hint") or "")


def test_skip_blueprint_named_auto_outline_suggests_submit_outline(tmp_path: Path):
    import json

    from report_writing.agents.chat_control import ChatControlState, build_chat_tools
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        mark_focus_entry_path,
        mark_focus_freeform_mode,
    )

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_skip_named", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "我的述职", home=tmp_path)
    mark_focus_entry_path(session, "freeform")
    mark_focus_freeform_mode(session, "auto_outline")
    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="自动生成大纲",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    data = json.loads(tools["skip_blueprint"].invoke({"path_setup_only": True}))
    assert "suggested_next_tool" not in data
    assert data.get("needs_doc_name") is False
