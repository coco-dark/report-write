"""同会话新开无蓝本写作不得覆盖已有安全预评价任务。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from report_writing.chat.actions import _action_skip_blueprint, _collect_freeform_outline
from report_writing.chat.session_documents import session_documents_map, upsert_session_document
from report_writing.chat.session_goal import apply_session_goal_to_task
from report_writing.controller import ensure_task
from report_writing.domain.models import (
    ChatPhase,
    ChatSession,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.store import TaskStore


def _seed_safety_task(home: Path) -> str:
    tid = "w_safety_pre_eval"
    path = home / "tasks" / tid
    ensure_task(path)
    store = TaskStore(path)
    task = store.load()
    task.title = "安全预评价报告"
    task.category = "安全预评价"
    task.outline = OutlineTree(
        chapters=[
            OutlineNode(key="intro", sequence="1", title="概述", points=["p"], keywords=["k"]),
            OutlineNode(key="scale", sequence="2", title="建设规模", points=["p"], keywords=["k"]),
        ]
    )
    store.save(task)
    return tid


def test_skip_blueprint_clears_task_and_does_not_overwrite_old(tmp_path: Path):
    old_tid = _seed_safety_task(tmp_path)
    session = ChatSession(
        id="bleed1",
        phase=ChatPhase.READY_TO_WRITE.value,
        task_id=old_tid,
        freeform={
            "session_goal": {
                "doc_category": "安全预评价",
                "doc_title": "安全预评价报告",
                "summary": "安全预评价｜安全预评价报告",
            },
            "focus_document_key": f"task:{old_tid}",
        },
    )
    upsert_session_document(
        session,
        kind="outline",
        task_id=old_tid,
        title="安全预评价报告",
    )

    invoke = MagicMock(return_value="1 个人总结\n2 工作业绩\n3 不足与展望")
    _action_skip_blueprint(
        session,
        home=tmp_path,
        invoke=invoke,
        title_hint="",
        extra_message="再写一份述职报告｜2026个人述职，不用蓝本",
    )

    assert session.task_id != old_tid
    assert not str((session.freeform or {}).get("focus_document_key") or "").startswith(
        f"task:{old_tid}"
    )

    # 旧任务文件未被改写成述职大纲
    old = TaskStore(tmp_path / "tasks" / old_tid).load()
    assert old.title == "安全预评价报告"
    assert [n.title for n in old.outline.chapters] == ["概述", "建设规模"]

    docs = session_documents_map(session)
    old_entry = docs.get(f"task:{old_tid}") or {}
    assert old_entry.get("title") == "安全预评价报告"
    assert old_entry.get("goal_snapshot")


def test_freeform_outline_regenerate_rejects_foreign_task(tmp_path: Path):
    old_tid = _seed_safety_task(tmp_path)
    session = ChatSession(
        id="bleed2",
        phase=ChatPhase.FREEFORM_OUTLINE.value,
        task_id=old_tid,
        freeform={
            "meta_confirmed": True,
            "category": "述职报告",
            "title": "2026个人述职",
            "outline_pending_edit": False,
            "session_documents": {
                f"task:{old_tid}": {
                    "kind": "outline",
                    "title": "安全预评价报告",
                    "task_id": old_tid,
                }
            },
        },
    )
    invoke = MagicMock(return_value="1 个人总结\n2 工作业绩\n3 不足与展望")
    _collect_freeform_outline(
        tmp_path,
        session,
        "帮我生成目录",
        invoke=invoke,
        regenerate=True,
    )
    assert session.task_id != old_tid
    old = TaskStore(tmp_path / "tasks" / old_tid).load()
    assert old.title == "安全预评价报告"
    assert [n.title for n in old.outline.chapters] == ["概述", "建设规模"]


def test_apply_session_goal_skips_mismatched_task_title():
    s = ChatSession(
        id="sg_mismatch",
        freeform={
            "session_goal": {
                "doc_category": "述职报告",
                "doc_title": "2026个人述职",
                "notes": "述职人李志",
            }
        },
    )
    task = WritingTask(
        id="w_old",
        title="安全预评价报告",
        category="安全预评价",
        outline=OutlineTree(
            chapters=[OutlineNode(key="c1", title="1.1", points=["p"], keywords=["k"])]
        ),
    )
    task.outline.chapters[0].aux_prompt = "原有补充"
    assert apply_session_goal_to_task(s, task, force=True) is False
    assert "李志" not in (task.outline.chapters[0].aux_prompt or "")
    assert "原有补充" in (task.outline.chapters[0].aux_prompt or "")
