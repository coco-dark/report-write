"""章节写作策略：资料驱动 / 模板填槽 / 混合 / 自动生成。"""

from __future__ import annotations

import re
from typing import Any

from report_writing.domain.content_blocks import ensure_html_fragment, strip_html

PRESENCE_ALWAYS = "always"
PRESENCE_IF_MATERIALS = "if_materials"
COMPOSE_SOURCE_ONLY = "source_only"
COMPOSE_TEMPLATE_FILL = "template_fill"
COMPOSE_HYBRID = "hybrid"
COMPOSE_AUTO_GENERATE = "auto_generate"
COMPOSE_FIXED = "fixed"
# 旧值别名（normalize 之后与上面对齐）
COMPOSE_GENERATE = COMPOSE_HYBRID
COMPOSE_TEMPLATE = COMPOSE_TEMPLATE_FILL
COMPOSE_VERBATIM = COMPOSE_FIXED

MATERIALS_OMIT_NOTE = "资料不足，本章省略"
MATERIALS_HANDOFF_NOTE = "无资料，整章转人工"
PARENT_OMIT_NOTE = "上级章节已省略"
OFFTOPIC_HANDOFF_NOTE = "主题无关，转人工"

_SLOT_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}|｛｛\s*([^｝]+?)\s*｝｝")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；\n])")
_UNFILLED = "\u2063UNFILLED\u2063"


def normalize_presence(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {
        PRESENCE_IF_MATERIALS,
        "if-materials",
        "optional",
        "omit",
        "conditional",
        "资料有才写",
        "有资料才出",
        "非固定",
    }:
        return PRESENCE_IF_MATERIALS
    return PRESENCE_ALWAYS


def normalize_compose(value: Any, *, fixed_output: bool = False, presence: Any = "") -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {COMPOSE_SOURCE_ONLY, "sourceonly", "资料驱动"}:
        return COMPOSE_SOURCE_ONLY
    if raw in {
        COMPOSE_AUTO_GENERATE,
        "autogenerate",
        "auto",
        "free_write",
        "knowledge",
        "自动生成",
        "不依赖资料",
        "不依赖资料自动生成",
    }:
        return COMPOSE_AUTO_GENERATE
    if raw in {
        COMPOSE_FIXED,
        "verbatim",
        "copy",
        "fixed_output",
        "照抄",
        "固定",
        "固定内容",
        "固定正文",
        "固定内容输出",
    }:
        return COMPOSE_FIXED
    if raw in {
        COMPOSE_TEMPLATE_FILL,
        "templatefill",
        "template",
        "boilerplate",
        "slots",
        "模板填槽",
        "套话",
        "填槽",
    }:
        return COMPOSE_TEMPLATE_FILL
    if raw in {COMPOSE_HYBRID, "generate", "混合", "按要点现写"}:
        if normalize_presence(presence) == PRESENCE_IF_MATERIALS:
            return COMPOSE_SOURCE_ONLY
        return COMPOSE_HYBRID
    if not raw and fixed_output:
        return COMPOSE_FIXED
    if normalize_presence(presence) == PRESENCE_IF_MATERIALS:
        return COMPOSE_SOURCE_ONLY
    return COMPOSE_HYBRID


def allows_write_without_materials(obj: Any) -> bool:
    return compose_mode(obj) in {COMPOSE_HYBRID, COMPOSE_AUTO_GENERATE}


def uses_slot_reference(value: Any, *, fixed_output: bool = False, presence: Any = "") -> bool:
    """资料驱动 / 模板填槽 / 混合：参考正文按填槽展示；自动生成与固定内容除外。"""
    mode = normalize_compose(value, fixed_output=fixed_output, presence=presence)
    return mode not in {COMPOSE_AUTO_GENERATE, COMPOSE_FIXED}


def compose_mode(obj: Any) -> str:
    return normalize_compose(
        getattr(obj, "compose", None),
        fixed_output=bool(getattr(obj, "fixed_output", False)),
        presence=getattr(obj, "presence", None),
    )


def presence_mode(obj: Any) -> str:
    return normalize_presence(getattr(obj, "presence", None))


def apply_compose_fields(obj: Any) -> None:
    """同步 presence / compose / fixed_output，旧数据只带 fixed_output 也能用。"""
    compose = normalize_compose(
        getattr(obj, "compose", None),
        fixed_output=bool(getattr(obj, "fixed_output", False)),
        presence=getattr(obj, "presence", None),
    )
    if hasattr(obj, "compose"):
        obj.compose = compose
    if hasattr(obj, "presence"):
        obj.presence = PRESENCE_IF_MATERIALS if compose == COMPOSE_SOURCE_ONLY else PRESENCE_ALWAYS
    if hasattr(obj, "fixed_output"):
        obj.fixed_output = compose == COMPOSE_FIXED


def extracts_from_slots(slots: list[Any] | None) -> list:
    from report_writing.domain.models import ExtractItem

    out: list[ExtractItem] = []
    seen: set[str] = set()
    for raw in slots or []:
        if isinstance(raw, ExtractItem):
            name = (raw.name or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            ExtractItem(
                name=name,
                kind=str(raw.get("kind") or "文本").strip() or "文本",
                hint=str(raw.get("hint") or "").strip(),
                required=bool(raw.get("required", True)),
                style=str(raw.get("style") or "").strip(),
            )
        )
    return out


def copy_compose_fields(dst: Any, src: Any) -> None:
    dst.compose = compose_mode(src)
    dst.presence = PRESENCE_IF_MATERIALS if dst.compose == COMPOSE_SOURCE_ONLY else PRESENCE_ALWAYS
    dst.fixed_output = dst.compose == COMPOSE_FIXED
    src_extracts = list(getattr(src, "extracts", None) or [])
    if src_extracts:
        dst.extracts = extracts_from_slots(src_extracts)


def infer_presence_compose(
    *,
    describes: str = "",
    points: list[str] | None = None,
    slots: list[Any] | None = None,
    excerpt: str = "",
    presence: Any = "",
    compose: Any = "",
    fixed_output: bool = False,
    structure_only: bool = False,
) -> tuple[str, str]:
    if structure_only:
        return PRESENCE_ALWAYS, COMPOSE_HYBRID
    got_compose = normalize_compose(compose, fixed_output=fixed_output, presence=presence)
    blob = "\n".join([describes or "", *(points or [])])
    if compose in (None, "") and not fixed_output:
        if "{{" in (excerpt or "") or "｛｛" in (excerpt or ""):
            got_compose = COMPOSE_TEMPLATE_FILL
        elif slots and _looks_boilerplate(excerpt, points or []):
            got_compose = COMPOSE_TEMPLATE_FILL
        elif presence in (None, "") and re.search(
            r"若无.{0,12}(不写|省略|不设|可无|转人工)|没有则不|资料中有则|视项目(有无|情况)|可选章节",
            blob,
        ):
            got_compose = COMPOSE_SOURCE_ONLY
    got_presence = PRESENCE_IF_MATERIALS if got_compose == COMPOSE_SOURCE_ONLY else PRESENCE_ALWAYS
    return got_presence, got_compose


def _looks_boilerplate(excerpt: str, points: list[str]) -> bool:
    text = excerpt or ""
    if not text.strip():
        return False
    if len(points) <= 4 and len(text) < 1200 and re.search(r"(本项目|该工程|现就|特此|依据有关)", text):
        return True
    return bool(re.search(r"[×xX]{2,}|_{3,}|【\s*】", text)) and len(points) <= 6


def is_materials_omit(chapter: Any | None) -> bool:
    from report_writing.domain.models import ChapterStatus

    if chapter is None:
        return False
    status = chapter.status
    if status != ChapterStatus.SKIPPED and str(status) != ChapterStatus.SKIPPED.value:
        return False
    note = chapter.human_note or ""
    return MATERIALS_OMIT_NOTE in note or PARENT_OMIT_NOTE in note


def parent_branch_omitted(
    requirement: Any,
    chapters: list | None,
    brief: Any | None = None,
) -> bool:
    if not chapters:
        return False
    by_key = {item.key: item for item in chapters if item.key}
    parent = str(getattr(requirement, "parent", "") or "").strip()
    seen: set[str] = set()
    while parent and parent not in seen:
        seen.add(parent)
        if is_materials_omit(by_key.get(parent)):
            return True
        nxt = ""
        if brief is not None:
            try:
                preq = brief.chapter(parent)
            except Exception:
                preq = None
            if preq is not None:
                nxt = str(getattr(preq, "parent", "") or "").strip()
        parent = nxt
    return False


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip()).lower()


def _plain_value(raw: Any, *, allow_long: bool = False) -> str:
    text = strip_html(str(raw or "")).strip()
    if not text:
        return ""
    if not allow_long and len(text) > 240:
        first = text.split("。")[0].strip()
        return (first or text)[:160]
    return text


def slot_values(requirement: Any, evidence: list | None = None) -> dict[str, str]:
    values: dict[str, str] = {}

    def put(name: str, value: str) -> None:
        name = (name or "").strip()
        value = (value or "").strip()
        if not name or not value:
            return
        values[name] = value
        values[_norm_name(name)] = value

    for row in getattr(requirement, "element_results", None) or []:
        if not isinstance(row, dict):
            continue
        put(str(row.get("name") or ""), _plain_value(row.get("value"), allow_long=True))
        put(str(row.get("ref_key") or ""), _plain_value(row.get("value"), allow_long=True))
    for item in getattr(requirement, "extracts", None) or []:
        name = getattr(item, "name", "") if not isinstance(item, dict) else item.get("name")
        source = getattr(item, "source", "") if not isinstance(item, dict) else item.get("source")
        put(str(name or ""), _plain_value(source))
    for card in evidence or []:
        adopted = bool(getattr(card, "adopted", False) if not isinstance(card, dict) else card.get("adopted"))
        if not adopted:
            continue
        point = str(getattr(card, "point", "") if not isinstance(card, dict) else card.get("point") or "")
        excerpt = str(getattr(card, "excerpt", "") if not isinstance(card, dict) else card.get("excerpt") or "")
        put(point, _plain_value(excerpt))
    return values


def _lookup(values: dict[str, str], name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    return values.get(raw) or values.get(_norm_name(raw)) or ""


def missing_required_materials(requirement: Any, evidence: list | None = None) -> list[str]:
    values = slot_values(requirement, evidence)
    extracts = list(getattr(requirement, "extracts", None) or [])
    missing: list[str] = []
    if extracts:
        for item in extracts:
            required = bool(getattr(item, "required", True) if not isinstance(item, dict) else item.get("required", True))
            if not required:
                continue
            name = str(getattr(item, "name", "") if not isinstance(item, dict) else item.get("name") or "").strip()
            if name and not _lookup(values, name):
                missing.append(name)
        return missing
    filled_elements = [
        row
        for row in (getattr(requirement, "element_results", None) or [])
        if isinstance(row, dict) and _plain_value(row.get("value"), allow_long=True)
    ]
    adopted = []
    for card in evidence or []:
        if bool(getattr(card, "adopted", False) if not isinstance(card, dict) else card.get("adopted")):
            adopted.append(card)
    if filled_elements or adopted:
        return []
    return ["本章资料"]


def should_omit_for_materials(requirement: Any, evidence: list | None = None) -> list[str]:
    """资料驱动：无资料则整章转人工（调用方设 needs_human，不再整章省略）。"""
    if compose_mode(requirement) != COMPOSE_SOURCE_ONLY:
        return []
    return missing_required_materials(requirement, evidence)


def fill_boilerplate_slots(
    text: str,
    values: dict[str, str],
    *,
    required_names: set[str] | None = None,
    drop_unfilled_sentences: bool = False,
) -> tuple[str, list[str]]:
    required_names = required_names or set()
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name = (match.group(1) or match.group(2) or "").strip()
        found = _lookup(values, name)
        if found:
            return found
        missing.append(name)
        if drop_unfilled_sentences:
            return _UNFILLED
        return ""

    filled = _SLOT_RE.sub(repl, text or "")
    if drop_unfilled_sentences and _UNFILLED in filled:
        kept: list[str] = []
        for part in _SENTENCE_SPLIT.split(filled):
            if _UNFILLED in part:
                continue
            kept.append(part)
        filled = "".join(kept).strip()
    filled = filled.replace(_UNFILLED, "")
    # 必填槽未填才算缺口；可选槽删句后不算失败
    required_missing = [name for name in missing if name in required_names or _norm_name(name) in {_norm_name(n) for n in required_names}]
    return filled, required_missing


def chapter_needs_llm(requirement: Any) -> bool:
    mode = compose_mode(requirement)
    blocks = list(getattr(requirement, "content_blocks", None) or [])
    has_template_src = bool(str(getattr(requirement, "reference_text", "") or "").strip()) or bool(blocks)
    if mode == COMPOSE_FIXED:
        return False
    if mode == COMPOSE_TEMPLATE_FILL:
        return not has_template_src
    if not blocks:
        return True
    explicit = [
        normalize_compose(getattr(b, "compose", None), fixed_output=False)
        for b in blocks
        if str(getattr(b, "compose", "") or "").strip()
    ]
    if explicit and all(m == COMPOSE_TEMPLATE_FILL for m in explicit) and len(explicit) == len(blocks):
        return False
    return True


def try_compose_draft(requirement: Any, evidence: list | None = None):
    """模板填槽章不走写作模型；资料驱动/混合返回 None。"""
    from report_writing.domain.models import ChapterDraft, Paragraph

    if chapter_needs_llm(requirement):
        return None
    mode = compose_mode(requirement)
    values = slot_values(requirement, evidence)
    required = {
        str(getattr(item, "name", "") or "").strip()
        for item in (getattr(requirement, "extracts", None) or [])
        if bool(getattr(item, "required", True))
    }
    required.discard("")
    paragraphs: list[Paragraph] = []
    uncovered: list[str] = []

    def add_html(raw: str) -> None:
        html = (raw or "").strip()
        if not html:
            return
        paragraphs.append(
            Paragraph(index=len(paragraphs), text=ensure_html_fragment(html), evidence_ids=[])
        )

    if mode == COMPOSE_FIXED:
        add_html(getattr(requirement, "reference_text", "") or "")
        return ChapterDraft(title=requirement.title, paragraphs=paragraphs, uncovered_points=[])

    blocks = list(getattr(requirement, "content_blocks", None) or [])
    if blocks:
        for block in blocks:
            kind = str(getattr(block, "kind", "text") or "text").lower()
            block_mode = normalize_compose(
                getattr(block, "compose", None) or COMPOSE_TEMPLATE_FILL, fixed_output=False
            )
            if block_mode != COMPOSE_TEMPLATE_FILL:
                continue
            if kind == "image":
                add_html('<figure class="image-placeholder"><figcaption>图片占位</figcaption></figure>')
                continue
            source = (
                str(getattr(block, "template_text", "") or "").strip()
                or str(getattr(block, "note", "") or "").strip()
            )
            if kind == "table" and not source:
                source = str(getattr(requirement, "reference_text", "") or "")
            filled, miss = fill_boilerplate_slots(
                source, values, required_names=required, drop_unfilled_sentences=False
            )
            uncovered.extend(miss)
            add_html(filled)
    else:
        source = str(getattr(requirement, "reference_text", "") or "").strip()
        filled, miss = fill_boilerplate_slots(
            source, values, required_names=required, drop_unfilled_sentences=False
        )
        uncovered.extend(miss)
        add_html(filled)

    return ChapterDraft(
        title=requirement.title,
        paragraphs=paragraphs,
        uncovered_points=list(dict.fromkeys(uncovered)),
    )
