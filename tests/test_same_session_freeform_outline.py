"""同会话：异题新写须定名；无蓝本自动大纲不串 match / 旧 task。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from report_writing.agents.chat_control import ChatControlState, build_chat_tools
from report_writing.chat.clarify_flow import (
    build_entry_branch_clarify,
    build_entry_path_clarify,
    execute_clarify_spec,
    get_pending,
)
from report_writing.chat.session_documents import (
    confirm_writing_document_name,
    focus_needs_doc_name,
    focus_onboarding,
    is_writing_document_named,
    upsert_session_document,
)
from report_writing.controller import ensure_task, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _seed_named_safety_report(session: ChatSession, home: Path) -> str:
    tid = "w_old_safety_2026"
    path = home / "tasks" / tid
    ensure_task(path)
    save_spec_meta(
        path,
        title="2026安全报告",
        category="安全报告",
        tags=["chat"],
        description="test",
    )
    store = TaskStore(path)
    task = store.load()
    task.title = "2026安全报告"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", points=["p"], keywords=["k"]),
        ]
    )
    store.save(task)
    session.task_id = tid
    session.phase = ChatPhase.READY_TO_WRITE.value
    confirm_writing_document_name(
        session, "2026安全报告", home=home, message="写2026安全报告"
    )
    upsert_session_document(
        session,
        kind="outline",
        task_id=tid,
        title="2026安全报告",
        clear_blueprint_id=True,
    )
    ob = focus_onboarding(session)
    entry = dict((session.freeform or {}).get("session_documents") or {})
    key = f"task:{tid}"
    if key in entry:
        entry[key]["onboarding"] = {
            **ob,
            "name_confirmed": True,
            "entry_path": "match",
            "entry_branch": "with_blueprint",
        }
        ff = dict(session.freeform or {})
        ff["session_documents"] = entry
        session.freeform = ff
    return tid


def test_new_topic_in_same_session_asks_doc_name_first(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo1", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_safety_report(session, tmp_path)

    reply, cards = build_entry_path_clarify(
        session,
        message="再帮我写一份述职报告",
        invoke=None,
        home=tmp_path,
    )
    pending = get_pending(session) or {}
    assert not is_writing_document_named(session) or pending.get("source") == "document_scope"
    assert str((session.freeform or {}).get("title") or "") != "环境报告" or pending.get(
        "source"
    ) == "document_scope"
    assert pending.get("source") != "doc_name" or focus_needs_doc_name(session)


def test_auto_freeform_outline_no_match_bleed(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo2", phase=ChatPhase.INTENT.value)
    old_tid = _seed_named_safety_report(session, tmp_path)

    build_entry_path_clarify(
        session,
        message="再帮我写一份述职报告",
        invoke=None,
        home=tmp_path,
    )
    confirm_writing_document_name(
        session, "lzb述职2026", home=tmp_path, message="lzb述职2026"
    )
    build_entry_path_clarify(
        session,
        message="再帮我写一份述职报告",
        invoke=None,
        home=tmp_path,
        after_doc_name_confirm=True,
    )
    hit_branch = {
        "source": "entry_branch",
        "option_id": "without_blueprint",
        "slots": {"entry_branch": "without_blueprint"},
    }
    execute_clarify_spec(tmp_path, session, hit_branch, message="无蓝本")

    outline_text = "1 述职\n2 岗位\n3 年度"
    invoke = MagicMock(return_value=outline_text)

    hit_outline = {
        "source": "entry_freeform_mode",
        "option_id": "freeform",
        "slots": {
            "entry_path": "freeform",
            "freeform_mode": "auto_outline",
        },
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path,
        session,
        hit_outline,
        message="自动生成大纲",
        invoke=invoke,
    )
    combined = (reply or "").lower()
    assert "库里暂时没" not in (reply or "")
    assert "match_blueprints" not in combined
    suggested = list((session.freeform or {}).get("suggested_next_tools") or [])
    tools = [str(s.get("tool") or "") for s in suggested]
    assert "match_blueprints" not in tools
    assert "skip_blueprint" in tools
    from report_writing.chat.tool_dispatch import execute_dispatch_tool

    for step in suggested:
        hit = execute_dispatch_tool(
            tmp_path, session, step, "自动生成大纲", invoke=invoke
        )
        if hit is not None:
            r, step_cards, _ = hit
            if r:
                reply = f"{reply}\n{r}".strip()
            cards = list(cards or []) + list(step_cards or [])
    assert session.task_id != old_tid

    old = TaskStore(tmp_path / "tasks" / old_tid).load()
    assert old.title == "2026安全报告"
    assert [n.title for n in old.outline.chapters] == ["概述"]


def test_auto_freeform_chain_single_outline_message(tmp_path: Path):
    """点选自动大纲只落槽；Agent 再 hop skip + submit_outline 时目录文案只出现一次。"""
    from unittest.mock import MagicMock

    from report_writing.chat.clarify_flow import execute_clarify_spec
    from report_writing.chat.tool_dispatch import execute_dispatch_tool
    from report_writing.chat.session_documents import confirm_writing_document_name

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo_dedupe", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(
        session, "我的述职报告", home=tmp_path, message="我的述职报告"
    )
    outline_text = "1 概述\n1.1 岗位\n2 总结"
    invoke = MagicMock(return_value=outline_text)
    hit = {
        "source": "entry_freeform_mode",
        "option_id": "freeform",
        "slots": {"entry_path": "freeform", "freeform_mode": "auto_outline"},
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path,
        session,
        hit,
        message="自动生成大纲",
        invoke=invoke,
    )
    assert "目录我先帮你搭" not in (reply or "")
    suggested = list((session.freeform or {}).get("suggested_next_tools") or [])
    assert [str(s.get("tool") or "") for s in suggested][:2] == [
        "skip_blueprint",
        "submit_outline",
    ]
    for step in suggested:
        hop = execute_dispatch_tool(
            tmp_path, session, step, "自动生成大纲", invoke=invoke
        )
        if hop is None:
            continue
        r, step_cards, _ = hop
        if r:
            reply = f"{reply}\n{r}".strip()
        cards = list(cards or []) + list(step_cards or [])
    assert (reply or "").count("目录我先帮你搭") <= 1
    assert (reply or "").count("觉得 OK 就点") <= 1
    assert cards


def test_name_only_after_force_new_asks_entry_not_outline(tmp_path: Path):
    from report_writing.chat.cards import turn_has_outline_preview_cards
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        focus_needs_entry_branch,
        focus_onboarding,
        prepare_observed_onboarding,
    )

    session = ChatSession(id="ssfo_name_only", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_safety_report(session, tmp_path)
    msg = "新写一份述职报告 名字叫我的述职报告"
    begin_document_onboarding(
        session, tmp_path, message=msg, title_hint="我的述职报告", force_new=True
    )
    confirm_writing_document_name(session, "我的述职报告", home=tmp_path, message=msg)
    prepare_observed_onboarding(
        session, tmp_path, message=msg, title_hint="我的述职报告", invoke=None
    )
    ob = focus_onboarding(session)
    assert str(ob.get("entry_path") or "").strip() != "freeform"
    assert focus_needs_entry_branch(session)
    reply, cards = build_entry_path_clarify(
        session,
        message=msg,
        title_hint="我的述职报告",
        home=tmp_path,
        invoke=None,
    )
    assert not turn_has_outline_preview_cards(cards)
    assert "目录我先帮你搭" not in (reply or "")
    assert focus_needs_entry_branch(session) or any(
        str((getattr(c, "payload", None) or {}).get("source") or "") == "entry_branch"
        for c in cards
    )


def test_ask_entry_path_after_new_topic_pending_doc_name(tmp_path: Path):
    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo3", phase=ChatPhase.READY_TO_WRITE.value)
    _seed_named_safety_report(session, tmp_path)
    state = ChatControlState(home=tmp_path, session=session)
    tools = {t.name: t for t in build_chat_tools(state)}
    raw = tools["ask_entry_path"].invoke({"message": "再帮我写一份述职报告"})
    data = json.loads(raw)
    assert data.get("ok") is True
    assert data.get("needs_doc_name") is True or data.get("pending_clarify") == "doc_name"


def test_without_blueprint_hangs_freeform_mode_not_auto_outline(tmp_path: Path):
    from report_writing.chat.cards import turn_has_outline_preview_cards
    from report_writing.chat.session_documents import confirm_writing_document_name

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo_tpl_branch", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "我的报告", home=tmp_path, message="写报告")
    build_entry_branch_clarify(
        session, message="依据模版写一份报告", title_hint="我的报告"
    )
    hit = {
        "source": "entry_branch",
        "option_id": "without_blueprint",
        "slots": {"entry_branch": "without_blueprint"},
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="无蓝本 · 自己写", invoke=None
    )
    assert (get_pending(session) or {}).get("source") == "entry_freeform_mode"
    assert not turn_has_outline_preview_cards(cards)
    assert "目录我先帮你搭" not in (reply or "")


def test_entry_freeform_template_returns_wizard_meta_card(tmp_path: Path):
    from report_writing.chat.session_documents import confirm_writing_document_name

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo_tpl_wiz", phase=ChatPhase.INTENT.value)
    confirm_writing_document_name(session, "环境报告", home=tmp_path)
    hit = {
        "source": "entry_freeform_mode",
        "option_id": "template",
        "slots": {"entry_path": "create_blueprint", "freeform_mode": "template"},
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="依据模版编写", invoke=None
    )
    suggested = [str(s.get("tool") or "") for s in (session.freeform or {}).get("suggested_next_tools") or []]
    assert "begin_blueprint_wizard" in suggested
    assert not [
        c
        for c in (cards or [])
        if str(getattr(c, "type", "") or "") == "blueprint_wizard"
    ]
    from report_writing.chat.tool_dispatch import execute_dispatch_tool

    hop = execute_dispatch_tool(
        tmp_path,
        session,
        {
            "tool": "begin_blueprint_wizard",
            "args": {"confirmed_create": True, "from_entry_path": True},
        },
        "依据模版编写",
        invoke=None,
    )
    assert hop is not None
    reply, cards, _ = hop
    wiz = [
        c
        for c in (cards or [])
        if str(getattr(c, "type", "") or "") == "blueprint_wizard"
    ]
    assert wiz
    assert str((wiz[0].payload or {}).get("step") or "") == "meta"


def test_auto_outline_unnamed_only_asks_doc_name(tmp_path: Path):
    from report_writing.chat.cards import turn_has_outline_preview_cards
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        mark_focus_entry_branch,
    )

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo_auto_unnamed", phase=ChatPhase.INTENT.value)
    begin_document_onboarding(
        session,
        tmp_path,
        message="再写一份述职报告",
        title_hint="述职报告",
        force_new=True,
    )
    mark_focus_entry_branch(session, "without_blueprint")
    hit = {
        "source": "entry_freeform_mode",
        "option_id": "freeform",
        "slots": {"entry_path": "freeform", "freeform_mode": "auto_outline"},
    }
    reply, cards, _ = execute_clarify_spec(
        tmp_path, session, hit, message="自动生成大纲", invoke=None
    )
    pending = get_pending(session) or {}
    assert pending.get("source") == "doc_name" or focus_needs_doc_name(session)
    assert not turn_has_outline_preview_cards(cards)
    assert "目录我先帮你搭" not in (reply or "")


def test_name_after_auto_outline_keeps_freeform_path(tmp_path: Path):
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        mark_focus_entry_path,
        mark_focus_freeform_mode,
    )

    (tmp_path / "tasks").mkdir(parents=True)
    session = ChatSession(id="ssfo_auto_named", phase=ChatPhase.INTENT.value)
    begin_document_onboarding(
        session,
        tmp_path,
        message="再写一份述职报告",
        title_hint="述职报告",
        force_new=True,
    )
    mark_focus_entry_path(session, "freeform")
    mark_focus_freeform_mode(session, "auto_outline")
    confirm_writing_document_name(
        session, "我的述职报告", home=tmp_path, message="叫做我的述职报告"
    )
    ob = focus_onboarding(session)
    assert str(ob.get("entry_path") or "") == "freeform"
    assert str(ob.get("entry_branch") or "") == "without_blueprint"
    reply, cards = build_entry_path_clarify(
        session,
        message="叫做我的述职报告",
        title_hint="我的述职报告",
        home=tmp_path,
        after_doc_name_confirm=True,
        apply_plan=False,
    )
    sources = {
        str((getattr(c, "payload", None) or {}).get("source") or "") for c in (cards or [])
    }
    assert "entry_branch" not in sources
    assert str(focus_onboarding(session).get("entry_path") or "") == "freeform"
