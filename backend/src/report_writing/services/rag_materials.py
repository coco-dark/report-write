"""把项目资料交给安源 RAG 向量化，并按多个任务编号一起检索。"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from report_writing.services.corpus import MATERIAL_SUFFIXES, CorpusHit, file_biz_type
from report_writing.infra.rag_parse import file_domain, rag_domain

_log = logging.getLogger("uvicorn.error")
_MANIFEST = ".rag-tasks.json"
_FILE_VECTOR_MANIFEST_SUFFIX = ".rag-vector.json"
_POLL_SECONDS = 5
_MAX_POLLS = 60
_RATE_LIMIT_RETRIES = 5
_RATE_LIMIT_BASE_SLEEP = 2.0
_RATE_LIMIT_HINT = "检索暂时限流（阿里云向量化请求过频），请稍等数十秒后再开写"


class RagMaterialIndex:
    """一次编写任务里的多个资料，对应多条 RAG 向量任务。"""

    def __init__(self, root: Path, biz_type: str) -> None:
        self.root = root
        self.biz_type = biz_type
        self._tasks: list[dict] = []

    def prepare(self, on_progress=None) -> None:
        files = [
            path
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and path.suffix.lower() in MATERIAL_SUFFIXES and path.name != _MANIFEST
        ]
        selected = [
            path
            for path in files
            if file_biz_type(str(path.relative_to(self.root)), self.biz_type) == self.biz_type
        ]
        manifest = _load_manifest(self.root)
        total = len(selected)
        for index, path in enumerate(selected, start=1):
            relative = str(path.relative_to(self.root))
            record = _ready_record(path, relative, manifest.get(relative), self.biz_type)
            manifest[relative] = record
            self._tasks.append(record)
            _save_manifest(self.root, manifest)
            _log.info("资料已交给 RAG 向量化 %s/%s %s 任务 %s", index, total, relative, record["task_id"])
            if on_progress is not None:
                on_progress(index, total)
        if not self._tasks:
            raise ValueError("当前类型没有可向量化的资料")

    def search(self, dataset_key: str, biz_type: str, query: str, limit: int = 8) -> list[CorpusHit]:
        del dataset_key
        task_ids = [item["task_id"] for item in self._tasks if item["biz_type"] == biz_type]
        biz_ids = [item["biz_id"] for item in self._tasks if item["biz_type"] == biz_type]
        if not task_ids or not query.strip():
            return []
        body = search_materials(query, biz_ids, task_ids, limit)
        hits: list[CorpusHit] = []
        names = {item["task_id"]: item["source_file"] for item in self._tasks}
        allowed_tasks = set(task_ids)
        for index, record in enumerate(_records(body), start=1):
            excerpt = _record_text(record)
            if not excerpt:
                continue
            task_id = _record_task_id(record)
            if allowed_tasks and task_id not in allowed_tasks:
                continue
            hits.append(
                CorpusHit(
                    id=f"rag-{index}",
                    source_file=names.get(task_id, str(task_id or "")),
                    location=_record_location(record),
                    excerpt=excerpt,
                    biz_type=biz_type,
                )
            )
            if len(hits) >= limit:
                break
        return hits


def search_materials(query: str, biz_ids: list[str], task_ids: list[int], limit: int) -> dict:
    return _request_json(
        "POST",
        f"{rag_domain()}/search",
        {
            "query": query,
            "bizIdIn": biz_ids,
            "taskIdIn": task_ids,
            "searchOption": {
                "searchType": "hybridSearch",
                "topK": limit,
                "rerankingEnabled": False,
            },
        },
    )


def load_file_vector_record(path: Path) -> dict | None:
    manifest_path = file_vector_manifest_path(path)
    if not manifest_path.is_file():
        return None
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def search_scope_from_record(record: dict | None) -> tuple[list[str], list[int]]:
    """从向量化 manifest 取出检索限定用的 bizId / taskId 列表。"""
    if not isinstance(record, dict):
        return [], []
    biz_ids: list[str] = []
    task_ids: list[int] = []
    for raw in list(record.get("search_biz_ids") or record.get("biz_ids") or []):
        text = str(raw or "").strip()
        if text and text not in biz_ids:
            biz_ids.append(text)
    primary_biz = str(record.get("rag_biz_id") or record.get("biz_id") or "").strip()
    if primary_biz and primary_biz not in biz_ids:
        biz_ids.insert(0, primary_biz)
    for raw in list(record.get("search_task_ids") or record.get("task_ids") or []):
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        if tid > 0 and tid not in task_ids:
            task_ids.append(tid)
    primary_task = record.get("rag_task_id", record.get("task_id"))
    try:
        tid = int(primary_task or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid > 0 and tid not in task_ids:
        task_ids.insert(0, tid)
    return biz_ids, task_ids


def rag_meta_from_record(record: dict | None) -> dict[str, str]:
    biz_ids, task_ids = search_scope_from_record(record)
    return {
        "rag_biz_id": biz_ids[0] if biz_ids else "",
        "rag_task_id": str(task_ids[0]) if task_ids else "",
    }


def search_materials_scoped(query: str, record: dict, limit: int) -> list[str]:
    """hybridSearch 后只保留 manifest 中登记 taskId 的片段，避免串到其它资料。"""
    biz_ids, task_ids = search_scope_from_record(record)
    if not query.strip() or not task_ids:
        return []
    want = set(task_ids)
    body = search_materials(query, biz_ids, task_ids, max(limit, 1) * 3)
    texts: list[str] = []
    for rec in _records(body):
        tid = _record_task_id(rec)
        if want and tid not in want:
            continue
        text = _record_text(rec)
        if text:
            texts.append(text[:800])
        if len(texts) >= limit:
            break
    return texts[:limit]


def file_vector_manifest_path(path: Path) -> Path:
    return Path(str(path) + _FILE_VECTOR_MANIFEST_SUFFIX)


def ensure_file_vectorized(path: Path, *, biz_type: str = "002") -> dict:
    """单文件向量化（旁路 manifest），供要素文件 / 资料包槽位 RAG 检索复用。"""
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")
    manifest_path = file_vector_manifest_path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    cached: dict | None = None
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cached = loaded
        except (OSError, json.JSONDecodeError):
            cached = None
    relative = path.name
    record = _ready_record(path, relative, cached, biz_type)
    manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def search_file_rag_excerpts(
    path: Path,
    query: str,
    *,
    limit: int = 5,
    biz_type: str = "002",
) -> list[str]:
    """对单个本地文件做 hybridSearch；失败时返回空列表，由调用方 fallback。"""
    q = (query or "").strip()
    if not q or not path.is_file():
        return []
    try:
        record = ensure_file_vectorized(path, biz_type=biz_type)
        return search_materials_scoped(q, record, limit)
    except Exception as error:
        _log.warning("要素 RAG 检索失败 %s: %s", path.name, error)
        return []


def _biz_id_for_file(path: Path, digest: str) -> str:
    """每个本地文件独立 bizId，检索时与 vector taskId 成对限定范围。"""
    path_key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"rw-{path_key}-{digest[:8]}"


def _vector_ids_from_created(created: dict, requested_biz_id: str) -> tuple[str, int]:
    task_raw = created.get("id") or created.get("taskId") or created.get("task_id") or 0
    try:
        task_id = int(task_raw or 0)
    except (TypeError, ValueError):
        task_id = 0
    biz_id = str(
        created.get("bizId")
        or created.get("biz_id")
        or created.get("biz")
        or requested_biz_id
        or ""
    ).strip()
    return biz_id, task_id


def _finalize_vector_record(
    *,
    path: Path,
    relative: str,
    stored_type: str,
    digest: str,
    biz_id: str,
    task_id: int,
    uploaded: str,
) -> dict:
    biz = str(biz_id or "").strip()
    tid = int(task_id or 0)
    return {
        "source_file": relative,
        "source_path": str(path.resolve()),
        "biz_type": stored_type,
        "biz_id": biz,
        "task_id": tid,
        "rag_biz_id": biz,
        "rag_task_id": tid,
        "search_biz_ids": [biz] if biz else [],
        "search_task_ids": [tid] if tid > 0 else [],
        "digest": digest,
        "url": uploaded,
    }


def _ready_record(path: Path, relative: str, cached: dict | None, biz_type: str) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    stored_type = file_biz_type(relative, biz_type)
    if cached and cached.get("digest") == digest:
        cached_tid = cached.get("rag_task_id", cached.get("task_id"))
        try:
            tid_ok = int(cached_tid or 0) > 0
        except (TypeError, ValueError):
            tid_ok = False
        if tid_ok and _task_done(int(cached_tid)):
            merged = {**cached, "source_file": relative, "biz_type": stored_type}
            biz_ids, task_ids = search_scope_from_record(merged)
            if biz_ids and task_ids:
                return merged
    biz_id = _biz_id_for_file(path, digest)
    uploaded = upload_material(path)
    created = create_vector_task(uploaded, biz_id)
    biz_id, task_id = _vector_ids_from_created(created, biz_id)
    if task_id <= 0:
        raise ValueError(f"{path.name} 的向量任务没有返回编号")
    if not _summary_done(created):
        _wait_until_done(task_id, path.name)
    return _finalize_vector_record(
        path=path,
        relative=relative,
        stored_type=stored_type,
        digest=digest,
        biz_id=biz_id,
        task_id=task_id,
        uploaded=uploaded,
    )


def upload_material(path: Path) -> str:
    boundary = "----ReportWritingFileBoundary"
    disposition = f"filename*=UTF-8''{quote(path.name)}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; {disposition}\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    payload = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = _request_json(
        "POST",
        f"{file_domain()}/file/upload",
        payload,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    url = _file_url(body)
    if not url:
        raise ValueError(f"{path.name} 上传后没有文件地址")
    return url


def _file_url(body) -> str:
    if isinstance(body, str):
        return body.strip()
    if not isinstance(body, dict):
        return ""
    nested = body.get("data")
    if isinstance(nested, dict):
        found = _file_url(nested)
        if found:
            return found
    return str(body.get("url") or body.get("fileUrl") or body.get("path") or "").strip()


def create_vector_task(file_url: str, biz_id: str) -> dict:
    body = _request_json(
        "POST",
        f"{rag_domain()}/vectorize-tasks",
        {
            "data": file_url,
            "bizId": biz_id,
            "datasourceType": "innerFile",
        },
    )
    if not isinstance(body, dict):
        raise ValueError("向量任务返回不是对象")
    return body


def _wait_until_done(task_id: int, name: str) -> None:
    for _ in range(_MAX_POLLS):
        if _task_done(task_id):
            return
        time.sleep(_POLL_SECONDS)
    raise ValueError(f"{name} 的向量任务 {task_id} 在限定时间内没有完成")


def _task_done(task_id: int) -> bool:
    summary = _request_json("GET", f"{rag_domain()}/vectorize-tasks/{task_id}/summary")
    if not isinstance(summary, dict):
        return False
    if _summary_failed(summary):
        raise ValueError(f"向量任务 {task_id} 失败")
    return _summary_done(summary)


def _summary_done(summary: dict) -> bool:
    if summary.get("done") is True:
        return True
    status = summary.get("stepStatus")
    return status in {"DONE", "done", "completed"}


def _summary_failed(summary: dict) -> bool:
    status = summary.get("stepStatus")
    return status in {"ERROR", "error", "failed"} or bool(summary.get("errorMessage"))


def _records(body: dict | list) -> list:
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    records = body.get("records") or []
    return records if isinstance(records, list) else []


def _record_text(record: dict) -> str:
    if not isinstance(record, dict):
        return ""
    segment = record.get("segement") or record.get("segment") or {}
    if isinstance(segment, dict) and segment.get("content"):
        return str(segment["content"]).strip()
    return str(record.get("content") or "").strip()


def _record_task_id(record: dict) -> int:
    segment = record.get("segement") or record.get("segment") or {}
    raw = record.get("taskId") or (segment.get("taskId") if isinstance(segment, dict) else None) or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _record_location(record: dict) -> str:
    segment = record.get("segement") or record.get("segment") or {}
    if isinstance(segment, dict) and segment.get("id") is not None:
        return f"片段 {segment['id']}"
    return "RAG"


def _load_manifest(root: Path) -> dict:
    path = root / _MANIFEST
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _save_manifest(root: Path, manifest: dict) -> None:
    (root / _MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_rate_limit(text: str, *, status: int | None = None) -> bool:
    if status == 429:
        return True
    lower = (text or "").lower()
    markers = (
        "429",
        "limit_requests",
        "rate-limit",
        "rate_limit",
        "request limit",
        "exceeded your current request",
        "限流",
        "超限",
        "请求过于频繁",
    )
    return any(token in lower for token in markers)


def _request_json(method: str, url: str, payload=None, headers: dict | None = None) -> dict | list | str:
    last_error: Exception | None = None
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        try:
            return _request_json_once(method, url, payload=payload, headers=headers)
        except ValueError as error:
            last_error = error
            if not _is_rate_limit(str(error)) or attempt >= _RATE_LIMIT_RETRIES:
                if _is_rate_limit(str(error)):
                    raise ValueError(_RATE_LIMIT_HINT) from error
                raise
            sleep_s = _RATE_LIMIT_BASE_SLEEP * (2**attempt)
            _log.warning(
                "RAG 限流，%.1fs 后重试 %s/%s：%s",
                sleep_s,
                attempt + 1,
                _RATE_LIMIT_RETRIES,
                error,
            )
            time.sleep(sleep_s)
    raise ValueError(_RATE_LIMIT_HINT) from last_error


def _request_json_once(method: str, url: str, payload=None, headers: dict | None = None) -> dict | list | str:
    data = None
    request_headers = dict(headers or {})
    if isinstance(payload, (dict, list)):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif isinstance(payload, bytes):
        data = payload
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        message = f"RAG 请求失败：{detail or error.reason}"
        if _is_rate_limit(detail or error.reason or "", status=error.code):
            raise ValueError(message) from error
        raise ValueError(message) from error
    except urllib.error.URLError as error:
        raise ValueError(f"RAG 请求失败：{error.reason}") from error
    if not raw.strip():
        return {}
    body = json.loads(raw)
    if isinstance(body, dict) and "code" in body:
        code = body.get("code")
        if code not in (None, 200, "200"):
            message = body.get("msg") or body.get("message") or "未知错误"
            raise ValueError(f"RAG 请求失败：{message}")
        data_field = body.get("data")
        return data_field if data_field is not None else body
    if isinstance(body, dict) and "success" in body and "data" in body:
        if body.get("success") is False:
            message = body.get("errMessage") or body.get("description") or "未知错误"
            raise ValueError(f"文件服务请求失败：{message}")
        return body.get("data")
    return body
