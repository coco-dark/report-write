"""蓝本收录名称查重；要素按名称+分类+标签查重。"""

from __future__ import annotations

from pathlib import Path

import pytest

from report_writing.chat.actions import _action_save_freeform_as_blueprint
from report_writing.chat.freeform_blueprint import promote_writing_task_to_blueprint
from report_writing.chat.resource_actions import _create_freeform_sample_document
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementNode, OutlineNode, OutlineTree
from report_writing.services.element_library import (
    find_library_items_by_identity,
    library_item_from_element,
    upsert_library_item,
)
from report_writing.services.store import TaskStore


def _ready_writing(home: Path, title: str = "收录文档") -> tuple[ChatSession, str]:
    (home / "tasks").mkdir(parents=True, exist_ok=True)
    session = ChatSession(id="s_dup")
    wid, wiz = _create_freeform_sample_document(home, session, title=title)
    store = TaskStore(home / "tasks" / wid)
    task = store.load()
    task.outline = OutlineTree(
        chapters=[OutlineNode(key="c1", sequence="1", title="概述", points=["p"])]
    )
    task.outline_confirmed = True
    store.save(task)
    session.freeform = {
        **dict(session.freeform or {}),
        "outline_confirmed": True,
        "meta_confirmed": True,
        "title": title,
        "blueprint_wizard": wiz,
    }
    return session, wid


def test_blueprint_collect_requires_category_and_tags(tmp_path: Path):
    session, wid = _ready_writing(tmp_path)
    with pytest.raises(ValueError, match="分类"):
        promote_writing_task_to_blueprint(tmp_path, wid, title="T1", category="", tags=["a"])
    with pytest.raises(ValueError, match="标签"):
        promote_writing_task_to_blueprint(tmp_path, wid, title="T1", category="安全", tags=[])


def test_blueprint_collect_rejects_duplicate_title(tmp_path: Path):
    session, wid = _ready_writing(tmp_path, title="同名蓝本")
    promote_writing_task_to_blueprint(
        tmp_path,
        wid,
        title="同名蓝本",
        category="安全评价",
        tags=["样本"],
    )
    # 再建一个写作任务再收录同名
    session2, wid2 = _ready_writing(tmp_path, title="另一文档")
    with pytest.raises(ValueError, match="同名"):
        promote_writing_task_to_blueprint(
            tmp_path,
            wid2,
            title="同名蓝本",
            category="环评",
            tags=["其他"],
        )
    # 换名可成功
    bp_id, task = promote_writing_task_to_blueprint(
        tmp_path,
        wid2,
        title="同名蓝本-修订版",
        category="环评",
        tags=["其他"],
    )
    assert bp_id.startswith("bp")
    assert task.title == "同名蓝本-修订版"


def test_action_save_passes_meta(tmp_path: Path):
    session, wid = _ready_writing(tmp_path, title="动作收录")
    reply, _ = _action_save_freeform_as_blueprint(
        tmp_path,
        session,
        {
            "task_id": wid,
            "title": "动作收录",
            "category": "述职",
            "tags": ["年度"],
        },
    )
    assert "收录" in reply
    bp_id = str((session.freeform or {}).get("collected_blueprint_id") or "")
    library = TaskStore(tmp_path / "tasks" / bp_id).load()
    assert library.category == "述职"
    assert "年度" in (library.tags or [])


def test_element_identity_duplicate_requires_all_three(tmp_path: Path):
    home = tmp_path
    (home / "tasks").mkdir(parents=True)
    ensure_task(home / "tasks" / "bpEl")
    el = ElementNode(
        id="el-1",
        name="项目概况",
        kind="rag_search",
        category="基础",
        tags=["概述", "项目"],
        query="q",
    )
    item = library_item_from_element(el, category="基础", tags=["概述", "项目"], purpose="用途")
    upsert_library_item(home, item)

    # 同名不同分类 → 不冲突
    assert not find_library_items_by_identity(home, "项目概况", category="其他", tags=["概述", "项目"])
    # 同名同分类不同标签 → 不冲突
    assert not find_library_items_by_identity(home, "项目概况", category="基础", tags=["概述"])
    # 三项都同 → 冲突
    hits = find_library_items_by_identity(
        home, "项目概况", category="基础", tags=["项目", "概述"]
    )
    assert len(hits) == 1
