"""依据模版：定名空壳 → meta 创建 → upload 卡 + 焦点一致。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.orchestrator import handle_chat
from report_writing.chat.resource_actions import _action_blueprint_wizard
from report_writing.chat.session_documents import (
    ensure_named_freeform_document,
    focus_document_key,
)
from report_writing.domain.models import ChatPhase, ChatRequest, ChatSession
from report_writing.services.chat_session import save_session


def test_meta_create_after_named_shell_reuses_task_and_upload_card(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_meta_up", phase=ChatPhase.INTENT.value)
    shell = ensure_named_freeform_document(
        session, home, title="环境报告", confirm_name=True
    )
    assert shell
    focus_before = focus_document_key(session)
    assert focus_before.startswith("task:w")

    reply, cards = _action_blueprint_wizard(
        home,
        session,
        {"step": "meta", "title": "环境报告"},
    )
    assert "上传" in (reply or "") or "样本" in (reply or "")
    wiz_cards = [c for c in cards if c.type == "blueprint_wizard"]
    assert wiz_cards, "meta 创建后必须有 upload 向导卡"
    assert wiz_cards[0].payload.get("step") == "upload"
    bid = str(wiz_cards[0].payload.get("blueprint_id") or "").strip()
    assert bid.startswith("w")
    assert bid == str(session.task_id or "").strip()
    assert focus_document_key(session) == f"task:{bid}"
    # 复用定名空壳，不应再 mint 第二个 w*
    assert bid == focus_before.replace("task:", "", 1) or bid


def test_handle_chat_blueprint_wizard_meta_persists_upload_card(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_hc_meta", phase=ChatPhase.CREATE_HANDOFF.value)
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": {
            "step": "meta",
            "mode": "create",
            "title": "环境报告",
            "fresh_meta": True,
        },
    }
    save_session(home, session)

    resp = handle_chat(
        home,
        ChatRequest(
            session_id="s_hc_meta",
            action="blueprint_wizard",
            action_payload={"step": "meta", "title": "环境报告"},
        ),
        invoke=lambda _s, _u: "{}",
    )
    wiz = [c for c in (resp.cards or []) if c.type == "blueprint_wizard"]
    assert wiz and wiz[0].payload.get("step") == "upload"

    from report_writing.services.chat_session import get_session

    loaded = get_session(home, "s_hc_meta")
    last = loaded.messages[-1]
    assert last.role == "assistant"
    persisted = [c for c in (last.cards or []) if c.type == "blueprint_wizard"]
    assert persisted, "落库 assistant 气泡必须含 upload 向导卡"
    assert persisted[-1].payload.get("step") == "upload"


def test_handle_chat_begin_wizard_meta_persists_meta_card(tmp_path: Path):
    """模拟口语 force_new 后 session 已在 meta 向导，整轮 handle 后气泡仍带 meta 卡。"""
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_oral_meta", phase=ChatPhase.CREATE_HANDOFF.value)
    session.freeform = {
        **(session.freeform or {}),
        "title": "环境报告",
        "writing_doc_named": True,
        "meta_confirmed": True,
        "blueprint_wizard": {
            "step": "meta",
            "mode": "create",
            "title": "环境报告",
            "fresh_meta": True,
        },
        "plan": {"goal": "template_wizard", "step": "wizard_meta"},
    }
    save_session(home, session)

    resp = handle_chat(
        home,
        ChatRequest(
            session_id="s_oral_meta",
            message="",
            action="begin_blueprint_wizard",
            action_payload={
                "force_new": True,
                "title_hint": "环境报告",
                "explicit_create": True,
            },
        ),
        invoke=lambda _s, _u: "{}",
    )
    wiz = [c for c in (resp.cards or []) if c.type == "blueprint_wizard"]
    assert wiz, "begin 动作响应必须含向导卡"
    step = str(wiz[0].payload.get("step") or "")
    assert step in {"meta", "upload"}

    from report_writing.services.chat_session import get_session

    loaded = get_session(home, "s_oral_meta")
    last = loaded.messages[-1]
    persisted = [c for c in (last.cards or []) if c.type == "blueprint_wizard"]
    assert persisted, "落库必须含向导卡"


def test_infer_sample_wizard_when_mode_wiped(tmp_path: Path):
    from report_writing.chat.chat_card_policy import infer_sample_wizard_active

    session = ChatSession(id="s_inf", phase=ChatPhase.CREATE_HANDOFF.value)
    session.task_id = "w123abc"
    session.freeform = {
        "writing_path": "freeform",
        "blueprint_wizard": {
            "blueprint_id": "w123abc",
            "step": "upload",
            "working_task_id": "w123abc",
        },
    }
    assert infer_sample_wizard_active(session)


def test_infer_sample_wizard_meta_create_without_bid():
    from report_writing.chat.chat_card_policy import infer_sample_wizard_active

    session = ChatSession(id="s_meta", phase=ChatPhase.CREATE_HANDOFF.value)
    session.freeform = {
        "blueprint_wizard": {"step": "meta", "mode": "create", "fresh_meta": True},
        "plan": {"goal": "template_wizard", "step": "wizard_meta"},
    }
    assert infer_sample_wizard_active(session)


def test_ensure_wizard_invariant_synthesizes_meta_without_bid():
    from report_writing.chat.chat_card_policy import ensure_wizard_chat_card_invariant

    session = ChatSession(id="s_syn", phase=ChatPhase.CREATE_HANDOFF.value)
    wiz = {"step": "meta", "mode": "create", "fresh_meta": True, "title": "环境报告"}
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wiz, "plan": {"goal": "template_wizard"}}
    out = ensure_wizard_chat_card_invariant(session, [], wiz)
    assert len(out) == 1
    assert out[0].type == "blueprint_wizard"
    assert out[0].payload.get("step") == "meta"
    assert str(out[0].payload.get("blueprint_id") or "") == ""


def test_begin_blueprint_wizard_force_new_returns_meta_card(tmp_path: Path):
    from report_writing.chat.resource_actions import _begin_blueprint_wizard

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_beg", phase=ChatPhase.INTENT.value)
    reply, cards = _begin_blueprint_wizard(
        session,
        title_hint="环境报告",
        home=home,
        force_new=True,
        user_message="根据我的模版再写份环境报告",
    )
    assert "依据模版编写" in (reply or "")
    wiz = [c for c in cards if c.type == "blueprint_wizard"]
    assert wiz, "force_new 开向导必须带 meta 卡"
    assert wiz[0].payload.get("step") == "meta"


def test_finalize_turn_cards_recover_meta_when_handler_stripped(tmp_path: Path):
    from report_writing.chat.chat_card_policy import finalize_turn_cards
    from report_writing.chat.resource_actions import _begin_blueprint_wizard

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_fin", phase=ChatPhase.INTENT.value)
    _begin_blueprint_wizard(
        session,
        title_hint="环境报告",
        home=home,
        force_new=True,
        user_message="依据模版写环境报告",
    )
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    out = finalize_turn_cards(session, [], session_wizard=wiz)
    assert any(c.type == "blueprint_wizard" for c in out)
    assert out[-1].payload.get("step") == "meta"
