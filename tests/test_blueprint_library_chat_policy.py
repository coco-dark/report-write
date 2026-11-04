"""蓝本库：对话侧不得删库；依据模版向导不入库。"""

from __future__ import annotations

from report_writing.agents.resource_tools import build_export_resource_tools
from report_writing.chat.resource_actions import _begin_blueprint_wizard
from report_writing.domain.models import ChatPhase, ChatSession


def test_chat_tools_exclude_delete_writing_blueprint(tmp_path):
    session = ChatSession(id="s-bp", phase=ChatPhase.INTENT.value)
    state = type("S", (), {"home": tmp_path, "session": session, "invoke": None})()
    names = {t.name for t in build_export_resource_tools(state)}
    assert "delete_writing_blueprint" not in names


def test_begin_wizard_copy_mentions_not_in_library():
    session = ChatSession(id="s-wiz", phase=ChatPhase.INTENT.value)
    reply, cards = _begin_blueprint_wizard(session, title_hint="测试文档")
    assert "收录" in reply or "蓝本库" in reply
    assert cards and cards[0].type == "blueprint_wizard"
