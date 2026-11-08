"""会话级写作目标记忆。"""

from __future__ import annotations

import json

from report_writing.chat.session_goal import (
    get_session_goal,
    ingest_user_message_goal,
    is_session_goal_message,
    message_looks_like_goal_update,
    remember_from_user_message,
    session_goal_brief,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_remember_phrase_is_goal_message():
    assert is_session_goal_message("记住，后面都按某某项目工作说明写")
    assert not is_session_goal_message("工作说明｜某某项目")


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


def test_chapter_ops_are_not_goal_updates():
    assert not message_looks_like_goal_update("分析第4.2章")
    assert not message_looks_like_goal_update("依据样本分析已选 3 章")
    assert message_looks_like_goal_update("工作说明｜城南变")
    assert message_looks_like_goal_update("记住，后面都按环评写")


def test_ingest_chapter_ops_does_not_call_llm():
    session = ChatSession(id="s1")
    calls = {"n": 0}

    def invoke(system: str, user: str) -> str:
        calls["n"] += 1
        return "{}"

    ingest_user_message_goal(session, "分析第4.2章", invoke=invoke)
    from report_writing.chat.writing_brief import ingest_user_turn

    ingest_user_turn(session, "写第4.2.1章", invoke=invoke)
    assert calls["n"] == 0
