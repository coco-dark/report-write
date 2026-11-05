"""Tool registry 与 LangChain description 绑定。"""

from __future__ import annotations

from pathlib import Path

from report_writing.agents.chat_control import ChatControlState, build_chat_tools
from report_writing.chat.tool_registry import CHAT_CONTROL_TOOL_DOCS, EXPORT_RESOURCE_TOOL_DOCS
from report_writing.domain.models import ChatSession


def test_chat_tools_use_registry_descriptions(tmp_path: Path):
    session = ChatSession(id="s-tr")
    state = ChatControlState(home=tmp_path, session=session)
    by_name = {t.name: t for t in build_chat_tools(state)}
    assert "set_session_meta" in by_name
    tool = by_name["set_session_meta"]
    assert tool.description == CHAT_CONTROL_TOOL_DOCS["set_session_meta"].strip()
    assert "title" in tool.description
    assert by_name["list_render_templates"].description == EXPORT_RESOURCE_TOOL_DOCS[
        "list_render_templates"
    ].strip()


def test_intent_plan_default_disabled():
    from report_writing.infra.config_settings import intent_plan_enabled

    assert intent_plan_enabled() is False
