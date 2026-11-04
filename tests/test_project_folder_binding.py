"""项目资料根：蓝本复用 + 写作任务换项目隔离。"""

from __future__ import annotations

from pathlib import Path

from report_writing.chat.writing import clone_blueprint_to_writing_task
from report_writing.controller import ensure_task
from report_writing.domain.elements import (
    effective_element_folder_ids,
    strip_project_scoped_folders_on_clone,
)
from report_writing.domain.models import (
    ElementNode,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.robots import resolve_knowledge_folders
from report_writing.services.store import TaskStore


def test_resolve_knowledge_folders_scopes():
    shared = ["kf-shared-law"]
    project = ["kf-project-a"]
    assert resolve_knowledge_folders(shared, knowledge_scope="shared") == shared
    assert resolve_knowledge_folders(shared, project_folder_ids=project, knowledge_scope="project") == project
    both = resolve_knowledge_folders(shared, project_folder_ids=project, knowledge_scope="both")
    assert both == ["kf-shared-law", "kf-project-a"]
    assert resolve_knowledge_folders([], project_folder_ids=[], knowledge_scope="both") is None


def test_effective_element_folder_ids_by_scope():
    el_shared = ElementNode(
        id="el1",
        kind="rag_search",
        knowledge_folder_ids=["kf-law"],
        knowledge_scope="shared",
    )
    el_project = ElementNode(
        id="el2",
        kind="rag_search",
        knowledge_folder_ids=["kf-old-project"],
        knowledge_scope="project",
    )
    el_both = ElementNode(
        id="el3",
        kind="rag_search",
        knowledge_folder_ids=["kf-law"],
        knowledge_scope="both",
    )
    project = ["kf-proj-b"]
    assert effective_element_folder_ids(el_shared, project_folder_ids=project) == ["kf-law"]
    assert effective_element_folder_ids(el_project, project_folder_ids=project) == ["kf-proj-b"]
    assert effective_element_folder_ids(el_both, project_folder_ids=project) == ["kf-law", "kf-proj-b"]


def test_strip_project_scoped_folders_on_clone():
    task = WritingTask(
        id="w1",
        project_folder_ids=["kf-should-clear"],
        project_folder_label="旧项目",
        element_nodes=[
            ElementNode(
                id="e-proj",
                kind="rag_search",
                knowledge_scope="project",
                knowledge_folder_ids=["kf-a"],
            ),
            ElementNode(
                id="e-shared",
                kind="rag_search",
                knowledge_scope="shared",
                knowledge_folder_ids=["kf-law"],
            ),
        ],
    )
    strip_project_scoped_folders_on_clone(task)
    assert task.project_folder_ids == []
    assert task.project_folder_label == ""
    by_id = {e.id: e for e in task.element_nodes}
    assert by_id["e-proj"].knowledge_folder_ids == []
    assert by_id["e-shared"].knowledge_folder_ids == ["kf-law"]


def test_clone_applies_project_folders_and_strips(tmp_path: Path):
    home = tmp_path
    bp_id = "bp_proj_bind"
    ensure_task(home / "tasks" / bp_id)
    bp = TaskStore(home / "tasks" / bp_id).load()
    bp.title = "安全预评价"
    bp.outline_confirmed = True
    bp.outline = OutlineTree(
        chapters=[
            OutlineNode(key="c1", sequence="1", title="概述", element_ids=["el-a"]),
            OutlineNode(key="c2", sequence="2", title="规模", element_ids=["el-b"]),
        ]
    )
    bp.element_nodes = [
        ElementNode(
            id="el-a",
            name="概述检索",
            kind="rag_search",
            query="工程概况",
            knowledge_scope="project",
            knowledge_folder_ids=["kf-old-a"],
        ),
        ElementNode(
            id="el-b",
            name="法规",
            kind="rag_search",
            query="法律法规",
            knowledge_scope="shared",
            knowledge_folder_ids=["kf-law"],
        ),
    ]
    TaskStore(home / "tasks" / bp_id).save(bp)

    cloned = clone_blueprint_to_writing_task(
        home,
        bp_id,
        chapter_keys=["c1", "c2"],
        project_folder_ids=["kf-project-b"],
        project_folder_label="项目B",
    )
    assert cloned.project_folder_ids == ["kf-project-b"]
    assert cloned.project_folder_label == "项目B"
    by_id = {e.id: e for e in cloned.element_nodes}
    assert by_id["el-a"].knowledge_folder_ids == []
    assert by_id["el-b"].knowledge_folder_ids == ["kf-law"]


def test_two_projects_keep_isolated_project_folder_ids():
    """同蓝本要素克隆到不同任务时，项目根互不串扰。"""
    assert effective_element_folder_ids(
        ElementNode(id="el-a", kind="file", knowledge_scope="project", knowledge_folder_ids=[]),
        project_folder_ids=["kf-project-a"],
    ) == ["kf-project-a"]
    assert effective_element_folder_ids(
        ElementNode(id="el-a", kind="file", knowledge_scope="project", knowledge_folder_ids=[]),
        project_folder_ids=["kf-project-b"],
    ) == ["kf-project-b"]
    assert effective_element_folder_ids(
        ElementNode(
            id="el-b",
            kind="file",
            knowledge_scope="both",
            knowledge_folder_ids=["kf-law"],
        ),
        project_folder_ids=["kf-project-a"],
    ) == ["kf-law", "kf-project-a"]
