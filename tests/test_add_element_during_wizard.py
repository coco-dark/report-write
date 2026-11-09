"""向导进行中也可给某章加知识库要素，不能被「还在向导里」吞掉。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.gates import _ensure_analysis_choice_cards
from report_writing.chat.chapter_resolve import resolve_chapter_key_by_title
from report_writing.chat.clarify_llm import resolve_wizard_action
from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message
from report_writing.chat.writing_control_route import apply_writing_control_classification
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatCard, ChatPhase, ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


MSG = "为建设规模添加知识库要素 关联知识库 检索词为建设规模 站址 工程投资"


def _seed(home: Path) -> str:
    bid = "bp_el_wizard"
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "安全预评价"
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", points=["p"], keywords=["k"]),
            OutlineNode(key="c_scale", sequence="2.1", title="建设规模"),
            OutlineNode(key="c3", sequence="3", title="未分析"),
        ]
    )
    TaskStore(path).save(task)
    return bid


def test_structural_seq_hint_from_message():
    assert parse_study_sequences_from_message(
        "为2.2添加知识库要素 检索词为建设规模、站址、工程总投资"
    ) == ["2.2"]


def test_title_matches_chapter(tmp_path: Path):
    bid = _seed(tmp_path)
    task = TaskStore(tmp_path / "tasks" / bid).load()
    assert resolve_chapter_key_by_title(task, MSG) == "c_scale"


def test_wizard_does_not_jail_add_element(tmp_path: Path):
    bid = _seed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )
    action = resolve_wizard_action(
        MSG,
        {"blueprint_id": bid, "step": "study"},
        invoke=lambda _s, _u: '{"command":"none","confidence":0.9}',
        session=session,
    )
    kind = str((action or {}).get("type") or "")
    cmd = str((action or {}).get("command") or "none")
    assert kind in {"", "command", "none"} or cmd in {"", "none"}
    if kind == "command":
        assert cmd in {"", "none"}
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"


def test_writing_control_adds_element_without_task_id(tmp_path: Path):
    bid = _seed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        task_id="",
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )

    hit = apply_writing_control_classification(
        tmp_path,
        session,
        MSG,
        {
            "tool": "add_chapter_element",
            "confidence": 0.9,
            "payload": {
                "kind": "rag_search",
                "name": "建设规模",
                "query": "建设规模 站址 工程投资",
                "chapter_key_hint": "建设规模",
            },
        },
    )
    assert hit is not None
    reply, cards, _ = hit
    assert "建设规模" in reply
    assert any(getattr(c, "type", "") == "element_configure_pending" for c in cards)
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"
    pending = ((session.freeform or {}).get("element_configure_pending") or [])[-1]
    draft = pending.get("draft") or {}
    # rag_search 已归一为 file
    assert draft.get("kind") in {"file", "rag_search"}
    assert "工程投资" in (draft.get("query") or "")
    assert pending.get("chapter_key") == "c_scale"


def test_analysis_cards_not_forced_on_element_pending(tmp_path: Path):
    bid = _seed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )
    cards = _ensure_analysis_choice_cards(
        tmp_path,
        session,
        [ChatCard(type="element_configure_pending", title="确认要素配置", payload={})],
    )
    assert all(c.type != "chapter_picker" for c in cards)
    assert any(c.type == "element_configure_pending" for c in cards)


def test_analysis_cards_not_forced_on_wizard_card(tmp_path: Path):
    bid = _seed(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )
    cards = _ensure_analysis_choice_cards(
        tmp_path,
        session,
        [ChatCard(type="blueprint_wizard", title="创建蓝本 · 分析要点", payload={"step": "study"})],
    )
    assert all(c.type != "chapter_picker" for c in cards)
