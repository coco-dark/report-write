"""无蓝本·依据样本：不入库；收录出新 bp*；同步回写库。选用线选用即 clone，可同步回写。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.actions import (
    _action_save_freeform_as_blueprint,
    _action_select_blueprint,
    _action_sync_document_to_blueprint_library,
)
from report_writing.chat.resource_actions import _action_blueprint_wizard, _create_freeform_sample_document
from report_writing.chat.session_documents import resolve_task_id, session_documents_map
from report_writing.chat.wizard_state import WIZARD_MODE_FREEFORM_SAMPLE
from report_writing.controller import create_blueprint, ensure_task
from report_writing.domain.models import ChatSession, OutlineNode, OutlineTree
from report_writing.services.store import TaskStore
from report_writing.services.task_ids import is_blueprint_task_id, is_writing_task_id


def test_freeform_sample_meta_creates_writing_task_not_library_bp(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_sample")
    reply, cards = _action_blueprint_wizard(
        home,
        session,
        {"step": "meta", "title": "样本预评价"},
    )
    assert "依据样本" in reply or "尚未入库" in reply
    wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert wiz.get("mode") == WIZARD_MODE_FREEFORM_SAMPLE
    wid = str(wiz.get("working_task_id") or "").strip()
    assert is_writing_task_id(wid)
    assert not is_blueprint_task_id(wid)
    assert session.task_id == wid
    assert not str(session.blueprint_id or "").strip()
    # 库目录下不应出现新 bp*
    task_dirs = [p.name for p in (home / "tasks").iterdir() if p.is_dir()]
    assert wid in task_dirs
    assert not any(is_blueprint_task_id(n) for n in task_dirs)
    assert cards and getattr(cards[0], "payload", {}).get("blueprint_id") == wid
    assert getattr(cards[0], "payload", {}).get("step") == "upload"

    # 向导进行中不得锁 freeform（前端会吞上传卡）；sync/reconcile 仍保留向导
    from report_writing.chat.intent_router import WRITING_PATH_FREEFORM, get_writing_path
    from report_writing.chat.session_runtime import reconcile_chat_session
    from report_writing.chat.session_state import user_chose_skip_blueprint
    from report_writing.chat.wizard_state import (
        finalize_wizard_cards_for_response,
        sync_blueprint_wizard_session,
    )

    assert get_writing_path(session) != WRITING_PATH_FREEFORM
    assert not user_chose_skip_blueprint(session)
    synced = sync_blueprint_wizard_session(home, session)
    assert synced is not None
    assert synced.get("mode") == WIZARD_MODE_FREEFORM_SAMPLE
    assert synced.get("step") == "upload"
    reconcile_chat_session(home, session)
    wiz2 = dict((session.freeform or {}).get("blueprint_wizard") or {})
    assert wiz2.get("step") == "upload"
    kept = finalize_wizard_cards_for_response(session, list(cards), wiz2)
    assert any(getattr(c, "type", "") == "blueprint_wizard" for c in kept)
    assert next(c for c in kept if c.type == "blueprint_wizard").payload.get("step") == "upload"


def test_collect_then_sync_updates_library_only_on_sync(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    session = ChatSession(id="s_collect")
    wid, wiz = _create_freeform_sample_document(home, session, title="收录文档")
    store = TaskStore(home / "tasks" / wid)
    task = store.load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", sequence="1", title="概述", points=["要点A"])]
    )
    task.outline_confirmed = True
    store.save(task)
    session.freeform = {
        **dict(session.freeform or {}),
        "outline_confirmed": True,
        "meta_confirmed": True,
        "title": "收录文档",
        "blueprint_wizard": wiz,
    }

    reply, _ = _action_save_freeform_as_blueprint(
        home,
        session,
        {
            "task_id": wid,
            "title": "收录文档",
            "category": "预评价",
            "tags": ["样本", "测试"],
        },
    )
    assert "收录" in reply
    bp_id = str((session.freeform or {}).get("collected_blueprint_id") or "").strip()
    assert is_blueprint_task_id(bp_id)
    writing = TaskStore(home / "tasks" / wid).load()
    assert str(writing.source_blueprint_id or "") == bp_id
    library = TaskStore(home / "tasks" / bp_id).load()
    assert library.category == "预评价"
    assert "样本" in (library.tags or [])

    # 改副本要点
    writing.outline.chapters[0].points = ["要点B-已改"]
    TaskStore(home / "tasks" / wid).save(writing)
    library_before = TaskStore(home / "tasks" / bp_id).load()
    assert library_before.outline.chapters[0].points == ["要点A"]

    sync_reply, _ = _action_sync_document_to_blueprint_library(home, session, {"task_id": wid})
    assert "同步" in sync_reply
    library_after = TaskStore(home / "tasks" / bp_id).load()
    assert library_after.outline.chapters[0].points == ["要点B-已改"]


def test_select_blueprint_clones_immediately_and_can_sync(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    bid = "bpSelectEarly"
    path = home / "tasks" / bid
    ensure_task(path)
    create_blueprint(path, title="库蓝本", category="", description="t")
    task = TaskStore(path).load()
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", points=["p"], keywords=["k"]),
        ]
    )
    task.outline_confirmed = True
    task.sample_ready = True
    TaskStore(path).save(task)

    session = ChatSession(id="s_sel", freeform={"title": "文档A", "writing_doc_named": True})
    _action_select_blueprint(home, session, {"blueprint_id": bid})
    assert session.blueprint_id == bid
    docs = session_documents_map(session)
    entry = docs.get(f"bp:{bid}") or {}
    wid = str(entry.get("writing_task_id") or session.task_id or "").strip()
    assert is_writing_task_id(wid)
    assert session.task_id == wid
    writing = TaskStore(home / "tasks" / wid).load()
    assert str(writing.source_blueprint_id or "") == bid
    # 对话变更应解析到副本，而非库蓝本
    assert resolve_task_id(session, {"blueprint_id": bid}) == wid

    writing.outline.chapters[0].points = ["副本已改"]
    TaskStore(home / "tasks" / wid).save(writing)
    assert TaskStore(home / "tasks" / bid).load().outline.chapters[0].points == ["p"]

    sync_reply, _ = _action_sync_document_to_blueprint_library(home, session, {"task_id": wid})
    assert "同步" in sync_reply
    assert TaskStore(home / "tasks" / bid).load().outline.chapters[0].points == ["副本已改"]
