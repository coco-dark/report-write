"""向导主动回退：re_upload_sample / re_extract_outline 须可靠落到对应 step。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.cards import _blueprint_wizard_card
from report_writing.chat.resource_actions import (
    _action_blueprint_wizard,
    _create_freeform_sample_document,
    execute_wizard_command,
)
from report_writing.chat.wizard_state import (
    WIZARD_MODE_FREEFORM_SAMPLE,
    sync_blueprint_wizard_session,
)
from report_writing.domain.models import ChatMessage, ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore


def _seed_sample_at_outline(home: Path, session: ChatSession, title: str = "环评样本") -> str:
    wid, wiz = _create_freeform_sample_document(home, session, title=title)
    store = TaskStore(home / "tasks" / wid)
    task = store.load()
    task.sample_ready = True
    task.sample_name = "sample.docx"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="总则"),
            OutlineNode(key="c2", sequence="2", title="工程概况"),
        ]
    )
    task.outline_confirmed = False
    store.save(task)
    wiz = {
        **dict(wiz),
        "step": "outline",
        "mode": WIZARD_MODE_FREEFORM_SAMPLE,
        "blueprint_id": wid,
        "working_task_id": wid,
        "chapter_count": 2,
    }
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wiz}
    session.messages = [
        ChatMessage(
            role="assistant",
            text="向导",
            cards=[
                _blueprint_wizard_card(
                    blueprint_id=wid,
                    step="outline",
                    title="依据样本 · 提取目录",
                    extra=wiz,
                )
            ],
            steps=[],
        )
    ]
    return wid


def test_re_upload_sample_sets_step_upload_and_keeps_pending(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_reupload")
    wid = _seed_sample_at_outline(home, session)

    reply, cards = execute_wizard_command(home, session, "re_upload_sample")
    assert "上传" in reply
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert wiz.get("step") == "upload"
    assert wiz.get("pending_wizard_command") == "re_upload_sample"
    assert wiz.get("outline_confirmed") is False
    assert cards and getattr(cards[0], "payload", {}).get("step") == "upload"

    # sync 不得因 sample_ready+目录 把 step 钉回 outline
    synced = sync_blueprint_wizard_session(home, session, blueprint_id=wid)
    assert synced is not None
    assert synced.get("step") == "upload"
    assert synced.get("pending_wizard_command") == "re_upload_sample"

    # 最新向导卡也应对齐 upload
    latest = None
    for msg in reversed(list(session.messages or [])):
        for card in reversed(list(msg.cards or [])):
            if getattr(card, "type", "") == "blueprint_wizard":
                latest = card
                break
        if latest:
            break
    assert latest is not None
    assert getattr(latest, "payload", {}).get("step") == "upload"


def test_re_upload_via_step_upload_without_command_when_past_upload(tmp_path: Path):
    """仅带 step=upload、无 wizard_command：已过上传时应视为主动回退，禁止兜底话术。"""
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_reupload_step")
    wid = _seed_sample_at_outline(home, session)

    reply, cards = _action_blueprint_wizard(
        home,
        session,
        {"step": "upload", "blueprint_id": wid},
    )
    assert "继续按卡片提示操作即可" not in reply
    assert "上传" in reply
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert wiz.get("step") == "upload"
    assert wiz.get("pending_wizard_command") == "re_upload_sample"
    assert cards and getattr(cards[0], "payload", {}).get("step") == "upload"


def test_re_extract_outline_sets_trigger_and_step(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_reextract")
    wid = _seed_sample_at_outline(home, session)
    # 推到确认目录后再回退提取
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    wiz["step"] = "outline_confirm"
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wiz}

    reply, cards = execute_wizard_command(home, session, "re_extract_outline")
    assert "提取" in reply
    wiz2 = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert wiz2.get("step") == "outline"
    assert wiz2.get("pending_wizard_command") == "re_extract_outline"
    assert wiz2.get("trigger_outline_extract") is True
    assert int(wiz2.get("chapter_count") or 0) == 0
    assert list(wiz2.get("chapters") or []) == []
    assert cards and getattr(cards[0], "payload", {}).get("step") == "outline"

    # sync 不得因任务里旧目录把向导又推回确认目录 / 清掉 re_extract
    synced = sync_blueprint_wizard_session(home, session, blueprint_id=wid)
    assert synced is not None
    assert synced.get("step") == "outline"
    assert synced.get("pending_wizard_command") == "re_extract_outline"
    assert synced.get("trigger_outline_extract") is True
    assert int(synced.get("chapter_count") or 0) == 0


def test_same_session_force_new_clears_sample_wizard(tmp_path: Path):
    """同会话样本向导未关 → force_new 开新文档应清掉 blueprint_wizard。"""
    from report_writing.chat.session_documents import begin_document_onboarding

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_focus")
    sample_wid = _seed_sample_at_outline(home, session, title="环评样本")
    wiz_before = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert wiz_before.get("working_task_id") == sample_wid
    assert wiz_before.get("mode") == WIZARD_MODE_FREEFORM_SAMPLE

    begin_document_onboarding(
        session, home=home, force_new=True, title_hint="述职报告｜lzb季度述职报告"
    )
    wiz_after = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert not wiz_after, f"force_new 后仍残留向导: {wiz_after}"


def test_collect_freeform_outline_clears_other_sample_wizard(tmp_path: Path):
    """同会话新开述职大纲后，清掉环评样本的 blueprint_wizard。"""
    from report_writing.chat.actions import _collect_freeform_outline
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        confirm_writing_document_name,
    )

    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_outline_clear")
    sample_wid = _seed_sample_at_outline(home, session, title="环评样本")

    begin_document_onboarding(
        session, home=home, force_new=True, title_hint="述职报告｜lzb季度述职报告"
    )
    confirm_writing_document_name(
        session, "述职报告｜lzb季度述职报告", message="名字叫述职", home=home
    )
    session.freeform = {
        **(session.freeform or {}),
        "writing_path": "freeform",
        "meta_confirmed": True,
        "title": "述职报告｜lzb季度述职报告",
        "writing_doc_named": True,
    }
    # force_new 已清向导；若残留则大纲路径也应清
    assert not (session.freeform or {}).get("blueprint_wizard")

    reply, cards = _collect_freeform_outline(
        home,
        session,
        "1 工作回顾\n2 问题与改进\n3 下季度计划",
        confirm=False,
    )
    assert reply or cards
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert not wiz
    assert session.task_id and session.task_id != sample_wid
