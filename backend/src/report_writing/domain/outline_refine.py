"""多轮目录：第一轮规则粗抽后，按章切段再发现子节（规则 + 可选 LLM）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from report_writing.domain.chapter_locator import build_heading_index, locate_chapter
from report_writing.domain.models import OutlineNode, OutlineTree
from report_writing.domain.outline import (
    discover_descendant_headings,
    finalize_outline,
    insert_outline_nodes,
    is_descendant_sequence,
    parent_sequence_of,
    _level_from_sequence,
    _sequence_sort_key,
    _short_heading_title,
)

_log = logging.getLogger("uvicorn.error")

EmitFn = Callable[[dict[str, Any]], None]


def _merge_discovered_nodes(primary: list[OutlineNode], extra: list[OutlineNode]) -> list[OutlineNode]:
    by_seq: dict[str, OutlineNode] = {}
    for item in list(primary or []) + list(extra or []):
        seq = (item.sequence or "").strip()
        title = (item.title or "").strip()
        if not seq or not title:
            continue
        prev = by_seq.get(seq)
        if prev is None or (len(title) < len(prev.title or "") and len(title) <= 40):
            by_seq[seq] = OutlineNode(
                sequence=seq,
                title=title[:80],
                level=_level_from_sequence(seq),
                parent_sequence=parent_sequence_of(seq),
            )
    return sorted(by_seq.values(), key=lambda n: _sequence_sort_key(n.sequence))


def llm_discover_descendants(
    text: str,
    parent_sequences: list[str],
    existing_sequences: set[str],
    *,
    parent_titles: dict[str, str] | None = None,
) -> list[OutlineNode]:
    """从正文片段中提取父序号下的子章节目录（单章/多父章均可）。"""
    from report_writing.domain.chapter_locator import focus_text_for_parents
    from report_writing.infra.llm import live_model

    model = live_model()
    parents = [p for p in parent_sequences if p]
    if model is None or not parents or not (text or "").strip():
        return []
    titles = parent_titles or {}
    focused = focus_text_for_parents(
        text,
        [(seq, titles.get(seq, "")) for seq in parents],
        max_chars_each=12000,
        max_total=28000,
    )
    parent_hint = "、".join(parents)
    existing_hint = "、".join(sorted(existing_sequences)[:120])
    prompt = (
        f"下面是报告正文片段（已按父章切段）。请提取「{parent_hint}」之下的全部子章节目录"
        "（含多级，如 2.3.1、2.3.2.1）。"
        "只返回 JSON 数组，不要解释。每项字段：sequence、title。"
        "title 只要章节短标题（一般不超过 24 字），不要正文。"
        "不要输出父章节自身，不要编造正文中没有的序号。\n"
        f"已有序号（勿重复）：{existing_hint or '无'}\n\n"
        f"{focused}"
    )
    try:
        message = model.invoke(prompt)
        content = getattr(message, "content", None) or str(message)
        match = re.search(r"\[[\s\S]*\]", content)
        payload = json.loads(match.group(0) if match else content)
        if not isinstance(payload, list):
            return []
    except Exception as error:
        _log.warning("按章模型提取子目录失败：%s", error)
        return []

    nodes: list[OutlineNode] = []
    seen: set[str] = set()
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        sequence = str(raw.get("sequence") or "").strip()
        title = _short_heading_title(str(raw.get("title") or "").strip())
        if not sequence or not title or sequence in existing_sequences or sequence in seen:
            continue
        if not any(is_descendant_sequence(sequence, parent) for parent in parents):
            continue
        seen.add(sequence)
        nodes.append(
            OutlineNode(
                sequence=sequence,
                title=title,
                level=_level_from_sequence(sequence),
                parent_sequence=parent_sequence_of(sequence),
            )
        )
    return nodes


def expand_outline_chapterwise(
    full_text: str,
    rough: OutlineTree,
    *,
    use_llm: bool = False,
    max_chars_per_chapter: int = 80000,
    on_event: EmitFn | None = None,
) -> OutlineTree:
    """第二轮：按粗目录逐章切正文，规则发现子节；use_llm 时再走模型补漏。"""
    text = (full_text or "").strip()
    if not text or rough is None or not rough.chapters:
        return rough

    def emit(title: str, **extra: Any) -> None:
        if on_event is None:
            return
        on_event(
            {
                "type": "step",
                "step": "outline",
                "status": "running",
                "title": title,
                **extra,
            }
        )

    emit("第二轮目录：按章扫描正文（规则匹配）", round=2, phase="chapter_scan")
    chapters = list(rough.chapters)
    existing = {(item.sequence or "").strip() for item in chapters if (item.sequence or "").strip()}
    hits = build_heading_index(text)

    queue: list[OutlineNode] = sorted(
        [item for item in chapters if (item.sequence or "").strip()],
        key=lambda n: (_level_from_sequence(n.sequence or ""), _sequence_sort_key(n.sequence or "")),
    )
    processed: set[str] = set()
    added_total = 0

    while queue:
        parent = queue.pop(0)
        pseq = (parent.sequence or "").strip()
        if not pseq or pseq in processed:
            continue
        processed.add(pseq)

        excerpt, matched = locate_chapter(
            text,
            sequence=pseq,
            title=(parent.title or "").strip(),
            index=hits,
            max_chars=max_chars_per_chapter,
            refine=True,
        )
        if not matched or not excerpt.strip():
            _log.debug("按章目录：未定位正文 parent=%s title=%s", pseq, parent.title)
            continue

        emit(
            f"扩展目录 {pseq} {(parent.title or '')[:20]}".strip(),
            round=2,
            parent_sequence=pseq,
        )
        regex_hits = discover_descendant_headings(excerpt, [pseq], existing)
        merged = list(regex_hits)
        if use_llm:
            llm_hits = llm_discover_descendants(
                excerpt,
                [pseq],
                existing,
                parent_titles={pseq: (parent.title or "").strip()},
            )
            merged = _merge_discovered_nodes(regex_hits, llm_hits)

        newcomers: list[OutlineNode] = []
        for node in merged:
            seq = (node.sequence or "").strip()
            if not seq or seq in existing:
                continue
            existing.add(seq)
            newcomers.append(node)

        if not newcomers:
            continue
        added_total += len(newcomers)
        chapters = insert_outline_nodes(chapters, newcomers)
        newcomers.sort(key=lambda n: (_level_from_sequence(n.sequence or ""), _sequence_sort_key(n.sequence or "")))
        queue.extend(newcomers)

    result = finalize_outline(chapters)
    emit(
        f"按章目录细化完成（新增 {added_total} 条）",
        round=2,
        status="done",
        result={"added": added_total, "total": len(result.chapters)},
    )
    _log.info(
        "目录第二轮完成 added=%s total=%s processed_parents=%s use_llm=%s",
        added_total,
        len(result.chapters),
        len(processed),
        use_llm,
    )
    return result
