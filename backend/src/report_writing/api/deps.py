"""Shared API helpers and path utilities."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import threading
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from report_writing.domain.models import WritingTask
from report_writing.services.store import TaskStore

_log = logging.getLogger("uvicorn.error")
_STEP_NAMES = {
    "upload": "上传",
    "outline": "目录",
    "study": "分析",
    "search": "检索",
    "write": "写作",
    "check": "检查",
}
# api/deps.py -> report_writing -> src -> backend -> report-writing
_PROJECT = Path(__file__).resolve().parents[4]
_STATIC = Path(__file__).resolve().parents[1] / "static"
_PAGE = _STATIC / "index.html"
_FILE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def resolve_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (_PROJECT / path).resolve()


def writing_model():
    """HTTP/CLI 入口取写作模型；下层用参数接收，不再各自 live_model。"""
    from report_writing.infra.llm import live_model

    return live_model()


def chat_invoke(system: str, user: str, *, model=None) -> str:
    from report_writing.infra.llm import or_live_model
    from report_writing.infra.llm_invoke import invoke_prompt

    chosen = or_live_model(model)
    if chosen is None:
        return (
            "当前未配置大模型。已收到消息；接入模型后将按机器人对话提示词回复。"
            f"\n（系统提示摘要：{(system or '')[:120]}）"
        )
    return invoke_prompt(chosen, system, user)


def data_home() -> Path:
    """全局 data 根目录（用户索引、注册登录），见 config/application.yml report_writing.data_home。"""
    from report_writing.infra.project_config import data_home_path

    return data_home_path()


def task_root() -> Path:
    base = data_home()
    from report_writing.services.users import current_user_id, ensure_default_user, user_workspace

    ensure_default_user(base)
    return user_workspace(base, current_user_id())



def task_dir(task_id: str) -> Path:
    if not task_id or "/" in task_id or task_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="任务编号不合法")
    return task_root() / "tasks" / task_id



def load_task(task_id: str) -> WritingTask:
    from report_writing.services.store import TaskNotFoundError
    from report_writing.services.task_ids import is_blueprint_task_id

    directory = task_dir(task_id)
    tid = (task_id or "").strip()
    if is_blueprint_task_id(tid):
        from report_writing.services.blueprint_catalog import catalog_available

        if not catalog_available():
            raise HTTPException(status_code=503, detail="数据库不可用，无法加载蓝本")
    elif not (directory / "task.json").is_file():
        from report_writing.services.writing_task_catalog import load_writing_task

        if load_writing_task(tid) is None:
            _log.warning("任务 %s 不存在，未找到 task.json。", task_id)
            raise HTTPException(status_code=404, detail="任务不存在，请先分析模版")
    try:
        task = TaskStore(directory).load()
    except TaskNotFoundError:
        _log.warning("任务 %s 不存在。", task_id)
        raise HTTPException(status_code=404, detail="任务不存在，请先分析模版") from None
    from report_writing.services.users import current_user_id, owns_user_resource

    if not owns_user_resource(current_user_id(), getattr(task, "user_id", "") or ""):
        raise HTTPException(status_code=404, detail="任务不存在，请先分析模版")
    return task



def _log_event(event: dict) -> None:
    kind = event.get("type")
    if kind == "error":
        _log.error("执行失败：%s", event.get("detail") or "未知错误")
        return
    if kind == "task":
        task = event.get("task") or {}
        chapters = task.get("chapters") or []
        _log.info(
            "任务 %s 状态 %s，共 %s 章，学习%s确认",
            task.get("id"),
            task.get("status"),
            len(chapters),
            "已" if task.get("brief_confirmed") else "未",
        )
        return
    if kind != "step":
        return
    step = _STEP_NAMES.get(event.get("step"), event.get("step"))
    status = {"running": "开始", "done": "完成", "skipped": "跳过"}.get(event.get("status"), event.get("status"))
    title = event.get("title") or ""
    chapter = event.get("chapter") or ""
    place = f"{title} {chapter}".strip()
    result = event.get("result") or {}
    detail = ""
    if event.get("step") == "search" and event.get("status") in {"done", "skipped"}:
        detail = f"采纳 {result.get('adopted', 0)} 条"
        if result.get("note"):
            detail = f"{detail}，{result['note']}"
    elif event.get("step") == "write" and event.get("status") == "done":
        detail = f"{len(result.get('paragraphs') or [])} 段"
    elif event.get("step") == "check" and event.get("status") == "done":
        detail = " ".join(part for part in (result.get("action"), result.get("reason")) if part)
    elif event.get("step") == "study" and event.get("status") == "done":
        chapters = result.get("chapters")
        if isinstance(chapters, int):
            count = chapters
        elif isinstance(chapters, list):
            count = len(chapters)
        else:
            count = result.get("matched") or 0
        if result.get("from_cache") is not None:
            saved = "沿用已保存结果" if result.get("from_cache") else "新学习并保存"
            detail = f"{saved}，{count} 章"
        else:
            matched = result.get("matched")
            detail = f"{count} 章" + (f"，匹配 {matched}" if matched is not None else "")
    line = f"{step}{status}"
    if place:
        line = f"{line} {place}"
    if detail:
        line = f"{line}：{detail}"
    _log.info(line)



def _iter_sse(worker, *, user_id: str | None = None):
    """SSE 在独立线程执行；须在 worker 内恢复用户上下文（中间件在流开始前已 reset）。"""
    from report_writing.services.users import reset_current_user_id, set_current_user_id

    uid = (user_id or "").strip() or None
    events: queue.Queue = queue.Queue()

    def run() -> None:
        token = None
        if uid:
            token = set_current_user_id(uid)

        def emit(event: dict) -> None:
            _log_event(event)
            events.put(event)

        try:
            worker(emit)
        except Exception as error:
            _log.exception("执行中断")
            emit({"type": "error", "detail": str(error)})
        finally:
            if token is not None:
                reset_current_user_id(token)
            events.put(None)

    threading.Thread(target=run, daemon=True).start()
    while True:
        item = events.get()
        if item is None:
            break
        try:
            payload = json.dumps(item, ensure_ascii=False, default=str)
        except TypeError:
            payload = json.dumps({"type": "process", "message": str(item)}, ensure_ascii=False)
        yield f"data: {payload}\n\n"


def sse_response(worker) -> StreamingResponse:
    from report_writing.services.users import current_user_id

    return StreamingResponse(
        _iter_sse(worker, user_id=current_user_id()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _safe_name(filename: str) -> str:
    cleaned = _FILE_NAME.sub("_", Path(filename).name).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="文件名不合法")
    return cleaned



def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    number = 2
    while True:
        candidate = path.with_name(f"{stem}-{number}{suffix}")
        if not candidate.exists():
            return candidate
        number += 1

