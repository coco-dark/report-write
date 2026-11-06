from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.blueprint_actions import finish_blueprint_match
from report_writing.chat.clarify_llm import classify_no_match_path_llm
from report_writing.chat.session_state import (
    WRITING_PATH_FREEFORM,
    lock_writing_path,
    touch_session_plan,
)
from report_writing.chat.session_goal import merge_session_goal
from report_writing.chat.write_path_after_match import (
    clear_pending_no_match,
    get_pending_no_match_context,
    is_awaiting_path_after_no_match,
    reply_ask_write_path,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_finish_match_without_results_asks_instead_of_outline(tmp_path: Path):
    session = ChatSession(id="s1")
    reply, cards = finish_blueprint_match(
        tmp_path,
        session,
        "帮我写一份安全预评价报告",
        "",
        [],
        invoke=None,
    )
    assert is_awaiting_path_after_no_match(session)
    assert "对口" in reply or "蓝本" in reply or "怎么开始" in reply
    assert any(
        getattr(c, "type", "") in {"intent_clarify", "skip_blueprint"} for c in cards
    )
    assert "49" not in reply


def test_no_match_with_template_talk_does_not_auto_start_wizard(tmp_path: Path):
    session = ChatSession(id="s-tpl")
    reply, cards = finish_blueprint_match(
        tmp_path,
        session,
        "我有模版写一份环境报告",
        "",
        [],
        invoke=None,
    )
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert not str(wiz.get("step") or "").strip()
    assert is_awaiting_path_after_no_match(session) or any(
        getattr(c, "type", "") in {"intent_clarify", "skip_blueprint"} for c in cards
    )
    assert "向导" not in (reply or "") or "怎么开始" in (reply or "") or "蓝本" in (reply or "")


def test_write_path_help_outline_clears_pending(tmp_path: Path):
    session = ChatSession(id="s2")
    reply_ask_write_path(session, "安全预评价报告")

    def invoke(system: str, user: str) -> str:
        if "no match" in system.lower() or "接下来怎么走" in system:
            return json.dumps({"intent": "help_build_outline", "confidence": 0.9})
        return "{}"

    pending = get_pending_no_match_context(session)
    topic = str(pending.get("topic") or "").strip()
    hit = classify_no_match_path_llm(
        "没有样板，先帮我起草目录", topic=topic, invoke=invoke
    )
    assert hit and hit.get("intent") == "help_build_outline"
    clear_pending_no_match(session)
    from report_writing.chat.actions import _clear_blueprint_wizard_state

    _clear_blueprint_wizard_state(session)
    session.blueprint_id = ""
    lock_writing_path(session, WRITING_PATH_FREEFORM)
    session.phase = ChatPhase.FREEFORM_ASK.value
    touch_session_plan(session, goal="help_draft_outline")
    if topic:
        merge_session_goal(session, {"notes": topic, "summary": topic[:200]})
    reply = (
        "好的，我们先不走蓝本，按你的题目把目录搭起来。"
        "跟我说写什么、文档打算叫什么就行；也可以直接说「帮我生成目录」。"
    )
    assert not is_awaiting_path_after_no_match(session)
    assert "目录" in reply or "题目" in reply or "叫什么" in reply
