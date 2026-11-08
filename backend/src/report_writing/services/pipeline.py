"""书写任务的创建、确认和导出。机器阶段由总控 Agent 管理。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.agents.supervisor import run_supervisor
from report_writing.services.corpus import load_task_corpus, load_template, normalize_biz_type, open_corpus
from report_writing.infra.llm import or_live_model
from report_writing.infra.postgres import attach_materials
from report_writing.domain.models import (
    ChapterState,
    ChapterStatus,
    CheckAction,
    HumanDecision,
    ParagraphComment,
    WritingTask,
)
from report_writing.services.store import BriefCache, TaskStore


def run_task(
    template_path: Path,
    dataset_dir: Path,
    task_dir: Path,
    cache_dir: Path,
    biz_type: str,
    force_study: bool = False,
    on_event=None,
    model=None,
) -> WritingTask:
    template = load_template(template_path)
    material_type = normalize_biz_type(biz_type)
    managed = run_supervisor(
        phase="full",
        template=template,
        corpus=attach_materials(dataset_dir, open_corpus(dataset_dir, material_type)),
        cache=BriefCache(cache_dir),
        model=or_live_model(model),
        force_study=force_study,
        on_event=on_event,
    )
    task = WritingTask(
        id=task_dir.name,
        template_id=template.id,
        template_version=template.version,
        dataset_name=dataset_dir.name,
        template_path=str(template_path),
        dataset_dir=str(dataset_dir),
        biz_type=material_type,
        brief=managed.brief,
        brief_from_cache=managed.brief_from_cache,
        study_runs=managed.study_runs,
        chapters=managed.chapters,
        steps=managed.steps,
    )
    task.refresh_status()
    TaskStore(task_dir).save(task)
    return task


def update_points(task_dir: Path, cache_dir: Path, chapter_key: str, points: list[str], save_cache: bool = False) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    if task.brief is None:
        raise RuntimeError("书写要求尚未生成")
    task.brief.chapter(chapter_key).points = points
    if save_cache:
        BriefCache(cache_dir).save(task.brief)
    task.refresh_status()
    store.save(task)
    return task


def rerun_chapter(
    task_dir: Path,
    template_path: Path,
    dataset_dir: Path | None = None,
    on_event=None,
    model=None,
) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    if task.brief is None:
        raise RuntimeError("书写要求尚未生成")
    template = load_template(template_path)
    corpus = load_task_corpus(dataset_dir or task.dataset_dir, task.biz_type or "002")
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
    task.chapters = managed.chapters
    task.steps = managed.steps
    task.refresh_status()
    store.save(task)
    return task


def run_chapters(
    task_dir: Path,
    *,
    chapter_key: str = "",
    include_children: bool = False,
    chapter_keys: list[str] | None = None,
    robot_id: str = "",
    on_event=None,
    search_only: bool = False,
    model=None,
    force_rerun: bool = False,
) -> WritingTask:
    """运行指定章节；父节点默认带子树。"""
    from report_writing.domain.elements import chapter_subtree_keys, resolve_elements_for_chapter, sync_brief_from_outline
    from report_writing.services.robots import get_robot

    store = TaskStore(task_dir)
    task = store.load()
    home = task_dir.parent.parent if task_dir.parent.name == "tasks" else task_dir.parent
    robot = get_robot(home, robot_id) if (robot_id or "").strip() else None
    if task.outline is None or not task.outline_confirmed:
        raise RuntimeError("请先确认目录结构")
    if task.brief is None or not task.brief_confirmed:
        from report_writing.controller import _ensure_brief_ready

        _ensure_brief_ready(task)
        store.save(task)
    if task.brief is None:
        raise RuntimeError("书写要求尚未生成，请先完成分析或保存大纲配置")
    if not task.template_path:
        # 无模版路径时用大纲生成最小 template.json，便于后续写章
        from report_writing.controller import _ensure_outline_template

        _ensure_outline_template(task, task_dir)
        store.save(task)
    if not task.template_path:
        raise RuntimeError("缺少模版路径，请先按目录分析或绑定原文")

    sync_brief_from_outline(task)
    if chapter_keys:
        keys = [key for key in chapter_keys if key]
    elif chapter_key:
        keys = chapter_subtree_keys(task.outline, chapter_key, include_children=include_children)
    else:
        raise RuntimeError("请指定要运行的章节")
    if not keys:
        raise RuntimeError("没有可运行的章节")

    key_set = set(keys)
    selected = [item for item in task.chapters if item.key in key_set]
    if not selected:
        raise RuntimeError("任务中没有匹配的章节状态，请先完成分析并确认")
    selected.sort(key=lambda item: keys.index(item.key) if item.key in keys else 0)

    # 重跑前清掉旧草稿/证据/检查，避免写作员沿用上一稿导致「改配置后仍一模一样」
    from report_writing.agents.check import revision_feedback_from_checks
    from report_writing.domain.models import ChapterStatus, CheckAction

    for chapter in selected:
        if chapter.status == ChapterStatus.CONFIRMED and not force_rerun:
            continue
        kept_defects = revision_feedback_from_checks(chapter.checks)
        chapter.draft = None
        chapter.evidence = []
        # 重试前保留最近一次未通过检查，写下一稿时仍能改正缺陷
        chapter.checks = (
            [c for c in (chapter.checks or []) if c.action != CheckAction.ACCEPT][-1:]
            if kept_defects
            else []
        )
        chapter.revision_count = 0
        chapter.official_text = ""
        chapter.human_note = ""
        chapter.status = ChapterStatus.PENDING if search_only else ChapterStatus.SEARCHING

    # 检索优先走要素解析结果（文件 / 引用槽位）；本地 corpus 作可选补充
    corpus = load_task_corpus(task.dataset_dir, task.biz_type or "002")
    project_folders = list(getattr(task, "project_folder_ids", None) or [])
    pack_id = str(getattr(task, "materials_pack_id", "") or "").strip()
    for requirement in task.brief.chapters:
        if requirement.key in key_set:
            resolve_elements_for_chapter(
                requirement,
                task.element_nodes,
                corpus,
                outline=task.outline,
                robot=robot,
                project_folder_ids=project_folders,
                materials_pack_id=pack_id,
                home=home,
                task_id=task.id,
                biz_type=normalize_biz_type(task.biz_type or "002"),
                model=model,
            )

    if search_only:
        from report_writing.agents.supervisor import execute_chapter_search
        from report_writing.domain.models import StepEvent

        model = or_live_model(model)
        new_steps: list[StepEvent] = []

        def on_step(step: str, status: str, chapter: str = "", title: str = "", payload: dict | None = None) -> None:
            event = {
                "type": "step",
                "step": step,
                "status": status,
                "chapter": chapter,
                "title": title,
                "result": payload or {},
            }
            new_steps.append(StepEvent.from_dict(event))
            if on_event is not None:
                on_event(event)

        for chapter in selected:
            requirement = task.brief.chapter(chapter.key)
            execute_chapter_search(
                chapter,
                requirement,
                corpus,
                model,
                robot,
                on_step,
                search_only=True,
                chapters=task.chapters,
                brief=task.brief,
            )
        task.steps = list(task.steps) + new_steps
        task.refresh_status()
        store.save(task)
        return task

    template = load_template(Path(task.template_path))
    managed = run_supervisor(
        phase="write",
        template=template,
        corpus=corpus,
        cache=BriefCache(task_dir / "unused-cache"),
        model=or_live_model(model),
        brief=task.brief,
        chapters=selected,
        on_event=on_event,
        robot=robot,
    )
    by_key = {item.key: item for item in managed.chapters}
    task.chapters = [by_key.get(item.key, item) for item in task.chapters]
    task.steps = list(task.steps) + list(managed.steps)
    task.refresh_status()
    store.save(task)
    if any(c.status == ChapterStatus.CONFIRMED for c in (task.chapters or [])):
        try:
            from report_writing.services.task_chapters import sync_confirmed_chapters

            sync_confirmed_chapters(task)
        except Exception:
            pass
    return task


def _append_outline_node_from_blueprint(task: WritingTask, chapter_key: str) -> bool:
    """写作 clone 大纲缺 key 时，从 source_blueprint 拷贝同 key 节点（正文仍落 clone）。"""
    key = (chapter_key or "").strip()
    if not key or task.outline is None:
        return False
    if any(str(getattr(n, "key", "") or "") == key for n in (task.outline.chapters or [])):
        return True
    source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
    if not source_bp:
        return False
    blueprint = None
    try:
        from report_writing.services.blueprint_catalog import load_blueprint_task

        blueprint = load_blueprint_task(source_bp)
    except Exception:
        blueprint = None
    if blueprint is None:
        # 文件系统回落：与 clone 同级 tasks/{bp}
        try:
            from report_writing.services.task_ids import is_blueprint_task_id

            if is_blueprint_task_id(source_bp):
                # 无法在无 home 时可靠定位；调用方 confirm_chapter 会再试
                pass
        except Exception:
            pass
        return False
    if blueprint.outline is None:
        return False
    node = next(
        (n for n in (blueprint.outline.chapters or []) if str(getattr(n, "key", "") or "") == key),
        None,
    )
    if node is None:
        return False
    import copy

    task.outline.chapters = list(task.outline.chapters or [])
    task.outline.chapters.append(copy.deepcopy(node))
    return True


def ensure_chapter_state(
    task: WritingTask,
    chapter_key: str,
    *,
    home: Path | None = None,
) -> ChapterState:
    """按 key 取章节状态；大纲里有但 chapters 未建时补一条，便于保存/确认正文。"""
    key = (chapter_key or "").strip()
    if not key:
        raise KeyError(chapter_key)
    for item in task.chapters or []:
        if item.key == key:
            return item
    title = key
    structure_only = False
    if task.outline is not None:
        node = next((n for n in (task.outline.chapters or []) if n.key == key), None)
        if node is None:
            # 写作 clone 子集大纲：尝试从蓝本补节点
            if _append_outline_node_from_blueprint(task, key):
                node = next((n for n in (task.outline.chapters or []) if n.key == key), None)
            if node is None and home is not None:
                source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
                if source_bp:
                    bp_path = Path(home) / "tasks" / source_bp
                    if (bp_path / "task.json").is_file():
                        try:
                            bp_task = TaskStore(bp_path).load()
                            bp_node = next(
                                (
                                    n
                                    for n in ((bp_task.outline.chapters if bp_task.outline else None) or [])
                                    if n.key == key
                                ),
                                None,
                            )
                            if bp_node is not None:
                                import copy

                                task.outline.chapters = list(task.outline.chapters or [])
                                task.outline.chapters.append(copy.deepcopy(bp_node))
                                node = task.outline.chapters[-1]
                        except Exception:
                            node = None
            if node is None:
                raise KeyError(key)
        title = (node.title or "").strip() or key
        structure_only = bool(getattr(node, "structure_only", False))
    else:
        raise KeyError(key)
    state = ChapterState(key=key, title=title)
    if structure_only:
        state.status = ChapterStatus.SKIPPED
        state.human_note = "目录节点，无需解析与写作"
    task.chapters.append(state)
    return state


def confirm_chapter(
    task_dir: Path,
    template_path: Path,
    dataset_dir: Path,
    chapter_key: str,
    decision: HumanDecision,
    text: str = "",
    comment: str = "",
    model=None,
) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    home = task_dir.parent.parent if task_dir.parent.name == "tasks" else task_dir.parent
    chapter = ensure_chapter_state(task, chapter_key, home=home)
    if decision == HumanDecision.SKIP:
        chapter.status = ChapterStatus.SKIPPED
        chapter.human_note = comment
    elif decision == HumanDecision.APPROVE:
        # 待人工/待确认：可直接用编辑后的正文确认；无正文时回落草稿
        if text.strip():
            chapter.official_text = text.strip()
        elif chapter.draft is not None:
            chapter.official_text = _draft_text(chapter)
        elif (chapter.official_text or "").strip():
            chapter.official_text = chapter.official_text.strip()
        else:
            raise RuntimeError("没有可确认的正文，请先编辑后再确认")
        chapter.status = ChapterStatus.CONFIRMED
        chapter.human_note = comment
    elif decision == HumanDecision.EDIT:
        if not text.strip():
            raise RuntimeError("改字后通过需要提供正文")
        chapter.official_text = text.strip()
        chapter.status = ChapterStatus.CONFIRMED
        chapter.human_note = comment
    elif decision == HumanDecision.RETURN:
        if task.brief is None or chapter.draft is None:
            raise RuntimeError("没有可退回的草稿")
        chapter.checks.append(
            _return_comment(chapter, comment or "请按人工意见修改本章")
        )
        chapter.status = ChapterStatus.WRITING
        _rewrite(task, chapter, template_path, dataset_dir, model=model)
    task.refresh_status()
    store.save(task)
    if decision in {HumanDecision.APPROVE, HumanDecision.EDIT}:
        try:
            from report_writing.services.task_chapters import sync_confirmed_chapters

            sync_confirmed_chapters(task)
        except Exception:
            pass
    return task


def export_confirmed(task: WritingTask) -> dict[str, Any]:
    chapters = {}
    for chapter in task.chapters:
        chapters[chapter.key] = chapter.official_text if chapter.status == ChapterStatus.CONFIRMED else ""
    return {"template_id": task.template_id, "template_version": task.template_version, "chapters": chapters}


def _rewrite(
    task: WritingTask,
    chapter: ChapterState,
    template_path: Path,
    dataset_dir: Path,
    *,
    model=None,
) -> None:
    template = load_template(template_path)
    managed = run_supervisor(
        phase="revise",
        template=template,
        corpus=attach_materials(dataset_dir, open_corpus(dataset_dir, normalize_biz_type(task.biz_type))),
        cache=BriefCache(Path(template_path).parent / ".unused"),
        model=or_live_model(model),
        brief=task.brief,
        chapters=[chapter],
    )
    index = next(position for position, item in enumerate(task.chapters) if item.key == chapter.key)
    task.chapters[index] = managed.chapters[0]
    task.steps.extend(managed.steps)


def _return_comment(chapter: ChapterState, comment: str):
    from report_writing.domain.models import CheckResult

    comments = [
        ParagraphComment(index=paragraph.index, message=comment)
        for paragraph in (chapter.draft.paragraphs if chapter.draft else [])
    ]
    return CheckResult(action=CheckAction.REVISE, comments=comments, reason=comment)


def _paragraphs_html(chapter: ChapterState) -> str:
    if chapter.draft is None:
        return ""
    from report_writing.domain.content_blocks import ensure_html_fragment

    parts = [ensure_html_fragment(paragraph.text) for paragraph in chapter.draft.paragraphs if paragraph.text]
    return "\n".join(parts).strip()


def _draft_text(chapter: ChapterState) -> str:
    """确认/导出：已保存正文优先；没有再拼本轮草稿。"""
    official = (getattr(chapter, "official_text", None) or "").strip()
    if official:
        return official
    return _paragraphs_html(chapter)


def seal_written_chapter(chapter: ChapterState, *, note: str = "写完自动保存") -> bool:
    """有正文则直接落库为已确认。重写后以本轮草稿覆盖旧 official_text。"""
    text = (_paragraphs_html(chapter) or _draft_text(chapter)).strip()
    if not text:
        return False
    chapter.official_text = text
    chapter.status = ChapterStatus.CONFIRMED
    chapter.human_note = note
    return True
