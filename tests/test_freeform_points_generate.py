"""无蓝本：协助生成写作要点。"""

from __future__ import annotations

import json
from pathlib import Path

from report_writing.chat.actions import _collect_freeform_outline, _collect_freeform_points
from report_writing.controller import confirm_outline, ensure_task, save_outline, save_spec_meta
from report_writing.domain.models import ChatPhase, ChatSession, OutlineNode


def test_generate_freeform_points_fills_outline(tmp_path: Path):
    session = ChatSession(
        id="s1",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id="w20260101120000aa",
        freeform={"meta_confirmed": True, "category": "工作说明", "title": "测试"},
    )
    wdir = tmp_path / "tasks" / session.task_id
    ensure_task(wdir)
    save_spec_meta(wdir, title="测试", category="工作说明")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="总则", level=1),
            OutlineNode(key="c2", sequence="2", title="范围", level=1),
        ],
    )
    confirm_outline(wdir)

    def invoke(_system: str, _user: str) -> str:
        return json.dumps(
            [
                {
                    "key": "c1",
                    "points": ["说明目的", "列依据"],
                    "describes": "总则章",
                    "keywords": ["总则 依据"],
                },
                {
                    "key": "c2",
                    "points": ["界定范围"],
                    "describes": "范围章",
                    "keywords": ["适用范围"],
                },
            ],
            ensure_ascii=False,
        )

    reply, cards = _collect_freeform_points(
        tmp_path, session, "", invoke=invoke, generate=True
    )
    assert "起草" in reply or "要点" in reply
    assert session.phase == ChatPhase.READY_TO_WRITE.value
    assert any(c.type == "start_writing" for c in cards)


def test_generate_freeform_points_respects_chapter_keys(tmp_path: Path):
    """单章生成不得覆盖其它章已有要点。"""
    from report_writing.chat.helpers import _load_task

    session = ChatSession(
        id="s2",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id="w20260101120000bb",
        freeform={"meta_confirmed": True, "category": "工作说明", "title": "测试"},
    )
    wdir = tmp_path / "tasks" / session.task_id
    ensure_task(wdir)
    save_spec_meta(wdir, title="测试", category="工作说明")
    keep_pts = ["原有范围要点", "勿覆盖"]
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="总则", level=1),
            OutlineNode(
                key="c2",
                sequence="2",
                title="范围",
                level=1,
                points=list(keep_pts),
                describes="范围原说明",
            ),
        ],
    )
    confirm_outline(wdir)

    def invoke(_system: str, user: str) -> str:
        # 模型只应收到 c1；即便误返回 c2 也不应写入
        assert '"key": "c2"' not in user and '"key":"c2"' not in user.replace(" ", "")
        return json.dumps(
            [
                {
                    "key": "c1",
                    "points": ["说明目的"],
                    "describes": "总则章",
                    "keywords": ["总则"],
                },
                {
                    "key": "c2",
                    "points": ["不该写入"],
                    "describes": "不该",
                    "keywords": ["坏"],
                },
            ],
            ensure_ascii=False,
        )

    reply, _cards = _collect_freeform_points(
        tmp_path,
        session,
        "",
        invoke=invoke,
        generate=True,
        payload={"chapter_keys": ["c1"]},
    )
    assert "起草" in reply or "要点" in reply
    task = _load_task(tmp_path, session.task_id)
    assert task and task.outline
    by_key = {str(c.key): c for c in task.outline.chapters}
    assert by_key["c1"].points and "说明目的" in by_key["c1"].points
    assert list(by_key["c2"].points or []) == keep_pts
    assert by_key["c2"].describes == "范围原说明"


def test_collect_points_does_not_infer_skip_from_message(tmp_path: Path):
    session = ChatSession(
        id="s-skip-msg",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id="w20260101120000cc",
        freeform={"meta_confirmed": True, "category": "工作说明", "title": "测试"},
    )
    wdir = tmp_path / "tasks" / session.task_id
    ensure_task(wdir)
    save_spec_meta(wdir, title="测试", category="工作说明")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="总则", level=1),
            OutlineNode(key="c2", sequence="2", title="范围", level=1),
        ],
    )
    confirm_outline(wdir)
    reply, _cards = _collect_freeform_points(
        tmp_path, session, "先跳过要点直接开写", invoke=None
    )
    assert (session.freeform or {}).get("points_step") != "skipped"
    assert "收到" in reply or "要点" in reply


def test_llm_draft_uses_rich_fallback_when_model_empty(tmp_path: Path):
    """模型空返回时用结构化兜底，不再两句万能概括。"""
    from report_writing.chat.freeform_points import llm_draft_chapter_points
    from report_writing.chat.helpers import _load_task

    tid = "w20260101120000cc"
    session = ChatSession(
        id="s3",
        phase=ChatPhase.FREEFORM_POINTS.value,
        task_id=tid,
        freeform={"meta_confirmed": True, "category": "环境影响评价", "title": "测试报告"},
    )
    wdir = tmp_path / "tasks" / tid
    ensure_task(wdir)
    save_spec_meta(wdir, title="测试报告", category="环境影响评价")
    save_outline(
        wdir,
        [
            OutlineNode(key="c1", sequence="1", title="评价工作等级", level=1),
            OutlineNode(key="c2", sequence="2", title="评价范围", level=1),
        ],
    )
    confirm_outline(wdir)
    task = _load_task(tmp_path, tid)
    assert task

    updated = llm_draft_chapter_points(
        session, task, invoke=lambda _s, _u: "[]"
    )
    by_key = {str(c.key): c for c in updated}
    assert len(by_key["c1"].points or []) >= 6
    assert len(str(by_key["c1"].describes or "")) >= 40
    assert "待补充" in "\n".join(by_key["c1"].points or []) or "信息类型" in "\n".join(
        by_key["c1"].points or []
    )
