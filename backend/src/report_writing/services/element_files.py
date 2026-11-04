"""要素「文件」类型：多文件上传；不自动解析；手动解析后才可供写作读取。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from report_writing.domain.models import ElementAttachedFile
from report_writing.services.users import current_user_id, user_workspace

_log = logging.getLogger("uvicorn.error")
_SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")
_ALLOWED = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(home: Path) -> Path:
    ws = user_workspace(home, current_user_id())
    root = ws / "element_files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path(home: Path) -> Path:
    return _root(home) / "index.json"


def _load_index(home: Path) -> list[dict[str, Any]]:
    path = _index_path(home)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return list(data["items"])
    if isinstance(data, list):
        return data
    return []


def _save_index(home: Path, items: list[dict[str, Any]]) -> None:
    _index_path(home).write_text(
        json.dumps({"items": items, "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _safe_filename(name: str) -> str:
    base = Path(name or "file").name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "file"
    return cleaned[:180]


def _to_model(raw: dict[str, Any]) -> ElementAttachedFile:
    return ElementAttachedFile.model_validate(
        {
            "file_id": str(raw.get("file_id") or raw.get("id") or ""),
            "file_name": str(raw.get("file_name") or raw.get("original_name") or ""),
            "source_path": str(raw.get("source_path") or raw.get("local_path") or ""),
            "parse_status": str(raw.get("parse_status") or "pending"),
            "parse_error": str(raw.get("parse_error") or ""),
            "file_size": int(raw.get("file_size") or 0),
            "created_at": str(raw.get("created_at") or ""),
            "parsed_at": str(raw.get("parsed_at") or ""),
            "rag_task_id": str(raw.get("rag_task_id") or ""),
            "rag_biz_id": str(raw.get("rag_biz_id") or ""),
        }
    )


def get_file(home: Path, file_id: str) -> ElementAttachedFile | None:
    fid = (file_id or "").strip()
    if not fid:
        return None
    for raw in _load_index(home):
        if str(raw.get("file_id") or raw.get("id") or "").strip() == fid:
            return _to_model(raw)
    return None


def resolve_path(home: Path, attached: ElementAttachedFile) -> Path | None:
    raw = str(attached.source_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = user_workspace(home, current_user_id()) / path
    return path if path.is_file() else None


def upload_file(home: Path, *, filename: str, data: bytes) -> ElementAttachedFile:
    original = _safe_filename(filename)
    suffix = Path(original).suffix.lower()
    if suffix not in _ALLOWED:
        raise ValueError("支持 PDF / Word / Excel / Markdown / 文本")
    file_id = f"ef-{uuid.uuid4().hex[:12]}"
    dest = _root(home) / f"{file_id}_{original}"
    dest.write_bytes(data or b"")
    ws = user_workspace(home, current_user_id())
    try:
        rel = str(dest.relative_to(ws))
    except ValueError:
        rel = str(dest)
    record = {
        "file_id": file_id,
        "file_name": original,
        "source_path": rel,
        "parse_status": "pending",
        "parse_error": "",
        "file_size": len(data or b""),
        "created_at": _now(),
        "parsed_at": "",
    }
    items = _load_index(home)
    items.append(record)
    _save_index(home, items)
    return _to_model(record)


def parse_file(home: Path, file_id: str, *, force: bool = False) -> ElementAttachedFile:
    """手动解析：写入旁路缓存；不自动向量化。"""
    from report_writing.infra.rag_parse import parse_full_text

    attached = get_file(home, file_id)
    if attached is None:
        raise KeyError(file_id)
    path = resolve_path(home, attached)
    if path is None:
        raise ValueError("本地文件不存在，无法解析")
    cache_md = Path(str(path) + ".rag-parse.md")
    cache_html = Path(str(path) + ".rag-parse.html")
    if (
        not force
        and attached.parse_status == "parsed"
        and (cache_md.is_file() or cache_html.is_file())
    ):
        return attached

    items = _load_index(home)
    record = next(
        (r for r in items if str(r.get("file_id") or "").strip() == attached.file_id),
        None,
    )
    if record is None:
        raise KeyError(file_id)

    try:
        text = ""
        try:
            text = parse_full_text(path, result_type="markdown") or ""
            if text.strip():
                cache_md.write_text(text, encoding="utf-8")
        except Exception as md_error:
            _log.warning("要素文件 markdown 解析失败，改试 html %s: %s", path.name, md_error)
            html = parse_full_text(path, result_type="html") or ""
            if not html.strip():
                raise ValueError(f"解析失败：{md_error}") from md_error
            cache_html.write_text(html, encoding="utf-8")
            # 简易去标签作检索正文
            text = re.sub(r"<[^>]+>", " ", html)
        if not (text or "").strip():
            raise ValueError("解析没有返回可用正文")
        record["parse_status"] = "parsed"
        record["parse_error"] = ""
        record["parsed_at"] = _now()
        try:
            from report_writing.services.rag_materials import ensure_file_vectorized, rag_meta_from_record

            vrec = ensure_file_vectorized(path)
            record.update(rag_meta_from_record(vrec))
        except Exception as vec_error:
            _log.warning("要素文件 RAG 向量化留待检索时执行 %s: %s", path.name, vec_error)
    except Exception as error:
        record["parse_status"] = "error"
        record["parse_error"] = str(error)[:1000]
        _save_index(home, items)
        raise ValueError(str(error)) from error

    _save_index(home, items)
    return _to_model(record)


def delete_file(home: Path, file_id: str) -> bool:
    fid = (file_id or "").strip()
    if not fid:
        return False
    items = _load_index(home)
    kept: list[dict[str, Any]] = []
    removed: dict[str, Any] | None = None
    for raw in items:
        if str(raw.get("file_id") or "").strip() == fid:
            removed = raw
        else:
            kept.append(raw)
    if removed is None:
        return False
    _save_index(home, kept)
    attached = _to_model(removed)
    path = resolve_path(home, attached)
    if path is not None:
        for extra in (
            path,
            Path(str(path) + ".rag-parse.md"),
            Path(str(path) + ".rag-parse.html"),
            Path(str(path) + ".rag-vector.json"),
        ):
            try:
                if extra.is_file():
                    extra.unlink()
            except OSError:
                pass
    return True


def read_parsed_text(home: Path, file_id: str) -> str:
    attached = get_file(home, file_id)
    if attached is None or attached.parse_status != "parsed":
        return ""
    path = resolve_path(home, attached)
    if path is None:
        return ""
    cache_md = Path(str(path) + ".rag-parse.md")
    cache_html = Path(str(path) + ".rag-parse.html")
    if cache_md.is_file():
        return cache_md.read_text(encoding="utf-8", errors="ignore").strip()
    if cache_html.is_file():
        html = cache_html.read_text(encoding="utf-8", errors="ignore")
        return re.sub(r"<[^>]+>", " ", html).strip()
    return ""


def search_parsed_files(
    home: Path,
    files: list[ElementAttachedFile] | list[dict[str, Any]] | None,
    query: str,
    *,
    limit: int = 5,
    biz_type: str = "002",
) -> tuple[str, list[str]]:
    """返回 (拼接摘录, 未解析文件名列表)。已解析文件优先 RAG hybridSearch。"""
    from report_writing.services.rag_materials import search_file_rag_excerpts

    q = (query or "").strip()
    parts: list[str] = []
    unparsed: list[str] = []
    for item in files or []:
        if hasattr(item, "model_dump"):
            attached = item if isinstance(item, ElementAttachedFile) else ElementAttachedFile.model_validate(item.model_dump())
        elif isinstance(item, dict):
            attached = _to_model(item)
        else:
            continue
        status = str(attached.parse_status or "").strip() or "pending"
        name = str(attached.file_name or attached.file_id or "文件").strip()
        if status != "parsed":
            unparsed.append(name)
            continue
        body = read_parsed_text(home, attached.file_id)
        if not body:
            unparsed.append(name)
            continue
        if not q:
            parts.append(f"【{name}】\n{body[:1200]}")
            continue
        src_path = resolve_path(home, attached)
        if src_path is not None:
            rag_hits = search_file_rag_excerpts(src_path, q, limit=limit, biz_type=biz_type)
            if rag_hits:
                block = "\n".join(rag_hits)
                parts.append(f"【{name}】\n{block}")
                continue
        tokens = [t for t in re.split(r"[\s,，、;；]+", q) if t]
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        scored: list[tuple[int, str]] = []
        for para in paragraphs:
            if len(para) < 8:
                continue
            score = sum(1 for t in tokens if t and t in para)
            if score:
                scored.append((score, para[:800]))
        scored.sort(key=lambda row: (-row[0], -len(row[1])))
        if scored:
            block = "\n".join(t for _, t in scored[:limit])
            parts.append(f"【{name}】\n{block}")
        else:
            parts.append(f"【{name}】\n{body[:600]}")
    return "\n\n".join(parts).strip(), unparsed
