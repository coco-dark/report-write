"""依据模版：上传样板后必须带回「提取目录」向导卡（含 freeform 误锁场景）。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.session_state import (
    WRITING_PATH_FREEFORM,
    lock_writing_path,
)
from report_writing.chat.orchestrator import handle_chat
from report_writing.chat.resource_actions import _create_freeform_sample_document
from report_writing.chat.wizard_state import WIZARD_MODE_FREEFORM_SAMPLE
from report_writing.domain.models import ChatPhase, ChatRequest, ChatSession, ChatUiStateRequest
from report_writing.services.chat_session import apply_chat_ui_state, get_session, save_session
from report_writing.services.store import TaskStore


def test_upload_done_keeps_outline_card_under_freeform_ui_wipe(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_tpl_up", phase=ChatPhase.CREATE_HANDOFF.value)
    wid, wiz = _create_freeform_sample_document(home, session, title="lzb安全报告")
    assert wiz.get("mode") == WIZARD_MODE_FREEFORM_SAMPLE
    lock_writing_path(session, WRITING_PATH_FREEFORM)
    assert (session.freeform or {}).get("blueprint_wizard", {}).get("mode") == WIZARD_MODE_FREEFORM_SAMPLE

    store = TaskStore(home / "tasks" / wid)
    task = store.load()
    task.sample_ready = True
    task.sample_name = "样板.docx"
    task.sample_chars = 1200
    store.save(task)
    save_session(home, session)

    # 前端误判 freeform 时会清空 blueprint_wizard
    session = apply_chat_ui_state(
        get_session(home, "s_tpl_up"),
        ChatUiStateRequest.model_validate(
            {"freeform_patch": {"blueprint_wizard": {}, "writing_path": "freeform"}}
        ),
    )
    assert (session.freeform or {}).get("blueprint_wizard", {}).get("mode") == WIZARD_MODE_FREEFORM_SAMPLE
    save_session(home, session)

    resp = handle_chat(
        home,
        ChatRequest(
            session_id="s_tpl_up",
            action="blueprint_wizard",
            action_payload={"step": "upload_done", "blueprint_id": wid},
            client_sync={
                "freeform_patch": {"blueprint_wizard": {}, "writing_path": "freeform"},
            },
        ),
        invoke=lambda _s, _u: "{}",
    )
    wiz_cards = [c for c in (resp.cards or []) if c.type == "blueprint_wizard"]
    assert wiz_cards
    assert wiz_cards[0].payload.get("step") == "outline"
    assert wiz_cards[0].payload.get("mode") == WIZARD_MODE_FREEFORM_SAMPLE
    assert "提取目录" in (resp.reply or "")


def test_upload_done_keeps_outline_card_when_mode_wiped(tmp_path: Path):
    """mode 被清掉 + writing_path=freeform 时，上传完成仍必须带回提取目录卡。"""
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_tpl_up2", phase=ChatPhase.CREATE_HANDOFF.value)
    wid, wiz = _create_freeform_sample_document(home, session, title="样板报告")
    lock_writing_path(session, WRITING_PATH_FREEFORM)
    store = TaskStore(home / "tasks" / wid)
    task = store.load()
    task.sample_ready = True
    task.sample_name = "样板.docx"
    task.sample_chars = 800
    store.save(task)
    # 模拟 ui-state 把 mode 清掉，只留 id/step
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "freeform",
        "blueprint_wizard": {
            "blueprint_id": wid,
            "step": "upload",
            "working_task_id": wid,
        },
    }
    save_session(home, session)

    resp = handle_chat(
        home,
        ChatRequest(
            session_id="s_tpl_up2",
            action="blueprint_wizard",
            action_payload={"step": "upload_done", "blueprint_id": wid},
            client_sync={"freeform_patch": {"writing_path": "freeform"}},
        ),
        invoke=lambda _s, _u: "{}",
    )
    wiz_cards = [c for c in (resp.cards or []) if c.type == "blueprint_wizard"]
    assert wiz_cards, "上传完成后必须有蓝本向导卡"
    assert wiz_cards[0].payload.get("step") == "outline"
    assert "提取目录" in (resp.reply or "")
    assert "提取目录" in str(wiz_cards[0].title or "") or wiz_cards[0].payload.get("step") == "outline"
