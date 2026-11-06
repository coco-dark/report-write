"""目录树：从全文目录生成、校验、导入，并按确认后的树回原文切章。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from report_writing.domain.models import ChapterElement, OutlineNode, OutlineTree

_log = logging.getLogger("uvicorn.error")
_AUTO_KEY = re.compile(r"^(ch-\d+|ch-new-\d+|chapter_[a-f0-9]{6,12})$")
_SLUG_TOKEN = re.compile(r"[^a-z0-9_]+")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")

_TOC_LINE = re.compile(
    r"HYPERLINK\s+\\[lL]\s+\\_Toc\d+\s+"
    r"(?P<sequence>\d+(?:\.\d+)*)\s+"
    r"(?P<title>.+?)\s+PAGEREF",
    re.IGNORECASE,
)
_NUMBERED = re.compile(
    # 「2.3.1 标题」「2.标题」「2.3.1变电站站址」（中文报告常无空格）
    r"^(?P<sequence>\d+(?:\.\d+)*)"
    r"(?:[.\s　]+|(?=[^\d.\s]))"
    r"(?P<title>\S.{0,80})$"
)
# 标题与正文粘在同一行时：只取序号后的短中文标题（到空白/句读/正文起笔为止）
_NUMBERED_INLINE = re.compile(
    r"^(?P<sequence>\d+(?:\.\d+)*)"
    r"[.\s　]*"
    r"(?P<title>[\u4e00-\u9fffA-Za-z0-9（）()]{2,40}?)"
    r"(?:$|[\s　]+|[，。；：]|(?=本工程|本站|本变|该|此处|根据|采用))"
)
_CHAPTER = re.compile(
    r"^(?P<sequence>第[0-9一二三四五六七八九十百零]+[章节篇]|[一二三四五六七八九十]+)"
    r"[章节篇、.．\s　]+"
    r"(?P<title>\S.{0,80})$"
)
_BOLD_TITLE = re.compile(r"^\*\*(?P<title>.+?)\*\*$")
_ATX_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<body>.+?)\s*$")
_TOC_HEAD = re.compile(r"(?:^|\n)\s*(?:\*\*)?目\s*录(?:\*\*)?|\bTOC\b|\\O\s*\"?\d", re.I)
_PAGE_TAIL_LINE = re.compile(r"\s+\d{1,4}\s*$")
_TOC_TAB_LINE = re.compile(
    r"^(?P<sequence>\d+(?:\.\d+)*)"
    r"[\s　]+(?P<title>.+?)"
    r"(?:[\s　]{2,}|\t+|[·．.]{2,})\s*\d{1,4}\s*$"
)


def _toc_max_chars(max_chars: int | None) -> int:
    if max_chars is not None and max_chars > 0:
        return max_chars
    from report_writing.infra.config_settings import outline_toc_max_chars

    return outline_toc_max_chars()


def slice_toc_region(text: str, *, max_chars: int | None = None) -> tuple[str, dict]:
    """从长文中切出目录候选区，避免超长正文污染抽目录。

    策略：HYPERLINK 目录块 > 「目录」标题后的短编号行 > 文首密集短标题带。
    返回 (切片文本, 元信息)。
    """
    raw = text or ""
    limit = _toc_max_chars(max_chars)
    meta = {"strategy": "full_head", "start": 0, "end": 0, "source_chars": len(raw), "max_chars": limit}
    if not raw.strip():
        return "", meta

    hyper_hits = list(_TOC_LINE.finditer(raw))
    if len(hyper_hits) >= 3:
        start = max(0, hyper_hits[0].start() - 80)
        end = min(len(raw), hyper_hits[-1].end() + 40)
        chunk = raw[start:end]
        meta.update({"strategy": "hyperlink_toc", "start": start, "end": end, "entries": len(hyper_hits)})
        return chunk[:limit], meta

    head = _TOC_HEAD.search(raw[: min(len(raw), 80000)])
    if head:
        start = head.start()
        end = _toc_region_end(raw, start, max_span=limit)
        chunk = raw[start:end]
        meta.update({"strategy": "toc_heading", "start": start, "end": end})
        return chunk[:limit], meta

    dense = _dense_heading_prefix(raw, max_chars=limit)
    if dense:
        meta.update({"strategy": "dense_prefix", "start": 0, "end": len(dense)})
        return dense, meta

    chunk = raw[: min(len(raw), limit)]
    meta.update({"strategy": "full_head", "start": 0, "end": len(chunk)})
    return chunk, meta


def outline_from_text(text: str) -> OutlineTree:
    """抽目录优先级：HYPERLINK 域 > 目录区编号行 > Markdown ATX 标题。

    长文（十余万字）时不对全文扫编号行，但目录区切片可长达数百章（见 outline_toc_max_chars）。
    少量文首 # 标题不得压过「目录」页上百条编号行。
    """
    raw = text or ""
    hyper = _from_toc_domain(raw)
    if len(hyper) >= 3:
        _log.info("目录抽取 strategy=hyperlink entries=%s", len(hyper))
        return finalize_outline(hyper)

    toc_slice, meta = slice_toc_region(raw)
    numbered = _from_numbered_lines(toc_slice)
    if meta.get("strategy") == "toc_heading" and meta.get("start", 0) >= 0:
        start = int(meta["start"])
        limit = int(meta.get("max_chars") or _toc_max_chars(None))
        wide_end = min(len(raw), start + limit)
        if wide_end > len(toc_slice) + 500:
            wide = _from_numbered_lines(raw[start:wide_end])
            if len(wide) > len(numbered):
                numbered = wide

    if meta.get("strategy") in {"toc_heading", "hyperlink_toc", "dense_prefix"}:
        atx = _from_markdown_atx_headings(toc_slice)
        if len(atx) < 3:
            atx = _from_markdown_atx_headings(raw)
    else:
        atx = _from_markdown_atx_headings(raw)
    nodes, strategy = _pick_outline_nodes(numbered, atx, toc_meta=meta)
    _log.info(
        "目录抽取 strategy=%s toc_slice=%s entries=%s source_chars=%s",
        strategy,
        meta.get("strategy"),
        len(nodes),
        len(raw),
    )
    if not nodes:
        limit = int(meta.get("max_chars") or _toc_max_chars(None))
        head = raw[:limit]
        if head != toc_slice:
            nodes = _from_numbered_lines(head)
            strategy = "numbered_head"
    if not nodes and atx and meta.get("strategy") not in {"toc_heading", "hyperlink_toc", "dense_prefix"}:
        nodes = atx
        strategy = "markdown_atx_fallback"
    if not nodes:
        raise ValueError("全文里没有可用的目录条目")
    nodes = _prune_body_like_titles(nodes)
    if meta.get("strategy") in {"toc_heading", "dense_prefix", "hyperlink_toc"} and toc_slice:
        nodes = _filter_nodes_to_toc_region(nodes, toc_slice)
    return finalize_outline(nodes)


def outline_tree_quality(tree: OutlineTree | None) -> float:
    """启发式质量分：用于在 RAG 规则 / Word 样式等多路结果里择优。"""
    nodes = list(getattr(tree, "chapters", None) or [])
    if not nodes:
        return 0.0
    n = len(nodes)
    seq_ok = sum(
        1 for item in nodes if re.fullmatch(r"\d+(?:\.\d+)*", (item.sequence or "").strip())
    )
    sequences = [(item.sequence or "").strip() for item in nodes if (item.sequence or "").strip()]
    dup = n - len(set(sequences))
    long_title = sum(1 for item in nodes if len((item.title or "").strip()) > 42)
    level1 = sum(1 for item in nodes if int(item.level or 1) == 1)
    nested = sum(1 for item in nodes if int(item.level or 1) >= 2)
    flat_penalty = 0.0
    if n >= 20 and level1 / n > 0.85:
        flat_penalty = 15.0
    score = (seq_ok / n) * 45.0 + min(n, 400) / 400.0 * 35.0 + min(nested, 300) / 300.0 * 18.0
    score -= dup * 4.0 + (long_title / n) * 25.0 + flat_penalty
    return max(0.0, score)


def outline_from_docx_path(path: Path) -> OutlineTree | None:
    """从 Word 标题样式 / outlineLvl 抽章节（常比 RAG 扁平 Markdown 更准）。"""
    if path.suffix.lower() != ".docx" or not path.is_file():
        return None
    from report_writing.services.documents import _read_docx_blocks, chapters_from_blocks

    blocks = _read_docx_blocks(path)
    styled = sum(1 for block in blocks if int(block.get("level") or 0) > 0)
    if styled < 5:
        return None
    chapters = chapters_from_blocks(blocks)
    payload = []
    for item in chapters:
        title = str(item.get("title") or "").strip()
        if not title or title == "正文" or _title_looks_like_body(title):
            continue
        payload.append(
            {
                "sequence": str(item.get("sequence") or "").strip(),
                "title": title,
                "level": int(item.get("level") or 1),
                "parent": str(item.get("parent") or "").strip(),
            }
        )
    if len(payload) < 5:
        return None
    try:
        return outline_from_payload(payload)
    except ValueError:
        return None


def _outline_tree_viable(tree: OutlineTree | None) -> bool:
    if tree is None or not tree.chapters:
        return False
    n = len(tree.chapters)
    numeric = sum(
        1
        for item in tree.chapters
        if re.fullmatch(r"\d+(?:\.\d+)*", (item.sequence or "").strip())
    )
    if numeric == 0:
        return n <= 3
    ratio = numeric / n
    if n >= 10 and ratio >= 0.65:
        return True
    if n >= 6 and ratio >= 0.8:
        return True
    return n >= 4 and ratio >= 0.95


def _toc_entry_count(tree: OutlineTree | None) -> int:
    """像正式目录条目的编号短标题数量。"""
    if tree is None:
        return 0
    return sum(
        1
        for item in tree.chapters
        if re.fullmatch(r"\d+(?:\.\d+)*", (item.sequence or "").strip())
        and len((item.title or "").strip()) <= 42
        and not _title_looks_like_body(item.title or "")
    )


def _nested_ratio(tree: OutlineTree | None) -> float:
    nodes = list(getattr(tree, "chapters", None) or [])
    if not nodes:
        return 0.0
    nested = sum(1 for item in nodes if int(item.level or 1) >= 2)
    return nested / len(nodes)


def choose_best_outline(*candidates: tuple[str, OutlineTree | None]) -> tuple[str, OutlineTree] | None:
    viable: list[tuple[str, OutlineTree, float, int, float]] = []
    rules_tree: OutlineTree | None = None
    for label, tree in candidates:
        if tree is None or not tree.chapters:
            continue
        if not _outline_tree_viable(tree):
            _log.info("目录候选不可用 source=%s chapters=%s", label, len(tree.chapters))
            continue
        if label == "rules":
            rules_tree = tree
        viable.append(
            (
                label,
                tree,
                outline_tree_quality(tree),
                _toc_entry_count(tree),
                _nested_ratio(tree),
            )
        )
    if not viable:
        return None
    best_count = max(item[3] for item in viable)
    # 明显更完整的编号目录优先，避免 40 章「干净树」压过 200+ 章正式目录
    complete = [item for item in viable if item[3] >= max(8, int(best_count * 0.82))]
    pool = complete or viable
    pool.sort(key=lambda item: (item[3], item[2], item[4]), reverse=True)
    label, tree, _score, count, nested = pool[0]
    if label == "docx" and rules_tree and _toc_entry_count(rules_tree) >= 8:
        rules_n = _toc_entry_count(rules_tree)
        rules_nested = _nested_ratio(rules_tree)
        if count > rules_n * 1.8 and nested < 0.35 and rules_nested >= 0.4:
            return ("rules", rules_tree)
    return (label, tree)


def _sequence_sort_key(sequence: str) -> tuple[int, ...]:
    seq = (sequence or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*", seq):
        return (999999,)
    return tuple(int(part) for part in seq.split("."))


def _title_looks_like_body(title: str) -> bool:
    text = (title or "").strip()
    if not text:
        return True
    if len(text) > 48 or text.endswith(("。", "；", "：")):
        return True
    if text.count("，") >= 2 or text.count("。") >= 1:
        return True
    return False


def nodes_list_quality(nodes: list[OutlineNode]) -> float:
    if not nodes:
        return 0.0
    n = len(nodes)
    numeric = [
        item
        for item in nodes
        if re.fullmatch(r"\d+(?:\.\d+)*", (item.sequence or "").strip())
    ]
    if not numeric:
        return min(n, 40) / 40.0 * 15.0
    keys = [_sequence_sort_key(item.sequence) for item in numeric]
    mono = sum(1 for i in range(1, len(keys)) if keys[i] > keys[i - 1])
    mono_rate = mono / max(1, len(keys) - 1)
    body = sum(1 for item in nodes if _title_looks_like_body(item.title))
    score = (len(numeric) / n) * 40.0 + mono_rate * 35.0 + min(n, 400) / 400.0 * 25.0
    score -= (body / n) * 35.0
    return max(0.0, score)


def _prune_body_like_titles(nodes: list[OutlineNode]) -> list[OutlineNode]:
    kept = [item for item in nodes if not _title_looks_like_body(item.title)]
    return kept if len(kept) >= max(3, len(nodes) // 4) else list(nodes)


def _filter_nodes_to_toc_region(nodes: list[OutlineNode], toc_slice: str) -> list[OutlineNode]:
    """去掉正文里误匹配的标题，要求序号在目录区文本中出现。"""
    if not nodes or not (toc_slice or "").strip():
        return nodes
    blob = toc_slice
    kept: list[OutlineNode] = []
    for item in nodes:
        seq = (item.sequence or "").strip()
        title = (item.title or "").strip()
        if not seq:
            kept.append(item)
            continue
        if seq not in blob:
            continue
        if title and title[: min(8, len(title))] not in blob:
            short = title[:4]
            if short and short not in blob:
                continue
        kept.append(item)
    if len(kept) >= max(8, int(len(nodes) * 0.35)):
        return kept
    return nodes


def _pick_outline_nodes(
    numbered: list[OutlineNode],
    atx: list[OutlineNode],
    *,
    toc_meta: dict | None = None,
) -> tuple[list[OutlineNode], str]:
    n_count = len(numbered or [])
    a_count = len(atx or [])
    nq = nodes_list_quality(numbered or [])
    aq = nodes_list_quality(atx or [])
    strategy = str((toc_meta or {}).get("strategy") or "")
    if n_count >= 10 and nq >= max(aq * 0.8, 25.0):
        return list(numbered or []), "numbered_toc"
    if strategy in {"toc_heading", "dense_prefix"} and n_count >= 3 and nq >= aq:
        return list(numbered or []), "numbered_toc"
    if n_count >= max(a_count, 3) and nq >= aq * 0.85:
        return list(numbered or []), "numbered_toc"
    if a_count >= 3 and aq > nq * 1.1:
        return list(atx or []), "markdown_atx"
    if n_count >= max(a_count, 3):
        return list(numbered or []), "numbered_toc"
    if a_count >= 3:
        return list(atx or []), "markdown_atx"
    if n_count > 0:
        return list(numbered), "numbered_sparse"
    return [], "none"


def _from_markdown_atx_headings(text: str) -> list[OutlineNode]:
    """从 RAG Markdown 的 # / ## 标题抽章节（如「# 1 **概述**」）。"""
    nodes: list[OutlineNode] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        raw = line.strip()
        match = _ATX_HEADING.match(raw)
        if not match:
            continue
        body = re.sub(r"\*+", "", match.group("body") or "").strip()
        body = re.sub(r"\s+", " ", body)
        seq_match = re.match(
            r"^(?P<sequence>\d+(?:\.\d+)*)"
            r"(?:[.\s　]+|(?=[^\d.\s]))"
            r"(?P<title>.+)$",
            body,
        )
        if not seq_match:
            # 「# 概述」无序号：用 # 深度当 level，序号留空则 finalize 难挂树，跳过
            continue
        sequence = (seq_match.group("sequence") or "").strip()
        title = _short_heading_title(_clean_title(seq_match.group("title") or ""))
        if not sequence or not title or sequence in seen:
            continue
        if title.endswith(("。", "；")) or len(title) > 40:
            continue
        seen.add(sequence)
        level_from_marks = len(match.group("marks") or "#")
        level_from_seq = _level_from_sequence(sequence)
        nodes.append(
            OutlineNode(
                sequence=sequence,
                title=title,
                level=max(level_from_seq, min(level_from_marks, 6)),
            )
        )
    return nodes


def _toc_region_end(text: str, start: int, *, max_span: int | None = None) -> int:
    """目录区终点：连续短标题后出现长正文，或一级标题二次出现。"""
    span_cap = _toc_max_chars(max_span)
    lines = (text or "")[start:].splitlines(keepends=True)
    if not lines:
        return min(len(text), start + 8000)
    seen_level1: set[str] = set()
    offset = start
    numbered_run = 0
    body_run = 0
    for index, line in enumerate(lines):
        offset += len(line)
        plain = re.sub(r"\*+", "", line).strip()
        if not plain:
            continue
        if index < 2 and re.search(r"目\s*录|\bTOC\b", plain, re.I):
            continue
        numbered = bool(_NUMBERED_INLINE.match(plain) or _NUMBERED.match(plain) or _CHAPTER.match(plain))
        page_tail = bool(_PAGE_TAIL_LINE.search(plain))
        hyper = bool(_TOC_LINE.search(line))
        # 长标题目录行放宽长度，避免几百章目录被误判为正文
        short_limit = 120 if numbered_run >= 8 else 48
        is_short = len(plain) <= short_limit
        if hyper or ((numbered or page_tail) and (is_short or numbered)):
            numbered_run += 1
            body_run = 0
            match = _NUMBERED_INLINE.match(plain) or _NUMBERED.match(plain)
            if match:
                seq = (match.group("sequence") or "").strip()
                if re.fullmatch(r"\d+", seq or ""):
                    if seq in seen_level1 and numbered_run >= 12:
                        return offset - len(line)
                    seen_level1.add(seq)
            continue
        if numbered_run >= 8:
            body_run += 1
            if body_run >= 4 and len(plain) >= 36:
                return offset - len(line)
            if offset - start >= span_cap:
                break
            continue
        if len(plain) >= 40 and numbered_run >= 3:
            return offset - len(line)
        if len(plain) >= 80:
            return offset - len(line)
        if offset - start >= span_cap:
            break
    return min(len(text), max(offset, start + 2000))


def _dense_heading_prefix(text: str, *, max_chars: int | None = None) -> str:
    """文首若连续出现较多短编号标题，则截到该带结束。"""
    limit = _toc_max_chars(max_chars)
    lines = (text or "").splitlines(keepends=True)
    if not lines:
        return ""
    count = 0
    end = 0
    for line in lines[:8000]:
        end += len(line)
        plain = re.sub(r"\*+", "", line).strip()
        if not plain:
            continue
        if len(plain) <= 48 and (_NUMBERED_INLINE.match(plain) or _NUMBERED.match(plain)):
            count += 1
            continue
        if count >= 8 and len(plain) >= 36:
            break
        if count < 8 and end > 6000:
            return ""
    if count < 8:
        return ""
    return (text or "")[: min(end, limit)]


def outline_from_payload(chapters: list[dict]) -> OutlineTree:
    if not chapters:
        raise ValueError("目录不能为空")
    nodes = []
    for index, item in enumerate(chapters, start=1):
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError(f"第 {index} 条缺少标题")
        sequence = str(item.get("sequence") or "").strip()
        level = int(item.get("level") or _level_from_sequence(sequence) or 1)
        parent_sequence = str(item.get("parent_sequence") or "").strip()
        parent = str(item.get("parent") or "").strip()
        points = item.get("points") or []
        if isinstance(points, str):
            points = [line.strip() for line in points.splitlines() if line.strip()]
        else:
            points = [str(line).strip() for line in points if str(line).strip()]
        elements = []
        for raw in item.get("elements") or []:
            if isinstance(raw, ChapterElement):
                elements.append(raw)
            elif isinstance(raw, dict) and (raw.get("name") or raw.get("kind")):
                elements.append(ChapterElement.model_validate(raw))
        material_files = [str(name).strip() for name in (item.get("material_files") or []) if str(name).strip()]
        knowledge_folder_ids = [
            str(name).strip() for name in (item.get("knowledge_folder_ids") or []) if str(name).strip()
        ]
        keywords = item.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [line.strip() for line in keywords.splitlines() if line.strip()]
        else:
            keywords = [str(line).strip() for line in keywords if str(line).strip()]
        element_ids = [str(name).strip() for name in (item.get("element_ids") or []) if str(name).strip()]
        from report_writing.domain.point_items_sync import chapter_point_items_from_payload

        point_items = chapter_point_items_from_payload(item)
        if point_items:
            synced_points = [str(row.get("text") or "").strip() for row in point_items if str(row.get("text") or "").strip()]
            if synced_points:
                points = synced_points
        from report_writing.domain.content_blocks import normalize_content_blocks
        from report_writing.domain.models import ContentBlock

        content_blocks = [
            ContentBlock.model_validate(block)
            for block in normalize_content_blocks(item.get("content_blocks"))
        ]
        nodes.append(
            OutlineNode(
                key=str(item.get("key") or ""),
                sequence=sequence,
                title=title,
                level=max(1, level),
                parent=parent,
                parent_sequence=parent_sequence,
                points=points,
                point_items=point_items,
                keywords=keywords,
                describes=str(item.get("describes") or "").strip(),
                content_blocks=content_blocks,
                reference_text=str(item.get("reference_text") or "").strip(),
                aux_prompt=str(item.get("aux_prompt") or "").strip(),
                material_files=material_files,
                knowledge_folder_ids=knowledge_folder_ids,
                element_ids=element_ids,
                elements=elements,
                structure_only=bool(item.get("structure_only")),
                fixed_output=bool(item.get("fixed_output")),
                search_terms_separate=bool(item.get("search_terms_separate")),
            )
        )
    if not all(str(n.key or "").strip() for n in nodes):
        nodes = assign_placeholder_keys(nodes)
    return finalize_outline(nodes, preserve_parents=True)


def outline_from_brief_chapters(chapters: list) -> OutlineTree:
    payload = [
        {
            "key": item.key,
            "sequence": item.sequence,
            "title": item.title,
            "level": item.level,
            "parent": item.parent,
            "points": list(getattr(item, "points", None) or []),
            "keywords": list(getattr(item, "keywords", None) or []),
            "describes": getattr(item, "describes", "") or "",
            "content_blocks": [
                block.to_dict() if hasattr(block, "to_dict") else block
                for block in (getattr(item, "content_blocks", None) or [])
            ],
            "reference_text": getattr(item, "reference_text", "") or "",
            "aux_prompt": getattr(item, "aux_prompt", "") or "",
            "material_files": list(getattr(item, "material_files", None) or []),
            "knowledge_folder_ids": list(getattr(item, "knowledge_folder_ids", None) or []),
            "element_ids": list(getattr(item, "element_ids", None) or []),
            "structure_only": bool(getattr(item, "structure_only", False)),
            "fixed_output": bool(getattr(item, "fixed_output", False)),
            "search_terms_separate": bool(getattr(item, "search_terms_separate", False)),
            "elements": [
                element.to_dict() if hasattr(element, "to_dict") else element
                for element in (getattr(item, "elements", None) or [])
            ],
        }
        for item in chapters
    ]
    return outline_from_payload(payload)


def is_auto_key(key: str | None) -> bool:
    cleaned = (key or "").strip()
    return not cleaned or bool(_AUTO_KEY.fullmatch(cleaned))


def normalize_slug(raw: str) -> str:
    text = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = _SLUG_TOKEN.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if text and text[0].isdigit():
        text = f"ch_{text}"
    return text[:64]


def slug_from_title(title: str, existing_keys: set[str] | None = None, *, translated: str | None = None) -> str:
    """章节名 → 英文蛇形占位符；冲突时追加 _2/_3。"""
    used = set(existing_keys or ())
    base = normalize_slug(translated or "")
    if not base:
        base = normalize_slug(title)
    if not base or _HAS_CJK.search(base):
        digest = hashlib.sha1((title or "chapter").encode("utf-8")).hexdigest()[:8]
        base = f"chapter_{digest}"
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def translate_titles_to_slugs(titles: list[str], *, model=None) -> list[str]:
    """一次模型调用把多个中文标题译成英文蛇形；失败则空串列表。"""
    cleaned = [str(item or "").strip() for item in titles]
    if not cleaned:
        return []
    if not any(_HAS_CJK.search(item) for item in cleaned):
        return [normalize_slug(item) for item in cleaned]
    try:
        from report_writing.infra.llm import or_live_model

        model = or_live_model(model)
    except Exception:
        model = None
    if model is None:
        return [""] * len(cleaned)
    numbered = "\n".join(f"{index}. {title}" for index, title in enumerate(cleaned, start=1))
    prompt = (
        "将下列中文章节标题译成英文蛇形标识（snake_case），只含小写字母、数字和下划线。"
        "返回与输入等长的 JSON 字符串数组，不要解释。\n\n"
        f"{numbered}"
    )
    try:
        message = model.invoke(prompt)
        content = getattr(message, "content", None) or str(message)
        match = re.search(r"\[[\s\S]*\]", content)
        payload = json.loads(match.group(0) if match else content)
        if not isinstance(payload, list):
            return [""] * len(cleaned)
        result = []
        for index, title in enumerate(cleaned):
            raw = str(payload[index]).strip() if index < len(payload) else ""
            result.append(normalize_slug(raw) if raw else "")
        return result
    except Exception as error:
        _log.warning("章节标题中译英失败，改用本地 slug：%s", error)
        return [""] * len(cleaned)


def assign_placeholder_keys(nodes: list[OutlineNode], *, model=None) -> list[OutlineNode]:
    """为空或自动 ch-* key 的节点按标题生成英文占位符，并重写 parent 引用。"""
    if not nodes:
        return []
    need_indexes = [index for index, node in enumerate(nodes) if is_auto_key(node.key)]
    translations = [""] * len(nodes)
    if need_indexes:
        batch = translate_titles_to_slugs(
            [nodes[index].title for index in need_indexes], model=model
        )
        for offset, index in enumerate(need_indexes):
            translations[index] = batch[offset] if offset < len(batch) else ""
    used: set[str] = set()
    for node in nodes:
        if not is_auto_key(node.key):
            used.add(node.key.strip())
    mapping: dict[str, str] = {}
    new_keys: list[str] = []
    for index, node in enumerate(nodes):
        old = (node.key or "").strip()
        if is_auto_key(old):
            new_key = slug_from_title(node.title, used, translated=translations[index] or None)
        else:
            new_key = old
        used.add(new_key)
        new_keys.append(new_key)
        if old and old != new_key:
            mapping[old] = new_key
    result = []
    for index, node in enumerate(nodes):
        parent = (node.parent or "").strip()
        if parent in mapping:
            parent = mapping[parent]
        result.append(
            OutlineNode(
                key=new_keys[index],
                sequence=node.sequence,
                title=node.title,
                level=node.level,
                parent=parent,
                parent_sequence=node.parent_sequence,
                points=list(node.points or []),
                keywords=list(getattr(node, "keywords", None) or []),
                describes=node.describes or "",
                content_blocks=list(getattr(node, "content_blocks", None) or []),
                reference_text=getattr(node, "reference_text", "") or "",
                aux_prompt=node.aux_prompt or "",
                material_files=list(node.material_files or []),
                knowledge_folder_ids=list(getattr(node, "knowledge_folder_ids", None) or []),
                element_ids=list(node.element_ids or []),
                elements=list(node.elements or []),
                structure_only=bool(getattr(node, "structure_only", False)),
                fixed_output=bool(getattr(node, "fixed_output", False)),
                search_terms_separate=bool(getattr(node, "search_terms_separate", False)),
            )
        )
    return result


def finalize_outline(
    nodes: list[OutlineNode],
    *,
    preserve_parents: bool = False,
) -> OutlineTree:
    """整理目录树。

    preserve_parents=True：学要点落盘用——不改 key、不因 level 栈重算父子，
    已有合法 parent 原样保留，避免确认后的目录在分析时嵌套漂移。
    """
    keyed = list(nodes) if preserve_parents else assign_placeholder_keys(list(nodes))
    ordered = []
    stack: list[tuple[int, str, str]] = []
    by_sequence: dict[str, str] = {}
    keyed_keys = {(item.key or "").strip() for item in keyed if (item.key or "").strip()}
    by_key = {(item.key or "").strip(): item for item in keyed if (item.key or "").strip()}

    def _parent_allowed(existing: str, sequence: str, parent_sequence: str) -> str:
        """preserve 模式：只保留与序号链一致的 parent，禁止把 2、3 章挂到 1 下。"""
        ep = (existing or "").strip()
        seq = (sequence or "").strip()
        ps = (parent_sequence or "").strip() or parent_sequence_of(seq)
        if not ps:
            return ""
        if ep and ep in keyed_keys:
            parent_node = by_key.get(ep)
            if parent_node is not None and (parent_node.sequence or "").strip() == ps:
                return ep
        if ps in by_sequence:
            return by_sequence[ps]
        return ""

    for node in keyed:
        key = node.key
        sequence = (node.sequence or "").strip()
        level = max(1, int(node.level or _level_from_sequence(sequence) or 1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = ""
        existing_parent = (node.parent or "").strip()
        if preserve_parents:
            parent = _parent_allowed(
                existing_parent,
                sequence,
                str(node.parent_sequence or "").strip(),
            )
        elif existing_parent and existing_parent in keyed_keys:
            parent = existing_parent
        elif existing_parent and any(item.key == existing_parent for item in ordered):
            parent = existing_parent
        elif node.parent_sequence and node.parent_sequence in by_sequence:
            parent = by_sequence[node.parent_sequence]
        elif stack:
            parent = stack[-1][1]
        parent_sequence = (node.parent_sequence or "").strip()
        if not preserve_parents:
            parent_sequence = parent_sequence or (stack[-1][2] if stack else "")
        elif not parent_sequence and parent:
            parent_sequence = next(
                ((item.sequence or "").strip() for item in ordered if item.key == parent),
                "",
            )
        if preserve_parents and not parent_sequence_of(sequence):
            parent_sequence = ""
            parent = ""
        item = OutlineNode(
            key=key,
            sequence=sequence,
            title=node.title.strip(),
            level=level,
            parent=parent,
            parent_sequence=parent_sequence,
            points=list(node.points or []),
            point_items=[dict(x) for x in list(getattr(node, "point_items", None) or []) if isinstance(x, dict)],
            keywords=list(getattr(node, "keywords", None) or []),
            describes=(node.describes or "").strip(),
            content_blocks=list(getattr(node, "content_blocks", None) or []),
            reference_text=getattr(node, "reference_text", "") or "",
            aux_prompt=(node.aux_prompt or "").strip(),
            material_files=list(node.material_files or []),
            knowledge_folder_ids=list(getattr(node, "knowledge_folder_ids", None) or []),
            element_ids=list(node.element_ids or []),
            elements=list(node.elements or []),
            structure_only=bool(getattr(node, "structure_only", False)),
            fixed_output=bool(getattr(node, "fixed_output", False)),
            search_terms_separate=bool(getattr(node, "search_terms_separate", False)),
        )
        ordered.append(item)
        if sequence:
            by_sequence[sequence] = key
        stack.append((level, key, sequence))
    _validate(ordered)
    return OutlineTree(chapters=ordered)


def outline_as_text(tree: OutlineTree) -> str:
    lines = []
    for item in tree.chapters:
        indent = "  " * max(0, item.level - 1)
        number = f"{item.sequence} " if item.sequence else ""
        lines.append(f"{indent}{number}{item.title}".rstrip())
    return "\n".join(lines)


def chapters_from_outline(tree: OutlineTree, full_text: str) -> list[dict]:
    """按已确认目录在原文中切出章节样本。"""
    lines = [line.strip() for line in (full_text or "").splitlines()]
    spans = _match_spans(tree.chapters, lines)
    chapters = []
    for index, node in enumerate(tree.chapters):
        start, end, matched = spans[index]
        paragraphs = [line for line in lines[start + 1 : end] if line and not _is_heading_line(line, node.title)]
        depends = [tree.chapters[index - 1].key] if index else []
        chapters.append(
            {
                "key": node.key,
                "title": node.title,
                "sequence": node.sequence,
                "level": node.level,
                "parent": node.parent,
                "sample_paragraphs": paragraphs,
                "points": list(node.points or []),
                "boundary": "",
                "depends_on": depends,
                "matched": matched,
            }
        )
    return chapters


def template_from_outline(
    tree: OutlineTree,
    full_text: str,
    *,
    biz_type: str,
    stem: str,
    version: str,
) -> dict:
    chapters = chapters_from_outline(tree, full_text)
    return {
        "id": f"{biz_type}-{stem}",
        "version": version,
        "title": stem,
        "biz_type": biz_type,
        "chapters": [
            {key: value for key, value in item.items() if key != "matched"}
            for item in chapters
        ],
    }


def read_template_text(path: Path) -> str:
    import logging

    from report_writing.services.documents import read_blocks, read_blocks_local
    from report_writing.infra.rag_parse import parse_full_text, use_remote_parse

    _log = logging.getLogger("uvicorn.error")
    if use_remote_parse():
        try:
            return parse_full_text(path, result_type="markdown")
        except ValueError as error:
            msg = str(error)
            if any(token in msg for token in ("502", "503", "504", "Bad Gateway", "Gateway Timeout")):
                _log.warning("RAG 全文解析不可用，回退本地抽取：%s", path.name)
                return "\n".join(block["text"] for block in read_blocks_local(path))
            raise
    return "\n".join(block["text"] for block in read_blocks(path))


def read_text_for_outline(path: Path, *, rag_markdown: str | None = None) -> str:
    """目录抽取专用正文：Word 优先本地段落（保留「目录页」1.1 标题），RAG Markdown 作后备。"""
    from report_writing.infra.config_settings import outline_prefer_local_docx

    rag = (rag_markdown or "").strip()
    if path.suffix.lower() == ".docx" and path.is_file() and outline_prefer_local_docx():
        try:
            from report_writing.services.documents import read_blocks_local

            local = "\n".join(
                str(block.get("text") or "").strip()
                for block in read_blocks_local(path)
                if str(block.get("text") or "").strip()
            )
            if len(local) >= 400:
                return local
        except Exception as error:
            _log.warning("本地 Word 正文读取失败，回退 RAG：%s", error)
    return rag or read_template_text(path)


def outline_from_html(html: str) -> OutlineTree | None:
    """RAG HTML 全文 → 去标签成纯文本后走同一套目录规则（比 Markdown 少丢层级时可用）。"""
    from html import unescape

    raw = html or ""
    if not raw.strip():
        return None
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)</?(?:h[1-6]|p|div|li|tr|table|section|article)[^>]*>", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text.strip()) < 40:
        return None
    try:
        return outline_from_text(text)
    except ValueError:
        return None


def try_outline_from_rag_html(path: Path) -> OutlineTree | None:
    from report_writing.infra.config_settings import outline_try_rag_html
    from report_writing.infra.rag_parse import parse_full_text, use_remote_parse

    if not outline_try_rag_html():
        return None
    cache = Path(str(path) + ".rag-parse.html")
    html = ""
    try:
        if (
            cache.is_file()
            and path.is_file()
            and cache.stat().st_mtime >= path.stat().st_mtime
            and cache.stat().st_size > 0
        ):
            html = cache.read_text(encoding="utf-8")
        elif use_remote_parse() and path.is_file():
            html = parse_full_text(path, result_type="html")
            try:
                cache.write_text(html or "", encoding="utf-8")
            except OSError:
                pass
        else:
            return None
        return outline_from_html(html)
    except Exception as error:
        _log.debug("RAG HTML 目录抽取跳过：%s", error)
        return None


def load_outline_json(raw: str | bytes | dict) -> OutlineTree:
    if isinstance(raw, dict):
        data = raw
    else:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        data = json.loads(text)
    chapters = data.get("chapters") if isinstance(data, dict) else data
    if not isinstance(chapters, list):
        raise ValueError("JSON 需要 chapters 数组")
    return outline_from_payload(chapters)


def _from_toc_domain(text: str) -> list[OutlineNode]:
    nodes = []
    seen = set()
    for match in _TOC_LINE.finditer(text or ""):
        sequence = match.group("sequence").strip()
        title = _clean_title(match.group("title"))
        if not title or (sequence, title) in seen:
            continue
        seen.add((sequence, title))
        nodes.append(
            OutlineNode(
                sequence=sequence,
                title=title,
                level=_level_from_sequence(sequence),
            )
        )
    return nodes


def _from_numbered_lines(text: str) -> list[OutlineNode]:
    nodes = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        cleaned = _BOLD_TITLE.sub(r"\g<title>", line.strip())
        cleaned = re.sub(r"\*+", "", cleaned).strip()
        cleaned = _PAGE_TAIL_LINE.sub("", cleaned).strip()
        cleaned = re.sub(r"[·．.]{2,}\s*\d+\s*$", "", cleaned).strip()
        tab_match = _TOC_TAB_LINE.match(cleaned)
        if tab_match:
            sequence = (tab_match.group("sequence") or "").strip()
            title = _clean_title(tab_match.group("title"))
            title = _short_heading_title(title)
            if sequence and title and sequence not in seen and not _title_looks_like_body(title):
                seen.add(sequence)
                nodes.append(
                    OutlineNode(
                        sequence=sequence,
                        title=title,
                        level=_level_from_sequence(sequence),
                    )
                )
            continue
        match = _NUMBERED_INLINE.match(cleaned) or _NUMBERED.match(cleaned) or _CHAPTER.match(cleaned)
        if not match:
            continue
        sequence = (match.group("sequence") or "").strip()
        title = _clean_title(match.group("title"))
        # 正文粘在标题后时再截一刀
        title = _short_heading_title(title)
        if not title or title.endswith(("。", "；")) or len(title) > 40:
            continue
        if not sequence or sequence in seen:
            continue
        if re.fullmatch(r"\d+", sequence) and int(sequence) > 15:
            continue
        seen.add(sequence)
        level = _level_from_sequence(sequence)
        if sequence.startswith("第") or re.fullmatch(r"[一二三四五六七八九十]+", sequence or ""):
            level = 1
        nodes.append(
            OutlineNode(
                sequence=sequence,
                title=title,
                level=level,
            )
        )
    return nodes


def _short_heading_title(title: str) -> str:
    text = (title or "").strip()
    if not text:
        return ""
    # 丢掉粘连正文：保留开头短标题
    cut = re.split(r"[\s　]+|[，。；：]", text, maxsplit=1)[0].strip()
    if 2 <= len(cut) <= 40:
        text = cut
    if len(text) > 40:
        text = text[:40].rstrip()
    return text


def _clean_title(title: str) -> str:
    text = re.sub(r"\*+", "", title or "")
    text = re.sub(r"\t.*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .\u3000")
    return text


def _level_from_sequence(sequence: str) -> int:
    if not sequence:
        return 1
    if re.fullmatch(r"\d+(?:\.\d+)*", sequence):
        return sequence.count(".") + 1
    return 1


def is_descendant_sequence(child: str, parent: str) -> bool:
    """如 2.3.1 是 2.3 / 2 的子孙序号。"""
    child = (child or "").strip()
    parent = (parent or "").strip()
    if not child or not parent:
        return False
    if not re.fullmatch(r"\d+(?:\.\d+)*", child) or not re.fullmatch(r"\d+(?:\.\d+)*", parent):
        return False
    return child.startswith(parent + ".")


def parent_sequence_of(sequence: str) -> str:
    sequence = (sequence or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*", sequence) or "." not in sequence:
        return ""
    return sequence.rsplit(".", 1)[0]


def discover_descendant_headings(
    text: str,
    parent_sequences: list[str],
    existing_sequences: set[str] | None = None,
) -> list[OutlineNode]:
    """从资料正文/目录中找出父序号下尚未进入大纲的子章节（如 2.3 → 2.3.1）。"""
    existing = {str(item).strip() for item in (existing_sequences or set()) if str(item).strip()}
    parents = [str(item).strip() for item in parent_sequences if str(item).strip()]
    if not parents:
        return []
    seen: set[str] = set()
    found: list[OutlineNode] = []
    for node in _from_toc_domain(text) + _from_numbered_lines(text):
        sequence = (node.sequence or "").strip()
        if not sequence or sequence in existing or sequence in seen:
            continue
        if not any(is_descendant_sequence(sequence, parent) for parent in parents):
            continue
        seen.add(sequence)
        found.append(
            OutlineNode(
                sequence=sequence,
                title=node.title,
                level=_level_from_sequence(sequence),
                parent_sequence=parent_sequence_of(sequence),
            )
        )
    found.sort(key=lambda item: _sequence_sort_key(item.sequence))
    return found


def _sequence_sort_key(sequence: str):
    sequence = (sequence or "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)*", sequence):
        return (0, [int(part) for part in sequence.split(".")])
    return (1, sequence)


def resolve_parent_key(chapters: list[OutlineNode], sequence: str) -> str:
    """按序号链在现有目录中找最近上级节点 key。"""
    parent_seq = parent_sequence_of(sequence)
    by_seq = {(item.sequence or "").strip(): item for item in chapters if (item.sequence or "").strip()}
    while parent_seq:
        found = by_seq.get(parent_seq)
        if found is not None:
            return found.key
        parent_seq = parent_sequence_of(parent_seq)
    return ""


def insert_outline_nodes(chapters: list[OutlineNode], newcomers: list[OutlineNode]) -> list[OutlineNode]:
    """把新章节按序号插入现有目录，再交给 finalize 校正父子。"""
    if not newcomers:
        return list(chapters)
    merged = list(chapters) + list(newcomers)
    numbered = []
    others = []
    for item in merged:
        if re.fullmatch(r"\d+(?:\.\d+)*", (item.sequence or "").strip()):
            numbered.append(item)
        else:
            others.append(item)
    numbered.sort(key=lambda item: _sequence_sort_key(item.sequence))
    # 无序号节点保持原相对位置：插在各自邻近编号章之前较难，先放末尾保留
    return numbered + others


def _validate(nodes: list[OutlineNode]) -> None:
    if not nodes:
        raise ValueError("目录不能为空")
    keys = set()
    for index, node in enumerate(nodes, start=1):
        if node.key in keys:
            raise ValueError(f"目录键重复：{node.key}")
        keys.add(node.key)
        if node.level < 1:
            raise ValueError(f"第 {index} 条层级无效")
        if node.parent and node.parent not in keys - {node.key}:
            # parent must appear earlier
            earlier = {item.key for item in nodes[: index - 1]}
            if node.parent not in earlier:
                raise ValueError(f"第 {index} 条的上级不存在：{node.parent}")


def _match_spans(nodes: list[OutlineNode], lines: list[str]) -> list[tuple[int, int, bool]]:
    """按标题索引优先对齐正文标题，避免文首目录抢先匹配。"""
    from report_writing.domain.chapter_locator import build_heading_index

    full_text = "\n".join(lines)
    heading_index = build_heading_index(full_text)
    starts: list[tuple[int, bool]] = []
    for node in nodes:
        found = -1
        try:
            from report_writing.domain.chapter_locator import _span_for_node

            span = _span_for_node(
                full_text,
                heading_index,
                sequence=node.sequence or "",
                title=node.title or "",
            )
            if span.matched and span.start_line >= 0:
                found = span.start_line
        except Exception as error:
            _log.warning("索引切章对齐失败 %s: %s", node.sequence, error)
        if found < 0:
            found = _find_heading(lines, 0, node, prefer_later=True)
        if found < 0:
            starts.append((0, False))
        else:
            starts.append((found, True))
    spans = []
    for index, (start, matched) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        if not matched:
            end = start
        elif end < start:
            end = start
        spans.append((start, end, matched))
    return spans


def _find_heading(lines: list[str], start: int, node: OutlineNode, *, prefer_later: bool = False) -> int:
    title = node.title.strip()
    sequence = (node.sequence or "").strip()
    patterns = []
    if sequence:
        patterns.append(f"{sequence} {title}")
        patterns.append(f"{sequence}.{title}")
        patterns.append(f"{sequence}{title}")
        patterns.append(sequence)
    else:
        # 无序号时才允许纯标题匹配；有序号时禁止同名串到 7.4 等其它章
        patterns.append(f"**{title}**")
        patterns.append(title)
    hits: list[int] = []
    for index in range(start, len(lines)):
        line = lines[index]
        plain = re.sub(r"^#{1,6}\s+", "", line)
        plain = re.sub(r"\*+", "", plain).strip()
        if sequence and _line_foreign_sequence(plain, sequence):
            continue
        if sequence and (
            plain == sequence
            or plain.startswith(sequence + " ")
            or plain.startswith(sequence + "　")
            or plain.startswith(sequence + ".")
            or plain.startswith(sequence + title)
        ):
            hits.append(index)
            continue
        for pattern in patterns:
            target = re.sub(r"\*+", "", pattern).strip()
            if plain == target or (plain.endswith(target) and len(plain) <= len(target) + 12):
                hits.append(index)
                break
            if line.strip() == pattern:
                hits.append(index)
                break
    if not hits:
        return -1
    if prefer_later and len(hits) > 1:
        # 同名多处时取后文（通常是正文标题，目录在前）
        return hits[-1]
    return hits[0]


def _line_foreign_sequence(plain: str, sequence: str) -> bool:
    seq = (sequence or "").strip()
    if not seq:
        return False
    match = re.match(r"(\d+(?:\.\d+)*)(?=$|[.\s　]|[^\d.])", (plain or "").strip())
    if not match:
        return False
    other = match.group(1)
    return other != seq and not other.startswith(seq + ".")


def _is_heading_line(line: str, title: str) -> bool:
    plain = re.sub(r"\*+", "", line).strip()
    return plain == title or plain.endswith(title)
