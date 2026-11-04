"""图片要素：上传、文生图落盘、占位符展开。"""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from report_writing.domain.models import ElementNode, WritingTask
from report_writing.infra.llm import _base_settings

_log = logging.getLogger("uvicorn.error")

IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{img:([^}]+)\}\}")
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_IMAGE_GEN_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"


def image_placeholder(element_id: str) -> str:
    eid = str(element_id or "").strip()
    return f"{{{{img:{eid}}}}}" if eid else "{{img:}}"


def _task_dir(home: Path, task_id: str) -> Path:
    return home / "tasks" / str(task_id or "").strip()


def element_images_dir(home: Path, task_id: str) -> Path:
    root = _task_dir(home, task_id) / "element_images"
    root.mkdir(parents=True, exist_ok=True)
    return root


def relative_image_path(element_id: str, ext: str = ".png") -> str:
    eid = str(element_id or "").strip() or "img"
    suffix = ext if ext.startswith(".") else f".{ext}"
    if suffix.lower() not in _ALLOWED_EXT:
        suffix = ".png"
    return f"element_images/{eid}{suffix}"


def absolute_image_path(home: Path, task_id: str, element: ElementNode | dict | None) -> Path | None:
    if element is None:
        return None
    rel = ""
    if isinstance(element, dict):
        rel = str(element.get("image_path") or "").strip()
    else:
        rel = str(getattr(element, "image_path", "") or "").strip()
    if not rel:
        eid = str(getattr(element, "id", "") or (element.get("id") if isinstance(element, dict) else "") or "").strip()
        if not eid:
            return None
        root = element_images_dir(home, task_id)
        for ext in _ALLOWED_EXT:
            candidate = root / f"{eid}{ext}"
            if candidate.is_file():
                return candidate
        return None
    path = _task_dir(home, task_id) / rel
    return path if path.is_file() else None


def save_image_bytes(
    home: Path,
    task_id: str,
    element_id: str,
    data: bytes,
    *,
    filename: str = "",
) -> str:
    if not data:
        raise ValueError("图片为空")
    eid = str(element_id or "").strip()
    if not eid:
        raise ValueError("需要 element_id")
    ext = Path(filename or "image.png").suffix.lower()
    if ext not in _ALLOWED_EXT:
        ext = ".png"
    rel = relative_image_path(eid, ext)
    dest = _task_dir(home, task_id) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel


def _dashscope_api_key() -> str:
    try:
        return str(_base_settings().api_key or "").strip()
    except RuntimeError:
        return ""


def generate_image_from_prompt(
    home: Path,
    task_id: str,
    element_id: str,
    prompt: str,
) -> str:
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("请填写文生图描述")
    api_key = _dashscope_api_key()
    if not api_key:
        raise ValueError("未配置 DashScope API Key，无法文生图")
    body = {
        "model": "wanx-v1",
        "input": {"prompt": text[:800]},
        "parameters": {"size": "1024*1024", "n": 1},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(_IMAGE_GEN_URL, headers=headers, json=body)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as error:
        _log.warning("文生图 HTTP 失败: %s", error)
        raise ValueError(f"文生图请求失败：{error}") from error
    results = (payload.get("output") or {}).get("results") or []
    if not results:
        raise ValueError("文生图未返回图片")
    item = results[0] if isinstance(results[0], dict) else {}
    url = str(item.get("url") or "").strip()
    b64 = str(item.get("b64_image") or "").strip()
    raw: bytes
    if b64:
        raw = base64.b64decode(b64)
    elif url:
        with httpx.Client(timeout=60.0) as client:
            img_resp = client.get(url)
            img_resp.raise_for_status()
            raw = img_resp.content
    else:
        raise ValueError("文生图结果无 url 或 b64_image")
    rel = save_image_bytes(home, task_id, element_id, raw, filename="generated.png")
    return rel


def ensure_image_element_ready(
    home: Path,
    task_id: str,
    element: ElementNode,
    *,
    generate_if_llm: bool = False,
) -> ElementNode:
    """上传模式需已有文件；llm 模式可在配置/生成时落盘。"""
    kind = str(element.kind or "").strip()
    if kind != "image":
        return element
    source = str(getattr(element, "image_source", "") or "upload").strip() or "upload"
    if absolute_image_path(home, task_id, element):
        return element
    if source == "llm" and generate_if_llm:
        prompt = str(element.prompt or element.name or "").strip()
        rel = generate_image_from_prompt(home, task_id, element.id, prompt)
        element.image_path = rel
    return element


def resolve_image_for_writing(home: Path, task_id: str, node: ElementNode) -> str:
    """写作上下文：返回占位符与图题说明。"""
    ph = image_placeholder(node.id)
    title = str(node.name or "").strip()
    path = absolute_image_path(home, task_id, node)
    if not path:
        return (
            f"插图占位符（正文 HTML 须原样输出）：{ph}"
            + (f"，图题：{title}" if title else "")
            + "（图片尚未就绪，可先输出占位符）"
        )
    return (
        f"插图占位符（正文 HTML 须原样输出，勿改花括号）：{ph}"
        + (f"，图题：{title}" if title else "")
    )


def _img_src_to_path(src: str, home: Path, task: WritingTask) -> Path | None:
    raw = str(src or "").strip()
    if not raw:
        return None
    if raw.startswith("data-rw-file:"):
        raw = raw.split(":", 1)[1].strip()
    if raw.startswith("/api/writing/tasks/") and "/element-images/" in raw:
        eid = raw.rsplit("/", 1)[-1].strip()
        el = next((e for e in (task.element_nodes or []) if e.id == eid), None)
        return absolute_image_path(home, task.id, el) if el else None
    parsed = urlparse(raw)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        return path if path.is_file() else None
    if raw.startswith("/") and "element_images" in raw:
        candidate = _task_dir(home, task.id) / raw.lstrip("/")
        if candidate.is_file():
            return candidate
    path = Path(raw)
    if path.is_file():
        return path
    candidate = _task_dir(home, task.id) / raw
    return candidate if candidate.is_file() else None


def expand_image_placeholders_html(
    html: str,
    home: Path,
    task: WritingTask,
    *,
    for_export: bool = False,
) -> str:
    by_id = {str(e.id or "").strip(): e for e in (task.element_nodes or []) if str(e.id or "").strip()}

    def repl(match: re.Match[str]) -> str:
        eid = str(match.group(1) or "").strip()
        el = by_id.get(eid)
        path = absolute_image_path(home, task.id, el) if el else None
        alt = str(getattr(el, "name", "") or eid if el else eid)
        if not path or not path.is_file():
            return f'<p class="rw-img-missing">[图片未就绪：{alt}]</p>'
        if for_export:
            return (
                f'<figure class="rw-figure"><img src="{path.as_uri()}" '
                f'data-rw-file="{path}" alt="{alt}" /></figure>'
            )
        url = f"/api/writing/tasks/{task.id}/element-images/{eid}"
        return f'<figure class="rw-figure"><img src="{url}" alt="{alt}" /></figure>'

    out = IMAGE_PLACEHOLDER_RE.sub(repl, html or "")
    return out


def element_image_payload(element: ElementNode | dict) -> dict[str, Any]:
    if isinstance(element, dict):
        return {
            "image_source": str(element.get("image_source") or "upload"),
            "image_path": str(element.get("image_path") or ""),
            "prompt": str(element.get("prompt") or ""),
        }
    return {
        "image_source": str(getattr(element, "image_source", "") or "upload"),
        "image_path": str(getattr(element, "image_path") or ""),
        "prompt": str(getattr(element, "prompt") or ""),
    }
