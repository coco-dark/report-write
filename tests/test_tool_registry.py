"""Tool 注解：description 在 @tool 上，参数说明在 Field 上。"""

from __future__ import annotations

from pathlib import Path

from report_writing.agents.chat_control import ChatControlState, build_chat_tools
from report_writing.domain.models import ChatSession


def test_chat_tools_use_inline_descriptions(tmp_path: Path):
    session = ChatSession(id="s-tr")
    state = ChatControlState(home=tmp_path, session=session)
    by_name = {t.name: t for t in build_chat_tools(state)}
    assert "set_session_meta" in by_name
    tool = by_name["set_session_meta"]
    assert "何时" in (tool.description or "")
    schema = tool.args_schema.model_json_schema()
    assert "必填" in schema["properties"]["title"]["description"]
    bp_schema = by_name["select_blueprint"].args_schema.model_json_schema()
    assert "blueprint_id" in bp_schema["properties"]
    assert bp_schema["properties"]["blueprint_id"]["description"]
    assert "何时" in (by_name["list_render_templates"].description or "")
    render_schema = by_name["get_render_template"].args_schema.model_json_schema()
    assert "必填" in (render_schema["properties"]["template_id"].get("description") or "")


def test_oral_multi_intent_module_removed():
    import importlib.util

    assert importlib.util.find_spec("report_writing.chat.intent_multi_plan") is None


def test_subagent_tools_use_inline_descriptions(tmp_path: Path):
    session = ChatSession(id="s-sub")
    state = ChatControlState(home=tmp_path, session=session)
    from report_writing.agents.subagents.blueprint import build_blueprint_subagent_tools

    by_name = {t.name: t for t in build_blueprint_subagent_tools(state)}
    match = by_name["match_blueprints_for_user"]
    assert "何时" in (match.description or "")
    schema = match.args_schema.model_json_schema()
    assert "可选" in (schema["properties"]["message"].get("description") or "")
    listed = by_name["list_blueprints_scoped"]
    assert "参数" not in (listed.description or "")
    list_schema = listed.args_schema.model_json_schema()
    assert "可选" in (list_schema["properties"]["query"].get("description") or "")
    wizard = by_name["begin_blueprint_wizard"]
    assert "参数" not in (wizard.description or "")
    hint = wizard.args_schema.model_json_schema()["properties"]["title_hint"]["description"]
    assert "可选" in hint


def test_all_bound_tools_have_descriptions(tmp_path: Path):
    session = ChatSession(id="s-audit")
    state = ChatControlState(home=tmp_path, session=session)
    tools = list(build_chat_tools(state))
    missing = [
        str(getattr(item, "name", "") or "")
        for item in tools
        if not str(getattr(item, "description", "") or "").strip()
    ]
    assert missing == []
    names = {str(getattr(item, "name", "") or "").strip() for item in tools}
    names.discard("")
    assert len(names) >= 50
