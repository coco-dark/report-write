"""Task controller helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from report_writing.agents.supervisor import run_supervisor
from report_writing.services.corpus import load_template, normalize_biz_type
from report_writing.infra.llm import live_model
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
_PENDING_STUDY_SOURCE = "pending_study_source.txt"


def analyze_template(
    template_path: Path,
    task_dir: Path,
    cache_dir: Path,
    biz_type: str,
    force_study: bool = False,
    on_event=None,
) -> WritingTask:
    """兼容旧接口：先生成目录并自动确认，再学习章节。"""
    from report_writing.controller.outline_flow import confirm_outline, generate_outline, study_from_outline
    source = Path(template_path)
    if source.suffix.lower() == ".json":
        # 旧路径已是切好的 template.json，直接学习
        template = load_template(source)
        material_type = normalize_biz_type(biz_type)
        managed = run_supervisor(
            phase="study",
            template=template,
            cache=BriefCache(cache_dir),
            model=live_model(),
            force_study=force_study,
            on_event=on_event,
        )
        outline = outline_from_brief_chapters(managed.brief.chapters) if managed.brief else OutlineTree()
        task = WritingTask(
            id=task_dir.name,
            template_id=template.id,
            template_version=template.version,
            template_path=str(source),
            source_path="",
            biz_type=material_type,
            outline=outline,
            outline_confirmed=True,
            brief=managed.brief,
            brief_from_cache=managed.brief_from_cache,
            brief_confirmed=False,
            study_runs=managed.study_runs,
            chapters=managed.chapters,
            steps=managed.steps,
        )
        task.refresh_status()
        TaskStore(task_dir).save(task)
        return task
    task = generate_outline(source, task_dir, biz_type, on_event=on_event)
    task = confirm_outline(task_dir)
    return study_from_outline(task_dir, cache_dir, force_study=force_study, on_event=on_event)


def _lock_brief_to_outline(brief, tree: OutlineTree) -> None:
    by_key = {item.key: item for item in tree.chapters}
    for chapter in brief.chapters:
        node = by_key.get(chapter.key)
        if node is None:
            continue
        chapter.sequence = node.sequence
        chapter.title = node.title
        chapter.level = node.level
        chapter.parent = node.parent
        chapter.aux_prompt = (node.aux_prompt or "").strip()
        chapter.material_files = list(node.material_files or [])
        chapter.knowledge_folder_ids = list(getattr(node, "knowledge_folder_ids", None) or [])
        chapter.element_ids = list(node.element_ids or [])
        # 始终与大纲对齐（含清空），否则文档窗删掉参考正文/说明后 brief 仍留旧值
        chapter.points = list(node.points or [])
        chapter.generate = "\n".join(node.points or [])
        node_keywords = [item.strip() for item in (getattr(node, "keywords", None) or []) if str(item).strip()]
        chapter.keywords = node_keywords
        chapter.describes = (node.describes or "").strip()
        chapter.reference_text = (getattr(node, "reference_text", None) or "").strip()
        chapter.structure_only = bool(getattr(node, "structure_only", False))
        chapter.fixed_output = bool(getattr(node, "fixed_output", False))
        chapter.search_terms_separate = bool(getattr(node, "search_terms_separate", False))
        blocks = _content_blocks_of(node)
        chapter.content_blocks = blocks


def _content_blocks_of(node) -> list:
    from report_writing.domain.models import ContentBlock

    raw = list(getattr(node, "content_blocks", None) or [])
    out = []
    for item in raw:
        if isinstance(item, ContentBlock):
            out.append(item)
        elif isinstance(item, dict):
            # ContentBlock.cells_sample 带 before validator，可吞一维字符串行
            out.append(ContentBlock.model_validate(item))
    return out


def _brief_from_outline(task) -> "WritingBrief":
    from report_writing.domain.models import ChapterRequirement, WritingBrief

    chapters = []
    for node in task.outline.chapters if task.outline else []:
        points = list(node.points or [])
        keywords = [item.strip() for item in (getattr(node, "keywords", None) or []) if str(item).strip()]
        if not keywords:
            keywords = list(points)
        structure_only = bool(getattr(node, "structure_only", False))
        fixed_output = bool(getattr(node, "fixed_output", False))
        chapters.append(
            ChapterRequirement(
                key=node.key,
                title=node.title,
                sequence=node.sequence or "",
                level=int(node.level or 1),
                parent=node.parent or "",
                describes=(node.describes or "").strip(),
                points=[] if structure_only else points,
                keywords=[] if structure_only or fixed_output else keywords,
                content_blocks=[] if structure_only else _content_blocks_of(node),
                reference_text=(getattr(node, "reference_text", None) or "").strip(),
                generate="" if structure_only else "\n".join(points),
                aux_prompt=(node.aux_prompt or "").strip(),
                material_files=list(node.material_files or []),
                knowledge_folder_ids=list(getattr(node, "knowledge_folder_ids", None) or []),
                element_ids=list(node.element_ids or []),
                structure_only=structure_only,
                fixed_output=fixed_output,
                search_terms_separate=bool(getattr(node, "search_terms_separate", False)),
            )
        )
    return WritingBrief(
        template_id=task.template_id or f"{task.biz_type or '002'}-outline",
        template_version=task.template_version or "1",
        paragraph_format=(
            "输出完整 HTML：文字用 <p>…</p>；表格用完整 <table>…</table>（含合并单元格）；"
            "图片用 <figure class=\"image-placeholder\"> 占位。标签必须成对闭合。"
        ),
        chapters=chapters,
    )


def _ensure_chapter_states(task) -> None:
    from report_writing.domain.models import ChapterState, ChapterStatus

    if task.outline is None:
        return
    existing = {item.key for item in task.chapters}
    for node in task.outline.chapters:
        if node.key not in existing:
            state = ChapterState(key=node.key, title=node.title)
            if bool(getattr(node, "structure_only", False)):
                state.status = ChapterStatus.SKIPPED
                state.human_note = "目录节点，无需解析与写作"
            task.chapters.append(state)
            existing.add(node.key)
    sync_structure_only_chapter_status(task)


def sync_structure_only_chapter_status(task) -> None:
    """目录节点不参与解析/写作：状态标为跳过。取消目录后恢复为待处理。"""
    from report_writing.domain.models import ChapterStatus

    if task is None or task.outline is None:
        return
    by_key = {item.key: item for item in (task.outline.chapters or [])}
    for chapter in task.chapters or []:
        node = by_key.get(chapter.key)
        if node is None:
            continue
        if bool(getattr(node, "structure_only", False)):
            if chapter.status != ChapterStatus.CONFIRMED:
                chapter.status = ChapterStatus.SKIPPED
                note = (chapter.human_note or "").strip()
                if not note or "目录" in note:
                    chapter.human_note = "目录节点，无需解析与写作"
        elif chapter.status == ChapterStatus.SKIPPED and "目录" in (chapter.human_note or ""):
            chapter.status = ChapterStatus.PENDING
            chapter.human_note = ""


def _ensure_brief_ready(task) -> None:
    """目录已确认时，保证有 brief / 章节状态，便于直接运行。"""
    if task.outline is None:
        return
    if task.brief is None:
        task.brief = _brief_from_outline(task)
    else:
        _lock_brief_to_outline(task.brief, task.outline)
        # 大纲新增的章节补进 brief
        known = {item.key for item in task.brief.chapters}
        for node in task.outline.chapters:
            if node.key not in known:
                from report_writing.domain.models import ChapterRequirement

                points = list(node.points or [])
                keywords = [item.strip() for item in (getattr(node, "keywords", None) or []) if str(item).strip()]
                structure_only = bool(getattr(node, "structure_only", False))
                fixed_output = bool(getattr(node, "fixed_output", False))
                task.brief.chapters.append(
                    ChapterRequirement(
                        key=node.key,
                        title=node.title,
                        sequence=node.sequence or "",
                        level=int(node.level or 1),
                        parent=node.parent or "",
                        describes=(node.describes or "").strip(),
                        points=[] if structure_only else points,
                        keywords=[] if structure_only or fixed_output else (keywords or list(points)),
                        content_blocks=[] if structure_only else _content_blocks_of(node),
                        reference_text=(getattr(node, "reference_text", None) or "").strip(),
                        generate="" if structure_only else "\n".join(points),
                        aux_prompt=(node.aux_prompt or "").strip(),
                        material_files=list(node.material_files or []),
                        knowledge_folder_ids=list(getattr(node, "knowledge_folder_ids", None) or []),
                        element_ids=list(node.element_ids or []),
                        structure_only=structure_only,
                        fixed_output=fixed_output,
                        search_terms_separate=bool(getattr(node, "search_terms_separate", False)),
                    )
                )
    _ensure_chapter_states(task)
    task.brief_confirmed = True


def _ensure_outline_template(task, task_dir: Path) -> None:
    """没有 template.json 时，按大纲写一份最小模版，供运行链路使用。"""
    if task.template_path and Path(task.template_path).is_file():
        return
    if task.outline is None:
        return
    material_type = normalize_biz_type(task.biz_type or "002")
    target = task_dir / "outline-template.json"
    payload = {
        "id": task.template_id or f"{material_type}-outline",
        "version": task.template_version or "1",
        "title": task.template_id or "outline",
        "biz_type": material_type,
        "chapters": [
            {
                "key": node.key,
                "sequence": node.sequence or "",
                "title": node.title,
                "level": int(node.level or 1),
                "parent": node.parent or "",
                "sample_paragraphs": [],
                "matched": False,
            }
            for node in task.outline.chapters
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    task.template_path = str(target)
    task.template_id = payload["id"]
    task.template_version = str(payload["version"])


def confirm_study(
    task_dir: Path,
    cache_dir: Path,
    voice: str,
    layout: str,
    paragraph_format: str,
    chapters: list[ChapterRequirement],
) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is not None and not task.outline_confirmed:
        raise RuntimeError("请先确认目录结构")
    if task.brief is None:
        raise RuntimeError("尚未分析模版")
    if voice.strip():
        task.brief.voice = voice.strip()
    if layout.strip():
        task.brief.layout = layout.strip()
    if paragraph_format.strip():
        task.brief.paragraph_format = paragraph_format.strip()
    supplied = {item.key: item for item in chapters}
    for requirement in task.brief.chapters:
        extra = supplied.get(requirement.key)
        if extra is None:
            continue
        if extra.title.strip():
            requirement.title = extra.title.strip()
        if extra.sequence.strip():
            requirement.sequence = extra.sequence.strip()
        if extra.level:
            requirement.level = extra.level
        if extra.parent.strip():
            requirement.parent = extra.parent.strip()
        if extra.extracts:
            requirement.extracts = list(extra.extracts)
        if extra.describes.strip():
            requirement.describes = extra.describes.strip()
        if extra.generate.strip():
            requirement.generate = extra.generate.strip()
            generated = [line.strip() for line in extra.generate.splitlines() if line.strip()]
            if generated:
                requirement.points = generated
        elif extra.points:
            requirement.points = [point.strip() for point in extra.points if point.strip()]
        if extra.keywords:
            requirement.keywords = [item.strip() for item in extra.keywords if item.strip()]
        try:
            task.chapter(requirement.key).title = requirement.title
        except KeyError:
            task.chapters.append(ChapterState(key=requirement.key, title=requirement.title))
    task.brief_confirmed = True
    BriefCache(cache_dir).save(task.brief)
    task.refresh_status()
    store.save(task)
    return task


def adopt_study(task_dir: Path, cache_dir: Path, biz_type: str, sample_id: str, sample_version: str) -> WritingTask:
    material_type = normalize_biz_type(biz_type)
    if not sample_id.startswith(f"{material_type}-"):
        raise RuntimeError("该分析结果不属于当前报告类型")
    library = open_library(cache_dir)
    record = library.load_study(sample_id, sample_version)
    if record is None:
        raise RuntimeError("没有找到已分析的模版")
    sample = library.load_sample(sample_id, sample_version)
    template_dir = task_dir.parent.parent / "uploads" / task_dir.name / "templates" / material_type
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / "template.json"
    template_path.write_text(
        json.dumps(_template_from_study(sample, record.brief), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    outline = outline_from_brief_chapters(record.brief.chapters)
    task = WritingTask(
        id=task_dir.name,
        template_id=record.brief.template_id,
        template_version=record.brief.template_version,
        template_path=str(template_path),
        biz_type=material_type,
        outline=outline,
        outline_confirmed=False,
        brief=record.brief,
        brief_from_cache=True,
        brief_confirmed=False,
        chapters=[ChapterState(key=item.key, title=item.title) for item in record.brief.chapters],
    )
    task.refresh_status()
    TaskStore(task_dir).save(task)
    return task


def _template_from_study(sample, brief) -> dict:
    saved = {item.key: item for item in (sample.chapters if sample else [])}
    chapters = []
    for item in brief.chapters:
        origin = saved.get(item.key)
        chapters.append({
            "key": item.key,
            "title": item.title,
            "sequence": item.sequence,
            "level": item.level,
            "parent": item.parent,
            "sample_paragraphs": list(origin.paragraphs) if origin else [],
            "points": list(item.points),
            "boundary": item.boundary,
            "depends_on": list(origin.depends_on) if origin else list(item.depends_on),
        })
    return {
        "id": brief.template_id,
        "version": brief.template_version,
        "title": sample.title if sample and sample.title else brief.template_id,
        "chapters": chapters,
    }


def _resolve_study_text(
    task_dir: Path,
    task: WritingTask,
    *,
    template_path: Path | None = None,
    source_text: str = "",
    persist: bool = False,
) -> str:
    """解析学习资料正文；persist=True 时把粘贴文本落到任务目录供下一阶段复用。"""
    from report_writing.controller.excerpt import _break_inline_headings, _plain_from_rich
    if template_path is not None:
        if not template_path.is_file():
            raise RuntimeError("报告模版不存在")
        text = _break_inline_headings(read_template_text(template_path))
        if persist:
            task.source_path = str(template_path)
        return text
    raw = (source_text or "").strip()
    if raw:
        text = _break_inline_headings(_plain_from_rich(raw))
        if persist:
            path = task_dir / _PENDING_STUDY_SOURCE
            path.write_text(text, encoding="utf-8")
            task.source_path = str(path)
        return text
    # 复用上一阶段已保存的资料
    saved = (task.source_path or "").strip()
    if saved:
        path = Path(saved)
        if path.is_file():
            if path.name == _PENDING_STUDY_SOURCE or path.suffix.lower() in {".txt", ".md"}:
                return _break_inline_headings(path.read_text(encoding="utf-8"))
            return _break_inline_headings(read_template_text(path))
    fallback = task_dir / _PENDING_STUDY_SOURCE
    if fallback.is_file():
        return _break_inline_headings(fallback.read_text(encoding="utf-8"))
    raise RuntimeError("请粘贴文档内容或上传文件")


def _merge_discovered_nodes(primary: list, extra: list) -> list:
    from report_writing.domain.models import OutlineNode
    from report_writing.domain.outline import _level_from_sequence, parent_sequence_of

    by_seq: dict[str, OutlineNode] = {}
    for item in list(primary or []) + list(extra or []):
        seq = (getattr(item, "sequence", None) or "").strip()
        title = (getattr(item, "title", None) or "").strip()
        if not seq or not title:
            continue
        prev = by_seq.get(seq)
        if prev is None or (len(title) < len(prev.title or "") and len(title) <= 40):
            by_seq[seq] = OutlineNode(
                sequence=seq,
                title=title[:40],
                level=_level_from_sequence(seq),
                parent_sequence=parent_sequence_of(seq),
            )
    return sorted(by_seq.values(), key=lambda n: [int(p) for p in n.sequence.split(".")])


def _discover_descendants_with_llm(
    text: str,
    parent_sequences: list[str],
    existing_sequences: set[str],
    *,
    parent_titles: dict[str, str] | None = None,
) -> list:
    from report_writing.domain.outline_refine import llm_discover_descendants

    return llm_discover_descendants(
        text,
        parent_sequences,
        existing_sequences,
        parent_titles=parent_titles,
    )


def extract_outline_from_document(
    task_dir: Path,
    chapter_keys: list[str],
    *,
    template_path: Path | None = None,
    source_text: str = "",
    on_event=None,
) -> WritingTask:
    """Tool 1：发现勾选父章下的新子孙目录（规则 + 大模型）。"""
    from report_writing.controller.excerpt import _break_inline_headings
    import uuid

    from report_writing.agents.study_document_tool import build_extract_outline_result
    from report_writing.domain.models import OutlineExtractDraft
    from report_writing.domain.outline import (
        discover_descendant_headings,
        resolve_parent_key,
        _level_from_sequence,
    )

    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is None or not task.outline.chapters:
        raise RuntimeError("请先配置目录大纲")
    keys = [key for key in chapter_keys if key]
    if not keys:
        raise RuntimeError("请先勾选要分析的章节")
    key_set = set(keys)
    selected = [item for item in task.outline.chapters if item.key in key_set]
    if not selected:
        raise RuntimeError("勾选的章节不在当前目录中")

    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "outline",
                "status": "running",
                "title": f"提取目录（相对 {len(selected)} 个勾选章节）",
            }
        )

    text = _resolve_study_text(
        task_dir,
        task,
        template_path=template_path,
        source_text=source_text,
        persist=True,
    )
    if not text.strip():
        raise RuntimeError("请粘贴文档内容或上传文件")

    from report_writing.domain.models import OutlineNode

    existing_sequences = {
        (item.sequence or "").strip() for item in task.outline.chapters if (item.sequence or "").strip()
    }
    parent_sequences = [(item.sequence or "").strip() for item in selected if (item.sequence or "").strip()]
    text = _break_inline_headings(text)
    discovered = discover_descendant_headings(text, parent_sequences, existing_sequences)
    # 规则可能漏检（标题粘正文等）；有模型时再抽一轮并合并
    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "outline",
                "status": "running",
                "title": "调用模型提取子章节目录",
                "result": {"regex_hits": len(discovered)},
            }
        )
    parent_titles = {
        (item.sequence or "").strip(): (item.title or "").strip()
        for item in selected
        if (item.sequence or "").strip()
    }
    llm_nodes = _discover_descendants_with_llm(
        text,
        parent_sequences,
        existing_sequences,
        parent_titles=parent_titles,
    )
    discovered = _merge_discovered_nodes(discovered, llm_nodes)

    provisional: list[OutlineNode] = []
    drafts: list[OutlineExtractDraft] = []
    for item in discovered:
        parent_key = resolve_parent_key(list(task.outline.chapters) + provisional, item.sequence)
        node = OutlineNode(
            key=f"ch-new-{uuid.uuid4().hex[:10]}",
            sequence=item.sequence or "",
            title=item.title,
            level=_level_from_sequence(item.sequence or ""),
            parent=parent_key,
            parent_sequence=item.parent_sequence or "",
        )
        provisional.append(node)
        drafts.append(
            OutlineExtractDraft(
                chapter_key=node.key,
                sequence=node.sequence,
                title=node.title,
                parent_key=node.parent,
                parent_sequence=node.parent_sequence,
                level=int(node.level or 1),
                accepted=True,
            )
        )

    tool_result = build_extract_outline_result(drafts, selected_chapter_keys=keys)
    task.pending_outline = drafts
    task.pending_outline_tool = tool_result
    task.pending_study = []
    task.pending_study_tool = None
    task.study_chapter_keys = list(keys)
    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "outline",
                "status": "done",
                "title": "目录提取完成" + ("，等待确认新章节" if drafts else "，无新增"),
                "result": {
                    "new_chapters": tool_result.new_chapter_count,
                    "outline_mismatch": tool_result.outline_mismatch,
                    "tool": tool_result.to_dict(),
                    "message": tool_result.message,
                    "selected_chapter_keys": keys,
                },
            }
        )
    store.save(task)
    return task


def confirm_outline_extract(
    task_dir: Path,
    drafts: list | None = None,
    *,
    apply_outline: bool = True,
    continue_study: bool = True,
    on_event=None,
) -> WritingTask:
    """确认 extract 结果：写入新目录后，默认按最新目录继续学写作要点。"""
    from report_writing.domain.models import OutlineExtractDraft, OutlineNode
    from report_writing.domain.outline import (
        _level_from_sequence,
        insert_outline_nodes,
        is_descendant_sequence,
        resolve_parent_key,
    )

    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is None:
        raise RuntimeError("请先配置目录大纲")
    pending = drafts if drafts is not None else task.pending_outline
    selected_keys = list(task.study_chapter_keys or [])
    if not selected_keys and task.pending_outline_tool:
        selected_keys = list(task.pending_outline_tool.selected_chapter_keys or [])

    newcomers: list[OutlineNode] = []
    if apply_outline and pending:
        chapters = list(task.outline.chapters)
        for raw in pending:
            draft = raw if isinstance(raw, OutlineExtractDraft) else OutlineExtractDraft.model_validate(raw)
            if draft.accepted is False:
                continue
            parent_key = draft.parent_key or resolve_parent_key(chapters + newcomers, draft.sequence)
            newcomers.append(
                OutlineNode(
                    key=draft.chapter_key or f"ch-new-{len(newcomers) + 1}",
                    sequence=(draft.sequence or "").strip(),
                    title=(draft.title or "").strip() or (draft.sequence or "新章节"),
                    level=int(draft.level or _level_from_sequence(draft.sequence or "") or 1),
                    parent=parent_key,
                    parent_sequence=(draft.parent_sequence or "").strip(),
                    points=[],
                    describes="",
                )
            )
        if newcomers:
            chapters = insert_outline_nodes(chapters, newcomers)
            # 插章后仍保已有父子，避免确认提取时整树重挂
            task.outline = finalize_outline(chapters, preserve_parents=True)
            if task.brief is not None:
                _lock_brief_to_outline(task.brief, task.outline)

    # 按「最新目录」收集：原勾选 + 其下已存在的全部子孙（含刚写入的）
    chapters_now = list(task.outline.chapters)
    by_key = {item.key: item for item in chapters_now}
    parent_seqs = [
        (by_key[key].sequence or "").strip()
        for key in selected_keys
        if key in by_key and (by_key[key].sequence or "").strip()
    ]
    study_keys: list[str] = []
    for item in chapters_now:
        if item.key in selected_keys:
            study_keys.append(item.key)
            continue
        seq = (item.sequence or "").strip()
        if seq and any(is_descendant_sequence(seq, parent) for parent in parent_seqs):
            study_keys.append(item.key)
    # 保底：勾选键仍在；无勾选时（如对话向导从样本生成整本目录）学习全部章节
    study_keys = list(dict.fromkeys(study_keys or selected_keys))
    if not study_keys and chapters_now:
        study_keys = [item.key for item in chapters_now if getattr(item, "key", None)]
    # 确认后接着学：跳过目录里已有要点的章，避免重进对话把整本再学一遍
    if continue_study and study_keys:
        by_now = {item.key: item for item in chapters_now}
        study_keys = [
            key
            for key in study_keys
            if not list(getattr(by_now.get(key), "points", None) or [])
            and not bool(getattr(by_now.get(key), "structure_only", False))
        ]

    task.study_chapter_keys = study_keys
    task.pending_outline = []
    task.pending_outline_tool = None
    # 与蓝本页「确认目录」一致：对话向导确认后须落库，否则蓝本配置仍提示未确认
    task.outline = finalize_outline(list(task.outline.chapters), preserve_parents=True)
    task.outline_confirmed = True
    task.refresh_status()
    store.save(task)

    if not continue_study:
        return task
    if not study_keys:
        return task
    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "study",
                "status": "running",
                "title": f"目录已确认，按最新目录分析要点 {len(study_keys)} 章",
            }
        )
    return auto_study_selected_chapters(
        task_dir,
        study_keys,
        on_event=on_event,
    )


def auto_study_selected_chapters(
    task_dir: Path,
    chapter_keys: list[str],
    *,
    template_path: Path | None = None,
    source_text: str = "",
    on_event=None,
) -> WritingTask:
    """Tool 2：仅对已有章节提炼写作要点（不再发现/插入新目录）。"""
    from report_writing.controller.excerpt import _best_chapter_excerpt, _break_inline_headings, _excerpt_foreign_sequence
    from report_writing.controller.points import _keywords_from_title_points, parse_points_and_keywords
    from report_writing.agents.study_document_tool import build_study_points_result
    from report_writing.domain.chapter_locator import CHAPTER_EXCERPT_MAX_CHARS
    from report_writing.domain.models import AutoStudyDraft, OutlineTree
    from report_writing.domain.outline import chapters_from_outline, _level_from_sequence

    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is None or not task.outline.chapters:
        raise RuntimeError("请先配置目录大纲")
    keys = [key for key in chapter_keys if key] or list(task.study_chapter_keys or [])
    if not keys:
        raise RuntimeError("请先勾选要分析的章节")
    key_set = set(keys)
    selected = [item for item in task.outline.chapters if item.key in key_set]
    if not selected:
        raise RuntimeError("勾选的章节不在当前目录中")

    text = _resolve_study_text(
        task_dir,
        task,
        template_path=template_path,
        source_text=source_text,
        persist=bool(template_path is not None or (source_text or "").strip()),
    )
    if not text.strip():
        raise RuntimeError("请粘贴文档内容或上传文件")

    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "study",
                "status": "running",
                "title": f"依据文档分析要点 {len(selected)} 章",
            }
        )

    # 先按换行后的目录切章；粘贴被压成单行时再用序号切片兜底，避免 matched=0 跳过模型
    text = _break_inline_headings(text)
    matched = chapters_from_outline(OutlineTree(chapters=list(task.outline.chapters)), text)
    by_key = {item["key"]: item for item in matched}
    drafts: list[AutoStudyDraft] = []

    from report_writing.domain.chapter_locator import build_heading_index

    heading_index = build_heading_index(text)
    total = len(selected)
    for index, node in enumerate(selected, start=1):
        label = f"{(node.sequence or '').strip()} {node.title}".strip()
        chunk = by_key.get(node.key) or {}
        # 多章连学时不再偏袒「仅当 matched」；与单章一样择优切段，避免切空/切错导致效果明显变差
        span_paras = list(chunk.get("sample_paragraphs") or []) if chunk.get("matched") else None
        # 行切结果若已串到其它序号，丢弃，避免污染择优
        if span_paras:
            span_head = "\n".join(span_paras[:3])
            if _excerpt_foreign_sequence(span_head, node.sequence or ""):
                span_paras = None
        excerpt, matched_ok = _best_chapter_excerpt(
            text,
            sequence=node.sequence or "",
            title=node.title or "",
            span_paragraphs=span_paras,
            # 有明确序号时禁止回退全文，否则模型易从同名其它章摘参考正文
            allow_full_fallback=len(selected) == 1 and not (node.sequence or "").strip(),
            heading_index=heading_index,
        )
        points: list[str] = []
        keywords: list[str] = []
        describes = ""
        content_blocks: list = []
        # 大纲上的 structure_only 以文档副本为准：用户取消「仅目录」后禁止启发式/模型再盖回
        user_structure_only = bool(getattr(node, "structure_only", False))
        structure_only = user_structure_only
        if on_event is not None:
            on_event(
                {
                    "type": "step",
                    "step": "study",
                    "status": "running",
                    "chapter": node.key,
                    "title": label,
                    "result": {
                        "note": (
                            "已标为目录节点，跳过写作要点"
                            if user_structure_only
                            else (
                                f"标题索引定位正文 {len(excerpt)} 字，分析要点"
                                if matched_ok
                                else "未匹配到正文，跳过模型"
                            )
                        ),
                        "index": index,
                        "total": total,
                        "sequence": node.sequence or "",
                        "matched": matched_ok,
                        "excerpt_chars": len(excerpt),
                    },
                }
            )
        if user_structure_only:
            describes = (
                (node.describes or "").strip()
                or f"目录节点「{node.title}」，正文由子节展开，本章不单独撰写。"
            )
        elif matched_ok and excerpt.strip():
            from report_writing.domain.models import ContentBlock
            from report_writing.domain.content_blocks import normalize_content_blocks

            located_excerpt = excerpt
            # 大纲未标仅目录：始终生成要点；启发式/模型不得把 structure_only 改回 True
            # （用户在文档副本取消「仅目录」后，分析不得再盖成目录节点）
            points, keywords, describes, raw_blocks, _reference_text, _parsed_so = parse_points_and_keywords(
                excerpt, title=node.title, sequence=node.sequence or ""
            )
            structure_only = False
            content_blocks = [
                ContentBlock.model_validate(item) for item in normalize_content_blocks(raw_blocks)
            ]
            if not keywords:
                keywords = _keywords_from_title_points(node.title, points)
            # 可写章分析完成至少落 1 条要点，避免 UI 仍显示「待分析」
            if not points:
                tip = (node.title or "").strip() or label
                points = [f"围绕「{tip}」交代清楚相关内容、依据与本章要点"]
            # 参考正文只认定位切段；模型常把同名章（如 7.4）写进 reference_text，一律不采信
            excerpt = located_excerpt[:CHAPTER_EXCERPT_MAX_CHARS]
        elif not user_structure_only and not (matched_ok and excerpt.strip()):
            # 未匹配正文且用户要写本章：不改 structure_only，留给人工补要点
            structure_only = False
            if not points:
                tip = (node.title or "").strip() or label
                points = [f"围绕「{tip}」补充本章应写清的内容与依据"]
            if not keywords:
                keywords = _keywords_from_title_points(node.title, points)
        if on_event is not None:
            on_event(
                {
                    "type": "step",
                    "step": "study",
                    "status": "done",
                    "chapter": node.key,
                    "title": label,
                    "result": {
                        "note": (
                            "目录节点，跳过写作要点"
                            if structure_only
                            else (
                                f"已生成简述、{len(points)} 条要点、{len(keywords)} 个检索词"
                                if points or keywords or describes
                                else "无要点"
                            )
                        ),
                        "index": index,
                        "total": total,
                        "sequence": node.sequence or "",
                        "matched": matched_ok,
                        "point_count": len(points),
                        "keyword_count": len(keywords),
                        "block_count": len(content_blocks),
                        "structure_only": structure_only,
                        "points": list(points),
                        "keywords": list(keywords),
                        "describes": describes or "",
                    },
                }
            )
        draft = AutoStudyDraft(
            chapter_key=node.key,
            sequence=node.sequence or "",
            title=node.title,
            points=points,
            keywords=keywords,
            describes=describes,
            content_blocks=content_blocks,
            matched=matched_ok,
            # 落盘完整定位节选（与送模上限一致）；勿再砍到 1200，否则建设规模等表体丢失
            sample_excerpt=excerpt[:CHAPTER_EXCERPT_MAX_CHARS],
            is_new=False,
            parent_key=node.parent or "",
            level=int(node.level or _level_from_sequence(node.sequence or "")),
            accepted=True,
            structure_only=structure_only,
        )
        drafts.append(draft)
        # 范围学习：学完一章立刻落盘，文档窗可「运行一章定位一章」看到已分析/要点
        _apply_study_drafts_to_outline(task, [draft])
        task.pending_study = list(drafts)
        task.study_chapter_keys = [item.chapter_key for item in drafts]
        store.save(task)
        try:
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(task.id)
            source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
            if source_bp:
                invalidate_doc_outline_cache(source_bp)
        except Exception:
            pass
        if on_event is not None:
            from report_writing.chat.gates import is_chapter_analyzed

            analyzed_n = sum(
                1
                for item in (task.outline.chapters if task.outline else [])
                if bool(getattr(item, "structure_only", False)) or is_chapter_analyzed(item)
            )
            total_n = len(list((task.outline.chapters if task.outline else []) or []))
            outline_node = next(
                (
                    c
                    for c in (task.outline.chapters if task.outline else [])
                    if str(getattr(c, "key", "") or "") == str(node.key or "")
                ),
                None,
            )
            progress_items = list(getattr(outline_node, "point_items", None) or [])
            on_event(
                {
                    "type": "study_chapter_progress",
                    "chapter": node.key,
                    "title": label,
                    "index": index,
                    "total": total,
                    "task_id": str(getattr(task, "id", "") or ""),
                    "points": list(getattr(outline_node, "points", None) or points),
                    "point_items": progress_items,
                    "keywords": list(keywords),
                    "describes": describes or "",
                    "structure_only": structure_only,
                    "study_analyzed": analyzed_n,
                    "study_unanalyzed": max(0, total_n - analyzed_n),
                }
            )

    tool_result = build_study_points_result(drafts)
    task.pending_outline = []
    task.pending_outline_tool = None
    # 收尾再全量对齐一次（幂等），避免漏章
    _apply_study_drafts_to_outline(task, drafts)
    # 蓝本页仍可展示草案；对话向导以 outline.points 为已学真值
    task.pending_study = drafts
    task.pending_study_tool = tool_result
    task.study_chapter_keys = [item.chapter_key for item in drafts]
    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "study",
                "status": "done",
                "title": "写作要点分析完成，等待确认",
                "result": {
                    "chapters": len(drafts),
                    "matched": sum(1 for item in drafts if item.matched),
                    "tool": tool_result.to_dict(),
                    "message": tool_result.message,
                },
            }
        )
    store.save(task)
    return task


def _apply_study_drafts_to_outline(task, drafts: list) -> set[str]:
    """把学习草案落到 outline 节点（要点/检索词等），供文档窗与向导进度共用。"""
    from report_writing.controller.excerpt import _excerpt_foreign_sequence
    from report_writing.controller.points import _keywords_from_title_points
    from report_writing.domain.chapter_locator import CHAPTER_EXCERPT_MAX_CHARS
    from report_writing.domain.models import AutoStudyDraft, ContentBlock
    from report_writing.domain.point_items_sync import apply_points_to_outline_node

    if task.outline is None:
        return set()
    by_key = {item.key: item for item in task.outline.chapters}
    confirmed_keys: set[str] = set()
    for raw in drafts or []:
        draft = raw if isinstance(raw, AutoStudyDraft) else AutoStudyDraft.model_validate(raw)
        node = by_key.get(draft.chapter_key)
        if node is None:
            continue
        confirmed_keys.add(draft.chapter_key)
        # 文档副本已是可写章（structure_only=False）时，草案不得再盖回仅目录
        draft_so = bool(getattr(draft, "structure_only", False))
        if bool(getattr(node, "structure_only", False)):
            structure_only = draft_so
        else:
            structure_only = False
        node.structure_only = structure_only
        if structure_only:
            node.points = []
            node.point_items = []
            node.keywords = []
            node.content_blocks = []
        else:
            if draft.points:
                raw_items = getattr(draft, "point_items", None)
                apply_points_to_outline_node(
                    node,
                    list(draft.points),
                    raw_point_items=raw_items if isinstance(raw_items, list) else None,
                )
            draft_keywords = [
                item.strip() for item in (getattr(draft, "keywords", None) or []) if str(item).strip()
            ]
            if draft_keywords:
                node.keywords = draft_keywords
            elif not (node.keywords or []):
                node.keywords = _keywords_from_title_points(
                    node.title, list(node.points or draft.points or [])
                )
            blocks = list(getattr(draft, "content_blocks", None) or [])
            if blocks:
                node.content_blocks = [
                    item if isinstance(item, ContentBlock) else ContentBlock.model_validate(item)
                    for item in blocks
                ]
        if (draft.describes or "").strip():
            node.describes = draft.describes.strip()
        excerpt = (draft.sample_excerpt or "").strip()
        if excerpt:
            existing = (getattr(node, "reference_text", None) or "").strip()
            if (not existing) or _excerpt_foreign_sequence(existing, node.sequence or ""):
                node.reference_text = excerpt[:CHAPTER_EXCERPT_MAX_CHARS]
    # 学要点只写内容，冻结已确认目录的父子/ key
    task.outline = finalize_outline(list(task.outline.chapters), preserve_parents=True)
    if task.outline.chapters:
        task.outline_confirmed = True
    return confirmed_keys


def _push_brief_points_to_outline(task) -> None:
    """from-outline 学习写在 brief 上：同步回目录节点，避免文档窗仍显示待学习。"""
    from report_writing.domain.point_items_sync import apply_points_to_outline_node

    if task is None or task.outline is None or task.brief is None:
        return
    by_key = {item.key: item for item in task.outline.chapters}
    changed = False
    for chapter in list(getattr(task.brief, "chapters", None) or []):
        node = by_key.get(getattr(chapter, "key", None))
        if node is None:
            continue
        if bool(getattr(node, "structure_only", False) or getattr(chapter, "structure_only", False)):
            continue
        points = [str(p).strip() for p in (getattr(chapter, "points", None) or []) if str(p).strip()]
        if not points:
            continue
        if list(node.points or []) != points:
            apply_points_to_outline_node(node, points)
            changed = True
        elif not list(getattr(node, "point_items", None) or []):
            apply_points_to_outline_node(node, points)
            changed = True
        keywords = [
            str(k).strip() for k in (getattr(chapter, "keywords", None) or []) if str(k).strip()
        ]
        if keywords and list(node.keywords or []) != keywords:
            node.keywords = keywords
            changed = True
        describes = (getattr(chapter, "describes", None) or "").strip()
        if describes and (getattr(node, "describes", "") or "").strip() != describes:
            node.describes = describes
            changed = True
        ref = (getattr(chapter, "reference_text", None) or "").strip()
        if ref and not (getattr(node, "reference_text", None) or "").strip():
            node.reference_text = ref[:8000]
            changed = True
    if changed:
        task.outline = finalize_outline(list(task.outline.chapters), preserve_parents=True)
        task.outline_confirmed = True


def confirm_auto_study(task_dir: Path, drafts: list | None = None) -> WritingTask:
    """确认学习草案并写入对应节点；可只确认部分章节，其余仍留在 pending_study。"""
    from report_writing.domain.models import AutoStudyDraft

    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is None:
        raise RuntimeError("请先配置目录大纲")
    to_confirm = drafts if drafts is not None else list(task.pending_study or [])
    if not to_confirm:
        raise RuntimeError("没有待确认的分析结果")
    confirmed_keys = _apply_study_drafts_to_outline(task, to_confirm)
    remaining = []
    for raw in task.pending_study or []:
        draft = raw if isinstance(raw, AutoStudyDraft) else AutoStudyDraft.model_validate(raw)
        if draft.chapter_key not in confirmed_keys:
            remaining.append(draft)
    task.pending_study = remaining
    if not remaining:
        task.pending_study_tool = None
        task.study_chapter_keys = []
    else:
        task.study_chapter_keys = [item.chapter_key for item in remaining]
    _ensure_brief_ready(task)
    task.refresh_status()
    store.save(task)
    return task


