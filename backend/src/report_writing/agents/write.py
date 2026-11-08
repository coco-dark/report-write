"""Write Agent：一次只写一章，事实句必须带证据编号；正文统一为完整 HTML。"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain.tools import tool
from pydantic import Field

from report_writing.agents.react import run_react, tool_result
from report_writing.agents.scripted import ScriptedChatModel, human_payload, tool_payloads
from report_writing.domain.content_blocks import ensure_html_fragment
from report_writing.domain.point_items_sync import SUPPLEMENT_TODO_RE, text_needs_supplement
from report_writing.domain.models import ChapterDraft, ChapterRequirement, EvidenceCard, Paragraph, ParagraphComment

_log = logging.getLogger("uvicorn.error")
_LLM_LOG_CHUNK = 12000


def _log_write_llm_payload(*, key: str, title: str, instructions: str, user_text: str) -> None:
    """把送给写作模型的系统提示 + 用户材料打到后台日志，便于核对要素与章节配置。"""
    sys_text = str(instructions or "")
    user = str(user_text or "")
    _log.info(
        "write-llm chapter=%s title=%s system_chars=%s user_chars=%s",
        key,
        title,
        len(sys_text),
        len(user),
    )
    _log_chunks("write-llm system_prompt", sys_text)
    _log_chunks("write-llm user_payload", user)


def _log_chunks(label: str, text: str) -> None:
    raw = str(text or "")
    if not raw:
        _log.info("%s: (empty)", label)
        return
    total = max(1, (len(raw) + _LLM_LOG_CHUNK - 1) // _LLM_LOG_CHUNK)
    for i in range(total):
        piece = raw[i * _LLM_LOG_CHUNK : (i + 1) * _LLM_LOG_CHUNK]
        _log.info("%s [%s/%s]\n%s", label, i + 1, total, piece)

INSTRUCTIONS = """# Role
你是资深文档写作专家。文档类型由 payload.doc_category 指定（函件、方案、说明书、纪要、合同、述职、报告等）。未指定时不要默认写成研究报告。称谓、结构、语气须匹配该类型。

# Task
只撰写当前这一章的正式正文。完成后调用工具 submit_chapter 交稿。
禁止检索，禁止审稿，禁止一次写多章。

# Inputs
用户消息为 JSON。优先阅读 `writing_materials`（已按标准标签组织的本章材料），再对照同名字段。

| 字段 | 作用 | 用法 |
| describes | 章节说明 | 约束风格、组成顺序、描述方式、排版、邻章边界。其中「如：…」是通例，不是正文用词。禁止把本段贴进正文。 |
| points / point_write_plan | 写作要点 | 按顺序逐条覆盖。有 element_value 时写入该块事实。不要把要点原文当小标题。 |
| element_results | 章节要素 | 事实来源。原文可能已按本章要点摘录；只使用材料中出现的内容，不要补全未给出的原文。 |
| aux_prompt | 补充提示词 | 仅当用户手填且非空时生效，是写法/体裁硬约束。 |
| reference_text | 参考正文 | 学写法与版式；fixed 则原文输出。禁止把样板实例当本项目事实。 |
| content_blocks | 版式块 | 学表头、列名与版式。表格用完整 <table>，文字用 <p>，图片只占位。行名以 describes 与要点为准。 |
| evidence | 已采纳证据 | 专名、数值、日期、结论的事实来源。嵌进对应组成块，不要单开「资料摘录」节。 |
| comments / previous | 改稿上下文 | 有则在上一稿上按意见修改。 |
| sequence | 章号 | 只作章号，不要另起目录。 |

# Priority
从高到低执行，后者不得推翻前者：
1. aux_prompt（写法硬约束。其中的工程名、投资额等不当事实）
2. comments（上一轮检查缺陷必须改掉）
3. describes + points / point_write_plan
4. element_value 与已采纳 evidence（事实）
5. reference_text 与 content_blocks（写法与版式）

# Strategy
以 payload.writing_strategy 为准，五种不可混用：source_only / template_fill / hybrid / auto_generate / fixed。细则见随后策略段。
- fixed：按 reference_text 原文输出，不改写。
- template_fill：只替换 {{槽}}，骨架不改，缺槽留空。
- source_only / hybrid：骨架只学写法；事实只来自证据或要素。
- auto_generate：对照写法与版式，严禁整段照抄；无证据也要写完整章。

# Constraints
- 某组成块写不了：放入 uncovered_points，其余块照常写完。aux_prompt 要求仅表格时，未入表要点进 uncovered_points，不要另写叙述段。
- comments.index≥0 改对应段落；index=-1 按 message 修正全章结构。有 previous 时在上一稿上改，不要跑题重写。
- 可见正文禁止：[ev-数字]、（来源：…）、文件名引用、「资料缺口」、【待补：…】。证据只用 paragraphs[].evidence_ids。
- 禁止编造证据中没有的专名、数值、日期（fixed / auto_generate 无证据时除外）。

# Output
调用 submit_chapter，参数：
- title: string
- paragraphs: [{ "text": HTML片段, "evidence_ids": string[] }]
- uncovered_points: string[]

HTML 规则：
- 段落用 <p>…</p>；表格用完整 <table><thead>…</thead><tbody>…</tbody></table>。
- 标签必须开闭完整。禁止 Markdown、半截表格、未闭合标签。
"""

_CITE = re.compile(r"\[(ev-\d+)\]")
# （来源：…）或 (来源：…)；允许嵌套较短括号
_SOURCE_PAREN = re.compile(
    r"[（(]\s*来源\s*[:：][^（）()]{0,240}[）)]"
)
_SOURCE_SHORTHAND = re.compile(r"[（(]\s*来源\s*[:：]\s*同上[^）)]{0,80}[）)]")
_PENDING_MARK = re.compile(r"【\s*待补[^】]{0,120}】")
_GAP_HEADING = re.compile(
    r"(?is)<h[1-6][^>]*>\s*[^<]*资料缺口[^<]*</h[1-6]>\s*(?:<(?:ul|ol)[^>]*>.*?</(?:ul|ol)>\s*)?"
)
_GAP_SECTION_P = re.compile(
    r"(?is)<p[^>]*>\s*(?:<strong>)?\s*资料缺口\s*(?:</strong>)?\s*[:：]?.*?</p>\s*"
    r"(?:<(?:ul|ol)[^>]*>.*?</(?:ul|ol)>\s*)?"
)


class WriteScriptModel(ScriptedChatModel):
    role: str = "write"

    def decide(self, messages: list[BaseMessage]):
        if tool_payloads(messages, "submit_chapter"):
            return self.finish("章节已提交")
        payload = human_payload(messages)
        comments = payload.get("comments") or []
        previous = payload.get("previous")
        if comments and previous:
            arguments = _revise_arguments(previous, comments, payload.get("evidence") or [])
        else:
            arguments = _draft_arguments(payload)
        return self.call("submit_chapter", arguments)


_FALLBACK_WRITE = """
## Fallback（无已采纳证据）
仍须调用 submit_chapter，paragraphs 至少 2 段且不得为空。
- evidence_ids 留空。
- 按 points 顺序与 describes 格式成文；不要另起提纲，不要把要点句贴进正文。
- 禁止编造专名、地址、投资额、设备型号、批复文号；缺事实写入 uncovered_points，正文用概括表述，不要写「（待补充）」。
- 参考正文只模仿写法与格式，禁止照搬样板实例。
""".strip()

_AUTO_GENERATE_NO_EVIDENCE = """
## Fallback（auto_generate 且无项目资料）
必须写出完整章节。按 points 顺序、describes 约束成文。
禁止整章转人工、只列缺口、「（待补充）」、把要点列表当正文、照抄 reference_text 句子。
""".strip()


def run_write(
    requirement: ChapterRequirement,
    paragraph_format: str,
    evidence: list[EvidenceCard],
    comments: list[ParagraphComment] | None = None,
    previous: ChapterDraft | None = None,
    prior_summary: str = "",
    model: BaseChatModel | None = None,
    *,
    robot=None,
    knowledge_fallback: bool = False,
    doc_category: str = "",
) -> ChapterDraft:
    adopted = [card for card in evidence if card.adopted]
    if knowledge_fallback:
        adopted = []
    known = {card.id for card in adopted}
    from report_writing.domain.chapter_compose import (
        COMPOSE_AUTO_GENERATE,
        COMPOSE_FIXED,
        COMPOSE_TEMPLATE_FILL,
        compose_mode,
        try_compose_draft,
    )
    from report_writing.domain.compose_analysis import reference_usage_hint, write_strategy_block

    composed = try_compose_draft(requirement, adopted)
    if composed is not None:
        return composed
    write_points_preview = list(requirement.points or [])
    aux_preview = (getattr(requirement, "aux_prompt", None) or "").strip()
    forbid_supplement_placeholders = not any(
        text_needs_supplement(p) for p in write_points_preview
    ) and ("【建议补充资料】" not in aux_preview)

    @tool(
        "submit_chapter",
        description=(
            "何时：本章已写完，提交 HTML 草稿。写作结束动作。"
            "不要：检索、再调其它工具；证据编号写在 paragraphs[].evidence_ids。"
        ),
    )
    def submit_chapter(
        title: Annotated[str, Field(description="必填。本章标题。")],
        paragraphs: Annotated[list[dict], Field(description="必填。每项含 text（HTML）和 evidence_ids。")],
        uncovered_points: Annotated[list[str], Field(description="可选。覆盖不了的要点，不要编造。")],
    ) -> str:
        stored = []
        extracted_gaps: list[str] = []
        for index, item in enumerate(paragraphs):
            raw = str(item.get("text") or "").strip()
            evidence_ids = [item_id for item_id in item.get("evidence_ids") or [] if item_id in known]
            for cite in _CITE.findall(raw):
                if cite in known and cite not in evidence_ids:
                    evidence_ids.append(cite)
            cleaned, gaps = _sanitize_visible_body(
                raw,
                strip_supplement_markers=forbid_supplement_placeholders,
            )
            extracted_gaps.extend(gaps)
            if not cleaned.strip():
                continue
            text = ensure_html_fragment(cleaned.strip())
            stored.append(Paragraph(index=len(stored), text=text, evidence_ids=evidence_ids))
        uncovered = _merge_uncovered(list(uncovered_points or []), extracted_gaps)
        draft = ChapterDraft(title=title or requirement.title, paragraphs=stored, uncovered_points=uncovered)
        return json.dumps(draft.to_dict(), ensure_ascii=False)

    content_blocks = [
        item.to_dict() if hasattr(item, "to_dict") else item
        for item in (getattr(requirement, "content_blocks", None) or [])
    ]
    fixed_output = bool(getattr(requirement, "fixed_output", False))
    reference_text = (getattr(requirement, "reference_text", None) or "").strip()
    aux_prompt = aux_preview
    write_points = write_points_preview
    from report_writing.domain.doc_genre import instructions_with_genre, normalize_doc_category, writer_persona
    from report_writing.domain.point_items_sync import build_point_write_plan
    from report_writing.domain.write_materials import (
        assemble_write_materials,
        pack_element_rows,
        pack_evidence_dicts,
        pack_point_write_plan,
        queries_for_write,
        excerpt_by_queries,
        WRITE_REF_BUDGET,
    )

    point_write_plan = [] if fixed_output else build_point_write_plan(
        getattr(requirement, "point_items", None),
        getattr(requirement, "element_results", None),
        points=write_points,
    )
    doc_cat = normalize_doc_category(doc_category)
    queries = queries_for_write(requirement, extra=list(write_points or []))
    packed_elements = pack_element_rows(
        list(requirement.element_results or []),
        queries=queries,
        prefer_ids={
            str(row.get("element_id") or "").strip()
            for row in point_write_plan
            if str(row.get("element_id") or "").strip()
        },
    )
    packed_plan = pack_point_write_plan(point_write_plan, queries=queries, packed_elements=packed_elements)
    ref_for_model, _ = excerpt_by_queries(reference_text, queries, WRITE_REF_BUDGET)
    writing_materials = assemble_write_materials(
        requirement,
        point_write_plan=packed_plan,
        reference_text=ref_for_model,
        aux_prompt=aux_prompt,
        element_results=packed_elements,
    )
    payload = {
        "doc_category": doc_cat,
        "writer_role": writer_persona(doc_cat),
        "sequence": requirement.sequence,
        "title": requirement.title,
        "writing_materials": writing_materials,
        "describes": requirement.describes,
        "generate": requirement.generate,
        "aux_prompt": aux_prompt,
        "aux_prompt_priority": "hard_override" if aux_prompt else "",
        "forbid_supplement_placeholders": forbid_supplement_placeholders,
        "reference_text": ref_for_model,
        "writing_strategy": compose_mode(requirement),
        "reference_usage": reference_usage_hint(
            compose_mode(requirement), has_reference=bool(reference_text)
        ),
        "fixed_output": fixed_output,
        "element_results": packed_elements,
        "point_write_plan": packed_plan,
        "points": [] if fixed_output else requirement.points,
        "content_blocks": [] if fixed_output else content_blocks,
        "boundary": requirement.boundary,
        "paragraph_format": paragraph_format
        or "输出完整 HTML：文字用 <p>，表格用完整 <table>，插图位置须原样输出要素给出的 {{img:要素id}} 占位符（可包在 <figure><p>图题</p></figure> 中）。",
        "output_format": "html",
        "prior_summary": prior_summary,
        "evidence": []
        if fixed_output
        else pack_evidence_dicts([card.to_dict() for card in adopted], queries=queries),
        "comments": [item.to_dict() for item in comments or []],
        "previous": previous.to_dict() if previous else None,
        "knowledge_fallback": bool(knowledge_fallback),
    }
    chosen = model or WriteScriptModel()
    from report_writing.services.robots import resolve_prompt

    instructions = resolve_prompt("write", robot, default=INSTRUCTIONS)
    instructions = instructions_with_genre(instructions, doc_cat)
    strategy = compose_mode(requirement)
    instructions = f"{instructions}\n\n{write_strategy_block(compose=strategy)}"
    if knowledge_fallback and strategy == COMPOSE_AUTO_GENERATE:
        instructions = f"{instructions}\n\n{_AUTO_GENERATE_NO_EVIDENCE}"
    elif knowledge_fallback and strategy not in {COMPOSE_FIXED, COMPOSE_TEMPLATE_FILL}:
        instructions = f"{instructions}\n\n{_FALLBACK_WRITE}"
    if aux_prompt:
        instructions = (
            f"{instructions}\n\n"
            "## Chapter extra_prompt\n"
            "以下为用户手填的写法硬约束，优先于 describes / points / reference_text。"
            "其中的项目元数据或样板实例不当事实；事实只来自已采纳证据或要素取值。\n"
            f"<extra_prompt>\n{aux_prompt}\n</extra_prompt>"
        )
    if comments:
        defect_lines = []
        for item in comments:
            idx = int(getattr(item, "index", -1) or -1)
            msg = str(getattr(item, "message", "") or "").strip()
            if not msg:
                continue
            defect_lines.append(
                f"- 第 {idx + 1} 段：{msg}" if idx >= 0 else f"- 整章：{msg}"
            )
        if defect_lines:
            instructions = (
                f"{instructions}\n\n"
                "## Review comments (must fix before submit)\n"
                + "\n".join(defect_lines[:12])
            )
    if forbid_supplement_placeholders:
        instructions = (
            f"{instructions}\n\n"
            "## Placeholder ban\n"
            "本章写作要点已配置完毕。可见正文禁止「（待补充：…）」「（待填写）」「待补充：」。"
            "缺具体数值时用要素结果或已采纳证据填写，否则用概括性表述。"
        )
    user_text = json.dumps(payload, ensure_ascii=False)
    _log_write_llm_payload(
        key=str(requirement.key or ""),
        title=str(requirement.title or ""),
        instructions=instructions,
        user_text=user_text,
    )
    result = run_react(
        chosen,
        [submit_chapter],
        instructions,
        user_text,
        f"write-{requirement.key}",
    )
    draft = ChapterDraft.from_dict(tool_result(result["messages"], "submit_chapter"))
    draft = _sanitize_draft_body(draft, strip_supplement_markers=forbid_supplement_placeholders)
    draft = _apply_aux_output_constraints(draft, aux_prompt)
    # 混合策略：模型若只列缺口不交正文，用要点生成可编辑骨架。资料驱动无资料不凑正文。
    from report_writing.domain.chapter_compose import COMPOSE_SOURCE_ONLY, compose_mode as _cm

    if _cm(requirement) != COMPOSE_SOURCE_ONLY:
        draft = _ensure_fallback_draft_body(requirement, draft, forbid_supplement_placeholders)
    return draft


def _ensure_fallback_draft_body(
    requirement: ChapterRequirement,
    draft: ChapterDraft,
    forbid_supplement_placeholders: bool = False,
) -> ChapterDraft:
    """无证据写作时，模型若只列缺口不交正文，用要点生成可编辑骨架段落。"""
    has_body = any(re.sub(r"(?s)<[^>]+>", "", (p.text or "")).strip() for p in draft.paragraphs)
    if has_body:
        return draft
    title = (requirement.title or draft.title or "本章").strip()
    points = [str(p).strip() for p in (requirement.points or []) if str(p).strip()]
    paragraphs: list[Paragraph] = []
    uncovered = list(draft.uncovered_points or [])

    def _block_label(point: str) -> str:
        m = re.match(r"【([^】]+)】", point)
        if m:
            return m.group(1).strip()
        head = re.split(r"[；;]", point, 1)[0].strip()
        return head[:24] if head else "本节"

    def _block_body(point: str) -> str:
        text = point
        if forbid_supplement_placeholders:
            text = SUPPLEMENT_TODO_RE.sub("", text).strip()
        text = re.sub(r"^【[^】]+】", "", text).strip()
        text = re.sub(r"版式\s*[:：][^；;]*[；;]?", "", text)
        text = re.sub(r"须覆盖\s*[:：]", "", text).strip(" ；;。")
        return text or point

    if not points:
        paragraphs.append(
            Paragraph(
                index=0,
                text=ensure_html_fragment(f"<p>「{title}」按本章既定结构撰写，具体内容结合项目资料补充。</p>"),
                evidence_ids=[],
            )
        )
    for point in points[:12]:
        label = _block_label(point)
        body = _block_body(point)
        hint = (
            "具体参数结合项目资料与关联要素据实填写。"
            if forbid_supplement_placeholders
            else "具体名称、数值与规格待项目资料齐备后写入。"
        )
        paragraphs.append(
            Paragraph(
                index=len(paragraphs),
                text=ensure_html_fragment(f"<p>{label}方面，{body}。{hint}</p>"),
                evidence_ids=[],
            )
        )
        if not forbid_supplement_placeholders:
            tag = f"待补充：{point}"
            if tag not in uncovered:
                uncovered.append(tag)
    if not paragraphs:
        paragraphs.append(
            Paragraph(
                index=0,
                text=ensure_html_fragment(f"<p>本节「{title}」正文待根据项目资料补充。</p>"),
                evidence_ids=[],
            )
        )
    return ChapterDraft(
        title=draft.title or title,
        paragraphs=paragraphs,
        uncovered_points=uncovered[:30],
    )


def _merge_uncovered(base: list[str], extra: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in list(base or []) + list(extra or []):
        text = re.sub(r"\s+", " ", str(item or "").strip())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _sanitize_visible_body(html: str, *, strip_supplement_markers: bool = False) -> tuple[str, list[str]]:
    """去掉正文中的来源标注 / 资料缺口 / 待补标记，缺口条目单独返回。"""
    text = html or ""
    gaps: list[str] = []

    def _gap_from_block(block: str) -> None:
        plain = re.sub(r"(?s)<[^>]+>", " ", block or "")
        plain = re.sub(r"\s+", " ", plain).strip()
        plain = re.sub(r"^资料缺口\s*[:：]?\s*", "", plain)
        if plain:
            gaps.append(plain[:240])

    for pattern in (_GAP_HEADING, _GAP_SECTION_P):
        for match in pattern.finditer(text):
            _gap_from_block(match.group(0))
        text = pattern.sub("", text)

    for match in _PENDING_MARK.finditer(text):
        mark = match.group(0)
        plain = mark.strip("【】").strip()
        if plain:
            gaps.append(plain[:240])
    text = _PENDING_MARK.sub("", text)
    text = _SOURCE_SHORTHAND.sub("", text)
    text = _SOURCE_PAREN.sub("", text)
    if strip_supplement_markers:
        text = SUPPLEMENT_TODO_RE.sub("", text)
        text = re.sub(r"待补充[：:][^。；;\n<]{0,80}", "", text)
    # 偶发「来源：xxx.docx」无括号残留
    text = re.sub(r"(?<=[。；;，,\s>])来源\s*[:：][^\n<。；;]{2,120}", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?is)(<p[^>]*>)\s*(</p>)", "", text)
    text = re.sub(r"\s+(</(?:p|li|td|th|h[1-6])>)", r"\1", text)
    text = re.sub(r"(<(?:p|li|td|th|h[1-6])[^>]*>)\s+", r"\1", text)
    return text.strip(), gaps


def _sanitize_draft_body(
    draft: ChapterDraft,
    *,
    strip_supplement_markers: bool = False,
) -> ChapterDraft:
    stored: list[Paragraph] = []
    gaps: list[str] = []
    for item in draft.paragraphs or []:
        cleaned, found = _sanitize_visible_body(
            item.text or "",
            strip_supplement_markers=strip_supplement_markers,
        )
        gaps.extend(found)
        if not cleaned.strip():
            continue
        stored.append(
            Paragraph(
                index=len(stored),
                text=ensure_html_fragment(cleaned),
                evidence_ids=list(item.evidence_ids or []),
            )
        )
    return ChapterDraft(
        title=draft.title,
        paragraphs=stored,
        uncovered_points=_merge_uncovered(list(draft.uncovered_points or []), gaps),
    )


def _apply_aux_output_constraints(draft: ChapterDraft, aux_prompt: str) -> ChapterDraft:
    """按辅助提示词做轻量后处理，兜住模型仍多写叙述的情况。"""
    aux = (aux_prompt or "").strip()
    if not aux or not draft.paragraphs:
        return draft
    if not _aux_wants_table_only(aux):
        return draft
    kept = []
    dropped = []
    for item in draft.paragraphs:
        html = item.text or ""
        if re.search(r"(?is)<table\b", html):
            # 去掉表格前后多余说明文字，只保留 table 块
            tables = re.findall(r"(?is)<table\b.*?</table>", html)
            if tables:
                text = ensure_html_fragment("\n".join(tables))
                kept.append(Paragraph(index=len(kept), text=text, evidence_ids=list(item.evidence_ids or [])))
            else:
                kept.append(Paragraph(index=len(kept), text=html, evidence_ids=list(item.evidence_ids or [])))
        else:
            plain = re.sub(r"(?s)<[^>]+>", "", html).strip()
            if plain:
                dropped.append(plain[:80])
    if not kept:
        return draft
    uncovered = list(draft.uncovered_points or [])
    if dropped:
        note = "辅助提示词要求仅表格，已去掉叙述：" + "；".join(dropped[:3])
        if note not in uncovered:
            uncovered.append(note)
    return ChapterDraft(title=draft.title, paragraphs=kept, uncovered_points=uncovered)


def _aux_wants_table_only(aux_prompt: str) -> bool:
    text = re.sub(r"\s+", "", (aux_prompt or "").lower())
    if not text:
        return False
    positive = any(
        key in text
        for key in (
            "只输出表格",
            "仅输出表格",
            "只要表格",
            "仅表格",
            "只写表格",
            "只生成表格",
            "仅生成表格",
            "表格内容",
            "onlytable",
            "tableonly",
        )
    )
    if not positive:
        # 「不要文字/不要叙述」且提到表格
        if "表格" in text and any(key in text for key in ("不要文字", "不要叙述", "不要段落", "禁止文字", "无文字")):
            return True
        return False
    # 明确「表格+文字」则不裁切
    if any(key in text for key in ("表格和文字", "表格与文字", "文字和表格", "含文字", "加上文字")):
        return False
    return True


def _draft_arguments(payload: dict) -> dict:
    title = payload.get("title") or ""
    paragraphs = []
    uncovered = []
    evidence = payload.get("evidence") or []
    if payload.get("knowledge_fallback") and not evidence:
        req = ChapterRequirement(
            key="k",
            title=title,
            points=list(payload.get("points") or []),
            describes=str(payload.get("describes") or ""),
        )
        skeleton = _ensure_fallback_draft_body(req, ChapterDraft(title=title, paragraphs=[], uncovered_points=[]))
        return {
            "title": skeleton.title,
            "paragraphs": [{"text": p.text, "evidence_ids": []} for p in skeleton.paragraphs],
            "uncovered_points": skeleton.uncovered_points,
        }
    if payload.get("fixed_output"):
        fixed = str(payload.get("reference_text") or "").strip()
        if fixed:
            paragraphs.append({"text": ensure_html_fragment(fixed), "evidence_ids": []})
        return {"title": title, "paragraphs": paragraphs, "uncovered_points": uncovered}
    table_only = _aux_wants_table_only(str(payload.get("aux_prompt") or ""))
    blocks = payload.get("content_blocks") or []
    if blocks:
        for block in blocks:
            kind = str(block.get("kind") or "text").lower()
            if kind == "image":
                if table_only:
                    continue
                paragraphs.append(
                    {
                        "text": '<figure class="image-placeholder"><figcaption>图片占位</figcaption></figure>',
                        "evidence_ids": [],
                    }
                )
                continue
            if kind == "table":
                paragraphs.append({"text": _scripted_table_html(block, evidence), "evidence_ids": _first_ids(evidence)})
                continue
            if table_only:
                continue
            card = evidence[0] if evidence else None
            if card is None:
                continue
            paragraphs.append(
                {
                    "text": ensure_html_fragment(f"{title}：{card['excerpt']}"),
                    "evidence_ids": [card["id"]],
                }
            )
        if paragraphs:
            return {"title": title, "paragraphs": paragraphs, "uncovered_points": uncovered}
    for point in payload.get("points") or []:
        card = next((item for item in evidence if item.get("point") == point or point in item.get("excerpt", "")), None)
        if card is None:
            uncovered.append(point)
            continue
        paragraphs.append(
            {
                "text": ensure_html_fragment(f"{title}涉及{point}。{card['excerpt']}"),
                "evidence_ids": [card["id"]],
            }
        )
    return {"title": title, "paragraphs": paragraphs, "uncovered_points": uncovered}


def _first_ids(evidence: list[dict]) -> list[str]:
    if not evidence:
        return []
    return [evidence[0]["id"]]


def _scripted_table_html(block: dict, evidence: list[dict]) -> str:
    rows = max(1, int(block.get("rows") or 0) or len(block.get("cells_sample") or []) or 2)
    cols = max(1, int(block.get("cols") or 0) or (len((block.get("cells_sample") or [[]])[0]) if block.get("cells_sample") else 2))
    headers = list(block.get("headers") or [])
    sample = block.get("cells_sample") or []
    merge_map = {
        (int(m.get("row") or 0), int(m.get("col") or 0)): m for m in (block.get("merges") or []) if isinstance(m, dict)
    }
    skip: set[tuple[int, int]] = set()
    thead = ""
    if headers:
        cells = "".join(f"<th>{_esc(h)}</th>" for h in (headers + [""] * cols)[:cols])
        thead = f"<thead><tr>{cells}</tr></thead>"
        start = 1 if sample and sample[0] == headers else 0
    else:
        start = 0
    body_rows = []
    for r in range(start, rows):
        tds = []
        for c in range(cols):
            if (r, c) in skip:
                continue
            merge = merge_map.get((r, c))
            attrs = ""
            if merge:
                rs = max(1, int(merge.get("rowspan") or 1))
                cs = max(1, int(merge.get("colspan") or 1))
                if rs > 1:
                    attrs += f' rowspan="{rs}"'
                if cs > 1:
                    attrs += f' colspan="{cs}"'
                for rr in range(r, r + rs):
                    for cc in range(c, c + cs):
                        if (rr, cc) != (r, c):
                            skip.add((rr, cc))
            value = ""
            if r < len(sample) and c < len(sample[r]):
                value = str(sample[r][c] or "")
            if not value and evidence and r == start and c == 0:
                value = str(evidence[0].get("excerpt") or "")[:40]
            tds.append(f"<td{attrs}>{_esc(value)}</td>")
        body_rows.append(f"<tr>{''.join(tds)}</tr>")
    tbody = f"<tbody>{''.join(body_rows)}</tbody>"
    note = _esc(str(block.get("note") or ""))
    return f'<!-- {note} --><table border="1" cellspacing="0" cellpadding="4">{thead}{tbody}</table>'


def _esc(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _revise_arguments(previous: dict, comments: list[dict], evidence: list[dict]) -> dict:
    flagged = {int(item["index"]): item.get("message") or "" for item in comments}
    paragraphs = []
    for item in previous.get("paragraphs") or []:
        index = int(item["index"])
        if index not in flagged:
            paragraphs.append(
                {
                    "text": ensure_html_fragment(item.get("text") or ""),
                    "evidence_ids": item.get("evidence_ids") or [],
                }
            )
            continue
        card = next((card for card in evidence if card["id"] in (item.get("evidence_ids") or [])), None)
        if card is None and evidence:
            card = evidence[0]
        text = f"<p>{card['excerpt']}</p>" if card else item.get("text") or ""
        paragraphs.append({"text": ensure_html_fragment(text), "evidence_ids": [card["id"]] if card else []})
    return {
        "title": previous.get("title") or "",
        "paragraphs": paragraphs,
        "uncovered_points": previous.get("uncovered_points") or [],
    }
