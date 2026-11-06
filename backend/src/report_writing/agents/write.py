"""Write Agent：一次只写一章，事实句必须带证据编号；正文统一为完整 HTML。"""

from __future__ import annotations

import json
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

INSTRUCTIONS = """你是报告章节写作员。只写当前这一章，写完用提交工具交稿，不要检索、不要检查。
按 sequence 组织标题，按 describes 理解本章功能，按 generate / points 覆盖内容。

【严禁照搬原文】——全章硬约束（fixed_output=true 除外）：
- 样板/参考正文（reference_text）以及证据摘录中的成段原文，严禁整段、整句原样照抄进正文。
- 原文与样板只作「写作特点与格式」参考：语气、句式节奏、段落展开顺序、小标题/条目习惯、表格与图注形态、单位与数值呈现方式。
- 具体事实（专名、地址、投资额、日期、规模、设备型号、结论表述等）只能来自已采纳证据，并用自己的话按样板写法重写。
- 禁止把样板项目的实例值写进本章；证据不足时写入 uncovered_points，正文用规范表述或「（待补充）」，不要贴原文凑字。

【最高优先级】aux_prompt（补充/辅助提示词）：
- 若 payload 中 aux_prompt 非空，它是本章写作的硬约束，优先级高于 describes / points / generate / reference_text / content_blocks / paragraph_format。
- 必须逐条遵守；与其它字段冲突时，以 aux_prompt 为准。
- aux_prompt 应是写法/体裁/篇幅等约束，不应当作项目元数据来源；其中若误含工程名、站名、投资额、地址等实例，不得当事实写入正文（事实仍只认已采纳证据）。
- 例：写「只输出表格」或「仅表格、不要文字」时：paragraphs 只能含完整 <table>…</table>，禁止任何 <p> 导语、结语、解释、标题复述或列表；不要为凑要点而加叙述段。
- 例：写「不要开头总结」时：不得输出概括性首段。
- 未写明禁止的内容，仍按下方一般规则与证据写作。

关于 reference_text（参考正文）——只学「怎么写 / 什么格式」，不学「写什么」：
- 必须重点模仿：描述方式、句式节奏、语气正式度、段落展开顺序、小标题/条目习惯、表格与图注形态、单位与数值的呈现格式。
- 禁止当作事实来源：不得照搬其中的专名、地址、投资额、日期、规模、结论等具体内容；这些只能来自已采纳证据。
- 禁止整段、整句抄写参考正文；应用同一套写法，把证据里的事实重新写成本章。
- 若同时有 content_blocks，版式以 content_blocks 为准，文风仍优先对齐 reference_text；若 aux_prompt 限制了体裁（如仅表格），则只保留被允许的块。

若 fixed_output 为 true：本章为固定内容。必须把 reference_text 忠实整理为 HTML 输出，不得增删改写事实与措辞，不得编造；evidence_ids 一律为空；uncovered_points 为空。可将纯文本拆成多个 <p>，表格保持为完整 <table>。
按 content_blocks 组织版式：text 用 <p>；table 必须按给出的 rows/cols/headers/merges 写成完整 <table>…</table>（含正确的 rowspan/colspan）；image 只输出占位
<figure class="image-placeholder"><figcaption>图片占位</figcaption></figure>，不要编造图片地址。
名称、数值和日期必须来自已采纳证据（fixed_output 除外）；在内部用 evidence_ids 关联证据，正文 HTML 中不要出现 [ev-1] 这类可见标记。
覆盖不了的要点放入 uncovered_points，不要编造（若 aux_prompt 要求仅表格，未写入表格的要点可放入 uncovered_points，仍不要另写叙述段）。
如果给了检查意见，只改被点名的段落。

输出格式（强制）：
- paragraphs[].text 一律为 HTML 片段，标签必须开始与结束完整，禁止 Markdown。
- 纯文字段落用 <p>…</p>；表格用完整 <table><thead>…</thead><tbody>…</tbody></table>。
- 禁止输出残缺标签、半截表格或未闭合的 p/table/tr/td。
- 禁止在可见正文中输出 [ev-数字] 引用标记。
- 禁止在可见正文中写（来源：…）、文件名引用、「资料缺口」小节、【待补：…】；正文只保留书写内容，缺口只进 uncovered_points。

用 submit_chapter 提交，参数含 title、paragraphs、uncovered_points。
paragraphs 每项含 text（HTML）和 evidence_ids。"""

_BODY_VISIBILITY = """
======== 正文可见性硬约束（必须遵守）========
- 可见正文只写章节内容，禁止出现：（来源：…）、(来源：…)、文件名.docx 引用、「资料缺口」小节标题与列表、【待补：…】。
- 证据关联只用 paragraphs[].evidence_ids；资料不足项只写入 uncovered_points。
======== 约束结束 ========
""".strip()

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
======== 知识库无采纳证据（fallback 模式）========
本章未检索到可采纳证据，但仍须 submit_chapter，且 paragraphs 至少 2 段、不得为空。
- paragraphs[].evidence_ids 一律留空。
- 正文须按 points 逐条展开：先总述本章作用，再分条写清应交代的信息类型与叙述结构（可引用规范表述）。
- 禁止编造具体项目名称、地址、投资额、设备型号数量、批复文号等可核验事实；此类内容在正文中用「（待补充）」占位，并同步写入 uncovered_points。
- 禁止只提交 title 与 uncovered_points 而无 paragraphs。
- 文风仍可对齐 reference_text（仅模仿写法与格式，严禁照搬原文句子或样板项目实例）。
======== fallback 结束 ========
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
) -> ChapterDraft:
    adopted = [card for card in evidence if card.adopted]
    if knowledge_fallback:
        adopted = []
    known = {card.id for card in adopted}
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
    reference_text = (getattr(requirement, "reference_text", None) or "").strip()[:8000]
    aux_prompt = aux_preview
    write_points = write_points_preview
    payload = {
        "sequence": requirement.sequence,
        "title": requirement.title,
        "describes": requirement.describes,
        "generate": requirement.generate,
        "aux_prompt": aux_prompt,
        "aux_prompt_priority": "hard_override" if aux_prompt else "",
        "forbid_supplement_placeholders": forbid_supplement_placeholders,
        "reference_text": reference_text,
        "reference_usage": (
            "fixed_copy"
            if fixed_output
            else (
                "style_format_only：高权重模仿描述方式、写作特点与格式；禁止采用其中的事实内容"
                if reference_text
                else ""
            )
        ),
        "fixed_output": fixed_output,
        "element_results": list(requirement.element_results or []),
        "points": [] if fixed_output else requirement.points,
        "content_blocks": [] if fixed_output else content_blocks,
        "boundary": requirement.boundary,
        "paragraph_format": paragraph_format
        or "输出完整 HTML：文字用 <p>，表格用完整 <table>，插图位置须原样输出要素给出的 {{img:要素id}} 占位符（可包在 <figure><p>图题</p></figure> 中）。",
        "output_format": "html",
        "prior_summary": prior_summary,
        "evidence": [] if fixed_output else [card.to_dict() for card in adopted],
        "comments": [item.to_dict() for item in comments or []],
        "previous": previous.to_dict() if previous else None,
        "knowledge_fallback": bool(knowledge_fallback),
    }
    chosen = model or WriteScriptModel()
    from report_writing.services.robots import resolve_prompt

    instructions = resolve_prompt("write", robot, default=INSTRUCTIONS)
    instructions = f"{instructions}\n\n{_BODY_VISIBILITY}"
    if knowledge_fallback:
        instructions = f"{instructions}\n\n{_FALLBACK_WRITE}"
    if aux_prompt:
        instructions = (
            f"{instructions}\n\n"
            "======== 本章补充提示词（硬约束，必须遵守）========\n"
            f"{aux_prompt}\n"
            "说明：以上仅为写法/体裁约束；严禁把其中的项目元数据或样板实例当事实照搬。"
            "事实只来自已采纳证据，原文只作格式与文风参考。\n"
            "======== 以上补充提示词结束 ========"
        )
    if forbid_supplement_placeholders:
        instructions = (
            f"{instructions}\n\n"
            "======== 待补充占位禁止 ========\n"
            "本章写作要点均已配置完毕，无待补资料项。"
            "可见正文中禁止出现「（待补充：…）」「（待填写）」「待补充：」等占位表述；"
            "缺具体数值时用要素结果或已采纳证据填写，否则用概括性表述，不要留待补括号。\n"
            "======== 禁止占位结束 ========"
        )
    result = run_react(
        chosen,
        [submit_chapter],
        instructions,
        json.dumps(payload, ensure_ascii=False),
        f"write-{requirement.key}",
    )
    draft = ChapterDraft.from_dict(tool_result(result["messages"], "submit_chapter"))
    draft = _sanitize_draft_body(draft, strip_supplement_markers=forbid_supplement_placeholders)
    draft = _apply_aux_output_constraints(draft, aux_prompt)
    # 模型未产出可见段落时一律落骨架，避免进度里「已跑完」但正文为空。
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
    describes = (requirement.describes or "").strip()
    paragraphs: list[Paragraph] = []
    if describes:
        paragraphs.append(
            Paragraph(
                index=0,
                text=ensure_html_fragment(f"<p>{describes}</p>"),
                evidence_ids=[],
            )
        )
    intro = (
        f"<p>本节「{title}」应依据项目立项、可研及设计文件，交代工程组成、建设规模与主要技术方案。"
        f"当前知识库未检索到可采纳证据，下列内容以报告体例与行业惯例组织，具体参数须据实补充。</p>"
    )
    paragraphs.append(Paragraph(index=len(paragraphs), text=ensure_html_fragment(intro), evidence_ids=[]))
    uncovered = list(draft.uncovered_points or [])
    for point in points[:12]:
        clean_point = point
        if forbid_supplement_placeholders:
            clean_point = SUPPLEMENT_TODO_RE.sub("", point).strip()
        paragraphs.append(
            Paragraph(
                index=len(paragraphs),
                text=ensure_html_fragment(
                    f"<p><strong>{clean_point}</strong>：应在本节说明与之相关的工程内容、技术原则及数据来源。"
                    + (
                        "具体参数结合项目资料与关联要素填写。</p>"
                        if forbid_supplement_placeholders
                        else "具体数值、名称与规格型号等待补充项目资料后填写（待补充）。</p>"
                    )
                ),
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
