"""Phase E：create_agent middleware 写链路日志，且不重复记工具。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import tool

from report_writing.agents.react import run_react
from report_writing.agents.scripted import ScriptedChatModel
from report_writing.chat.agent_chain_log import session_invoked_tools
from report_writing.domain.models import ChatPhase, ChatSession


class _PingThenFinish(ScriptedChatModel):
    role: str = "ping_then_finish"

    def decide(self, messages: list[BaseMessage]) -> AIMessage:
        if not any(getattr(m, "name", None) == "ping" for m in messages):
            return self.call("ping", {})
        return self.finish("done")


@tool
def ping() -> str:
    """noop ping for middleware tests."""
    return json.dumps({"ok": True}, ensure_ascii=False)


def test_run_react_middleware_logs_each_tool_once():
    session = ChatSession(id="mw", phase=ChatPhase.INTENT.value)
    run_react(
        _PingThenFinish(),
        [ping],
        "test",
        "hi",
        "chat_control",
        session=session,
    )
    names = session_invoked_tools(session)
    assert names == ["ping"]
    results = [
        row
        for row in (session.freeform or {}).get("agent_chain_log") or []
        if isinstance(row, dict) and row.get("step") == "tool_result"
    ]
    assert len(results) == 1
    assert results[0]["detail"]["tool"] == "ping"


def test_run_react_middleware_without_session_still_runs():
    result = run_react(_PingThenFinish(), [ping], "test", "hi", "anon")
    messages = result["messages"]
    assert any(getattr(m, "name", None) == "ping" for m in messages)
