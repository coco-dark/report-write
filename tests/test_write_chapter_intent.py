"""「帮我写2.3.1」应识别为开写，不能误判成学习。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.clarify_llm import resolve_wizard_action
from report_writing.chat.write_chapter_intent import (
    keys_for_write_scope,
    message_asks_to_write_not_study,
    parse_write_sequences_from_message,
    resolve_write_chapter_keys,
    try_write_chapters_from_message,
    utterance_requests_study,
    write_sequences_have_descendants,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_write_not_study_probe():
    assert message_asks_to_write_not_study("帮我写第3章")
    assert message_asks_to_write_not_study("写某一张")
    assert not message_asks_to_write_not_study("开始分析第3章")
    assert not message_asks_to_write_not_study("继续学习")
    assert utterance_requests_study("分析第4.2.3章")
    assert utterance_requests_study("重新分析章节")
    assert not utterance_requests_study("你好")
    assert not utterance_requests_study("先看进度")


def test_parse_write_sequences():
    assert parse_write_sequences_from_message("帮我写2.3.1章节") == ["2.3.1"]
    assert parse_write_sequences_from_message("写第3章") == ["3"]
    assert parse_write_sequences_from_message("写第二章") == ["2"]
    assert parse_write_sequences_from_message("写1.1章节") == ["1.1"]


def _seed_blueprint(home: Path, *, analyzed_231: bool = True) -> str:
    bid = "bp_write_intent"
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "测试蓝本"
    chapters = [
        OutlineNode(key="c2", sequence="2", title="工程概况"),
        OutlineNode(key="c23", sequence="2.3", title="工艺"),
        OutlineNode(
            key="c231",
            sequence="2.3.1",
            title="原料",
            points=["要点"] if analyzed_231 else [],
            keywords=["kw"] if analyzed_231 else [],
        ),
        OutlineNode(key="c3", sequence="3", title="结论", points=["p"], keywords=["k"]),
    ]
    task.outline = OutlineTree(chapters=chapters)
    task.outline_confirmed = True
    TaskStore(path).save(task)
    return bid


def _seed_outline_11(home: Path, *, analyzed: bool = True) -> str:
    bid = "bp_write_11"
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "带1.1的蓝本"
    pts = ["要点"] if analyzed else []
    kws = ["kw"] if analyzed else []
    chapters = [
        OutlineNode(key="c1", sequence="1", title="概述", points=pts, keywords=kws),
        OutlineNode(key="c11", sequence="1.1", title="背景", points=pts, keywords=kws),
    ]
    task.outline = OutlineTree(chapters=chapters)
    task.outline_confirmed = True
    TaskStore(path).save(task)
    return bid


def test_resolve_prefers_exact_sequence(tmp_path: Path):
    bid = _seed_blueprint(tmp_path)
    task = TaskStore(tmp_path / "tasks" / bid).load()
    assert resolve_write_chapter_keys(task, "帮我写2.3.1章节") == ["c231"]
    assert resolve_write_chapter_keys(task, "写第3章") == ["c3"]


def test_select_write_accepts_sequences_as_chapter_keys(tmp_path: Path, monkeypatch):
    """模型把序号塞进 chapter_keys 时仍应开写，不能误报「没有已分析的」。"""
    from report_writing.chat.actions import _action_select_write_chapters
    from report_writing.domain.models import ChapterStatus, ChapterState

    bid = _seed_outline_11(tmp_path, analyzed=True)
    path = tmp_path / "tasks" / bid
    task = TaskStore(path).load()
    task.study_chapter_keys = ["c11"]
    TaskStore(path).save(task)
    session = ChatSession(id="s_seq_keys", blueprint_id=bid, freeform={})

    ran: list[list[str]] = []

    def fake_run_chapters(task_path, *, chapter_keys=None, **kwargs):
        keys = list(chapter_keys or [])
        ran.append(keys)
        store = TaskStore(task_path)
        writing = store.load()
        for key in keys:
            ch = next((c for c in (writing.chapters or []) if c.key == key), None)
            if ch is None:
                ch = ChapterState(key=key, title=key)
                writing.chapters = list(writing.chapters or []) + [ch]
            ch.status = ChapterStatus.AWAITING_CONFIRM
            ch.official_text = f"正文-{key}"
        store.save(writing)
        return store.load()

    monkeypatch.setattr("report_writing.services.pipeline.run_chapters", fake_run_chapters)

    reply, _cards = _action_select_write_chapters(
        tmp_path,
        session,
        {"blueprint_id": bid, "chapter_keys": ["1.1"]},
    )
    assert "没有已分析" not in (reply or "")
    assert session.freeform.get("write_chapter_keys") == ["c11"]
    flat = [k for call in ran for k in call]
    assert flat == ["c11"]


def test_select_write_uses_focus_copy_when_session_blueprint_empty(tmp_path: Path, monkeypatch):
    """会话 blueprint_id 空时，仍按焦点文档副本开写，不要抛「请先选择蓝本」。"""
    from report_writing.chat.actions import _action_select_write_chapters
    from report_writing.chat.session_documents import (
        set_focus_document_key,
        upsert_session_document,
    )
    from report_writing.domain.models import ChapterStatus, ChapterState

    bid = _seed_outline_11(tmp_path, analyzed=True)
    wid = "wWriteFocus01"
    src = TaskStore(tmp_path / "tasks" / bid).load()
    ensure_task(tmp_path / "tasks" / wid)
    clone = src.to_dict()
    clone["id"] = wid
    clone["source_blueprint_id"] = bid
    writing = type(src).from_dict(clone)
    TaskStore(tmp_path / "tasks" / wid).save(writing)

    session = ChatSession(id="s_focus_write", blueprint_id="", task_id=bid, freeform={})
    upsert_session_document(
        session,
        kind="blueprint",
        task_id=bid,
        blueprint_id=bid,
        writing_task_id=wid,
        title="焦点文档",
        registry_key=f"bp:{bid}",
    )
    set_focus_document_key(session, f"bp:{bid}")

    def fake_run_chapters(task_path, *, chapter_keys=None, **kwargs):
        store = TaskStore(task_path)
        writing_task = store.load()
        for key in list(chapter_keys or []):
            ch = next((c for c in (writing_task.chapters or []) if c.key == key), None)
            if ch is None:
                ch = ChapterState(key=key, title=key)
                writing_task.chapters = list(writing_task.chapters or []) + [ch]
            ch.status = ChapterStatus.AWAITING_CONFIRM
            ch.official_text = f"正文-{key}"
        store.save(writing_task)
        return store.load()

    monkeypatch.setattr("report_writing.services.pipeline.run_chapters", fake_run_chapters)
    reply, _cards = _action_select_write_chapters(
        tmp_path,
        session,
        {"chapter_keys": ["c11"]},
    )
    assert "请先选择蓝本" not in (reply or "")
    assert session.blueprint_id == bid
    assert session.freeform.get("write_chapter_keys") == ["c11"]


def test_select_write_without_document_returns_hint(tmp_path: Path):
    from report_writing.chat.actions import _action_select_write_chapters

    session = ChatSession(id="s_empty_write", freeform={})
    reply, cards = _action_select_write_chapters(
        tmp_path, session, {"chapter_keys": ["c1"]}
    )
    assert "请先选择蓝本" not in (reply or "")
    assert "正在写的文档" in (reply or "")
    assert cards == []


def test_recent_study_write_via_llm_target(tmp_path: Path):
    """「刚才分析的那几个章节」由 LLM target=recent_study 落到最近分析批，不查进度。"""
    bid = _seed_outline_11(tmp_path, analyzed=True)
    path = tmp_path / "tasks" / bid
    task = TaskStore(path).load()
    task.study_chapter_keys = ["c11"]
    TaskStore(path).save(task)
    session = ChatSession(id="s_recent", blueprint_id=bid, freeform={})

    def invoke(_system: str, _user: str) -> str:
        return json.dumps(
            {"target": "recent_study", "chapter_sequences": [], "confidence": 0.9}
        )

    reply, cards, _ = try_write_chapters_from_message(
        tmp_path, session, "刚才分析的那几个章节", invoke=invoke
    )
    assert session.freeform.get("write_chapter_keys") == ["c11"]
    assert "进度在这儿" not in (reply or "")
    assert not any(getattr(c, "type", "") == "writing_pace" for c in cards)


def test_write_target_status_returns_progress(tmp_path: Path):
    bid = _seed_outline_11(tmp_path, analyzed=True)
    path = tmp_path / "tasks" / bid
    task = TaskStore(path).load()
    task.study_chapter_keys = ["c11"]
    TaskStore(path).save(task)
    session = ChatSession(
        id="s_status",
        blueprint_id=bid,
        task_id=bid,
        freeform={},
    )

    def invoke(_system: str, _user: str) -> str:
        return json.dumps({"target": "status", "chapter_sequences": [], "confidence": 0.92})

    hit = try_write_chapters_from_message(
        tmp_path, session, "写到哪了", invoke=invoke
    )
    assert hit is None
    assert not session.freeform.get("write_chapter_keys")


def test_write_scope_self_vs_whole(tmp_path: Path):
    bid = _seed_blueprint(tmp_path)
    task = TaskStore(tmp_path / "tasks" / bid).load()
    assert write_sequences_have_descendants(task, ["2.3"]) is True
    assert write_sequences_have_descendants(task, ["2.3.1"]) is False
    assert keys_for_write_scope(task, ["2.3"], scope="self") == ["c23"]
    assert keys_for_write_scope(task, ["2.3"], scope="whole") == ["c23", "c231"]


def test_try_write_leaf_chapter_skips_pace(tmp_path: Path):
    bid = _seed_blueprint(tmp_path, analyzed_231=True)
    session = ChatSession(id="s1", blueprint_id=bid, freeform={})
    reply, cards, _ = try_write_chapters_from_message(
        tmp_path, session, "帮我写2.3.1章节"
    )
    assert "没认出要学" not in reply
    assert "学习" not in (reply or "")
    assert session.freeform.get("write_chapter_keys") == ["c231"]
    assert not any(getattr(c, "type", "") == "writing_pace" for c in cards)


def test_try_write_parent_asks_scope(tmp_path: Path):
    bid = _seed_blueprint(tmp_path, analyzed_231=True)
    session = ChatSession(id="s1", blueprint_id=bid, freeform={})
    reply, cards, _ = try_write_chapters_from_message(
        tmp_path, session, "帮我写2.3章节"
    )
    assert "只要本章" in (reply or "") or "下属" in (reply or "")
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)


def test_try_write_whole_scope_starts_without_pace(tmp_path: Path):
    """整章开写直接批写，不再弹出书写节奏卡。"""
    bid = _seed_blueprint(tmp_path, analyzed_231=True)
    path = tmp_path / "tasks" / bid
    task = TaskStore(path).load()
    for node in task.outline.chapters:
        if str(node.key) in {"c23", "c231"}:
            node.points = ["p"]
            node.keywords = ["k"]
    TaskStore(path).save(task)
    session = ChatSession(id="s1", blueprint_id=bid, freeform={})

    def invoke(system: str, _user: str) -> str:
        if "开写目标" in (system or "") or '"target"' in (system or ""):
            return json.dumps(
                {"target": "sequences", "chapter_sequences": ["2.3"], "confidence": 0.9}
            )
        return json.dumps({"scope": "whole", "confidence": 0.9})

    reply, cards, _ = try_write_chapters_from_message(
        tmp_path, session, "帮我写2.3整章", invoke=invoke
    )
    assert session.freeform.get("write_chapter_keys") == ["c23", "c231"]
    assert not any(getattr(c, "type", "") == "writing_pace" for c in cards)
    assert any(getattr(c, "type", "") in {"progress", "export", "resource_choice"} for c in cards)
    assert (reply or "").strip()


def test_try_write_unanalyzed_guides_analysis(tmp_path: Path):
    bid = _seed_blueprint(tmp_path, analyzed_231=False)
    session = ChatSession(id="s1", blueprint_id=bid, freeform={})
    reply, cards, _ = try_write_chapters_from_message(
        tmp_path, session, "帮我写2.3.1章节"
    )
    assert "没认出要学" not in reply
    assert "分析" in reply
    assert any(c.type in {"doc_window_hint", "chapter_picker", "handoff"} for c in cards)


def test_wizard_route_write_not_restudy(tmp_path: Path):
    bid = _seed_blueprint(tmp_path, analyzed_231=True)
    session = ChatSession(
        id="s1",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )

    action = resolve_wizard_action(
        "帮我写2.3.1章节",
        {"blueprint_id": bid, "step": "study"},
        invoke=lambda _s, _u: '{"command":"none","confidence":0.9}',
        session=session,
    )
    # 开写不归向导；分类器 none → 交回统一调度 / ReAct
    assert str((action or {}).get("command") or "none") in {"", "none"}
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") == "study"


def test_write_1_1_chapter_resolves_without_clarify(tmp_path: Path):
    """「写1.1章节」应对上大纲，不要挂「我还没听准要写哪一章」。"""
    bid = _seed_outline_11(tmp_path, analyzed=True)
    session = ChatSession(id="s11", blueprint_id=bid, freeform={})
    processes: list[str] = []

    def emit(ev: dict) -> None:
        if ev.get("type") == "process":
            processes.append(str(ev.get("message") or ev.get("text") or ""))

    reply, cards, _ = try_write_chapters_from_message(
        tmp_path, session, "写1.1章节", emit=emit
    )
    assert session.freeform.get("write_chapter_keys") == ["c11"]
    assert "我还没听准" not in (reply or "")
    assert not any(
        getattr(c, "type", "") == "intent_clarify"
        and "没听准" in str(getattr(c, "title", "") or "")
        for c in cards
    )
    # 「准备开写」应在解析成功后；匹配阶段可先发中性提示
    assert any("匹配" in p or "准备开写" in p for p in processes)


def test_write_chapter_key_hint_without_sequences(tmp_path: Path):
    bid = _seed_outline_11(tmp_path, analyzed=True)
    session = ChatSession(id="s11b", blueprint_id=bid, freeform={})
    task = TaskStore(tmp_path / "tasks" / bid).load()
    keys = resolve_write_chapter_keys(
        task, "", chapter_key_hint="1.1", llm_sequences=None
    )
    assert keys == ["c11"]
    reply, _cards, _ = try_write_chapters_from_message(
        tmp_path,
        session,
        "开写",
        chapter_key_hint="1.1",
        chapter_sequences=None,
    )
    assert session.freeform.get("write_chapter_keys") == ["c11"]
    assert "我还没听准" not in (reply or "")


def test_action_slot_resume_sets_sequences_for_select_write(tmp_path: Path):
    from report_writing.chat.action_slots import build_action_slot_clarify, try_resolve_action_slot_message

    bid = _seed_outline_11(tmp_path, analyzed=True)
    task = TaskStore(tmp_path / "tasks" / bid).load()
    session = ChatSession(id="s11c", blueprint_id=bid, freeform={})
    build_action_slot_clarify(
        session,
        "select_write_chapters",
        {"blueprint_id": bid, "task_id": bid},
        ["chapter_sequences"],
        task=task,
        message="写哪一章",
    )
    hit = try_resolve_action_slot_message(session, "写1.1", task=task)
    assert hit is not None
    args = dict((hit.get("slots") or {}).get("args") or {})
    assert args.get("chapter_key") == "c11"
    assert args.get("chapter_sequences") == ["1.1"]
    assert args.get("chapter_keys") == ["c11"]
