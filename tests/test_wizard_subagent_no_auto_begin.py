"""向导子代理不得用 create-intent 代调 begin；只 observation 给总控。"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from report_writing.agents.state import ChatControlState
from report_writing.agents.subagents.blueprint_wizard_impl import _make_wizard_scripted_model
from report_writing.domain.models import ChatPhase, ChatSession
from report_writing.chat.clarify_llm import resolve_wizard_action


def test_resolve_wizard_action_noop_without_active_wizard():
    assert (
        resolve_wizard_action(
            "我有模版写一份报告",
            {},
            invoke=lambda _s, _u: '{"command":"submit_meta","confidence":0.9}',
        )
        is None
    )


def test_wizard_scripted_does_not_call_begin(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="wiz_obs", phase=ChatPhase.INTENT.value)
    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="我有模版写一份报告",
    )
    model = _make_wizard_scripted_model(state, invoke=None)
    msg = model.decide([HumanMessage(content="我有模版写一份报告")])
    assert not getattr(msg, "tool_calls", None)
    tools = [
        str(t.get("tool") or "")
        for t in (session.freeform or {}).get("suggested_next_tools") or []
    ]
    assert tools == ["begin_blueprint_wizard"]
    args = ((session.freeform or {}).get("suggested_next_tools") or [{}])[0].get("args") or {}
    assert args.get("explicit_create") is True
    assert "begin_blueprint_wizard" in str(msg.content or "")


def test_wizard_scripted_hops_in_wizard_command(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="wiz_cmd",
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={
            "blueprint_wizard": {
                "blueprint_id": "bp_w",
                "step": "outline",
            }
        },
    )
    state = ChatControlState(home=tmp_path, session=session)
    model = _make_wizard_scripted_model(
        state,
        invoke=lambda _s, _u: '{"command":"go_outline_confirm","confidence":0.9}',
    )
    msg = model.decide([HumanMessage(content="确认目录")])
    calls = list(getattr(msg, "tool_calls", None) or [])
    assert calls
    assert calls[0].get("name") == "go_outline_confirm_step"
