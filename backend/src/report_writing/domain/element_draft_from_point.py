"""根据写作要点待补项，生成要素配置草稿（供用户确认）。"""

from __future__ import annotations

import json
import re
from typing import Any

_DOC_HINTS = re.compile(
    r"责任书|报告|方案|合同|协议|批复|文件|资料|证书|清单|表格|证明|纪要|通知|制度|标准"
)


def _parse_element_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.I)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _fallback_draft(
    *,
    slot: str,
    point_text: str,
    chapter_title: str,
    sequence: str,
) -> dict[str, Any]:
    slot = str(slot or "").strip()
    point_text = str(point_text or "").strip()
    name = slot[:24] if slot else (point_text[:20] or "写作要素")
    label = f"{sequence} {chapter_title}".strip() or chapter_title or "本章"
    purpose = f"用于「{label}」中「{name}」要点的资料要素"
    if _DOC_HINTS.search(slot or point_text):
        query = slot or point_text[:48]
        return {
            "kind": "file",
            "name": name,
            "category": name,
            "purpose": purpose,
            "tags": [name],
            "query": query,
            "value": "",
            "prompt": "",
            "kv_pairs": [],
        }
    key = slot or name
    return {
        "kind": "kv",
        "name": name,
        "category": name,
        "purpose": purpose,
        "tags": [name],
        "query": "",
        "value": "",
        "prompt": "",
        "kv_pairs": [{"key": key, "value": ""}],
    }


_ELEMENT_SYSTEM = (
    "你是报告写作要素配置助手。用户在某章写作要点里点了「关联要素」，要为「待补资料项」生成要素草稿。\n"
    "只返回 JSON，不要 Markdown：\n"
    '{"kind":"kv|fixed|file|file_slot","name":"","category":"","purpose":"","tags":[],'
    '"kv_pairs":[{"key":"","value":""}],"query":"","value":""}\n'
    "规则：\n"
    "- 待补项多为需上传/检索的资料 → 优先 kind=file，query 为 12～48 字检索词。\n"
    "- 固定字段、指标、姓名等 → kind=kv，kv_pairs 填 key，value 可留空。\n"
    "- name 简短中文（≤24字）；purpose 说明在本章写作中的用途。\n"
    "- 勿编造具体项目数值、地址、日期；value 留空表示待用户填写。\n"
    "- 未明确槽位编号时不要使用 file_slot。"
)


def derive_element_draft_from_point(
    *,
    chapter_title: str = "",
    sequence: str = "",
    category: str = "报告",
    describes: str = "",
    point_text: str = "",
    slot: str = "",
    task_elements: list | None = None,
) -> tuple[dict[str, Any], bool]:
    """返回 (draft 字段 dict, used_llm)。"""
    from report_writing.chat.element_configure_pending import payload_to_draft_enriched

    slot = str(slot or "").strip()
    point_text = str(point_text or "").strip()
    title = str(chapter_title or "").strip() or "本章"

    fb = _fallback_draft(
        slot=slot,
        point_text=point_text,
        chapter_title=title,
        sequence=str(sequence or "").strip(),
    )

    try:
        from report_writing.infra.llm import live_model
        from report_writing.infra.llm_invoke import invoke_messages
        from langchain_core.messages import HumanMessage, SystemMessage

        model = live_model(label="element_draft")
    except Exception:
        model = None

    if model is None:
        payload = payload_to_draft_enriched(
            fb,
            task_elements=task_elements,
            chapter_title=title,
        )
        return payload, False

    user = json.dumps(
        {
            "文档类型": category or "报告",
            "章节": f"{sequence} {title}".strip(),
            "章节说明": (describes or "")[:400],
            "写作要点": point_text[:500],
            "待补资料项": slot or "（未命名）",
        },
        ensure_ascii=False,
    )
    try:
        raw = invoke_messages(model, [SystemMessage(content=_ELEMENT_SYSTEM), HumanMessage(content=user)])
        data = _parse_element_json(raw)
    except Exception:
        data = {}

    merged = {**fb, **{k: v for k, v in (data or {}).items() if v not in (None, "", [], {})}}
    if not str(merged.get("name") or "").strip():
        merged["name"] = fb["name"]
    payload = payload_to_draft_enriched(
        merged,
        task_elements=task_elements,
        chapter_title=title,
    )
    hint = str(slot or point_text[:40]).strip()
    if hint and not str(payload.get("purpose") or "").strip():
        payload["purpose"] = fb["purpose"]
    return payload, bool(data)
