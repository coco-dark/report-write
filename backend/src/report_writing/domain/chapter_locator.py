"""长文档章节定位：标题索引 + 边界切章 + TOC/正文消歧 + 滑动窗修边界。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBERED_LINE = re.compile(
    r"^(?P<sequence>\d+(?:\.\d+)*)"
    r"(?:[.\s　]+|(?=[^\d.\s　]))"
    # 含顿号/空格：保留「法律、法规…」「法律 法规…」完整标题，避免截成「法律」与 1.5.1 撞名
    r"(?P<title>[\u4e00-\u9fffA-Za-z0-9（）()·\-—、，,\s　]{1,48}?)"
    r"(?=\s+\d{1,4}\s*$|$)"
)
_CHAPTER_LINE = re.compile(
    r"^(?P<sequence>第[0-9一二三四五六七八九十百零]+[章节篇]|[一二三四五六七八九十]+)"
    r"[章节篇、.．\s　]+"
    r"(?P<title>\S.{0,40})"
)
_TOC_MARK = re.compile(r"目\s*录|\bTOC\b|HYPERLINK|PAGEREF|_Toc\d+", re.I)
_PAGE_TAIL = re.compile(r"\s+\d{1,4}\s*$")
_BOLD_WRAP = re.compile(r"^\*\*(.+?)\*\*$")
_HYPERLINK_TOC = re.compile(
    r"HYPERLINK\s+\\[lL]\s+\\_Toc\d+\s+"
    r"(?P<sequence>\d+(?:\.\d+)*)\s+"
    r"(?P<title>.+?)\s+PAGEREF",
    re.IGNORECASE,
)
_HEADING_AT = re.compile(r"(?<![\d.])(\d+(?:\.\d+){0,})(?=[.\s　]|[^\d.]|$)")


# 单章送入分析/落盘的正文上限（建设规模等含表章节需要更长上下文）
CHAPTER_EXCERPT_MAX_CHARS = 16_000


@dataclass(frozen=True)
class HeadingHit:
    sequence: str
    title: str
    line_index: int
    char_offset: int
    toc_like: bool


@dataclass(frozen=True)
class ChapterSpan:
    sequence: str
    title: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    matched: bool
    hit: HeadingHit | None = None


def build_heading_index(text: str) -> list[HeadingHit]:
    """扫描全文，建立标题命中索引（含 HYPERLINK 目录、TOC 标记、无序号短标题）。"""
    lines, offsets = _split_lines(text or "")
    toc_end = _detect_toc_zone_end(lines)
    hits: list[HeadingHit] = []
    seen: set[tuple[str, str, int]] = set()

    def _add(sequence: str, title: str, line_index: int, toc_like: bool) -> None:
        key = (sequence, title, line_index)
        if key in seen or not title:
            return
        seen.add(key)
        hits.append(
            HeadingHit(
                sequence=sequence,
                title=title,
                line_index=line_index,
                char_offset=offsets[line_index] if line_index < len(offsets) else 0,
                toc_like=toc_like,
            )
        )

    for index, line in enumerate(lines):
        plain = _plain_line(line)
        for match in _HYPERLINK_TOC.finditer(line or ""):
            seq = (match.group("sequence") or "").strip()
            title = _clean_toc_title(match.group("title") or "")
            if seq and title:
                _add(seq, title, index, True)
        if not plain or len(plain) > 80:
            continue
        # 仅「像目录条目」的行标 toc，避免 toc_end 偏大时把正文标题一并降权
        toc_like = _looks_toc_line(line, plain)
        sequence, title = _parse_heading(plain)
        if not toc_like and index < toc_end and sequence and title and len(plain) <= 48:
            toc_like = True
        if sequence and title:
            # 目录区内若该标题后紧跟长正文，视为正文标题
            if toc_like and index < toc_end and _followed_by_body(lines, index):
                toc_like = False
            _add(sequence, title, index, toc_like)
            continue
        if _looks_title_only_heading(line, plain):
            _add("", plain[:40], index, False if _followed_by_body(lines, index) else toc_like)
    hits.sort(key=lambda item: item.line_index)
    return hits


def locate_chapter(
    text: str,
    *,
    sequence: str,
    title: str,
    index: list[HeadingHit] | None = None,
    max_chars: int = CHAPTER_EXCERPT_MAX_CHARS,
    refine: bool = True,
) -> tuple[str, bool]:
    """定位单章正文：索引对齐 → 模糊锚定兜底 → 边界切段 → 滑动窗/密度窗精修。"""
    body = text or ""
    if not body.strip():
        return "", False
    hits = index if index is not None else build_heading_index(body)
    span = _span_for_node(body, hits, sequence=sequence or "", title=title or "")
    if not span.matched or span.start_char < 0:
        span = _fuzzy_anchor_span(body, sequence=sequence or "", title=title or "")
    if not span.matched or span.start_char < 0:
        return "", False
    excerpt = body[span.start_char : span.end_char].strip()
    if refine:
        excerpt = refine_excerpt_window(
            excerpt,
            max_chars=max_chars,
            title=title or "",
            sequence=sequence or "",
        )
    else:
        excerpt = excerpt[:max_chars]
    return excerpt, bool(excerpt.strip())


def focus_text_for_parents(
    text: str,
    parents: list[tuple[str, str]],
    *,
    max_chars_each: int = 6000,
    max_total: int = 14000,
) -> str:
    """为目录提取拼出各父章正文片段，避免把整篇长文塞进模型。"""
    body = text or ""
    if not body.strip() or not parents:
        return body[:max_total]
    hits = build_heading_index(body)
    parts: list[str] = []
    used = 0
    for sequence, title in parents:
        seq = (sequence or "").strip()
        ttl = (title or "").strip()
        if not seq and not ttl:
            continue
        excerpt, ok = locate_chapter(
            body,
            sequence=seq,
            title=ttl,
            index=hits,
            max_chars=max_chars_each,
            refine=True,
        )
        if not ok or not excerpt.strip():
            continue
        block = f"### 父章 {seq} {ttl}\n{excerpt}".strip()
        if used + len(block) > max_total and parts:
            break
        parts.append(block)
        used += len(block) + 2
    if not parts:
        return body[:max_total]
    return "\n\n".join(parts)[:max_total]


def locate_all_chapters(
    text: str,
    chapters: list[dict],
    *,
    max_chars: int = CHAPTER_EXCERPT_MAX_CHARS,
    refine: bool = True,
) -> list[dict]:
    """批量定位；chapters 项需含 sequence/title，可选 key。"""
    body = text or ""
    hits = build_heading_index(body)
    results = []
    for item in chapters:
        seq = str(item.get("sequence") or "").strip()
        title = str(item.get("title") or "").strip()
        excerpt, matched = locate_chapter(
            body,
            sequence=seq,
            title=title,
            index=hits,
            max_chars=max_chars,
            refine=refine,
        )
        results.append(
            {
                "key": item.get("key") or "",
                "sequence": seq,
                "title": title,
                "matched": matched,
                "excerpt": excerpt,
                "sample_paragraphs": [line for line in excerpt.splitlines() if line.strip()][:80],
            }
        )
    return results


def refine_excerpt_window(
    excerpt: str,
    *,
    max_chars: int = CHAPTER_EXCERPT_MAX_CHARS,
    title: str = "",
    sequence: str = "",
    initial_paras: int = 10,
    step_paras: int = 8,
    min_body_chars: int = 80,
) -> str:
    """在已切边界内：先从头扩窗；超长章再比密度窗，取正文更充实的一段。"""
    text = (excerpt or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars and _body_char_count(text, title, sequence) >= min_body_chars:
        return _shrink_trailing_foreign_heading(text, sequence)[:max_chars]

    paragraphs = _paragraphs(text)
    if not paragraphs:
        return _trim_to_paragraph(text, max_chars)

    end = min(len(paragraphs), max(1, initial_paras))
    window = "\n".join(paragraphs[:end]).strip()
    while end < len(paragraphs) and len(window) < max_chars:
        body_chars = _body_char_count(window, title, sequence)
        if body_chars >= min_body_chars and _looks_complete(window):
            break
        end = min(len(paragraphs), end + max(1, step_paras))
        window = "\n".join(paragraphs[:end]).strip()
        if len(window) >= max_chars:
            break

    window = _shrink_trailing_foreign_heading(window, sequence)
    if len(window) > max_chars:
        window = _trim_to_paragraph(window, max_chars)

    # 章很长时：在边界内另选正文密度更高的窗口（保留章首标题优先）
    # 章首已含表格/「见下表」时勿滑到中段，避免丢掉表2.x-1
    if len(paragraphs) > initial_paras + step_paras and len(text) > max_chars:
        densest = _pick_densest_window(
            paragraphs,
            max_chars=max_chars,
            title=title,
            sequence=sequence,
            step=max(1, step_paras),
        )
        densest = _shrink_trailing_foreign_heading(densest, sequence)
        head_has_table = _has_table_body(window) or _promises_table(window)
        densest_keeps_table = _has_table_body(densest)
        if head_has_table and not densest_keeps_table:
            return window.strip()
        if _body_char_count(densest, title, sequence) > _body_char_count(window, title, sequence) * 1.12:
            return densest.strip()
    return window.strip()


def _pick_densest_window(
    paragraphs: list[str],
    *,
    max_chars: int,
    title: str,
    sequence: str,
    step: int = 4,
) -> str:
    """在段落序列上滑动，选取不超过 max_chars 且正文密度最高的窗口。"""
    if not paragraphs:
        return ""
    n = min(len(paragraphs), 220)
    paras = paragraphs[:n]
    best = ""
    best_score = -1.0
    for start in range(0, n, max(1, step)):
        chunk_parts: list[str] = []
        length = 0
        for index in range(start, n):
            part = paras[index]
            add = len(part) + (2 if chunk_parts else 0)
            if chunk_parts and length + add > max_chars:
                break
            chunk_parts.append(part)
            length += add
            if length >= max_chars:
                break
        if not chunk_parts:
            continue
        chunk = "\n\n".join(chunk_parts).strip()
        if len(chunk) > max_chars:
            chunk = _trim_to_paragraph(chunk, max_chars)
        score = float(_body_char_count(chunk, title, sequence))
        if start == 0:
            score += 50.0  # 略偏章首（含标题与开篇）
        if score > best_score:
            best_score = score
            best = chunk
    return best or "\n\n".join(paras)[:max_chars]


def _span_for_node(
    text: str,
    hits: list[HeadingHit],
    *,
    sequence: str,
    title: str,
) -> ChapterSpan:
    lines, offsets = _split_lines(text)
    best = _best_hit(hits, sequence=sequence, title=title)
    if best is None:
        return ChapterSpan(
            sequence=sequence,
            title=title,
            start_char=-1,
            end_char=-1,
            start_line=-1,
            end_line=-1,
            matched=False,
        )
    start_char = best.char_offset
    start_line = best.line_index
    end_line = len(lines)
    end_char = len(text)
    # 边界以大纲序号为准（正文命中可能是无序号标题）
    bound_seq = (sequence or best.sequence or "").strip()
    for other in hits:
        if other.line_index <= best.line_index:
            continue
        other_seq = (other.sequence or "").strip()
        if bound_seq and other_seq and _is_same_or_descendant(other_seq, bound_seq):
            continue
        # 无序号标题：若高度像本章标题则跳过，否则视为下一章边界
        if not other_seq and title and _title_similarity(other.title, title) >= 0.9:
            continue
        # 表题/伪标题：当前片段已「见下表」却尚未吃到表体时，跳过直到真正兄弟/父级编号标题
        if not other_seq and _looks_table_caption_or_header(other.title or ""):
            continue
        if not other_seq:
            window = text[start_char : other.char_offset]
            if _promises_table(window) and not _has_table_body(window):
                continue
        end_line = other.line_index
        end_char = other.char_offset
        break
    return ChapterSpan(
        sequence=sequence,
        title=title,
        start_char=start_char,
        end_char=end_char,
        start_line=start_line,
        end_line=end_line,
        matched=True,
        hit=best,
    )


def _fuzzy_anchor_span(text: str, *, sequence: str, title: str) -> ChapterSpan:
    """索引未命中时：在全文模糊搜序号+标题，取后文命中并切到下一非本枝标题。"""
    body = text or ""
    seq = (sequence or "").strip()
    title = (title or "").strip()
    if not body.strip() or (not seq and not title):
        return ChapterSpan(seq, title, -1, -1, -1, -1, False)

    patterns: list[str] = []
    if seq and title:
        patterns.append(re.escape(seq) + r"[.\s　]*" + re.escape(title))
        patterns.append(re.escape(seq) + re.escape(title))
    if seq:
        patterns.append(r"(?<![\d.])" + re.escape(seq) + r"(?=[.\s　]|[^\d.]|$)")
    # 仅无序号时才允许纯标题锚定，避免同名章节（1.5.1 vs 7.4）串配
    if title and not seq:
        patterns.append(r"(?m)^(?:#{1,6}\s+)?(?:\*\*)?" + re.escape(title) + r"(?:\*\*)?\s*$")

    candidates: list[re.Match[str]] = []
    for pattern in patterns:
        try:
            candidates.extend(list(re.finditer(pattern, body)))
        except re.error:
            continue
    if seq:
        candidates = [item for item in candidates if _match_aligned_to_sequence(body, item, seq)]
    if not candidates:
        return ChapterSpan(seq, title, -1, -1, -1, -1, False)

    # 多处命中：优先后文，且标题后要有一定正文
    def _rank(match: re.Match[str]) -> tuple:
        start = match.start()
        tail = body[start : start + 400]
        body_after = len(re.sub(r"\s+", "", tail[len(match.group(0)) :]))
        return (1 if body_after >= 20 else 0, start)

    best_match = max(candidates, key=_rank)
    start_char = best_match.start()
    end_char = _next_boundary_offset(body, start_char, seq)
    lines, offsets = _split_lines(body)
    start_line = _line_index_at(offsets, start_char)
    end_line = _line_index_at(offsets, end_char)
    return ChapterSpan(
        sequence=seq,
        title=title,
        start_char=start_char,
        end_char=end_char,
        start_line=start_line,
        end_line=end_line,
        matched=True,
    )


def _match_aligned_to_sequence(text: str, match: re.Match[str], sequence: str) -> bool:
    """模糊命中须落在目标序号附近，排除行首是别的数字标题（如搜 1.5.1 却落到 7.4）。"""
    seq = (sequence or "").strip()
    if not seq:
        return True
    start = match.start()
    line_start = text.rfind("\n", 0, start) + 1
    head = text[line_start : start + len(match.group(0)) + 8]
    plain = re.sub(r"^#{1,6}\s+", "", head.strip())
    plain = re.sub(r"\*+", "", plain).strip()
    if plain.startswith(seq) and (
        len(plain) == len(seq) or plain[len(seq)] in ".． 　\t" or not plain[len(seq)].isdigit()
    ):
        # 排除「1.5.10」误当成「1.5.1」：下一位若是数字则否
        if len(plain) > len(seq) and plain[len(seq)].isdigit():
            return False
        return True
    # 命中串本身以目标序号开头也可以
    snippet = match.group(0)
    return snippet.startswith(seq) and not (len(snippet) > len(seq) and snippet[len(seq)].isdigit())


def _next_boundary_offset(text: str, start: int, sequence: str) -> int:
    """从 start 起找下一个非本枝数字标题的字符偏移。"""
    body = text or ""
    seq = (sequence or "").strip()
    search_from = start + max(1, len(seq))
    tail = body[search_from:]
    end_rel = len(tail)
    for match in _HEADING_AT.finditer(tail):
        other = match.group(1)
        if not re.fullmatch(r"\d+(?:\.\d+)*", other or ""):
            continue
        if seq and (other == seq or other.startswith(seq + ".")):
            continue
        # 避免 7.0 这类量纲：后接字母单位则跳过
        after = tail[match.end() : match.end() + 4]
        if re.match(r"(?i)(km|m|kv|%|mw)", after.lstrip()):
            continue
        end_rel = match.start()
        break
    return search_from + end_rel


def _line_index_at(offsets: list[int], char_offset: int) -> int:
    if not offsets:
        return 0
    for index in range(len(offsets) - 1, -1, -1):
        if offsets[index] <= char_offset:
            return index
    return 0


def _clean_toc_title(title: str) -> str:
    text = re.sub(r"\*+", "", title or "")
    text = re.sub(r"\t.*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .\u3000")
    if len(text) > 40:
        text = text[:40]
    return text


def _best_hit(hits: list[HeadingHit], *, sequence: str, title: str) -> HeadingHit | None:
    seq = (sequence or "").strip()
    title = (title or "").strip()
    if not hits or (not seq and not title):
        return None
    scored: list[tuple[float, HeadingHit]] = []
    for hit in hits:
        score = _score_hit(hit, sequence=seq, title=title)
        if score > 0:
            scored.append((score, hit))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1].line_index), reverse=True)
    return scored[0][1]


def _score_hit(hit: HeadingHit, *, sequence: str, title: str) -> float:
    score = 0.0
    seq_ok = bool(sequence) and hit.sequence == sequence
    title_sim = _title_similarity(hit.title, title) if title else 0.0
    title_only = not (hit.sequence or "").strip()
    hit_seq = (hit.sequence or "").strip()

    # 双方都有数字序号且不一致：一律否决（同名标题如 1.5.1 / 7.4 不得串章）
    if sequence and hit_seq and _is_numeric_sequence(sequence) and _is_numeric_sequence(hit_seq):
        if hit_seq != sequence:
            return -1.0

    # 误把「## 7.4 标题」当无序号标题时：内嵌序号与目标不符则否决
    if title_only and sequence:
        embedded = _leading_numeric_sequence(hit.title)
        if embedded and embedded != sequence:
            return -1.0

    if seq_ok and title_sim >= 0.72:
        score += 120.0
    elif seq_ok and title_sim >= 0.4:
        score += 90.0
    elif seq_ok:
        score += 70.0
    elif title_only and title_sim >= 0.92:
        score += 88.0  # 正文常见「无序号短标题」，优先于目录区带序号命中
    elif (not sequence) and title_sim >= 0.92:
        score += 55.0
    elif (not sequence) and title_sim >= 0.75:
        score += 35.0
    else:
        return -1.0

    if hit.toc_like:
        score -= 45.0
    else:
        score += 25.0
    # 同名多次时略偏后文（正文常在目录之后）
    score += min(hit.line_index, 400) * 0.02
    return score


def _is_numeric_sequence(value: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)*", (value or "").strip()))


def _leading_numeric_sequence(title: str) -> str:
    text = re.sub(r"^#{1,6}\s+", "", (title or "").strip())
    text = re.sub(r"\*+", "", text).strip()
    match = re.match(r"(\d+(?:\.\d+)*)(?=$|[.\s　]|[^\d.])", text)
    return match.group(1) if match else ""


_TABLE_CAPTION = re.compile(
    r"^表\s*\d+(?:[.\-]\d+)*(?:\s|$)|"
    r"^附表\s*\d+|"
    r"^表\s*[一二三四五六七八九十百]+"
)
_TABLE_HEADER_CELL_ROW = re.compile(
    r"^(?:栏目|项目|序号|名称|内容|备注|单位)(?:[\s、,，/|].*)?$"
)


def _looks_table_caption_or_header(plain: str) -> bool:
    """表题/表头行不得进入标题索引，否则会在「见下表」后截断丢表体。"""
    text = (plain or "").strip()
    if not text:
        return False
    if _TABLE_CAPTION.match(text):
        return True
    if _TABLE_HEADER_CELL_ROW.match(text) and len(text) <= 24:
        return True
    return False


def _looks_title_only_heading(raw: str, plain: str) -> bool:
    text = (plain or "").strip()
    if not text or len(text) < 2 or len(text) > 40:
        return False
    if _looks_toc_line(raw, text):
        return False
    if _looks_table_caption_or_header(text):
        return False
    if re.search(r"[。；！？]", text):
        return False
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    # 加粗行或短标题行
    if _BOLD_WRAP.match((raw or "").strip()):
        return True
    if len(text) <= 24 and not re.match(r"^\d+(?:\.\d+)*$", text):
        return True
    return False


def _title_similarity(left: str, right: str) -> float:
    a = _normalize_title(left)
    b = _normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        return 0.82 + 0.15 * (shorter / longer)
    # 字符 Jaccard
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return inter / union


def _normalize_title(title: str) -> str:
    text = re.sub(r"\*+", "", title or "")
    text = re.sub(r"\s+", "", text)
    text = text.strip(" .．、:：-—")
    return text.lower()


def _is_same_or_descendant(child: str, parent: str) -> bool:
    child = (child or "").strip()
    parent = (parent or "").strip()
    if not child or not parent:
        return False
    if child == parent:
        return True
    if not re.fullmatch(r"\d+(?:\.\d+)*", child) or not re.fullmatch(r"\d+(?:\.\d+)*", parent):
        return False
    return child.startswith(parent + ".")


def _detect_toc_zone_end(lines: list[str]) -> int:
    """推断目录区结束行（不含）；无目录则 0。"""
    limit = min(len(lines), 120)
    toc_start = -1
    for index in range(limit):
        plain = _plain_line(lines[index])
        if re.search(r"目\s*录", plain) or ( "HYPERLINK" in lines[index].upper() and "TOC" in lines[index].upper()):
            toc_start = index
            break
    if toc_start < 0:
        # 文首连续短编号标题且几乎无长正文 → 视作目录
        numbered = 0
        for index in range(min(40, len(lines))):
            plain = _plain_line(lines[index])
            seq, title = _parse_heading(plain)
            if seq and title and len(plain) <= 40:
                numbered += 1
            elif len(plain) >= 50:
                break
        if numbered >= 5:
            toc_start = 0
        else:
            return 0

    # 目录结束后：正文标题（后接长段）或较长非标题正文
    for index in range(toc_start + 1, min(len(lines), toc_start + 200)):
        plain = _plain_line(lines[index])
        if not plain:
            continue
        if _looks_toc_line(lines[index], plain):
            continue
        seq, title = _parse_heading(plain)
        if seq and title and len(plain) <= 48:
            for look in range(index + 1, min(len(lines), index + 5)):
                nxt = _plain_line(lines[look])
                if not nxt:
                    continue
                nxt_seq, _ = _parse_heading(nxt)
                if len(nxt) >= 12 and not nxt_seq:
                    return index  # 本章标题起算正文区
                break
            continue
        if len(plain) >= 18 or (not seq and len(plain) >= 12):
            return index
    return min(len(lines), toc_start + 80)


def _followed_by_body(lines: list[str], index: int) -> bool:
    """标题行后若干行内出现非标题正文。"""
    for look in range(index + 1, min(len(lines), index + 5)):
        nxt = _plain_line(lines[look])
        if not nxt:
            continue
        nxt_seq, _ = _parse_heading(nxt)
        if len(nxt) >= 12 and not nxt_seq and not _looks_toc_line(lines[look], nxt):
            return True
        return False
    return False


def _looks_toc_line(raw: str, plain: str) -> bool:
    if _TOC_MARK.search(raw or "") or _TOC_MARK.search(plain or ""):
        return True
    if _PAGE_TAIL.search(plain or "") and len(plain) <= 48:
        return True
    return False


def _parse_heading(plain: str) -> tuple[str, str]:
    text = (plain or "").strip()
    match = _NUMBERED_LINE.match(text) or _CHAPTER_LINE.match(text)
    if not match:
        return "", ""
    sequence = (match.group("sequence") or "").strip()
    title = (match.group("title") or "").strip()
    title = re.sub(r"\s+", " ", title).strip(" .．:：")
    # 去掉目录行尾页码
    title = re.sub(r"\s+\d{1,4}$", "", title).strip()
    if len(title) > 40:
        title = title[:40]
    # 过滤「7.0km」类非标题
    if re.fullmatch(r"\d+(?:\.\d+)*", sequence or "") and re.match(r"^[\d.]+(km|m|%|kv)?$", title, re.I):
        return "", ""
    return sequence, title


def _plain_line(line: str) -> str:
    text = (line or "").strip()
    text = re.sub(r"^#{1,6}\s+", "", text)  # Markdown ATX
    bold = _BOLD_WRAP.match(text)
    if bold:
        text = bold.group(1).strip()
    text = re.sub(r"\*+", "", text).strip()
    return text


def _split_lines(text: str) -> tuple[list[str], list[int]]:
    raw = text or ""
    lines = raw.splitlines()
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1  # +1 for '\n' (splitlines 去掉换行；末行无换行时略偏可接受)
    if not lines:
        return [""], [0]
    return lines, offsets


def _paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text or "")
    paragraphs = [part.strip() for part in parts if part.strip()]
    if len(paragraphs) >= 2:
        return paragraphs
    # 单段文档：按行当段，便于扩窗
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _body_char_count(excerpt: str, title: str = "", sequence: str = "") -> int:
    title = (title or "").strip()
    sequence = (sequence or "").strip()
    total = 0
    for line in (excerpt or "").splitlines():
        item = line.strip()
        if not item:
            continue
        plain = _plain_line(item)
        if sequence and (plain == sequence or plain.startswith(sequence + " ") or plain.startswith(sequence + "　")):
            rest = plain[len(sequence) :].strip(" .．、　")
            if not rest or rest == title:
                continue
        if title and (plain == title or plain.endswith(title) and len(plain) <= len(title) + 8):
            continue
        seq, ttl = _parse_heading(plain)
        if seq and ttl and (seq == sequence or _title_similarity(ttl, title) >= 0.9):
            continue
        total += len(plain)
    return total


_TABLE_PROMISE = re.compile(
    r"见下表|如下表|见上表|见表|见附表|如下所示|工程特性见|详见下表|列于下表"
)
_HTML_TABLE = re.compile(r"<table\b|</table>", re.I)
_MD_TABLE_ROW = re.compile(r"^\|.+\|\s*$", re.M)


def _promises_table(text: str) -> bool:
    return bool(_TABLE_PROMISE.search(text or ""))


def _has_table_body(text: str) -> bool:
    t = text or ""
    if _HTML_TABLE.search(t):
        return True
    if _MD_TABLE_ROW.search(t) and re.search(r"^\|[\s:\-|]+\|\s*$", t, re.M):
        return True
    # Word 粘贴：表题后多行「栏目 + 数值」且含表号
    if re.search(r"表\s*\d+(?:[.\-]\d+)*", t) and t.count("\n") >= 4:
        return True
    return False


def _looks_complete(excerpt: str) -> bool:
    text = (excerpt or "").strip()
    if len(text) < 120:
        return False
    # 「见下表 2.2-1。」这类开篇句号不能当成章末，否则表体进不了分析窗
    if _promises_table(text) and not _has_table_body(text):
        return False
    tail = text[-1]
    if tail in "。！？；…」』\"'）)]":
        return True
    # 表格或列表收尾也视为可停
    if text.rstrip().endswith(("</table>", "|", "——", "---")):
        return True
    return len(text) >= 1500


def _shrink_trailing_foreign_heading(excerpt: str, sequence: str) -> str:
    """去掉段末误吸入的非本枝标题及其后内容。"""
    lines = (excerpt or "").splitlines()
    if len(lines) < 2:
        return excerpt or ""
    cut = len(lines)
    for index in range(1, len(lines)):
        plain = _plain_line(lines[index])
        seq, title = _parse_heading(plain)
        if not seq or not title:
            continue
        if _is_same_or_descendant(seq, sequence or ""):
            continue
        if sequence and seq == sequence:
            continue
        cut = index
        break
    return "\n".join(lines[:cut]).strip()


def _trim_to_paragraph(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    lower = clipped.lower()
    # HTML 表：优先落在完整 </table>；半截表则退到 <table 之前（若章首仍够长）
    open_at = lower.rfind("<table")
    close_at = lower.rfind("</table>")
    if open_at >= 0 and (close_at < 0 or close_at < open_at):
        if open_at >= int(max_chars * 0.4):
            return clipped[:open_at].rstrip()
    elif close_at >= int(max_chars * 0.5):
        end = close_at + len("</table>")
        return clipped[:end].rstrip()
    # 尽量在换行处截断
    break_at = max(clipped.rfind("\n\n"), clipped.rfind("\n"))
    if break_at >= max_chars * 0.6:
        return clipped[:break_at].strip()
    return clipped.strip()
