"""新写作入口：先定名+路径澄清，勿默认 match；关键词勿靠「报告」误匹配。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.agents.chat_control import ChatControlState, build_chat_tools
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree
from report_writing.services.robots import search_blueprints
from report_writing.services.store import TaskStore


def test_ask_entry_path_tool_asks_name_first(tmp_path: Path):
    session = ChatSession(id="s1", phase=ChatPhase.INTENT.value)
    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="在帮我写一份述职报告",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    assert "ask_entry_path" in tools
    raw = tools["ask_entry_path"].invoke(
        {"message": "在帮我写一份述职报告", "title_hint": "述职报告"}
    )
    data = json.loads(raw)
    assert data.get("ok") is True
    assert data.get("needs_doc_name") is not True
    hint = data.get("reply_hint") or ""
    assert "先给这份文档起个名字" not in hint
    assert "路" in hint or "蓝本" in hint or state.cards


def test_match_blueprints_requires_doc_name(tmp_path: Path):
    session = ChatSession(id="s2", phase=ChatPhase.INTENT.value)
    state = ChatControlState(
        home=tmp_path,
        session=session,
        last_user_message="在帮我写一份述职报告",
    )
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["match_blueprints"].invoke({"message": "在帮我写一份述职报告"})
    data = json.loads(raw)
    assert data.get("ok") is True
    assert "doc_name" not in (data.get("missing_slots") or [])


def test_search_does_not_match_unrelated_report_by_generic_term(tmp_path: Path):
    home = tmp_path
    path = home / "tasks" / "bp_safe"
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "1031-安全报告"
    task.category = "安全"
    task.description = "用于写安全预评价报告"
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", title="概况", points=["p"], sequence="1")]
    )
    task.outline_confirmed = True
    TaskStore(path).save(task)

    # 「述职」特色词未命中 → 不得仅因「报告」返回安全蓝本
    assert search_blueprints(home, q="在帮我写一份述职报告") == []
    # 对口词仍能命中
    hits = search_blueprints(home, q="帮我写一份安全预评价报告")
    assert any(item.id == "bp_safe" for item in hits)
