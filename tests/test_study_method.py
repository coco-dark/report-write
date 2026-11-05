"""重新分析本章：选择原文 / 样本 / 自动分析。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.clarify_flow import execute_clarify_spec, try_handle_pending_message
from report_writing.chat.resource_actions import execute_wizard_command
from report_writing.chat.study_chapter_resolve import bound_study_method, infer_study_method
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree, WritingTask
from report_writing.services.store import TaskStore


def _seed(home: Path, *, sample_ready: bool = True) -> str:
    bid = "bp_study_method"
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "分析方式"
    task.sample_ready = sample_ready
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", points=["p"], keywords=["k"]),
            OutlineNode(key="c2", sequence="2", title="评价单元"),
        ]
    )
    TaskStore(path).save(task)
    return bid


def _session(bid: str) -> ChatSession:
    return ChatSession(
        id="s-study-method",
        blueprint_id=bid,
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )


def test_infer_study_method_phrases():
    assert infer_study_method("", "根据原文内容分析本章") == "source"
    assert infer_study_method("", "依据样本再分析第1章") == "sample"
    assert infer_study_method("", "没有样本就自动分析") == "auto"
    assert infer_study_method("paste", "") == "source"
    assert infer_study_method("", "重新分析本章") == ""


def test_bound_study_method_follows_sample():
    assert bound_study_method(WritingTask(id="t", sample_ready=True)) == "sample"
    assert bound_study_method(WritingTask(id="t", sample_ready=False)) == "auto"


def test_re_study_asks_method_when_unspecified(tmp_path: Path):
    bid = _seed(tmp_path, sample_ready=True)
    session = _session(bid)
    reply, cards = execute_wizard_command(
        tmp_path,
        session,
        "re_study_chapters",
        study_message="重新分析第1章",
        chapter_sequences=["1"],
        study_scope="self",
    )
    assert "哪种方式" in (reply or "")
    assert any(c.type == "intent_clarify" for c in cards)
    opts = (cards[0].payload or {}).get("options") or []
    labels = [str(o.get("label") or "") for o in opts]
    assert "根据原文内容分析" in labels
    assert "依据样本分析" in labels
    assert not ((session.freeform or {}).get("blueprint_wizard") or {}).get("trigger_chapter_study")


def test_re_study_auto_label_without_sample(tmp_path: Path):
    bid = _seed(tmp_path, sample_ready=False)
    session = _session(bid)
    reply, cards = execute_wizard_command(
        tmp_path,
        session,
        "re_study_chapters",
        study_message="重新分析第1章",
        chapter_sequences=["1"],
        study_scope="self",
    )
    opts = (cards[0].payload or {}).get("options") or []
    labels = [str(o.get("label") or "") for o in opts]
    assert "自动分析" in labels
    assert "依据样本分析" not in labels
    assert "哪种方式" in (reply or "")


def test_re_study_source_without_text_returns_paste_card(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    reply, cards = execute_wizard_command(
        tmp_path,
        session,
        "re_study_chapters",
        study_message="根据原文分析第1章",
        chapter_sequences=["1"],
        study_scope="self",
        study_method="source",
    )
    assert any(c.type == "study_source_paste" for c in cards)
    assert "粘贴" in (reply or "")
    pending = (session.freeform or {}).get("study_source_pending") or {}
    assert pending.get("chapter_keys") == ["c1"]


def test_re_study_sample_sets_trigger(tmp_path: Path):
    bid = _seed(tmp_path, sample_ready=True)
    session = _session(bid)
    reply, cards = execute_wizard_command(
        tmp_path,
        session,
        "re_study_chapters",
        study_message="依据样本分析第1章",
        chapter_sequences=["1"],
        study_scope="self",
        study_method="sample",
    )
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("trigger_chapter_study") == ["c1"]
    assert wiz.get("study_method") == "sample"
    assert "依据样本" in (reply or "")
    assert any(c.type == "blueprint_wizard" for c in cards)


def test_paste_message_resumes_source_study(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    execute_wizard_command(
        tmp_path,
        session,
        "re_study_chapters",
        study_message="根据原文分析第1章",
        chapter_sequences=["1"],
        study_scope="self",
        study_method="source",
    )
    source = "4.1.1 评价单元划分\n" + ("本工程按不同危险性划分评价单元。" * 6)
    spec = try_handle_pending_message(session, source)
    assert isinstance(spec, dict)
    reply, cards, _ = execute_clarify_spec(tmp_path, session, spec, message=source)
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("trigger_chapter_study") == ["c1"]
    assert wiz.get("study_method") == "source"
    assert "按原文" in (reply or "")
    assert not (session.freeform or {}).get("study_source_pending")
    assert cards
