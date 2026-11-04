from report_writing.chat.blueprint_wizard_route import _merge_wizard_action_payload
from report_writing.domain.models import ChatSession, ChatUiStateRequest
from report_writing.services.chat_session import apply_chat_ui_state


def test_empty_payload_blueprint_id_keeps_session_bid():
    session = ChatSession(id="s1", blueprint_id="bp_existing")
    wizard = {"step": "upload", "blueprint_id": "bp_existing", "title": "工程安全预评价"}
    payload = {"step": "meta", "title": "工程安全预评价", "blueprint_id": ""}
    merged = _merge_wizard_action_payload(session, wizard, payload)
    assert merged["blueprint_id"] == "bp_existing"
    assert merged["step"] == "meta"


def test_ui_state_empty_bid_does_not_wipe_server_wizard():
    session = ChatSession(
        id="s1",
        blueprint_id="bp_existing",
        freeform={
            "blueprint_wizard": {
                "blueprint_id": "bp_existing",
                "step": "upload",
                "title": "安全预评价-新",
            }
        },
    )
    apply_chat_ui_state(
        session,
        ChatUiStateRequest(
            freeform_patch={
                "runtime": {
                    "workflows": {
                        "blueprint_wizard": {
                            "step": "meta",
                            "blueprint_id": "",
                            "title": "安全预评价-新",
                        }
                    }
                },
                "blueprint_wizard": {"step": "meta", "blueprint_id": "", "title": "安全预评价-新"},
            }
        ),
    )
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("blueprint_id") == "bp_existing"
    assert wiz.get("step") == "upload"
    assert session.blueprint_id == "bp_existing"


def test_ui_state_card_patch_does_not_regress_wizard_step():
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.domain.models import ChatMessage

    bid = "bp_card_patch"
    upload_card = _blueprint_wizard_card(
        blueprint_id=bid,
        step="upload",
        title="创建蓝本 · 上传样板",
        extra={"title": "T", "blueprint_id": bid, "step": "upload"},
    )
    session = ChatSession(
        id="s_card",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "upload", "title": "T"}},
        messages=[
            ChatMessage(role="assistant", text="蓝本已建好", cards=[upload_card], steps=[]),
        ],
    )
    apply_chat_ui_state(
        session,
        ChatUiStateRequest(
            message_patches=[
                {
                    "index": 0,
                    "text": "蓝本已建好",
                    "steps": [{"message": "正在处理蓝本向导…", "kind": "process"}],
                    "cards": [
                        {
                            "type": "blueprint_wizard",
                            "title": "创建蓝本 · 填写名称",
                            "payload": {"step": "meta", "title": "T", "blueprint_id": ""},
                        }
                    ],
                }
            ]
        ),
    )
    card = session.messages[0].cards[0]
    assert card.payload.get("step") == "upload"
    assert card.payload.get("blueprint_id") == bid
    assert session.messages[0].steps and session.messages[0].steps[0]["message"]


def test_ui_state_patch_does_not_paint_assistant_text_onto_user():
    """错位序号或带 role 的补丁不得把 AI 正文写成用户的话。"""
    from report_writing.domain.models import ChatMessage

    session = ChatSession(
        id="s_role",
        messages=[
            ChatMessage(role="user", text="再写一份安全报告"),
            ChatMessage(role="assistant", text="这边有 1 份蓝本，用哪份你来定"),
        ],
    )
    apply_chat_ui_state(
        session,
        ChatUiStateRequest(
            message_patches=[
                {
                    "index": 0,
                    "role": "assistant",
                    "text": "这边有 1 份蓝本，用哪份你来定",
                    "steps": [{"message": "列出可用蓝本完成", "kind": "process"}],
                }
            ]
        ),
    )
    assert session.messages[0].text == "再写一份安全报告"
    assert not session.messages[0].steps
    assert session.messages[1].text == "这边有 1 份蓝本，用哪份你来定"


def test_ui_state_slims_large_chapter_lists():
    session = ChatSession(
        id="s_slim",
        blueprint_id="bp_slim",
        freeform={
            "blueprint_wizard": {
                "blueprint_id": "bp_slim",
                "step": "outline_confirm",
            }
        },
    )
    fat = [{"key": f"c{i}", "title": f"章{i}", "sequence": str(i)} for i in range(1, 40)]
    apply_chat_ui_state(
        session,
        ChatUiStateRequest(
            freeform_patch={
                "blueprint_wizard": {
                    "blueprint_id": "bp_slim",
                    "step": "outline_confirm",
                    "chapters": fat,
                    "chapter_count": 39,
                }
            }
        ),
    )
    wiz = (session.freeform or {}).get("blueprint_wizard") or {}
    assert wiz.get("chapter_count") == 39
    assert len(wiz.get("chapters") or []) <= 8
