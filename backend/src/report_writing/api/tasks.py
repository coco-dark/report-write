"""Task / study / write HTTP routes."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import threading
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from report_writing.api.deps import (
    _FILE_NAME,
    _PROJECT,
    _log,
    _log_event,
    _safe_name,
    _unique_path,
    load_task,
    resolve_path,
    sse_response,
    task_dir,
    task_root,
)
from report_writing.controller import (
    _best_chapter_excerpt,
    _excerpt_foreign_sequence,
    adopt_study,
    analyze_template,
    attach_source,
    auto_study_selected_chapters,
    clear_blueprint_sample,
    confirm_auto_study,
    confirm_outline,
    confirm_outline_extract,
    confirm_study,
    ensure_task,
    extract_outline_from_document,
    generate_outline,
    generate_outline_from_sample,
    import_outline,
    maybe_restore_outline_from_source_blueprint,
    outline_as_text,
    outline_from_pasted_text,
    parse_points_and_keywords,
    parse_writing_points,
    remember_dataset,
    resolve_sample_text,
    revoke_outline,
    save_blueprint_sample,
    save_element_nodes,
    save_outline,
    save_spec_meta,
    study_from_outline,
    write_from_study,
)
from report_writing.domain.models import (
    AutoStudyConfirmRequest,
    AutoStudyTextRequest,
    ConfirmChapterRequest,
    ConfirmOutlineExtractRequest,
    ConfirmStudyRequest,
    ConfirmedExport,
    CreateTaskRequest,
    OutlineTextRequest,
    MetaFromPointsRequest,
    ElementDraftFromPointRequest,
    ParsePointsRequest,
    RunChaptersRequest,
    SampleDocument,
    SaveElementsRequest,
    SaveOutlineRequest,
    SpecMetaRequest,
    StudiedTemplate,
    StudyRecord,
    UpdatePointsRequest,
    UseStudyRequest,
    WritingTask,
)
from report_writing.services.corpus import normalize_biz_type
from report_writing.services.documents import MATERIAL_SUFFIXES, read_material
from report_writing.services.pipeline import confirm_chapter, export_confirmed, rerun_chapter, run_chapters, run_task, update_points
from report_writing.services.store import TaskStore
from report_writing.infra.postgres import open_library

router = APIRouter(tags=["tasks"])

@router.post("/api/writing/tasks", response_model=WritingTask)
def create_task(body: CreateTaskRequest) -> WritingTask:
    template = resolve_path(body.template_path)
    dataset = resolve_path(body.dataset_dir)
    if not template.is_file():
        raise HTTPException(status_code=400, detail="模板文件不存在")
    if not dataset.is_dir():
        raise HTTPException(status_code=400, detail="数据集目录不存在")
    try:
        material_type = normalize_biz_type(body.biz_type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return run_task(
        template,
        dataset,
        task_dir(body.task_id),
        task_root() / "cache",
        material_type,
        body.force_study,
    )



@router.post("/api/writing/uploads")
async def upload_materials(
    task_id: str = Form(...),
    biz_type: str = Form(...),
    files: list[UploadFile] = File(...),
    template: UploadFile | None = File(None),
    template_biz_type: str = Form(""),
) -> dict:
    task_dir(task_id)
    try:
        material_type = normalize_biz_type(biz_type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not files:
        raise HTTPException(status_code=400, detail="请上传至少一个资料文件")
    dataset = task_root() / "uploads" / task_id / "materials"
    typed_dir = dataset / material_type
    if typed_dir.exists():
        shutil.rmtree(typed_dir)
    saved: list[str] = []
    for upload in files:
        name = _safe_name(upload.filename or "")
        suffix = Path(name).suffix.lower()
        if suffix == ".doc":
            raise HTTPException(status_code=400, detail="旧版 .doc 请另存为 .docx 后再上传")
        if suffix not in MATERIAL_SUFFIXES:
            raise HTTPException(status_code=400, detail="资料请上传 Word、PDF、Excel、图片或文本")
        target = _unique_path(typed_dir / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await upload.read())
        try:
            read_material(target)
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        saved.append(str(target.relative_to(dataset)))
    remember_dataset(task_dir(task_id), dataset)
    _log.info("资料已上传 任务 %s 类型 %s 文件 %s", task_id, material_type, "、".join(saved))
    if not (task_dir(task_id) / "task.json").exists():
        _log.warning("任务 %s 还没有学习结果。上传资料不会创建任务，需要先分析模版。", task_id)
    template_path = ""
    template_source = ""
    bound_type = ""
    if template is not None and template.filename:
        try:
            bound_type = normalize_biz_type(template_biz_type or material_type)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if bound_type != material_type:
            raise HTTPException(status_code=400, detail="资料类型与模版类型需要一致")
        raw = await template.read()
        template_path, template_source = _store_template(task_id, bound_type, raw, template.filename or "")
    return {
        "template_path": str(template_path),
        "template_source": str(template_source),
        "template_biz_type": bound_type,
        "dataset_dir": str(dataset),
        "biz_type": material_type,
        "files": saved,
    }



@router.post("/api/writing/tasks/stream")
def stream_task(body: CreateTaskRequest) -> StreamingResponse:
    template = resolve_path(body.template_path)
    dataset = resolve_path(body.dataset_dir)
    if not template.is_file():
        raise HTTPException(status_code=400, detail="模板文件不存在")
    if not dataset.is_dir():
        raise HTTPException(status_code=400, detail="数据集目录不存在")
    try:
        material_type = normalize_biz_type(body.biz_type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    def worker(emit) -> None:
        task = run_task(
            template,
            dataset,
            task_dir(body.task_id),
            task_root() / "cache",
            material_type,
            body.force_study,
            emit,
        )
        emit({"type": "task", "task": task.to_dict()})

    return sse_response(worker)



@router.post("/api/writing/tasks/analyze/stream")
async def analyze_stream(
    task_id: str = Form(...),
    biz_type: str = Form(...),
    template: UploadFile = File(...),
    force_study: str = Form("false"),
) -> StreamingResponse:
    """兼容旧客户端：上传后自动确认目录并学习章节。"""
    task_dir(task_id)
    try:
        material_type = normalize_biz_type(biz_type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    raw = await template.read()
    template_path, source = _store_template(task_id, material_type, raw, template.filename or "")
    learn_again = force_study.strip().lower() in {"1", "true", "on", "yes"}

    def worker(emit) -> None:
        # 旧接口仍走 template.json；有原文时优先按目录先行再学习
        path = source if source.suffix.lower() in {".pdf", ".doc", ".docx"} else template_path
        task = analyze_template(
            path,
            task_dir(task_id),
            task_root() / "cache",
            material_type,
            learn_again,
            emit,
        )
        emit({"type": "task", "task": task.to_dict()})

    return sse_response(worker)



@router.post("/api/writing/tasks/outline/generate/stream")
async def outline_generate_stream(
    task_id: str = Form(...),
    biz_type: str = Form("002"),
    template: UploadFile | None = File(None),
    use_llm: bool = Form(False),
    use_sample: bool = Form(False),
) -> StreamingResponse:
    """上传 Word/PDF 或使用蓝本样本，RAG 解析后自动抽取目录大纲。"""
    task_dir(task_id)
    try:
        material_type = normalize_biz_type(biz_type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    use_sample_flag = bool(use_sample)
    raw = b""
    source = None
    version = ""
    if not use_sample_flag:
        if template is None or not template.filename:
            raise HTTPException(status_code=400, detail="请上传文件，或选择依据蓝本样本")
        raw = await template.read()
        if not raw:
            raise HTTPException(status_code=400, detail="上传文件为空")
        _template_path, source = _store_template(task_id, material_type, raw, template.filename or "")
        version = hashlib.sha256(raw).hexdigest()[:16]

    def worker(emit) -> None:
        try:
            if use_sample_flag:
                task = generate_outline_from_sample(
                    task_dir(task_id),
                    use_llm=bool(use_llm),
                    on_event=emit,
                )
            else:
                task = generate_outline(
                    source,
                    task_dir(task_id),
                    material_type,
                    version=version,
                    use_llm=bool(use_llm),
                    on_event=emit,
                )
            emit({"type": "task", "task": task.to_dict()})
        except Exception as error:
            _log.exception("生成目录失败")
            emit({"type": "error", "message": str(error)})

    return sse_response(worker)



@router.post("/api/writing/tasks/{task_id}/sample", response_model=WritingTask)
async def post_blueprint_sample(task_id: str, file: UploadFile = File(...)) -> WritingTask:
    """蓝本上传样本并 RAG 解析缓存。"""
    load_task(task_id)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise HTTPException(status_code=400, detail="样本请上传 PDF 或 Word")
    directory = task_root() / "uploads" / task_id / "sample"
    directory.mkdir(parents=True, exist_ok=True)
    # 清空旧样本目录内容
    for old in directory.iterdir():
        try:
            if old.is_file():
                old.unlink()
        except OSError:
            pass
    target = directory / _safe_name(file.filename or f"sample{suffix}")
    target.write_bytes(raw)
    try:
        return save_blueprint_sample(task_dir(task_id), target, original_name=file.filename or target.name)
    except Exception as error:
        _log.exception("样本解析失败")
        raise HTTPException(status_code=500, detail=f"样本解析失败：{error}") from error



@router.delete("/api/writing/tasks/{task_id}/sample", response_model=WritingTask)
def delete_blueprint_sample(task_id: str) -> WritingTask:
    load_task(task_id)
    return clear_blueprint_sample(task_dir(task_id))



@router.put("/api/writing/tasks/{task_id}/outline", response_model=WritingTask)
def put_outline(task_id: str, body: SaveOutlineRequest) -> WritingTask:
    load_task(task_id)
    try:
        return save_outline(
            task_dir(task_id),
            body.chapters,
            replace_tree=bool(getattr(body, "replace_tree", False)),
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/outline/import", response_model=WritingTask)
async def post_outline_import(task_id: str, file: UploadFile = File(...)) -> WritingTask:
    task_dir(task_id)
    raw = await file.read()
    try:
        return import_outline(task_dir(task_id), raw)
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/outline/from-text", response_model=WritingTask)
def post_outline_from_text(task_id: str, body: OutlineTextRequest) -> WritingTask:
    task_dir(task_id)
    try:
        return outline_from_pasted_text(
            task_dir(task_id),
            body.text,
            use_llm=body.use_llm,
            biz_type=body.biz_type,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/outline/confirm", response_model=WritingTask)
def post_outline_confirm(task_id: str) -> WritingTask:
    load_task(task_id)
    try:
        return confirm_outline(task_dir(task_id))
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/outline/revoke", response_model=WritingTask)
def post_outline_revoke(task_id: str) -> WritingTask:
    load_task(task_id)
    try:
        return revoke_outline(task_dir(task_id))
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.get("/api/writing/tasks/{task_id}/outline/text")
def get_outline_text(task_id: str) -> dict:
    task = load_task(task_id)
    if task.outline is None:
        raise HTTPException(status_code=400, detail="还没有目录结构")
    return {"text": outline_as_text(task.outline)}



@router.post("/api/writing/tasks/{task_id}/outline/parse-points")
def post_parse_points(task_id: str, body: ParsePointsRequest) -> dict:
    load_task(task_id)
    try:
        source = body.text or ""
        # 粘贴多章全文时先按序号切章，避免模型从同名 7.4 摘参考正文
        if (body.sequence or "").strip() and len(source) > 1200:
            excerpt, matched = _best_chapter_excerpt(
                source,
                sequence=body.sequence or "",
                title=body.title or "",
                allow_full_fallback=False,
            )
            if matched and excerpt.strip():
                source = excerpt
        points, keywords, describes, content_blocks, reference_text, structure_only, _slots = parse_points_and_keywords(
            source, title=body.title, sequence=body.sequence
        )
        # 参考正文固定为切章结果（或原文），防止模型串到同名其它章
        if (body.sequence or "").strip():
            reference_text = source[:8000]
        elif _excerpt_foreign_sequence(reference_text or "", body.sequence or ""):
            reference_text = source[:8000]
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "points": points,
        "keywords": keywords,
        "describes": describes,
        "content_blocks": content_blocks,
        "reference_text": reference_text,
        "structure_only": structure_only,
    }


@router.post("/api/writing/tasks/{task_id}/outline/meta-from-points")
def post_meta_from_points(task_id: str, body: MetaFromPointsRequest) -> dict:
    """根据 point_items 生成 describes / aux_prompt（文档窗编辑要点后同步）。"""
    task = load_task(task_id)
    chapter_key = str(body.chapter_key or "").strip()
    title = str(body.title or "").strip()
    sequence = str(body.sequence or "").strip()
    parent_title = ""
    if task.outline and task.outline.chapters:
        by_key = {str(n.key or "").strip(): n for n in task.outline.chapters}
        node = by_key.get(chapter_key) if chapter_key else None
        if node is not None:
            if not title:
                title = str(node.title or "").strip()
            if not sequence:
                sequence = str(node.sequence or "").strip()
            pk = str(getattr(node, "parent", "") or "").strip()
            parent = by_key.get(pk)
            if parent is not None:
                parent_title = str(parent.title or "").strip()
    items = list(body.point_items or [])
    if not items and chapter_key and task.outline:
        node = next((n for n in task.outline.chapters if n.key == chapter_key), None)
        if node is not None:
            items = list(getattr(node, "point_items", None) or [])
    from report_writing.domain.chapter_meta_from_points import derive_chapter_meta_from_points

    category = str(getattr(task, "category", "") or "报告").strip() or "报告"
    describes, aux_prompt = derive_chapter_meta_from_points(
        title=title or chapter_key or "本章",
        sequence=sequence,
        category=category,
        parent_title=parent_title,
        point_items=items,
    )
    return {"describes": describes, "aux_prompt": aux_prompt}


@router.post("/api/writing/tasks/{task_id}/elements/draft-from-point")
def post_element_draft_from_point(task_id: str, body: ElementDraftFromPointRequest) -> dict:
    """写作要点「关联要素」：按待补项与要点文案生成要素草稿，用户确认后保存。"""
    task = load_task(task_id)
    chapter_key = str(body.chapter_key or "").strip()
    title = str(body.title or "").strip()
    sequence = str(body.sequence or "").strip()
    describes = str(body.describes or "").strip()
    if task.outline and task.outline.chapters and chapter_key:
        node = next((n for n in task.outline.chapters if n.key == chapter_key), None)
        if node is not None:
            if not title:
                title = str(node.title or "").strip()
            if not sequence:
                sequence = str(node.sequence or "").strip()
            if not describes:
                describes = str(getattr(node, "describes", "") or "").strip()
    from report_writing.domain.element_draft_from_point import derive_element_draft_from_point

    category = str(getattr(task, "category", "") or "报告").strip() or "报告"
    draft, from_llm = derive_element_draft_from_point(
        chapter_title=title or chapter_key or "本章",
        sequence=sequence,
        category=category,
        describes=describes,
        point_text=str(body.point_text or "").strip(),
        slot=str(body.slot or "").strip(),
        task_elements=list(task.element_nodes or []),
    )
    return {"draft": draft, "from_llm": from_llm}


@router.post("/api/writing/tasks/{task_id}/source", response_model=WritingTask)
async def post_source(
    task_id: str,
    biz_type: str = Form(""),
    source: UploadFile = File(...),
) -> WritingTask:
    """学习步上传原文报告，供按目录回文切章。"""
    task = load_task(task_id)
    try:
        material_type = normalize_biz_type(biz_type.strip() or task.biz_type or "002")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    raw = await source.read()
    _path, stored = _store_template(task_id, material_type, raw, source.filename or "source.docx")
    try:
        return attach_source(task_dir(task_id), stored, material_type)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/study/from-outline/stream")
def study_from_outline_stream(task_id: str, force_study: bool = False) -> StreamingResponse:
    load_task(task_id)

    def worker(emit) -> None:
        try:
            task = study_from_outline(
                task_dir(task_id),
                task_root() / "cache",
                force_study=force_study,
                on_event=emit,
            )
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        if task is None:
            emit({"type": "error", "detail": "学习未返回任务结果"})
            return
        emit({"type": "task", "task": task.to_dict()})

    return sse_response(worker)



@router.put("/api/writing/tasks/{task_id}/study", response_model=WritingTask)
def put_study(task_id: str, body: ConfirmStudyRequest) -> WritingTask:
    load_task(task_id)
    try:
        return confirm_study(
            task_dir(task_id),
            task_root() / "cache",
            body.voice,
            body.layout,
            body.paragraph_format,
            body.chapters,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/write/stream")
def stream_write(task_id: str) -> StreamingResponse:
    task = load_task(task_id)
    dataset = Path(task.dataset_dir) if task.dataset_dir else Path()

    def worker(emit) -> None:
        try:
            updated = write_from_study(task_dir(task_id), dataset, emit)
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)



@router.get("/api/writing/tasks/{task_id}", response_model=WritingTask)
def get_task(task_id: str) -> WritingTask:
    task = load_task(task_id)
    return maybe_restore_outline_from_source_blueprint(task_dir(task_id), task)


@router.get("/api/writing/tasks/{task_id}/draft-preview")
def get_draft_preview(
    task_id: str,
    written_only: bool = Query(True, description="仅包含已有正文的章节"),
    confirmed_only: bool = Query(False, description="仅已确认章节（默认含草稿）"),
) -> dict:
    """全文预览：按大纲顺序拼接本章写作正文为 HTML；按 task_id 隔离，不串其它文档。

    蓝本 id 时对齐 doc-outline：解析最近写作 clone，用 clone 正文（hydrate）生成预览。
    """
    from report_writing.services.render import (
        _hydrate_chapters_from_db,
        build_written_chapters_preview,
    )
    from report_writing.services.writing_task_catalog import (
        is_writing_task_id,
        latest_writing_task_id_for_blueprint,
        load_writing_task,
    )

    tid = (task_id or "").strip()
    preview_task = None
    if tid and not is_writing_task_id(tid):
        wid = latest_writing_task_id_for_blueprint(tid)
        if wid:
            preview_task = load_writing_task(wid)
            if preview_task is None:
                try:
                    preview_task = load_task(wid)
                except Exception:
                    preview_task = None
    if preview_task is None:
        preview_task = load_task(tid)
    preview_task = _hydrate_chapters_from_db(preview_task)
    # 蓝本大纲顺序 + clone 正文：若预览落在 clone 且大纲为子集，合并蓝本大纲结构
    if (
        is_writing_task_id(str(getattr(preview_task, "id", "") or ""))
        and tid
        and not is_writing_task_id(tid)
    ):
        try:
            blueprint = load_task(tid)
            if blueprint.outline and blueprint.outline.chapters:
                # 用蓝本大纲顺序展示；正文状态仍来自 clone chapters
                from report_writing.domain.models import OutlineTree
                import copy

                preview_task.outline = OutlineTree.from_dict(
                    copy.deepcopy(blueprint.outline.to_dict())
                )
                if not str(getattr(preview_task, "title", "") or "").strip():
                    preview_task.title = str(getattr(blueprint, "title", "") or "")
        except Exception:
            pass
    from report_writing.api.deps import data_home

    home = data_home()
    return build_written_chapters_preview(
        preview_task,
        written_only=bool(written_only),
        confirmed_only=bool(confirmed_only),
        home=home,
    )


@router.get("/api/writing/tasks/{task_id}/doc-outline")
def get_doc_outline(
    task_id: str,
    fresh: bool = Query(False, description="为 true 时跳过缓存，直读落库真值"),
) -> dict:
    """文档窗轻量目录：不含 steps/写作正文等大字段；命中内存/Redis 缓存则不再读整包 MySQL。"""
    from report_writing.services.doc_outline_cache import (
        get_doc_outline_cached,
        set_doc_outline_cached,
    )
    from report_writing.services.users import current_user_id, owns_user_resource

    if not fresh:
        cached = get_doc_outline_cached(task_id)
        if isinstance(cached, dict) and cached.get("id"):
            owner = str(cached.get("_owner_user_id") or "")
            if owns_user_resource(current_user_id(), owner):
                out = {k: v for k, v in cached.items() if not str(k).startswith("_")}
                return out

    task = load_task(task_id)
    task = maybe_restore_outline_from_source_blueprint(task_dir(task_id), task)
    from report_writing.chat.cards import _chapter_draft_text
    from report_writing.domain.models import ChapterStatus
    from report_writing.services.writing_task_catalog import (
        is_writing_task_id,
        latest_writing_task_id_for_blueprint,
        load_writing_task,
    )

    state_by_key = {
        str(getattr(item, "key", "") or "").strip(): item for item in (task.chapters or [])
    }
    writing = None
    if not is_writing_task_id(task_id):
        wid = latest_writing_task_id_for_blueprint(task_id)
        writing = load_writing_task(wid) if wid else None
        if writing is None and wid:
            try:
                writing = load_task(wid)
            except Exception:
                writing = None
        if writing is not None:
            for item in writing.chapters or []:
                key = str(getattr(item, "key", "") or "").strip()
                if not key:
                    continue
                # 写作任务状态优先覆盖蓝本空状态
                prev = state_by_key.get(key)
                if prev is None:
                    state_by_key[key] = item
                    continue
                prev_st = str(
                    getattr(getattr(prev, "status", None), "value", None)
                    or getattr(prev, "status", "")
                    or ""
                ).strip()
                next_st = str(
                    getattr(getattr(item, "status", None), "value", None)
                    or getattr(item, "status", "")
                    or ""
                ).strip()
                written = {
                    ChapterStatus.CONFIRMED.value,
                    ChapterStatus.AWAITING_CONFIRM.value,
                    ChapterStatus.NEEDS_HUMAN.value,
                }
                if next_st in written or (next_st and prev_st not in written):
                    state_by_key[key] = item
    elements_by_id: dict = {}
    for item in task.element_nodes or []:
        eid = str(getattr(item, "id", "") or "").strip()
        if eid:
            elements_by_id[eid] = item
    from report_writing.domain.point_items_sync import chapter_point_items_from_payload

    chapters: list[dict] = []
    for node in list((task.outline.chapters if task.outline else None) or []):
        state = state_by_key.get(str(node.key or "").strip())
        status = ""
        has_draft = False
        if state is not None:
            raw_status = getattr(state, "status", None)
            status = str(raw_status.value if hasattr(raw_status, "value") else raw_status or "").strip()
            try:
                has_draft = bool((_chapter_draft_text(state) or "").strip()) or bool(
                    (getattr(state, "official_text", "") or "").strip()
                )
            except Exception:
                has_draft = False
            if status in {
                ChapterStatus.CONFIRMED.value,
                ChapterStatus.AWAITING_CONFIRM.value,
                ChapterStatus.NEEDS_HUMAN.value,
            }:
                # 写完自动保存后 needs_human 也可能带正文；统一按已写作展示
                if has_draft or status != ChapterStatus.NEEDS_HUMAN.value:
                    has_draft = True
                if status in {
                    ChapterStatus.CONFIRMED.value,
                    ChapterStatus.AWAITING_CONFIRM.value,
                } or (status == ChapterStatus.NEEDS_HUMAN.value and has_draft):
                    status = ChapterStatus.CONFIRMED.value
        chapters.append(
            {
                "key": node.key,
                "sequence": node.sequence,
                "title": node.title,
                "level": getattr(node, "level", 1) or 1,
                "parent": getattr(node, "parent", "") or "",
                "parent_sequence": getattr(node, "parent_sequence", "") or "",
                "points": list(node.points or []),
                "point_items": chapter_point_items_from_payload(
                    {
                        "points": list(node.points or []),
                        "point_items": list(getattr(node, "point_items", None) or []),
                        "element_ids": list(getattr(node, "element_ids", None) or []),
                    },
                    elements_by_id=elements_by_id,
                ),
                "keywords": list(getattr(node, "keywords", None) or []),
                "describes": getattr(node, "describes", "") or "",
                "aux_prompt": getattr(node, "aux_prompt", "") or "",
                "reference_text": getattr(node, "reference_text", "") or "",
                "structure_only": bool(getattr(node, "structure_only", False)),
                "fixed_output": bool(getattr(node, "fixed_output", False)),
                "element_ids": list(getattr(node, "element_ids", None) or []),
                "material_files": list(getattr(node, "material_files", None) or []),
                "knowledge_folder_ids": list(getattr(node, "knowledge_folder_ids", None) or []),
                "status": status,
                "has_draft": bool(has_draft),
            }
        )
    elements = []
    for item in task.element_nodes or []:
        if hasattr(item, "model_dump"):
            elements.append(item.model_dump())
        elif hasattr(item, "dict"):
            elements.append(item.dict())
        elif isinstance(item, dict):
            elements.append(item)
        else:
            elements.append({"id": getattr(item, "id", ""), "title": getattr(item, "title", "")})
    # 项目资料根：写作 clone 优先（蓝本 id 请求时上方已加载 writing）
    project_src = writing if writing is not None else task
    project_folder_ids = list(getattr(project_src, "project_folder_ids", None) or [])
    project_folder_label = str(getattr(project_src, "project_folder_label", "") or "").strip()
    # 资料包只绑写作文档；纯蓝本不返回包 id
    pack_src = writing
    if pack_src is None and (
        str(getattr(task, "source_blueprint_id", "") or "").strip()
        or str(getattr(task, "id", "") or "").startswith("w")
    ):
        pack_src = task
    materials_pack_id = (
        str(getattr(pack_src, "materials_pack_id", "") or "").strip() if pack_src is not None else ""
    )
    payload = {
        "id": task.id,
        "outline_confirmed": bool(task.outline_confirmed),
        "sample_ready": bool(getattr(task, "sample_ready", False)),
        "sample_name": str(getattr(task, "sample_name", "") or ""),
        "chapter_count": len(chapters),
        "chapters": chapters,
        "element_nodes": elements,
        "project_folder_ids": project_folder_ids,
        "project_folder_label": project_folder_label,
        "materials_pack_id": materials_pack_id,
        "_owner_user_id": (getattr(task, "user_id", "") or "").strip() or current_user_id(),
    }
    set_doc_outline_cached(task_id, payload)
    return {k: v for k, v in payload.items() if not str(k).startswith("_")}


@router.get("/api/writing/tasks/{task_id}/chapter-picker")
def get_chapter_picker(task_id: str) -> dict:
    """对话勾选树：按蓝本/任务返回可写章节扁平列表（前端组树）。"""
    from report_writing.chat_orchestrator import (
        _chapter_picker_payload,
        _outline_chapter_picker_items,
        partition_blueprint_chapters,
    )
    from report_writing.domain.models import ChatSession

    task = load_task(task_id)
    parts = partition_blueprint_chapters(task)
    meta = _chapter_picker_payload(ChatSession(blueprint_id=task_id), task)
    return {
        **meta,
        "chapters": _outline_chapter_picker_items(task),
        "analyzed_count": len(parts["analyzed"]),
        "unanalyzed_count": len(parts["unanalyzed"]),
    }



@router.put("/api/writing/tasks/{task_id}/chapters/{chapter_key}/points", response_model=WritingTask)
def put_points(task_id: str, chapter_key: str, body: UpdatePointsRequest) -> WritingTask:
    load_task(task_id)
    try:
        return update_points(task_dir(task_id), task_root() / "cache", chapter_key, body.points, body.save_cache)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"章节不存在: {error}") from error



@router.put("/api/writing/tasks/{task_id}/elements", response_model=WritingTask)
def put_elements(task_id: str, body: SaveElementsRequest) -> WritingTask:
    load_task(task_id)
    return save_element_nodes(task_dir(task_id), body.elements)



@router.put("/api/writing/tasks/{task_id}/spec", response_model=WritingTask)
def put_spec_meta(task_id: str, body: SpecMetaRequest) -> WritingTask:
    ensure_task(task_dir(task_id))
    return save_spec_meta(
        task_dir(task_id),
        title=body.title,
        category=body.category,
        tags=body.tags,
        description=body.description,
    )


@router.put("/api/writing/tasks/{task_id}/project-folders", response_model=WritingTask)
def put_project_folders(task_id: str, body: dict) -> WritingTask:
    """绑定本写作任务的项目资料根；换项目只改这里，不改蓝本要素。"""
    from report_writing.domain.models import ProjectFoldersRequest
    from report_writing.services.store import TaskStore
    from report_writing.services.writing_task_catalog import save_writing_task

    req = ProjectFoldersRequest.model_validate(body or {})
    task = load_task(task_id)
    folders = [str(x).strip() for x in (req.project_folder_ids or []) if str(x).strip()]
    task.project_folder_ids = folders
    task.project_folder_label = str(req.project_folder_label or "").strip()
    directory = task_dir(task_id)
    ensure_task(directory)
    store = TaskStore(directory)
    store.save(task)
    try:
        save_writing_task(task)
    except Exception:
        pass
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(task_id)
        source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
        if source_bp:
            invalidate_doc_outline_cache(source_bp)
    except Exception:
        pass
    return task



def _parse_chapter_keys_form(chapter_keys: str) -> list[str]:
    try:
        keys = json.loads(chapter_keys or "[]")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="chapter_keys 须为 JSON 数组") from error
    if not isinstance(keys, list):
        raise HTTPException(status_code=400, detail="chapter_keys 须为 JSON 数组")
    return [str(item) for item in keys]



@router.post("/api/writing/tasks/{task_id}/study/extract-outline/stream")
async def stream_extract_outline(
    task_id: str,
    chapter_keys: str = Form("[]"),
    template: UploadFile | None = File(None),
    use_sample: bool = Form(False),
) -> StreamingResponse:
    task = load_task(task_id)
    selected = _parse_chapter_keys_form(chapter_keys)
    use_sample_flag = bool(use_sample)
    stored: Path | None = None
    if not use_sample_flag:
        if template is None or not template.filename:
            raise HTTPException(status_code=400, detail="请上传文件，或传 use_sample=true 使用蓝本样本")
        raw = await template.read()
        material_type = normalize_biz_type(task.biz_type or "002")
        _path, stored = _store_template(task_id, material_type, raw, template.filename or "study-template.docx")

    def worker(emit) -> None:
        try:
            # 未勾选章节：从样本生成整本目录（对话向导），不要求先「选章节学习」
            if use_sample_flag and not selected:
                updated = generate_outline_from_sample(
                    task_dir(task_id),
                    use_llm=True,
                    on_event=emit,
                )
            elif use_sample_flag:
                updated = extract_outline_from_document(
                    task_dir(task_id),
                    selected,
                    source_text=resolve_sample_text(task),
                    on_event=emit,
                )
            else:
                updated = extract_outline_from_document(
                    task_dir(task_id),
                    selected,
                    template_path=stored,
                    on_event=emit,
                )
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)



def _task_has_outline(task) -> bool:
    return bool(task.outline and (task.outline.chapters or []))


def _unset_outline_confirmed_for_regen(task_id: str) -> None:
    """整本从样本重提前解开确认，与向导 re_extract_outline 一致。"""
    from report_writing.services.store import TaskStore

    store = TaskStore(task_dir(task_id))
    if not store.path.is_dir():
        return
    task = store.load()
    if not getattr(task, "outline_confirmed", False):
        return
    task.outline_confirmed = False
    task.refresh_status()
    store.save(task)


@router.post("/api/writing/tasks/{task_id}/study/extract-outline/text/stream")
def stream_extract_outline_text(task_id: str, body: AutoStudyTextRequest) -> StreamingResponse:
    task = load_task(task_id)
    selected = [str(item) for item in (body.chapter_keys or []) if str(item).strip()]
    use_sample = bool(body.use_sample)
    re_extract = bool(body.re_extract)
    if re_extract and use_sample and not selected:
        _unset_outline_confirmed_for_regen(task_id)
        task = load_task(task_id)
    # 已确认目录时禁止静默整本 regenerate（须 re_extract 或向导先解开确认）
    block_full_regen = bool(
        use_sample
        and not selected
        and not re_extract
        and getattr(task, "outline_confirmed", False)
        and _task_has_outline(task)
    )

    def worker(emit) -> None:
        try:
            if block_full_regen:
                emit(
                    {
                        "type": "error",
                        "detail": "目录已确认。若要重提，请先点「重新提取目录」。",
                    }
                )
                return
            # 未勾选章节：从样本生成整本目录（对话向导），与蓝本页「依据样本生成大纲」一致
            if use_sample and not selected:
                updated = generate_outline_from_sample(
                    task_dir(task_id),
                    use_llm=bool(body.use_llm),
                    on_event=emit,
                )
                emit({"type": "task", "task": updated.to_dict()})
                return
            source = body.text or ""
            if use_sample:
                source = resolve_sample_text(task)
            updated = extract_outline_from_document(
                task_dir(task_id),
                selected,
                source_text=source,
                on_event=emit,
            )
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)



@router.post("/api/writing/tasks/{task_id}/study/confirm-outline", response_model=WritingTask)
def post_confirm_outline_extract(task_id: str, body: ConfirmOutlineExtractRequest) -> WritingTask:
    """同步确认目录；若 continue_study=true 会接着学要点（耗时可改用 stream）。"""
    load_task(task_id)
    try:
        return confirm_outline_extract(
            task_dir(task_id),
            body.drafts or None,
            apply_outline=body.apply_outline,
            continue_study=body.continue_study,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/study/confirm-outline/stream")
def stream_confirm_outline_extract(task_id: str, body: ConfirmOutlineExtractRequest) -> StreamingResponse:
    """确认目录后按最新目录流式学习要点。"""
    load_task(task_id)

    def worker(emit) -> None:
        try:
            updated = confirm_outline_extract(
                task_dir(task_id),
                body.drafts or None,
                apply_outline=body.apply_outline,
                continue_study=body.continue_study,
                on_event=emit,
            )
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)



@router.post("/api/writing/tasks/{task_id}/study/points/stream")
async def stream_study_points(
    task_id: str,
    chapter_keys: str = Form("[]"),
    template: UploadFile | None = File(None),
) -> StreamingResponse:
    load_task(task_id)
    selected = _parse_chapter_keys_form(chapter_keys)
    stored = None
    if template is not None and template.filename:
        raw = await template.read()
        if raw:
            material_type = normalize_biz_type(load_task(task_id).biz_type or "002")
            _path, stored = _store_template(
                task_id, material_type, raw, template.filename or "study-template.docx"
            )

    def worker(emit) -> None:
        try:
            updated = auto_study_selected_chapters(
                task_dir(task_id),
                selected,
                template_path=stored,
                on_event=emit,
            )
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)



@router.post("/api/writing/tasks/{task_id}/study/points/text/stream")
def stream_study_points_text(task_id: str, body: AutoStudyTextRequest) -> StreamingResponse:
    task = load_task(task_id)
    selected = [str(item) for item in (body.chapter_keys or []) if str(item).strip()]
    source = body.text or ""
    if body.use_sample:
        try:
            source = resolve_sample_text(task)
        except RuntimeError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    def worker(emit) -> None:
        try:
            updated = auto_study_selected_chapters(
                task_dir(task_id),
                selected,
                source_text=source,
                on_event=emit,
            )
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)


# 兼容旧路径：auto-study → 仅学要点；extract 请走 study/extract-outline

@router.post("/api/writing/tasks/{task_id}/auto-study", response_model=WritingTask)
async def post_auto_study(
    task_id: str,
    chapter_keys: str = Form("[]"),
    template: UploadFile = File(...),
) -> WritingTask:
    load_task(task_id)
    selected = _parse_chapter_keys_form(chapter_keys)
    raw = await template.read()
    material_type = normalize_biz_type(load_task(task_id).biz_type or "002")
    _path, stored = _store_template(task_id, material_type, raw, template.filename or "study-template.docx")
    try:
        return auto_study_selected_chapters(
            task_dir(task_id),
            selected,
            template_path=stored,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/auto-study/stream")
async def stream_auto_study(
    task_id: str,
    chapter_keys: str = Form("[]"),
    template: UploadFile = File(...),
) -> StreamingResponse:
    return await stream_study_points(task_id, chapter_keys=chapter_keys, template=template)



@router.post("/api/writing/tasks/{task_id}/auto-study/text/stream")
def stream_auto_study_text(task_id: str, body: AutoStudyTextRequest) -> StreamingResponse:
    return stream_study_points_text(task_id, body)



@router.post("/api/writing/tasks/{task_id}/auto-study/confirm", response_model=WritingTask)
def post_auto_study_confirm(task_id: str, body: AutoStudyConfirmRequest) -> WritingTask:
    load_task(task_id)
    try:
        return confirm_auto_study(task_dir(task_id), body.drafts or None)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/chapters/run", response_model=WritingTask)
def post_run_chapters(task_id: str, body: RunChaptersRequest) -> WritingTask:
    load_task(task_id)
    try:
        return run_chapters(
            task_dir(task_id),
            chapter_key=body.chapter_key,
            include_children=body.include_children,
            chapter_keys=body.chapter_keys or None,
            robot_id=body.robot_id or "",
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.post("/api/writing/tasks/{task_id}/chapters/run/stream")
def stream_run_chapters(task_id: str, body: RunChaptersRequest) -> StreamingResponse:
    load_task(task_id)

    def worker(emit) -> None:
        try:
            updated = run_chapters(
                task_dir(task_id),
                chapter_key=body.chapter_key,
                include_children=body.include_children,
                chapter_keys=body.chapter_keys or None,
                robot_id=body.robot_id or "",
                on_event=emit,
            )
        except RuntimeError as error:
            emit({"type": "error", "detail": str(error)})
            return
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)



@router.post("/api/writing/tasks/{task_id}/rerun", response_model=WritingTask)
def post_rerun(task_id: str) -> WritingTask:
    task = load_task(task_id)
    dataset = Path(task.dataset_dir) if task.dataset_dir else None
    return rerun_chapter(task_dir(task_id), Path(task.template_path), dataset)



@router.post("/api/writing/tasks/{task_id}/rerun/stream")
def stream_rerun(task_id: str) -> StreamingResponse:
    task = load_task(task_id)
    dataset = Path(task.dataset_dir) if task.dataset_dir else None

    def worker(emit) -> None:
        updated = rerun_chapter(task_dir(task_id), Path(task.template_path), dataset, emit)
        emit({"type": "task", "task": updated.to_dict()})

    return sse_response(worker)



@router.post("/api/writing/tasks/{task_id}/chapters/{chapter_key}/confirm", response_model=WritingTask)
def post_confirm(task_id: str, chapter_key: str, body: ConfirmChapterRequest) -> WritingTask:
    task = load_task(task_id)
    try:
        return confirm_chapter(
            task_dir(task_id),
            Path(task.template_path),
            Path(task.dataset_dir),
            chapter_key,
            body.decision,
            body.text,
            body.comment,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"章节不存在: {error}") from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.get("/api/writing/tasks/{task_id}/export", response_model=ConfirmedExport)
def get_export(task_id: str) -> ConfirmedExport:
    return ConfirmedExport.model_validate(export_confirmed(load_task(task_id)))



@router.get("/api/writing/studies", response_model=list[StudiedTemplate])
def list_studies(biz_type: str) -> list[StudiedTemplate]:
    try:
        material_type = normalize_biz_type(biz_type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return open_library(task_root() / "cache").list_studies(material_type)



@router.post("/api/writing/tasks/use-study", response_model=WritingTask)
def post_use_study(body: UseStudyRequest) -> WritingTask:
    try:
        return adopt_study(
            task_dir(body.task_id),
            task_root() / "cache",
            body.biz_type,
            body.sample_id,
            body.sample_version,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.get("/api/writing/samples/{sample_id}/{version}", response_model=SampleDocument)
def get_sample(sample_id: str, version: str) -> SampleDocument:
    sample = open_library(task_root() / "cache").load_sample(sample_id, version)
    if sample is None:
        raise HTTPException(status_code=404, detail="样板不存在")
    return sample



@router.get("/api/writing/samples/{sample_id}/{version}/study", response_model=StudyRecord)
def get_study(sample_id: str, version: str) -> StudyRecord:
    record = open_library(task_root() / "cache").load_study(sample_id, version)
    if record is None:
        raise HTTPException(status_code=404, detail="书写要求不存在")
    return record



def _store_template(task_id: str, biz_type: str, raw: bytes, filename: str) -> tuple[Path, Path]:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise HTTPException(status_code=400, detail="模版请上传 PDF 或 Word（.docx）")
    directory = task_root() / "uploads" / task_id / "templates" / biz_type
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / _safe_name(filename)
    source.write_bytes(raw)
    version = hashlib.sha256(raw).hexdigest()[:16]
    path = directory / "template.json"
    try:
        from report_writing.services.documents import template_document

        parsed = template_document(source, biz_type, version)
    except ValueError:
        # 目录先行流程允许先只落盘原文，确认目录后再切章
        parsed = {
            "id": f"{biz_type}-{source.stem}",
            "version": version,
            "title": source.stem,
            "biz_type": biz_type,
            "chapters": [],
        }
    path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, source



