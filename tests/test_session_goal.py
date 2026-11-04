"""会话级写作目标记忆。"""

from __future__ import annotations

import json

from report_writing.chat.session_goal import (
    get_session_goal,
    ingest_user_message_goal,
    is_session_goal_message,
    remember_from_user_message,
    session_goal_brief,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_remember_phrase_is_goal_message():
    def invoke(system: str, user: str) -> str:
        if "记住" in user and "｜" not in user:
            return json.dumps({"intent": "remember_goal", "confidence": 0.9})
        return json.dumps({"intent": "none", "confidence": 0.9})

    assert is_session_goal_message("记住，后面都按某某项目工作说明写", invoke=invoke)
    assert not is_session_goal_message("工作说明｜某某项目", invoke=invoke)


def test_ingest_meta_line_updates_goal():
    session = ChatSession(id="s1", phase=ChatPhase.FREEFORM_ASK.value)
    assert ingest_user_message_goal(session, "工作说明｜某某项目工作说明")
    g = get_session_goal(session)
    assert g["doc_category"] == "工作说明"
    assert g["doc_title"] == "某某项目工作说明"
    assert "工作说明" in session_goal_brief(session)


def test_remember_tool_reply():
    session = ChatSession(id="s1")

    def invoke(system: str, user: str) -> str:
        if "remember_goal" in system or "明确要求助手「记住」" in system:
            return json.dumps({"intent": "remember_goal", "confidence": 0.9})
        if "写作会话目标提取器" in system or "doc_category" in system:
            return json.dumps(
                {
                    "project": "城南变电站",
                    "notes": "项目是城南变电站，写环评报告",
                }
            )
        return "{}"

    reply = remember_from_user_message(session, "记住，项目是城南变电站，写环评报告", invoke=invoke)
    assert "记下" in reply
    assert get_session_goal(session).get("project") or get_session_goal(session).get("notes")
