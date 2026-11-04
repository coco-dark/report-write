"""要素库导航：执行层按 plan 调 Tool，不再正则纠偏。"""

from __future__ import annotations

from report_writing.chat.intent_dispatch import execute_intent_plan
from report_writing.domain.models import ChatSession


def test_dispatch_empty_plan_returns_none(tmp_path):
    session = ChatSession(id="s1", phase="intent")
    assert execute_intent_plan(tmp_path, session, [], "看看有哪些要素") is None


def test_dispatch_does_not_regex_remap_wrong_match_plan(tmp_path):
    session = ChatSession(id="s1", phase="intent")
    hit = execute_intent_plan(
        tmp_path,
        session,
        [{"tool": "match_blueprints", "args": {}}],
        "看看有哪些要素",
    )
    assert hit is not None
    _reply, cards, _ = hit
    assert not any(
        getattr(c, "type", "") == "nav_action"
        and (getattr(c, "payload", None) or {}).get("target") == "elements"
        for c in cards
    )


def test_dispatch_opens_element_library_when_llm_selects(tmp_path):
    session = ChatSession(id="s1", phase="intent")
    reply, cards, _ = execute_intent_plan(
        tmp_path,
        session,
        [{"tool": "open_element_library", "args": {}}],
        "看看有哪些要素",
    )
    assert "要素" in reply
    assert cards and cards[0].type == "nav_action"
    assert cards[0].payload.get("target") == "elements"
