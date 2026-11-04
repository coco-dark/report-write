"""定名后必选路 + 焦点对话隔离。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.dialogue import format_full_dialogue
from report_writing.chat.intent_clarify import build_ask_intent_clarify
from report_writing.chat.session_documents import (
    ensure_named_freeform_document,
    focus_document_prefers_freeform_path,
    focus_needs_entry_path,
    focus_onboarding,
    set_focus_document_key,
    upsert_session_document,
)
from report_writing.domain.models import ChatMessage, ChatPhase, ChatSession


def test_ensure_named_does_not_skip_entry_path(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_named")
    # 同会话残留旧蓝本，不应让新文档跳过选路
    session.blueprint_id = "bp_old_env"
    tid = ensure_named_freeform_document(
        session,
        home,
        title="述职报告｜lzb的述职报告",
        category="述职",
        confirm_name=True,
    )
    assert tid
    ff = dict(session.freeform or {})
    assert ff.get("writing_doc_named") is True
    assert str(ff.get("writing_path") or "") != "freeform"
    assert focus_needs_entry_path(session)
    ob = focus_onboarding(session)
    assert ob.get("name_confirmed") is True
    assert not str(ob.get("entry_path") or "").strip()


def test_ensure_named_clears_leftover_freeform_path_and_phase(tmp_path: Path):
    """上一份无蓝本写完后新开文档：不得沿用 writing_path/phase，否则对话卡串台。"""
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(
        id="s_bleed",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        freeform={
            "writing_path": "freeform",
            "blueprint_wizard": {"step": "study", "mode": "create"},
            "writing_brief": {"facts": ["旧文档事实"], "narrative": "旧叙事"},
        },
    )
    tid = ensure_named_freeform_document(
        session,
        home,
        title="【述职报告】lzb的个人述职",
        confirm_name=True,
    )
    assert tid
    ff = dict(session.freeform or {})
    assert not str(ff.get("writing_path") or "").strip()
    assert not ff.get("blueprint_wizard")
    assert focus_needs_entry_path(session)
    assert not focus_document_prefers_freeform_path(session)
    assert session.phase == ChatPhase.INTENT.value


def test_ask_intent_continues_to_entry_path_despite_leftover_blueprint(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_ask")
    session.blueprint_id = "bp_leftover"
    ensure_named_freeform_document(
        session,
        home,
        title="述职报告｜lzb的述职报告",
        confirm_name=True,
    )
    assert focus_needs_entry_path(session)

    reply, cards = build_ask_intent_clarify(
        session, message="继续", invoke=None, home=home
    )
    assert "先写已分析" not in reply
    assert "加要素" not in reply
    sources = [
        str((getattr(c, "payload", None) or {}).get("source") or "")
        for c in (cards or [])
    ]
    assert any(s in {"entry_branch", "entry", "entry_freeform_mode"} for s in sources) or (
        "蓝本" in reply or "自己" in reply or "样本" in reply or "无蓝本" in reply
    )


def test_format_full_dialogue_focus_only_filters_other_docs():
    session = ChatSession(id="s_dlg")
    upsert_session_document(
        session, kind="outline", task_id="w_old", title="环评报告", clear_blueprint_id=True
    )
    upsert_session_document(
        session, kind="outline", task_id="w_new", title="述职报告", clear_blueprint_id=True
    )
    set_focus_document_key(session, "task:w_new")
    session.messages = [
        ChatMessage(role="user", text="环评怎么写", document_key="task:w_old"),
        ChatMessage(role="assistant", text="先上传样板", document_key="task:w_old"),
        ChatMessage(role="user", text="写述职", document_key="task:w_new"),
        ChatMessage(role="assistant", text="先定线路", document_key="task:w_new"),
    ]
    rows = format_full_dialogue(session, focus_only=True, max_turns=10)
    texts = " ".join(str(r.get("text") or "") for r in rows)
    assert "写述职" in texts
    assert "先定线路" in texts
    assert "环评怎么写" not in texts
    assert "先上传样板" not in texts
    assert any("另有" in str(r.get("text") or "") for r in rows if r.get("role") == "system")


def test_format_full_dialogue_focus_only_excludes_untagged_in_multi_doc():
    """多文档时未盖戳历史不得回填为当前焦点。"""
    session = ChatSession(id="s_untagged")
    upsert_session_document(
        session, kind="outline", task_id="w_old", title="环评报告", clear_blueprint_id=True
    )
    upsert_session_document(
        session, kind="outline", task_id="w_new", title="述职报告", clear_blueprint_id=True
    )
    set_focus_document_key(session, "task:w_new")
    session.messages = [
        ChatMessage(role="user", text="旧文档未盖戳话", document_key=""),
        ChatMessage(role="assistant", text="旧文档未盖戳回", document_key=""),
        ChatMessage(role="user", text="写述职", document_key="task:w_new"),
    ]
    rows = format_full_dialogue(session, focus_only=True, max_turns=10)
    texts = " ".join(str(r.get("text") or "") for r in rows)
    assert "写述职" in texts
    assert "旧文档未盖戳话" not in texts
    assert "旧文档未盖戳回" not in texts
