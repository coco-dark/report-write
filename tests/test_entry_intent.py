"""入口结构提示（不再整句 LLM 路由）。"""

from __future__ import annotations

from types import SimpleNamespace

from report_writing.chat.entry_intent import (
    allows_agent_blueprint_select,
    is_create_blueprint_intent,
    message_explicitly_requests_blueprint,
    pick_auto_blueprint_id,
)


def _bp(bid: str, title: str):
    return SimpleNamespace(id=bid, title=title, description="")


def test_vague_write_does_not_auto_pick_single_blueprint():
    matches = [_bp("bp1", "工程环境影响报告")]
    assert pick_auto_blueprint_id("帮我写一个报告", matches) == ""
    assert pick_auto_blueprint_id("帮我写一份报告", matches) == ""
    assert pick_auto_blueprint_id("用工程环境影响报告写一份", matches) == ""
    assert pick_auto_blueprint_id("就用这份蓝本写", matches) == ""


def test_vague_write_disallows_agent_select():
    assert allows_agent_blueprint_select(message="帮我写一份报告", user_action="") is False
    assert allows_agent_blueprint_select(
        message="帮我写一份安全报告", user_action=""
    ) is False
    assert allows_agent_blueprint_select(
        message="帮我写一份报告", user_action="select_blueprint"
    ) is True


def test_message_explicitly_requests_blueprint():
    assert message_explicitly_requests_blueprint("看看有哪些蓝本") is True
    assert message_explicitly_requests_blueprint("写一份航天器说明书") is False


def test_create_blueprint_lexical():
    assert is_create_blueprint_intent("我有模版写一份报告") is True
    assert is_create_blueprint_intent("按样本写环境报告") is True
    assert is_create_blueprint_intent("帮我写一份报告") is False
