"""写作载荷：配置与要素全文落盘；送给模型时按本章要点摘录，避免几万字撑爆上下文。"""

from __future__ import annotations

import re
from typing import Any

from report_writing.domain.models import ChapterRequirement

# 章节说明 / 补充提示通常不长，只做极端上限
WRITE_DESC_AUX_MAX = 12000
# 参考正文只学写法，不必整章原文
WRITE_REF_BUDGET = 8000
# 要素取值：全章合计、单条上限
WRITE_ELEMENT_TOTAL = 16000
WRITE_ELEMENT_EACH_MAX = 5000
WRITE_ELEMENT_EACH_MIN = 800
WRITE_EVIDENCE_EACH = 1200
WRITE_EVIDENCE_TOTAL = 8000
WRITE_CONTENT_BASIS_TOTAL = 4000
WRITE_CONTENT_BASIS_EACH = 500

_CHUNK_SPLIT = re.compile(r"\n{2,}|(?<=[。！？；])")
_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{3,}")


def _match_needles(queries: list[str]) -> list[str]:
    """查询词切成可匹配片段（含 4 字窗），避免整句要点对不上要素里的短句。"""
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for tok in _TOKEN.findall(q or ""):
            pieces = [tok]
            if len(tok) > 4:
                pieces.extend(tok[i : i + 4] for i in range(0, len(tok) - 3))
            for piece in pieces:
                if piece not in seen:
                    seen.add(piece)
                    out.append(piece)
    return out[:80]


def clip_text(text: str, limit: int, *, note: str = "") -> str:
    raw = str(text or "")
    if limit <= 0 or len(raw) <= limit:
        return raw
    keep = max(0, limit - 24)
    tail = str(note or f"（原文 {len(raw)} 字，已截取前部）")
    return raw[:keep].rstrip() + "\n" + tail


def queries_for_write(requirement: ChapterRequirement, extra: list[str] | None = None) -> list[str]:
    bits = [
        str(getattr(requirement, "title", "") or ""),
        str(getattr(requirement, "describes", "") or ""),
        *[str(p) for p in (getattr(requirement, "points", None) or [])],
        *(extra or []),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for raw in bits:
        text = str(raw or "").strip()
        if len(text) >= 4:
            key = re.sub(r"\s+", "", text)[:80]
            if key and key not in seen:
                seen.add(key)
                out.append(text[:80])
        for part in re.split(r"[\n；;。，,]", text):
            part = part.strip()
            if len(part) < 4:
                continue
            key = re.sub(r"\s+", "", part)[:40]
            if key and key not in seen:
                seen.add(key)
                out.append(part[:40])
    return out[:48]


def _chunks(text: str, size: int = 480) -> list[tuple[int, str]]:
    raw = str(text or "")
    parts = [p.strip() for p in _CHUNK_SPLIT.split(raw) if p and p.strip()]
    if not parts:
        return [(0, raw)] if raw else []
    packed: list[tuple[int, str]] = []
    buf = ""
    start = 0
    cursor = 0
    for part in parts:
        if buf and len(buf) + len(part) > size:
            packed.append((start, buf.strip()))
            cursor += len(buf)
            start = cursor
            buf = part
        else:
            if not buf:
                start = cursor
            buf = f"{buf}{part}" if not buf else f"{buf}{part}"
        cursor = start + len(buf)
    if buf.strip():
        packed.append((start, buf.strip()))
    return packed or [(0, raw)]


def excerpt_by_queries(text: str, queries: list[str], limit: int) -> tuple[str, bool]:
    """按查询词挑段落；不超过 limit。短文原样返回。"""
    raw = str(text or "").strip()
    if not raw:
        return "", False
    if limit <= 0:
        return "", True
    if len(raw) <= limit:
        return raw, False
    queries = [str(q).strip() for q in queries if str(q).strip()]
    chunks = _chunks(raw)
    scored: list[tuple[float, int, str]] = []
    needles = _match_needles(queries)
    for idx, (_pos, chunk) in enumerate(chunks):
        score = 0.0
        for q in queries:
            if q and q in chunk:
                score += min(12.0, len(q) / 4)
        for tok in needles:
            if tok and tok in chunk:
                score += min(6.0, len(tok) / 3)
        if idx == 0:
            score += 1.5  # 文首保留项目身份信息
        scored.append((score, idx, chunk))
    scored.sort(key=lambda row: (-row[0], row[1]))
    picked: dict[int, str] = {}
    used = 0
    marker = f"\n（原文共 {len(raw)} 字，已按本章要点摘录；未列出段落不要当事实编造）"
    room = max(80, limit - len(marker))
    for score, idx, chunk in scored:
        if score <= 0 and picked:
            continue
        piece = chunk if len(chunk) <= room - used else chunk[: max(0, room - used)]
        if not piece:
            break
        picked[idx] = piece
        used += len(piece) + 1
        if used >= room:
            break
    if not picked:
        head = raw[: room // 2]
        tail = raw[-(room - len(head)) :] if room > len(head) else ""
        body = head if not tail or tail in head else f"{head}\n……\n{tail}"
        return (body + marker).strip(), True
    ordered = [picked[i] for i in sorted(picked)]
    joined = "\n……\n".join(ordered) if len(ordered) > 1 else ordered[0]
    return (joined + marker).strip(), True


def _fair_limit(n: int, total: int, each_max: int, each_min: int) -> int:
    if n <= 0:
        return each_max
    share = max(each_min, total // n)
    return min(each_max, share)


def pack_element_rows(
    rows: list[Any],
    *,
    queries: list[str],
    total_budget: int = WRITE_ELEMENT_TOTAL,
    each_max: int = WRITE_ELEMENT_EACH_MAX,
    prefer_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """要素全文仍在调用方对象上；返回给模型的副本按预算摘录。"""
    items = [dict(row) for row in rows if isinstance(row, dict)]
    prefer = prefer_ids or set()

    def _len(row: dict[str, Any]) -> int:
        return len(str(row.get("value") or "").strip())

    long_n = sum(1 for row in items if _len(row) > WRITE_ELEMENT_EACH_MIN)
    each = _fair_limit(max(1, long_n), total_budget, each_max, WRITE_ELEMENT_EACH_MIN)
    used = 0
    out: list[dict[str, Any]] = []

    def take(row: dict[str, Any]) -> dict[str, Any]:
        nonlocal used
        copied = dict(row)
        value = str(copied.get("value") or "").strip()
        if not value:
            copied["value_omitted"] = 0
            return copied
        remain = max(0, total_budget - used)
        if remain <= 0:
            copied["value"] = "（要素原文过长，本章预算已用完，取值未再展开）"
            copied["value_omitted"] = len(value)
            return copied
        cap = min(each, remain, each_max)
        packed, omitted = excerpt_by_queries(value, queries, cap)
        copied["value"] = packed
        copied["value_omitted"] = len(value) if omitted else 0
        used += len(packed)
        return copied

    preferred = [row for row in items if str(row.get("id") or "").strip() in prefer]
    rest = [row for row in items if str(row.get("id") or "").strip() not in prefer]
    for row in preferred:
        out.append(take(row))
    for row in rest:
        out.append(take(row))
    return out


def pack_point_write_plan(
    plan: list[dict[str, Any]],
    *,
    queries: list[str],
    packed_elements: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    by_id = {
        str(row.get("id") or "").strip(): row
        for row in (packed_elements or [])
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    out: list[dict[str, Any]] = []
    for row in plan or []:
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        eid = str(copied.get("element_id") or "").strip()
        packed = by_id.get(eid)
        if packed is not None:
            copied["element_value"] = str(packed.get("value") or "")
            copied["value_omitted"] = int(packed.get("value_omitted") or 0)
        else:
            raw = str(copied.get("element_value") or "").strip()
            packed_val, omitted = excerpt_by_queries(raw, queries, WRITE_ELEMENT_EACH_MAX)
            copied["element_value"] = packed_val
            copied["value_omitted"] = len(raw) if omitted else 0
        out.append(copied)
    return out


def pack_evidence_dicts(cards: list[dict[str, Any]], *, queries: list[str]) -> list[dict[str, Any]]:
    used = 0
    out: list[dict[str, Any]] = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        copied = dict(card)
        excerpt = str(copied.get("excerpt") or "")
        remain = max(0, WRITE_EVIDENCE_TOTAL - used)
        cap = min(WRITE_EVIDENCE_EACH, remain)
        packed, _omitted = excerpt_by_queries(excerpt, queries, cap if cap else 0)
        if cap <= 0:
            packed = ""
        copied["excerpt"] = packed
        used += len(packed)
        out.append(copied)
    return out


def bind_content_basis(requirement: ChapterRequirement, lines: list[str] | None) -> None:
    """把对话原文挂到本章要求上，不改要素取值。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines or []:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text[:WRITE_CONTENT_BASIS_EACH])
    requirement.content_basis = out[-24:]


def assemble_write_materials(
    requirement: ChapterRequirement,
    *,
    point_write_plan: list[dict] | None = None,
    reference_text: str = "",
    aux_prompt: str = "",
    element_results: list[dict] | None = None,
    content_basis: list[str] | None = None,
) -> str:
    """拼给模型的本章材料。要素/参考过长时用摘录，章节说明与补充提示尽量给全。"""
    describes = clip_text(str(getattr(requirement, "describes", "") or "").strip(), WRITE_DESC_AUX_MAX)
    aux = clip_text(
        str(aux_prompt or getattr(requirement, "aux_prompt", "") or "").strip(),
        WRITE_DESC_AUX_MAX,
    )
    ref = str(reference_text or getattr(requirement, "reference_text", "") or "").strip()
    queries = queries_for_write(requirement)
    ref_packed, ref_omitted = excerpt_by_queries(ref, queries, WRITE_REF_BUDGET)
    if ref_omitted and "原文共" not in ref_packed:
        ref_packed = clip_text(ref, WRITE_REF_BUDGET, note=f"（参考正文共 {len(ref)} 字，只保留学写法所需片段）")

    plan = [row for row in (point_write_plan or []) if isinstance(row, dict)]
    point_blocks: list[str] = []
    if plan:
        for row in plan:
            idx = row.get("index") or ""
            point = str(row.get("point") or "").strip()
            name = str(row.get("element_name") or "").strip()
            value = str(row.get("element_value") or "").strip()
            omitted = int(row.get("value_omitted") or 0)
            inner = [f'<point index="{idx}">', f"<instruction>{point}</instruction>"]
            if name or value:
                omit_attr = f' source_chars="{omitted}"' if omitted else ""
                inner.append(
                    f'<linked_element name="{name or "未命名"}"{omit_attr}>'
                    f"{value or '（无取值）'}</linked_element>"
                )
            inner.append("</point>")
            point_blocks.append("\n".join(inner))
    else:
        points = [str(p).strip() for p in (requirement.points or []) if str(p).strip()]
        if points:
            for i, p in enumerate(points, 1):
                point_blocks.append(f'<point index="{i}">\n<instruction>{p}</instruction>\n</point>')
        else:
            point_blocks.append("（未配置）")

    chapter_els = [
        row
        for row in (element_results if element_results is not None else getattr(requirement, "element_results", None) or [])
        if isinstance(row, dict)
    ]
    bound_ids = {str(row.get("element_id") or "").strip() for row in plan if str(row.get("element_id") or "").strip()}
    el_blocks: list[str] = []
    for row in chapter_els:
        eid = str(row.get("id") or "").strip()
        name = str(row.get("name") or eid or "要素").strip()
        kind = str(row.get("kind") or "").strip()
        kind_attr = f' kind="{kind}"' if kind else ""
        if eid and eid in bound_ids:
            el_blocks.append(f'<element name="{name}"{kind_attr}>取值见 writing_points 中同名 linked_element</element>')
            continue
        value = str(row.get("value") or "").strip()
        omitted = int(row.get("value_omitted") or 0)
        omit_attr = f' source_chars="{omitted}"' if omitted else ""
        el_blocks.append(
            f'<element name="{name}"{kind_attr}{omit_attr}>{value or "（无取值）"}</element>'
        )
    if not el_blocks:
        el_blocks.append("（未关联章节要素）")

    basis_src = (
        content_basis
        if content_basis is not None
        else list(getattr(requirement, "content_basis", None) or [])
    )
    basis_blocks: list[str] = []
    used_basis = 0
    for raw in basis_src:
        text = str(raw or "").strip()
        if not text:
            continue
        packed = clip_text(text, WRITE_CONTENT_BASIS_EACH)
        if used_basis + len(packed) > WRITE_CONTENT_BASIS_TOTAL:
            break
        used_basis += len(packed)
        basis_blocks.append(f"<utterance>{packed}</utterance>")
    if not basis_blocks:
        basis_blocks.append("（对话中尚未提供额外事实）")

    title = str(getattr(requirement, "title", "") or "").strip()
    return "\n".join(
        [
            "<writing_materials>",
            "<note>章节说明、补充提示、要点为原文；要素与参考正文过长时已按本章要点摘录。对话原文与配置要素同为内容依据，不得据此新建要素。未列出内容不得当作事实。</note>",
            f"<chapter_title>{title or '（无标题）'}</chapter_title>",
            "<chapter_spec>",
            describes or "（未填写）",
            "</chapter_spec>",
            "<extra_prompt>",
            aux or "（未填写）",
            "</extra_prompt>",
            "<writing_points>",
            *point_blocks,
            "</writing_points>",
            "<chapter_elements>",
            *el_blocks,
            "</chapter_elements>",
            "<dialogue_content_basis>",
            *basis_blocks,
            "</dialogue_content_basis>",
            "<reference_text>",
            ref_packed or "（无）",
            "</reference_text>",
            "</writing_materials>",
        ]
    )
