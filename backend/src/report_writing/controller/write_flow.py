"""Task controller helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from report_writing.agents.supervisor import run_supervisor
from report_writing.services.corpus import load_template, normalize_biz_type
from report_writing.infra.llm import or_live_model
from report_writing.domain.models import (
    ChapterRequirement,
    ChapterState,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.domain.outline import (
    finalize_outline,
    load_outline_json,
    outline_as_text,
    outline_from_brief_chapters,
    outline_from_payload,
    outline_from_text,
    read_template_text,
    slice_toc_region,
    template_from_outline,
)
from report_writing.infra.postgres import open_library
from report_writing.services.store import BriefCache, TaskStore

_log = logging.getLogger("uvicorn.error")

def write_from_study(task_dir: Path, dataset_dir: Path, on_event=None, model=None) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    if task.brief is None or not task.brief_confirmed:
        raise RuntimeError("请先确认分析结果")
    template = load_template(Path(task.template_path))
    _log.info("开始编写 任务 %s，共 %s 章", task.id, len(task.chapters))

    def vector_progress(done: int, total: int) -> None:
        if on_event is None:
            return
        on_event({
            "type": "step",
            "step": "search",
            "status": "running",
            "title": f"RAG 向量化 {done}/{total}",
            "result": {"note": f"{done}/{total}"},
        })

    from report_writing.services.corpus import load_task_corpus
    from report_writing.domain.elements import resolve_elements_for_chapter, sync_brief_from_outline

    sync_brief_from_outline(task)
    root = dataset_dir if dataset_dir and dataset_dir.is_dir() else None
    corpus = load_task_corpus(root or task.dataset_dir, task.biz_type or "002", on_progress=vector_progress)
    if on_event is not None:
        on_event({
            "type": "step",
            "step": "search",
            "status": "done",
            "title": "资料就绪" if root else "准备检索资料",
            "result": {"note": "可以按章节检索"},
        })
    project_folders = list(getattr(task, "project_folder_ids", None) or [])
    pack_id = str(getattr(task, "materials_pack_id", "") or "").strip()
    home = task_dir.parent.parent if task_dir.parent.name == "tasks" else task_dir.parent
    for requirement in task.brief.chapters:
        resolve_elements_for_chapter(
            requirement,
            task.element_nodes,
            corpus,
            outline=task.outline,
            project_folder_ids=project_folders,
            materials_pack_id=pack_id,
            home=home,
            task_id=task.id,
            biz_type=normalize_biz_type(task.biz_type or "002"),
        )
    managed = run_supervisor(
        phase="write",
        template=template,
        corpus=corpus,
        cache=BriefCache(task_dir / "unused-cache"),
        model=or_live_model(model),
        brief=task.brief,
        chapters=task.chapters,
        on_event=on_event,
    )
    if root is not None:
        task.dataset_dir = str(root)
        task.dataset_name = root.name
    task.chapters = managed.chapters
    task.steps.extend(managed.steps)
    task.refresh_status()
    store.save(task)
    return task


def remember_dataset(task_dir: Path, dataset_dir: Path) -> None:
    path = task_dir / "task.json"
    if not path.exists():
        return
    store = TaskStore(task_dir)
    task = store.load()
    task.dataset_dir = str(dataset_dir)
    task.dataset_name = dataset_dir.name
    store.save(task)


def ensure_task(task_dir: Path, biz_type: str = "002") -> WritingTask:
    from report_writing.services.store import TaskNotFoundError

    store = TaskStore(task_dir)
    try:
        return store.load()
    except TaskNotFoundError:
        pass
    from report_writing.services.users import current_user_id

    task = WritingTask(
        id=task_dir.name,
        biz_type=normalize_biz_type(biz_type or "002"),
        brief_confirmed=False,
        title="",
        category="",
        tags=[],
        description="",
        user_id=current_user_id(),
    )
    store.save(task)
    return task


