"""学习未完成也可写已分析章；继续学习只触发未学章。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.resource_actions import execute_wizard_command
from report_writing.chat.write_chapter_intent import try_write_chapters_from_message
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _seed_partial(home: Path) -> str:
    bid = "bp_partial_study"
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "部分已学"
    task.sample_ready = True
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="已学", points=["p1"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="未学"),
            OutlineNode(key="c3", sequence="3", title="也未学"),
        ]
    )
    TaskStore(path).save(task)
    return bid


def test_go_study_continues_unanalyzed_only(tmp_path: Path):
    bid = _seed_partial(tmp_path)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )
    reply, cards = execute_wizard_command(tmp_path, session, "go_study")
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("trigger_begin_study") in (None, False)
    keys = list(wizard.get("trigger_chapter_study") or [])
    assert set(keys) == {"c2", "c3"}
    assert "接着学" in reply or "还没完成" in reply
    assert cards and cards[0].type == "blueprint_wizard"


def test_try_write_analyzed_offers_picker(tmp_path: Path):
    bid = _seed_partial(tmp_path)
    session = ChatSession(id="s1", blueprint_id=bid, freeform={})
    reply, cards, _ = try_write_chapters_from_message(
        tmp_path, session, "先写已分析的章节", write_analyzed_only=True
    )
    assert "没认出要学" not in reply
    assert any(c.type == "chapter_picker" for c in cards) or "分析" in reply or "写" in reply
