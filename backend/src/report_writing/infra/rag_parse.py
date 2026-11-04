"""调用安源 RAG 的全文解析。资料取 Markdown，模版取 HTML。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

_log = logging.getLogger("uvicorn.error")

_FULL_TEXT = "/internal/v1/documents/parse-full-text"
# 单段日志长度；完整正文按段打出，避免被终端一次截断
def _log_chunk() -> int:
    from report_writing.infra.config_settings import document_parse_log_chunk

    return max(1000, document_parse_log_chunk())


def use_remote_parse() -> bool:
    from report_writing.infra.config_settings import document_parse_mode

    return document_parse_mode() != "local"


def rag_domain() -> str:
    """RAG 服务域名：config/application.yml 或 RAG_DOMAIN 环境变量。"""
    from report_writing.infra.project_config import suiyue_section

    merged = suiyue_section("rag")
    return str(merged.get("domain") or "").strip().rstrip("/")


def parse_full_text(path: Path, result_type: str = "markdown") -> str:
    """上传文件并取回全文。旧版 .doc 先转成 .docx，RAG 不接受原始 .doc。"""
    domain = rag_domain()
    if not domain:
        raise ValueError("未读到 suiyue.rag.domain，无法沿用安源全文解析")
    upload = _docx_for_upload(path)
    boundary = "----ReportWritingBoundary7f3a"
    disposition = f"filename*=UTF-8''{quote(upload.name)}"
    kind = (result_type or "markdown").strip().lower()
    type_part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="type"\r\n\r\n'
        f"{kind}\r\n"
    ).encode("utf-8")
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; {disposition}\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    payload = type_part + head + upload.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        f"{domain}{_FULL_TEXT}?type={quote(kind)}",
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        status = int(getattr(error, "code", 0) or 0)
        hint = ""
        if status in {502, 503, 504}:
            hint = (
                " RAG 解析服务暂时不可用（可稍后重试；"
                "或设置 REPORT_WRITING_PARSE=local 使用本地解析）。"
            )
        raise ValueError(
            f"全文解析失败（HTTP {status}）{hint}：{detail or error.reason}"
        ) from error
    except urllib.error.URLError as error:
        raise ValueError(f"全文解析失败：{error.reason}") from error
    code = body.get("code")
    if code not in (None, 200, "200"):
        message = body.get("msg") or body.get("message") or "未知错误"
        raise ValueError(f"全文解析失败：{message}")
    data = body.get("data")
    if not isinstance(data, str) or not data.strip():
        raise ValueError("全文解析没有返回正文")
    _log_rag_content(upload=upload, source=path, result_type=kind, domain=domain, content=data)
    return data


def _log_rag_content(
    *,
    upload: Path,
    source: Path,
    result_type: str,
    domain: str,
    content: str,
) -> None:
    """把 RAG 返回正文打到后台日志，便于对照切章/学习效果。"""
    text = content or ""
    lines = text.count("\n") + (1 if text else 0)
    _log.info(
        "RAG 全文解析完成 file=%s upload=%s type=%s domain=%s chars=%s lines=%s",
        source.name,
        upload.name,
        result_type,
        domain,
        len(text),
        lines,
    )
    if not text.strip():
        _log.info("RAG 返回正文为空 file=%s", source.name)
        return
    chunk_size = _log_chunk()
    total = max(1, (len(text) + chunk_size - 1) // chunk_size)
    for index in range(total):
        start = index * chunk_size
        chunk = text[start : start + chunk_size]
        _log.info(
            "RAG 解析正文 file=%s type=%s part=%s/%s offset=%s..\n%s",
            source.name,
            result_type,
            index + 1,
            total,
            start,
            chunk,
        )


def _docx_for_upload(path: Path) -> Path:
    if path.suffix.lower() != ".doc":
        return path
    target = path.with_name(f"{path.stem}.converted.docx")
    if target.is_file() and target.stat().st_mtime >= path.stat().st_mtime and target.stat().st_size > 0:
        return target
    try:
        subprocess.run(
            ["textutil", "-convert", "docx", "-output", str(target), str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or error
        raise ValueError(f"{path.name} 无法转成 .docx：{detail}") from error
    if not target.is_file() or target.stat().st_size <= 0:
        raise ValueError(f"{path.name} 转成 .docx 后没有内容")
    return target


def paragraphs_from_markdown(markdown: str) -> list[str]:
    """按空行切块；完整保留 <table>…</table>；图片换成占位标记。"""
    import re

    text = markdown or ""
    # 图片先换成占位，避免被拆碎
    text = re.sub(
        r"<img\b[^>]*>",
        "[图片] 图片占位（暂不解析图像内容）",
        text,
        flags=re.IGNORECASE,
    )
    blocks: list[str] = []
    current: list[str] = []
    in_table = False
    for line in text.splitlines():
        lowered = line.lower()
        if "<table" in lowered:
            if current and not in_table:
                blocks.append("\n".join(current).strip())
                current = []
            in_table = True
            current.append(line.rstrip())
            if "</table>" in lowered:
                blocks.append("\n".join(current).strip())
                current = []
                in_table = False
            continue
        if in_table:
            current.append(line.rstrip())
            if "</table>" in lowered:
                blocks.append("\n".join(current).strip())
                current = []
                in_table = False
            continue
        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line.rstrip())
    if current:
        blocks.append("\n".join(current).strip())
    return [item for item in blocks if item]


def file_domain() -> str:
    from report_writing.infra.project_config import suiyue_section

    merged = suiyue_section("file")
    return str(merged.get("domain") or "https://images.smartqa.cn").strip().rstrip("/")
