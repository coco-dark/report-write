"""含糊「继续」澄清辅助；多蓝本匹配挂 select_blueprint。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from report_writing.chat.action_slots import (
    ACTION_SLOT_SOURCE,
    build_action_slot_clarify,
    try_resolve_action_slot_message,
)
from report_writing.chat.blueprint_actions import finish_blueprint_match
from report_writing.chat.clarify_flow import build_freeform_next_clarify, get_pending
from report_writing.chat.progress_continue import is_progress_continue_message
from report_writing.controller import ensure_task, save_outline, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode


def test_is_progress_continue_message():
    assert is_progress_continue_message("继续")
    assert is_progress_continue_message("好的")
    assert is_progress_continue_message("接着写")
    assert not is_progress_continue_message("开始写")
    assert not is_progress_continue_message("帮我写2.3")


def test_continue_freeform_points_hangs(tmp_path: Path):
    tid = "w20260101150000ff"
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id=tid,
        freeform={
            "meta_confirmed": True,
            "outline_confirmed": True,
            "skip_blueprint": True,
            "points_step": "ask",
            "category": "工作说明",
            "title": "测试",
        },
    )
    wdir = tmp_path / "tasks" / tid
    ensure_task(wdir)
    save_spec_meta(wdir, title="测试", category="工作说明")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="一", level=1),
            OutlineNode(key="c2", sequence="2", title="二", level=1),
        ],
    )
    assert is_progress_continue_message("继续")
    reply, cards = build_freeform_next_clarify(
        session,
        phase=ChatPhase.FREEFORM_POINTS.value,
        message="继续",
        home=tmp_path,
    )
    assert cards and cards[0].type == "intent_clarify"
    pending = get_pending(session)
    assert pending and pending["source"] == "freeform"
    assert "要点" in reply or "开写" in reply


def test_continue_blueprint_select_hangs(tmp_path: Path):
    session = ChatSession(
        id="s2",
        phase=ChatPhase.BLUEPRINT_SELECT.value,
        freeform={"plan": {"goal": "pick_blueprint", "candidates": ["bp_a", "bp_b"]}},
    )
    assert is_progress_continue_message("继续")
    reply, cards = build_action_slot_clarify(
        session,
        "select_blueprint",
        {"message": "继续"},
        ["blueprint_id"],
        message="继续",
        blueprints=[{"id": "bp_a", "title": "bp_a"}, {"id": "bp_b", "title": "bp_b"}],
    )
    assert any(c.type == "intent_clarify" for c in cards)
    pending = get_pending(session)
    assert pending and pending["source"] == ACTION_SLOT_SOURCE
    assert pending["candidate"]["name"] == "select_blueprint"
    assert reply


def test_finish_match_hangs_select_clarify(tmp_path: Path):
    session = ChatSession(id="s3", phase=ChatPhase.INTENT.value)
    matches = [
        SimpleNamespace(
            id="bp1",
            title="述职报告A",
            category="述职",
            score=0.9,
            tags=[],
            chapter_count=5,
            outline_confirmed=True,
            description="",
            enabled=True,
        ),
        SimpleNamespace(
            id="bp2",
            title="述职报告B",
            category="述职",
            score=0.8,
            tags=[],
            chapter_count=6,
            outline_confirmed=True,
            description="",
            enabled=True,
        ),
    ]
    reply, cards = finish_blueprint_match(
        tmp_path, session, "写述职报告", "找到两份", matches
    )
    assert session.phase == ChatPhase.BLUEPRINT_SELECT.value
    assert any(c.type == "intent_clarify" for c in cards)
    pending = get_pending(session)
    assert pending and pending["candidate"]["name"] == "select_blueprint"
    assert "选用" in reply or "份" in reply or "挑" in reply

    # 口语「用第二份」
    hit = try_resolve_action_slot_message(session, "用第二份")
    assert hit is not None
    assert hit["slots"].get("blueprint_id") == "bp2" or (
        (hit["slots"].get("args") or {}).get("blueprint_id") == "bp2"
    )
