"""P5：handle_chat 黄金路径断言 Tool 名与阶段；离线脚本只跟 suggested_next_tools。"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage

from report_writing.agents.chat_control import ChatControlScriptModel, ChatControlState, run_chat_control
from report_writing.agents.scripted import ScriptedChatModel
from report_writing.chat.agent_chain_log import session_invoked_tools
from report_writing.chat_orchestrator import handle_chat
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChatPhase,
    ChatRequest,
    ChatSession,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.chat_session import load_or_create
from report_writing.services.store import TaskStore


def _invoke(_s: str, _u: str) -> str:
    return "{}"


def _tools_since(session: ChatSession, prior: int) -> list[str]:
    names = session_invoked_tools(session)
    return names[prior:]


class _NoToolModel(ScriptedChatModel):
    """从不调 Tool，用来验证 UI shortcut 兜底。"""

    role: str = "no_tool"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        del messages
        return self.finish("嗯。")


def test_oral_write_auto_outline_asks_entry_path(tmp_path: Path):
    resp = handle_chat(
        tmp_path,
        ChatRequest(message="写一份工作汇报 自动生成目录"),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, resp.session_id)
    tools = session_invoked_tools(session)
    assert "ask_entry_path" in tools
    assert resp.phase == ChatPhase.INTENT.value
    assert any(getattr(c, "type", "") == "intent_clarify" for c in resp.cards)


def test_click_without_blueprint_resolves_and_hops_suggested(tmp_path: Path):
    first = handle_chat(
        tmp_path,
        ChatRequest(message="写一份工作汇报 自动生成目录"),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, first.session_id)
    prior = len(session_invoked_tools(session))
    resp = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=first.session_id,
            message="无蓝本 · 自己写",
            action="resolve_intent_clarify",
            action_payload={
                "option_id": "without_blueprint",
                "option_label": "无蓝本 · 自己写",
            },
        ),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, resp.session_id)
    tools = _tools_since(session, prior)
    assert tools[0] == "resolve_intent_clarify"
    assert "skip_blueprint" in tools or "submit_outline" in tools or "set_session_meta" in tools
    assert resp.phase in {
        ChatPhase.INTENT.value,
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.READY_TO_WRITE.value,
    }


def test_click_skip_blueprint_invokes_same_tool(tmp_path: Path):
    named = handle_chat(
        tmp_path,
        ChatRequest(message="写一份工作汇报 自动生成目录"),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, named.session_id)
    prior = len(session_invoked_tools(session))
    resp = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=named.session_id,
            action="skip_blueprint",
            action_payload={"path_setup_only": True},
            message="",
        ),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, resp.session_id)
    tools = _tools_since(session, prior)
    assert "skip_blueprint" in tools


def test_handle_chat_select_blueprint_records_tool_and_phase(tmp_path: Path):
    from report_writing.services.robots import create_robot

    create_robot(tmp_path, {"id": "r-gold", "name": "bot"})
    path = tmp_path / "tasks" / "bp_gold"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "黄金蓝本"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)
    resp = handle_chat(
        tmp_path,
        ChatRequest(
            message="",
            action="select_blueprint",
            action_payload={"blueprint_id": "bp_gold"},
            robot_id="r-gold",
        ),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, resp.session_id)
    assert "select_blueprint" in session_invoked_tools(session)
    assert session.blueprint_id == "bp_gold" or "蓝本" in (resp.reply or "")
    assert resp.phase != ChatPhase.INTENT.value or session.blueprint_id == "bp_gold"


def test_ui_shortcut_when_agent_skips_tools(tmp_path: Path):
    from report_writing.services.robots import create_robot

    create_robot(tmp_path, {"id": "r-short", "name": "bot"})
    path = tmp_path / "tasks" / "bp_short"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "兜底蓝本"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)
    resp = handle_chat(
        tmp_path,
        ChatRequest(
            action="select_blueprint",
            action_payload={"blueprint_id": "bp_short"},
            robot_id="r-short",
        ),
        invoke=_invoke,
        model=_NoToolModel(),
    )
    session = load_or_create(tmp_path, resp.session_id)
    assert session.blueprint_id == "bp_short" or "蓝本" in (resp.reply or "")


def test_scripted_follows_suggested_not_json_plan(tmp_path: Path):
    session = ChatSession(
        id="s-sug",
        phase=ChatPhase.FREEFORM_POINTS.value,
        freeform={
            "plan": {"goal": "skip_blueprint"},
            "suggested_next_tools": [{"tool": "generate_freeform_points", "args": {}}],
            "skip_blueprint": True,
            "meta_confirmed": True,
            "title": "调研",
        },
        task_id="w-sug",
    )
    TaskStore(tmp_path / "tasks" / "w-sug").save(WritingTask(id="w-sug", title="调研"))
    state = ChatControlState(home=tmp_path, session=session)
    user_text = json.dumps(
        {
            "action": "",
            "message": "继续",
            "session": {"phase": session.phase, "task_id": session.task_id},
            "suggested_next_tools": [{"tool": "generate_freeform_points", "args": {}}],
        },
        ensure_ascii=False,
    )
    run_chat_control(
        model=ChatControlScriptModel(),
        state=state,
        user_text=user_text,
        system_prompt="",
    )
    tools = session_invoked_tools(session)
    assert "generate_freeform_points" in tools
    assert "skip_blueprint" not in tools
    assert "submit_points" not in tools


def test_rewrite_another_copy_isolates_then_asks_entry(tmp_path: Path):
    first = handle_chat(
        tmp_path,
        ChatRequest(message="写一份工作汇报 自动生成目录"),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, first.session_id)
    prior = len(session_invoked_tools(session))
    resp = handle_chat(
        tmp_path,
        ChatRequest(
            session_id=first.session_id,
            message="再写一份述职报告 自动生成大纲",
        ),
        invoke=_invoke,
    )
    session = load_or_create(tmp_path, resp.session_id)
    tools = _tools_since(session, prior)
    assert "ask_entry_path" in tools or any(
        getattr(c, "type", "") == "intent_clarify" for c in resp.cards
    )
