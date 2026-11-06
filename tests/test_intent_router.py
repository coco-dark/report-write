"""入口意图（意图模型，非正则表）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from report_writing.chat.intent_router import (
    allows_agent_blueprint_select,
    message_explicitly_requests_blueprint,
    pick_auto_blueprint_id,
)


def _bp(bid: str, title: str):
    return SimpleNamespace(id=bid, title=title, description="")


def _entry_invoke(system: str, user: str) -> str:
    msg = user.split("用户：", 1)[-1].strip() if "用户：" in user else user
    if "入口意图" in system:
        if "看看有哪些蓝本" in msg:
            return json.dumps({"intent": "match_blueprints", "confidence": 0.9}, ensure_ascii=False)
        if "航天器说明书" in msg:
            return json.dumps({"intent": "general_write", "confidence": 0.9, "is_vague_write": True})
        if "写一份报告" in msg or "写一个报告" in msg or "帮我写一份安全报告" in msg:
            return json.dumps(
                {"intent": "general_write", "confidence": 0.9, "is_vague_write": True},
                ensure_ascii=False,
            )
        if "就用这份蓝本" in msg:
            return json.dumps(
                {
                    "intent": "explicit_select",
                    "confidence": 0.9,
                    "agent_may_select": True,
                },
                ensure_ascii=False,
            )
    if "候选" in system or "候选：" in system:
        if "工程环境影响报告" in msg:
            return json.dumps({"blueprint_id": "bp1", "confidence": 0.92}, ensure_ascii=False)
        if "就用这份蓝本" in msg and len(system) > 50:
            return json.dumps({"blueprint_id": "bp1", "confidence": 0.92}, ensure_ascii=False)
        return json.dumps({"blueprint_id": "", "confidence": 0.2}, ensure_ascii=False)
    return "{}"


def test_vague_write_does_not_auto_pick_single_blueprint():
    matches = [_bp("bp1", "工程环境影响报告")]
    assert pick_auto_blueprint_id("帮我写一个报告", matches, invoke=_entry_invoke) == ""
    assert pick_auto_blueprint_id("帮我写一份报告", matches, invoke=_entry_invoke) == ""


def test_title_in_message_auto_picks():
    matches = [_bp("bp1", "工程环境影响报告"), _bp("bp2", "其他")]
    assert pick_auto_blueprint_id("用工程环境影响报告写一份", matches, invoke=_entry_invoke) == "bp1"


def test_vague_write_disallows_agent_select():
    assert (
        allows_agent_blueprint_select(message="帮我写一份报告", user_action="", invoke=_entry_invoke)
        is False
    )
    assert (
        allows_agent_blueprint_select(
            message="帮我写一份安全报告", user_action="", invoke=_entry_invoke
        )
        is False
    )
    assert allows_agent_blueprint_select(message="帮我写一份报告", user_action="select_blueprint") is True


def test_explicit_select_single():
    matches = [_bp("bp1", "工程环境影响报告")]
    assert pick_auto_blueprint_id("就用这份蓝本写", matches, invoke=_entry_invoke) == "bp1"


def test_message_explicitly_requests_blueprint():
    assert message_explicitly_requests_blueprint("看看有哪些蓝本", invoke=_entry_invoke) is True
    assert message_explicitly_requests_blueprint("写一份航天器说明书", invoke=_entry_invoke) is False


def test_create_blueprint_lexical_only_when_offline():
    from report_writing.chat.entry_intent import is_create_blueprint_intent

    assert is_create_blueprint_intent("我有模版写一份报告", invoke=None) is True

    def _none(_s: str, _u: str) -> str:
        return json.dumps({"intent": "none", "confidence": 0.9})

    assert is_create_blueprint_intent("我有模版写一份报告", invoke=_none) is False

    def _create(_s: str, _u: str) -> str:
        return json.dumps({"intent": "create_blueprint", "confidence": 0.9})

    assert is_create_blueprint_intent("按样本写环境报告", invoke=_create) is True
