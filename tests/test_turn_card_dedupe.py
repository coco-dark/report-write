"""同回合卡片去重（选蓝本 vs 定名）。"""

from report_writing.chat.cards import (
    ChatCard,
    collapse_duplicate_turn_cards,
    turn_has_blueprint_pick_cards,
    turn_should_defer_doc_name_card,
)
from report_writing.domain.models import ChatPhase, ChatSession


def test_turn_has_blueprint_pick_from_list_card():
    session = ChatSession(id="s", phase=ChatPhase.BLUEPRINT_SELECT.value)
    cards = [
        ChatCard(type="blueprint_list", title="选", payload={"items": []}),
        ChatCard(type="skip_blueprint", title="跳过", payload={}),
    ]
    assert turn_has_blueprint_pick_cards(cards, session=session)


def test_collapse_drops_doc_name_while_picking():
    session = ChatSession(id="s", phase=ChatPhase.BLUEPRINT_SELECT.value)
    cards = [
        ChatCard(type="blueprint_list", title="选", payload={"items": []}),
        ChatCard(
            type="intent_clarify",
            title="定名",
            payload={"source": "doc_name", "question": "起名"},
        ),
        ChatCard(
            type="intent_clarify",
            title="定名2",
            payload={"source": "doc_name", "question": "起名"},
        ),
        ChatCard(
            type="intent_clarify",
            title="入口",
            payload={"source": "entry_branch", "question": "用哪份蓝本"},
        ),
    ]
    out = collapse_duplicate_turn_cards(cards)
    assert len(out) == 1
    assert out[0].type == "blueprint_list"


def test_defer_doc_name_after_select_with_doc_window():
    session = ChatSession(id="s", phase="create_handoff")
    session.blueprint_id = "bp1"
    cards = [
        ChatCard(type="doc_window_hint", title="打开文档窗口", payload={}),
        ChatCard(type="materials_pack_gate", title="资料包", payload={}),
    ]
    assert turn_should_defer_doc_name_card(cards, session=session)


def test_append_doc_name_deferred_after_select_cards(tmp_path):
    from report_writing.chat.session_documents import append_doc_name_clarify_if_needed

    session = ChatSession(id="s", phase="create_handoff")
    session.blueprint_id = "bp1"
    cards = [
        ChatCard(type="doc_window_hint", title="打开", payload={}),
        ChatCard(type="blueprint_wizard", title="向导", payload={}),
    ]
    hint, merged = append_doc_name_clarify_if_needed(
        session, message="", reply="好，就用这份蓝本", cards=cards
    )
    assert "先给这份文档起个名字" not in hint
    assert len(merged) == 2
    assert not any(
        str((getattr(c, "payload", None) or {}).get("source") or "") == "doc_name"
        for c in merged
    )


def test_append_doc_name_deferred_while_picking():
    from report_writing.chat.session_documents import append_doc_name_clarify_if_needed

    session = ChatSession(id="s", phase=ChatPhase.BLUEPRINT_SELECT.value)
    cards = [ChatCard(type="blueprint_list", title="选", payload={"items": []})]
    hint, merged = append_doc_name_clarify_if_needed(
        session, message="写安全报告", reply="匹配结果", cards=cards
    )
    assert "先给这份文档起个名字" not in hint
    assert len(merged) == 1
    assert merged[0].type == "blueprint_list"


def test_focus_needs_doc_name_false_after_named_then_select_registry():
    from report_writing.chat.session_documents import (
        focus_needs_doc_name,
        patch_focus_onboarding,
        set_focus_document_key,
        upsert_session_document,
    )

    session = ChatSession(id="s_named_sel")
    session.blueprint_id = "bp1"
    ff = dict(session.freeform or {})
    ff["title"] = "大晚上安全报告"
    ff["writing_doc_named"] = True
    ff["meta_confirmed"] = True
    session.freeform = ff
    upsert_session_document(
        session,
        kind="writing",
        task_id="w1",
        blueprint_id="bp1",
        writing_task_id="w1",
        title="大晚上安全报告",
        registry_key="doc:abc1",
    )
    patch_focus_onboarding(session, name_confirmed=False, entry_path="match")
    set_focus_document_key(session, "doc:abc1")
    assert focus_needs_doc_name(session) is False


def test_append_no_naming_hint_when_already_named():
    from report_writing.chat.session_documents import append_doc_name_clarify_if_needed

    session = ChatSession(id="s", phase="create_handoff")
    session.blueprint_id = "bp1"
    session.freeform = {
        **dict(session.freeform or {}),
        "title": "我的报告",
        "writing_doc_named": True,
    }
    cards = [ChatCard(type="doc_window_hint", title="打开", payload={})]
    hint, merged = append_doc_name_clarify_if_needed(
        session, reply="好，就用这份蓝本", cards=cards
    )
    assert "起名" not in hint and "文档名" not in hint
    assert "还要" not in hint
    assert len(merged) == 1


def test_collapse_keeps_one_outline_preview_per_task():
    cards = [
        ChatCard(
            type="outline_preview",
            title="a",
            payload={"task_id": "w1", "outline_text": "1"},
        ),
        ChatCard(
            type="outline_preview",
            title="b",
            payload={"task_id": "w1", "outline_text": "2"},
        ),
    ]
    out = collapse_duplicate_turn_cards(cards)
    assert len(out) == 1
    assert str((out[0].payload or {}).get("outline_text") or "") == "2"
