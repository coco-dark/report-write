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
