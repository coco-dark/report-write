"""会话 runtime 收拢与 reconcile。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.session_runtime import (
    WORKFLOW_BLUEPRINT,
    WORKFLOW_WRITING,
    attach_turn_study_kick,
    migrate_legacy_freeform,
    reconcile_chat_session,
    runtime_public_view,
    workflow_get,
)
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def test_migrate_legacy_blueprint_into_runtime():
    session = ChatSession(
        id="cs1",
        freeform={
            "blueprint_wizard": {"blueprint_id": "bp_x", "step": "outline_confirm", "process_log": ["a"]},
            "writing_pace": "step",
        },
    )
    migrate_legacy_freeform(session)
    bp = workflow_get(session, WORKFLOW_BLUEPRINT)
    assert bp["blueprint_id"] == "bp_x"
    assert bp["step"] == "outline_confirm"
    view = runtime_public_view(session)
    assert view["workflows"][WORKFLOW_WRITING]["pace"] == "step"


def test_reconcile_blueprint_step_from_task(tmp_path: Path):
    bid = "bp_rt"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="一", sequence="1")])
    task.outline_confirmed = False
    TaskStore(path).save(task)

    session = ChatSession(
        id="cs2",
        blueprint_id=bid,
        freeform={"blueprint_wizard": {"blueprint_id": bid, "step": "done"}},
    )
    reconcile_chat_session(tmp_path, session)
    assert workflow_get(session, WORKFLOW_BLUEPRINT)["step"] == "outline_confirm"


def test_reconcile_clears_wizard_on_freeform_path(tmp_path: Path):
    bid = "bp_ff_stale"
    path = tmp_path / "tasks" / bid
    ensure_task(path)
    task = TaskStore(path).load()
    task.sample_ready = True
    task.outline = OutlineTree(chapters=[OutlineNode(key="c1", title="一", sequence="1")])
    task.outline_confirmed = True
    TaskStore(path).save(task)

    session = ChatSession(
        id="cs_ff",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        blueprint_id=bid,
        freeform={
            "writing_path": "freeform",
            "blueprint_wizard": {"blueprint_id": bid, "step": "study", "chapter_count": 1},
        },
    )
    reconcile_chat_session(tmp_path, session)
    assert not (session.freeform or {}).get("blueprint_wizard")
    assert not workflow_get(session, WORKFLOW_BLUEPRINT)
    assert not (session.blueprint_id or "").strip()


def test_reconcile_keeps_freeform_sample_upload_wizard(tmp_path: Path):
    """依据样本：创建后保留上传向导；即使误带 writing_path=freeform 也不得清掉。"""
    from report_writing.chat.resource_actions import _create_freeform_sample_document
    from report_writing.chat.wizard_state import WIZARD_MODE_FREEFORM_SAMPLE

    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
    session = ChatSession(id="cs_sample_ff", phase=ChatPhase.CREATE_HANDOFF.value)
    wid, wiz = _create_freeform_sample_document(tmp_path, session, title="我的安全报告")
    assert wiz.get("mode") == WIZARD_MODE_FREEFORM_SAMPLE
    assert wiz.get("step") == "upload"
    # 模拟旧会话误锁 freeform：reconcile 仍须保留向导
    session.freeform = {**(session.freeform or {}), "writing_path": "freeform"}
    reconcile_chat_session(tmp_path, session)
    kept = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert kept.get("mode") == WIZARD_MODE_FREEFORM_SAMPLE
    assert kept.get("step") == "upload"
    assert str(kept.get("working_task_id") or "") == wid
    assert workflow_get(session, WORKFLOW_BLUEPRINT).get("step") == "upload"


def test_export_and_freeform_in_runtime():
    session = ChatSession(
        id="cs4",
        phase="freeform_outline",
        freeform={
            "category": "安全",
            "title": "某项目",
            "export_prefs": {"template_id": "rt1", "style_id": "ws_x", "confirmed_only": True},
        },
    )
    migrate_legacy_freeform(session)
    ex = workflow_get(session, "export")
    assert ex["template_id"] == "rt1"
    assert ex["confirmed_only"] is True
    ff = workflow_get(session, "freeform")
    assert ff["category"] == "安全"


def test_template_touch_and_card_refresh():
    from report_writing.chat.session_runtime import (
        WORKFLOW_TEMPLATE,
        refresh_template_wizard_card_in_session,
        workflow_touch_template,
    )
    from report_writing.domain.models import ChatCard, ChatMessage

    session = ChatSession(
        id="cs5",
        messages=[
            ChatMessage(
                role="assistant",
                text="模版",
                cards=[
                    ChatCard(
                        type="template_wizard",
                        title="渲染模版向导",
                        payload={"resource": "render_template", "step": "choose"},
                    )
                ],
            )
        ],
    )
    workflow_touch_template(session, {"resource": "render_template", "step": "done", "item_id": "rt9"})
    assert workflow_get(session, WORKFLOW_TEMPLATE)["item_id"] == "rt9"
    refresh_template_wizard_card_in_session(session)
    payload = session.messages[0].cards[0].payload
    assert payload["step"] == "done"
    assert payload["item_id"] == "rt9"


def test_derive_writing_workflow_in_runtime(tmp_path: Path):
    session = ChatSession(
        id="cs3",
        phase=ChatPhase.WRITING.value,
        task_id="w123",
        blueprint_id="bp_y",
        freeform={"writing_pace": "batch", "write_chapter_keys": ["c1"]},
    )
    reconcile_chat_session(tmp_path, session)
    wf = workflow_get(session, WORKFLOW_WRITING)
    assert wf["active"] is True
    assert wf["pace"] == "batch"
    assert wf["chapter_keys"] == ["c1"]


def test_public_runtime_strips_study_kick_done_event_keeps_it():
    session = ChatSession(
        id="cs_study_kick",
        freeform={
            "blueprint_wizard": {
                "blueprint_id": "bp_k",
                "working_task_id": "w_k",
                "step": "study",
                "study_method": "sample",
                "trigger_chapter_study": ["c1.1"],
                "chapter_study_keys": ["c1.1"],
            }
        },
    )
    view = runtime_public_view(session)
    public = dict(view.get("blueprint_wizard") or {})
    assert "trigger_chapter_study" not in public
    assert "chapter_study_keys" not in public
    live = dict((session.freeform or {}).get("blueprint_wizard") or {})
    done = attach_turn_study_kick(public, live)
    assert done["trigger_chapter_study"] == ["c1.1"]
    assert done["chapter_study_keys"] == ["c1.1"]
    assert done["working_task_id"] == "w_k"
    assert done["step"] == "study"
