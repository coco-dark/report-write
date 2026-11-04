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


def attach_source(task_dir: Path, source_path: Path, biz_type: str = "") -> WritingTask:
    """学习前绑定原文报告，供按目录回文切章。"""
    from report_writing.controller.write_flow import ensure_task

    store = TaskStore(task_dir)
    try:
        task = store.load()
    except Exception:
        task = ensure_task(task_dir)
    if task.outline is None or not (task.outline.chapters or []):
        raise RuntimeError("请先生成并确认目录结构")
    if not source_path.is_file():
        raise RuntimeError("原文报告不存在")
    material_type = normalize_biz_type(biz_type or task.biz_type or "002")
    task.source_path = str(source_path)
    task.biz_type = material_type
    if not task.template_id:
        task.template_id = f"{material_type}-{source_path.stem}"
    if not task.template_version:
        task.template_version = hashlib.sha256(source_path.read_bytes()).hexdigest()[:16]
    store.save(task)
    return task


def _sample_cache_path(sample_path: Path) -> Path:
    return Path(str(sample_path) + ".rag-parse.md")


def _workspace_root() -> Path | None:
    try:
        from report_writing.infra.project_config import data_home_path
        from report_writing.services.users import current_user_id, ensure_default_user, user_workspace

        base = data_home_path()
        ensure_default_user(base)
        return user_workspace(base, current_user_id())
    except Exception:
        return None


def _as_existing_sample_file(raw: str) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_file() else None


def _sample_owned_by_other_task(task_id: str, path: Path) -> bool:
    """样本落在 uploads/{other}/ 时，写作副本不得删库蓝本文件。"""
    tid = str(task_id or "").strip()
    if not tid:
        return False
    try:
        parts = path.resolve().parts
    except Exception:
        return False
    if "uploads" not in parts:
        return False
    idx = parts.index("uploads")
    if idx + 1 >= len(parts):
        return False
    owner = str(parts[idx + 1] or "").strip()
    return bool(owner) and owner != tid


def _sample_file_in_uploads(task_id: str) -> Path | None:
    tid = str(task_id or "").strip()
    if not tid or "/" in tid or tid in {".", ".."}:
        return None
    root = _workspace_root()
    if root is None:
        return None
    directory = root / "uploads" / tid / "sample"
    if not directory.is_dir():
        return None
    files = [
        child
        for child in sorted(directory.iterdir())
        if child.is_file() and child.suffix.lower() in {".pdf", ".doc", ".docx"}
    ]
    return files[0] if files else None


def _load_source_blueprint_for_sample(task: WritingTask) -> WritingTask | None:
    bid = str(getattr(task, "source_blueprint_id", "") or "").strip()
    tid = str(getattr(task, "id", "") or "").strip()
    if not bid or bid == tid:
        return None
    try:
        from report_writing.services.blueprint_catalog import load_blueprint_task

        source = load_blueprint_task(bid)
        if source is not None:
            return source
    except Exception:
        pass
    root = _workspace_root()
    if root is None:
        return None
    try:
        return TaskStore(root / "tasks" / bid).load()
    except Exception:
        return None


def resolve_sample_path(task: WritingTask) -> Path | None:
    """写作副本优先用自己的样本；文件缺失时回落到源蓝本样本。"""
    found = _as_existing_sample_file(getattr(task, "sample_path", "") or "")
    if found is not None:
        return found
    found = _sample_file_in_uploads(str(getattr(task, "id", "") or ""))
    if found is not None:
        return found
    source = _load_source_blueprint_for_sample(task)
    if source is not None:
        found = _as_existing_sample_file(getattr(source, "sample_path", "") or "")
        if found is not None:
            return found
        found = _sample_file_in_uploads(str(getattr(source, "id", "") or ""))
        if found is not None:
            return found
    bid = str(getattr(task, "source_blueprint_id", "") or "").strip()
    if bid:
        found = _sample_file_in_uploads(bid)
        if found is not None:
            return found
    return None


def _read_sample_text(path: Path, *, force: bool = False) -> str:
    cache = _sample_cache_path(path)
    if (
        not force
        and cache.is_file()
        and cache.stat().st_mtime >= path.stat().st_mtime
        and cache.stat().st_size > 0
    ):
        return cache.read_text(encoding="utf-8")
    text = read_template_text(path)
    cache.write_text(text or "", encoding="utf-8")
    return text or ""


def save_blueprint_sample(task_dir: Path, source_path: Path, *, original_name: str = "") -> WritingTask:
    """蓝本上传样本：落盘后立即 RAG 解析并缓存，供大纲/学习按序号匹配。"""
    from report_writing.services.store import TaskNotFoundError

    store = TaskStore(task_dir)
    try:
        task = store.load()
    except TaskNotFoundError:
        raise RuntimeError("蓝本不存在") from None
    if not source_path.is_file():
        raise RuntimeError("样本文件不存在")
    # 清理旧样本旁路缓存；共享源蓝本文件时不得删库侧样本
    old = Path(task.sample_path) if (task.sample_path or "").strip() else None
    if (
        old
        and old.is_file()
        and old.resolve() != source_path.resolve()
        and not _sample_owned_by_other_task(str(task.id or ""), old)
    ):
        try:
            old.unlink(missing_ok=True)
            _sample_cache_path(old).unlink(missing_ok=True)
            Path(str(old) + ".rag-parse.html").unlink(missing_ok=True)
        except OSError:
            pass
    task.sample_path = str(source_path)
    task.sample_name = (original_name or source_path.name).strip() or source_path.name
    text = read_template_text(source_path)
    cache = _sample_cache_path(source_path)
    cache.write_text(text or "", encoding="utf-8")
    try:
        from report_writing.infra.rag_parse import parse_full_text, use_remote_parse

        if use_remote_parse():
            html = parse_full_text(source_path, result_type="html")
            Path(str(source_path) + ".rag-parse.html").write_text(html or "", encoding="utf-8")
    except Exception as error:
        _log.warning("样本 HTML 解析缓存失败：%s", error)
    task.sample_chars = len(text or "")
    task.sample_ready = bool((text or "").strip())
    # 样本也可作为 source，便于按目录学习复用
    task.source_path = str(source_path)
    # 换新样板后须重新提取目录，避免向导仍停在「确认目录」却没有提取按钮
    task.outline = None
    task.outline_confirmed = False
    task.brief = None
    task.brief_confirmed = False
    task.pending_outline = []
    task.pending_outline_tool = None
    task.pending_study = []
    task.pending_study_tool = None
    task.study_chapter_keys = []
    task.chapters = []
    task.refresh_status()
    store.save(task)
    _log.info(
        "蓝本样本已解析 task=%s file=%s chars=%s",
        task.id,
        task.sample_name,
        task.sample_chars,
    )
    return task


def clear_blueprint_sample(task_dir: Path) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    path = Path(task.sample_path) if (task.sample_path or "").strip() else None
    if path and path.is_file() and not _sample_owned_by_other_task(str(task.id or ""), path):
        try:
            path.unlink(missing_ok=True)
            _sample_cache_path(path).unlink(missing_ok=True)
            Path(str(path) + ".rag-parse.html").unlink(missing_ok=True)
        except OSError as error:
            _log.warning("删除样本文件失败：%s", error)
    task.sample_path = ""
    task.sample_name = ""
    task.sample_chars = 0
    task.sample_ready = False
    store.save(task)
    return task


def resolve_sample_text(task: WritingTask, *, force: bool = False) -> str:
    """读取蓝本样本的 RAG 解析正文（优先旁路缓存）。写作副本可回落源蓝本样本。"""
    path = resolve_sample_path(task)
    if path is None:
        raise RuntimeError("请先为蓝本上传样本")
    return _read_sample_text(path, force=force)


def _blueprint_visible_for_user(task: WritingTask) -> bool:
    from report_writing.services.users import current_user_id, owns_user_resource

    if str(task.id or "").startswith("w"):
        return False
    return owns_user_resource(current_user_id(), getattr(task, "user_id", "") or "")


def list_blueprints(tasks_root: Path, *, include_disabled: bool = True) -> list:
    from report_writing.services.blueprint_catalog import catalog_available, list_blueprint_summaries

    if catalog_available():
        return list_blueprint_summaries(include_disabled=include_disabled)

    _log.warning("蓝本列表不可用：未连接数据库")
    return []


def create_blueprint(
    task_dir: Path,
    *,
    title: str = "",
    category: str = "",
    tags: list[str] | None = None,
    description: str = "",
    biz_type: str = "002",
) -> WritingTask:
    from report_writing.controller.write_flow import ensure_task
    from report_writing.services.users import current_user_id

    task = ensure_task(task_dir, biz_type=biz_type)
    task.title = (title or "").strip() or "未命名蓝本"
    task.category = (category or "").strip()
    task.tags = [str(item).strip() for item in (tags or []) if str(item).strip()]
    task.description = (description or "").strip()
    task.enabled = True
    if not (task.user_id or "").strip():
        task.user_id = current_user_id()
    TaskStore(task_dir).save(task)
    from report_writing.services.blueprint_catalog import save_blueprint_task

    save_blueprint_task(task)
    return task


def set_blueprint_enabled(task_dir: Path, *, enabled: bool) -> WritingTask:
    from report_writing.controller.write_flow import ensure_task
    task = ensure_task(task_dir)
    task.enabled = bool(enabled)
    TaskStore(task_dir).save(task)
    from report_writing.services.blueprint_catalog import save_blueprint_task

    save_blueprint_task(task)
    return task


def delete_blueprint(task_dir: Path) -> None:
    import shutil

    from report_writing.services.blueprint_catalog import delete_blueprint_task, load_blueprint_task

    bid = task_dir.name
    if load_blueprint_task(bid) is None and not (task_dir / "task.json").is_file():
        raise KeyError(bid)
    delete_blueprint_task(bid)
    if task_dir.exists():
        shutil.rmtree(task_dir)


def save_spec_meta(task_dir: Path, *, title: str = "", category: str = "", tags: list[str] | None = None, description: str = "") -> WritingTask:
    from report_writing.controller.write_flow import ensure_task
    task = ensure_task(task_dir)
    task.title = (title or "").strip()
    task.category = (category or "").strip()
    task.tags = [str(item).strip() for item in (tags or []) if str(item).strip()]
    task.description = (description or "").strip()
    TaskStore(task_dir).save(task)
    from report_writing.services.blueprint_catalog import save_blueprint_task

    save_blueprint_task(task)
    return task


