"""统一调度执行层：向导进行中仍可写 / 加要素 / 再学。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.intent_clarify import execute_clarify_spec, try_handle_pending_message
from report_writing.chat.intent_dispatch import execute_intent_plan
from report_writing.chat.intent_llm import resolve_wizard_action
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _dispatch(home, session, message, plan, *, invoke=None):
    if not plan:
        return None
    return execute_intent_plan(home, session, plan, message, invoke=invoke)


def _seed(home: Path) -> str:
    bid = "bp_dispatch"
    path = home / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "调度蓝本"
    task.sample_ready = True
    task.outline_confirmed = True
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", points=["p"], keywords=["k"]),
            OutlineNode(
                key="c231",
                sequence="2.3.1",
                title="原料",
                points=["要点"],
                keywords=["kw"],
            ),
            OutlineNode(key="c3", sequence="3", title="结论"),
            OutlineNode(key="c_scale", sequence="2.1", title="建设规模"),
            OutlineNode(key="c22", sequence="2.2", title="安全"),
        ]
    )
    TaskStore(path).save(task)
    return bid


def _session(bid: str) -> ChatSession:
    return ChatSession(
        id="s-dispatch",
        blueprint_id=bid,
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "study"}},
    )


def test_dispatch_write_chapter_not_restudy(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "帮我写2.3.1章节",
        [{"tool": "select_write_chapters", "args": {}}],
    )
    assert "没认出要学" not in (reply or "")
    assert "重新学习" not in (reply or "")
    assert session.freeform.get("write_chapter_keys") == ["c231"]
    assert not any(c.type == "writing_pace" for c in cards)
    assert ((session.freeform or {}).get("blueprint_wizard") or {}).get("step") == "study"


def test_dispatch_select_write_does_not_duplicate_pace(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "帮我写2.3.1章节",
        [
            {"tool": "select_write_chapters", "args": {}},
            {"tool": "start_writing", "args": {}},
        ],
    )
    pace = [c for c in cards if getattr(c, "type", "") == "writing_pace"]
    assert len(pace) <= 1
    assert session.freeform.get("write_chapter_keys") == ["c231"]


def test_dispatch_add_element_keeps_wizard(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    msg = "为建设规模添加知识库要素 关联知识库 检索词为建设规模 站址 工程投资"
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        msg,
        [
            {
                "tool": "add_chapter_element",
                "args": {
                    "kind": "rag_search",
                    "chapter_key_hint": "建设规模",
                    "query": "建设规模 站址 工程投资",
                },
            }
        ],
    )
    assert "建设规模" in (reply or "")
    assert any(getattr(c, "type", "") == "element_configure_pending" for c in cards)
    assert ((session.freeform or {}).get("blueprint_wizard") or {}).get("step") == "study"
    assert "整章" not in (reply or "")
    assert not ((session.freeform or {}).get("blueprint_wizard") or {}).get("trigger_chapter_study")


def test_dispatch_add_element_when_llm_selects_add(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    msg = "为2.2添加知识库要素 关联安全 检索词为建设规模、站址、工程总投资"
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        msg,
        [
            {
                "tool": "add_chapter_element",
                "args": {
                    "kind": "rag_search",
                    "chapter_key_hint": "2.2",
                    "query": "建设规模、站址、工程总投资",
                },
            }
        ],
    )
    assert "整章" not in (reply or "")
    assert not ((session.freeform or {}).get("blueprint_wizard") or {}).get("trigger_chapter_study")
    assert any(getattr(c, "type", "") == "element_configure_pending" for c in cards)
    assert ((session.freeform or {}).get("blueprint_wizard") or {}).get("step") == "study"


def test_dispatch_study_chapter_not_write(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "学习第3章",
        [{"tool": "re_study_chapters", "args": {"chapter_sequences": ["3"], "study_method": "sample"}}],
    )
    assert reply
    wizard = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wizard.get("step") in {"study", "outline_confirm"}
    assert not session.freeform.get("write_chapter_keys")


def test_dispatch_empty_plan_returns_none(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    assert _dispatch(tmp_path, session, "这一章", []) is None


def test_dispatch_skip_blueprint_abandons_wizard(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "不用这个蓝本",
        [{"tool": "skip_blueprint", "args": {}}],
    )
    assert not (session.blueprint_id or "").strip()
    assert not (session.freeform or {}).get("blueprint_wizard")
    assert "不用" in (reply or "") or "先不用" in (reply or "")
    assert not any(getattr(c, "type", "") == "blueprint_wizard" for c in cards)


def test_dispatch_skip_creating_wizard_goes_freeform(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="s-skip-create",
        blueprint_id="w_create",
        task_id="w_create",
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={
            "title": "lzb述职报告",
            "writing_doc_named": True,
            "focus_document_key": "task:w_create",
            "blueprint_wizard": {
                "blueprint_id": "w_create",
                "step": "meta",
                "mode": "create",
                "title": "lzb述职报告",
            },
        },
    )
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "我没有模版",
        [{"tool": "skip_blueprint", "args": {}}],
    )
    assert not (session.freeform or {}).get("blueprint_wizard")
    pending = (session.freeform or {}).get("intent_clarify") or {}
    assert pending.get("source") != "entry_branch"
    assert not any(
        str((getattr(c, "payload", None) or {}).get("source") or "") == "entry_branch"
        for c in cards
    )
    assert reply


def test_dispatch_new_report_asks_entry_path(tmp_path: Path):
    session = ChatSession(id="s-new", phase=ChatPhase.INTENT.value)
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "你好 帮我写一份安全报告",
        [{"tool": "select_write_chapters", "args": {}}],
    )
    assert "请先选择蓝本" not in (reply or "")
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)
    pending = ((session.freeform or {}).get("intent_clarify") or {})
    assert pending.get("source") in {
        "doc_name",
        "entry",
        "entry_branch",
        "entry_freeform_mode",
    }
    if pending.get("source") == "doc_name":
        assert "名字" in (reply or "") or "叫「" in (reply or "")


def test_dispatch_ask_entry_path_starts_clarify(tmp_path: Path):
    session = ChatSession(id="s-new", phase=ChatPhase.INTENT.value)
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "帮我出个安全预评价",
        [{"tool": "ask_entry_path", "args": {}}],
    )
    assert "请先选择蓝本" not in (reply or "")
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)


def test_dispatch_unclear_starts_multiturn(tmp_path: Path):
    session = ChatSession(id="s-new", phase=ChatPhase.INTENT.value)

    def invoke(system: str, _user: str) -> str:
        if "多轮意图澄清" in system:
            return json.dumps(
                {
                    "question": "你是想先找现成蓝本，还是自己开写？",
                    "options": [
                        {
                            "id": "match",
                            "label": "先匹配已有蓝本",
                            "tool": "match_blueprints",
                        },
                        {
                            "id": "skip",
                            "label": "不用蓝本自己定大纲",
                            "tool": "skip_blueprint",
                        },
                    ],
                    "confidence": 0.86,
                }
            )
        return "{}"

    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "来份安评",
        [{"tool": "ask_intent", "args": {}}],
        invoke=invoke,
    )
    assert "请先选择蓝本" not in (reply or "")
    assert "现成蓝本" in (reply or "")
    assert any(getattr(c, "type", "") == "intent_clarify" for c in cards)
    pending = (session.freeform or {}).get("intent_clarify") or {}
    assert pending.get("source") == "control"

    hit = try_handle_pending_message(session, "不用蓝本自己定大纲")
    assert isinstance(hit, dict)
    assert hit["slots"].get("tool") == "skip_blueprint"
    follow_reply, follow_cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="不用蓝本自己定大纲"
    )
    assert follow_reply
    pending2 = (session.freeform or {}).get("intent_clarify") or {}
    assert (
        any(
            getattr(c, "type", "") in {"intent_clarify", "outline_preview"}
            for c in follow_cards
        )
        or pending2.get("source") in {
            "doc_name",
            "entry_branch",
            "entry_freeform_mode",
            "entry",
            "freeform",
        }
        or "目录" in (follow_reply or "")
        or "大纲" in (follow_reply or "")
    )


def test_wizard_unclaimed_is_not_jail(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)
    action = resolve_wizard_action(
        "今天天气怎么样",
        {"blueprint_id": bid, "step": "study"},
        invoke=lambda _s, _u: '{"command":"none","confidence":0.9}',
        session=session,
    )
    cmd = str((action or {}).get("command") or "none")
    assert cmd in {"", "none"} or str((action or {}).get("type") or "") in {"", "none"}


def test_dispatch_extract_done_via_llm(tmp_path: Path):
    bid = "bp_extract_done"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.title = "目录蓝本"
    task.sample_ready = True
    task.outline_confirmed = False
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key=f"c{i}", sequence=str(i), title=f"章{i}")
            for i in range(1, 13)
        ]
    )
    TaskStore(path).save(task)
    session = ChatSession(
        id="s-extract",
        blueprint_id=bid,
        phase=ChatPhase.CREATE_HANDOFF.value,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "outline_confirm"}},
    )
    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "完成目录提取",
        [{"tool": "blueprint_wizard", "args": {"step": "outline_done", "blueprint_id": bid}}],
    )
    assert "确认目录" in (reply or "") or "目录已经" in (reply or "") or reply
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("step") == "outline_confirm"
    assert not any(getattr(c, "type", "") == "intent_clarify" for c in cards)


def test_dispatch_study_all_of_chapter_skips_scope_question(tmp_path: Path):
    bid = _seed(tmp_path)
    session = _session(bid)

    def invoke(system: str, _user: str) -> str:
        if '"scope"' in system and "unclear" in system:
            return json.dumps({"scope": "whole", "confidence": 0.9})
        return "{}"

    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        "学习第二章所有",
        [{"tool": "re_study_chapters", "args": {"chapter_sequences": ["2"], "study_method": "sample"}}],
        invoke=invoke,
    )
    assert reply
    assert "还是只要下面的子章节" not in (reply or "")
    assert not any(getattr(c, "type", "") == "intent_clarify" for c in cards)
    keys = ((session.freeform or {}).get("blueprint_wizard") or {}).get("trigger_chapter_study") or []
    assert keys


def test_dispatch_study_self_chapter_excludes_children(tmp_path: Path):
    bid = _seed(tmp_path)
    path = tmp_path / "tasks" / bid
    task = TaskStore(path).load()
    task.outline.chapters.extend(
        [
            OutlineNode(key="c11", sequence="1.1", title="背景"),
            OutlineNode(key="c12", sequence="1.2", title="依据"),
        ]
    )
    TaskStore(path).save(task)
    session = _session(bid)
    msg = "学习第一章就够了 不要子章节"

    def invoke(system: str, _user: str) -> str:
        if '"scope"' in system and "unclear" in system:
            return json.dumps({"scope": "self", "confidence": 0.93})
        return "{}"

    reply, cards, _ = _dispatch(
        tmp_path,
        session,
        msg,
        [{"tool": "re_study_chapters", "args": {"chapter_sequences": ["1"], "study_method": "sample"}}],
        invoke=invoke,
    )
    assert "下面的全部子章节" not in (reply or "")
    assert "不含下属" in (reply or "") or "本章" in (reply or "")
    assert not any(getattr(c, "type", "") == "intent_clarify" for c in cards)
    keys = ((session.freeform or {}).get("blueprint_wizard") or {}).get("trigger_chapter_study") or []
    assert keys == ["c1"]
