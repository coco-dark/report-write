"""Study Agent：只读模板，提交书写要求。"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from report_writing.agents.react import run_react, tool_result
from report_writing.agents.scripted import ScriptedChatModel, tool_payloads
from report_writing.services.corpus import ReportTemplate
from report_writing.domain.chapter_locator import CHAPTER_EXCERPT_MAX_CHARS
from report_writing.domain.content_blocks import normalize_content_blocks
from report_writing.domain.models import ChapterRequirement, ContentBlock, ExtractItem, WritingBrief

_log = logging.getLogger("uvicorn.error")
_SAMPLE_LIMIT = CHAPTER_EXCERPT_MAX_CHARS

INSTRUCTIONS = """你是报告章节配置助手（对齐 anyuan V2 章节分析）。只阅读已经写过的样板正文，不要阅读本次项目材料，不要写新报告。
先 list_chapters，再对每一章 read_chapter，最后 submit_writing_brief。
submit_writing_brief 的参数包含 voice、layout、paragraph_format、chapters。

分两层：固定层=版式与写法；元数据层=随项目而变的名称/型号/数值等 → 只进 extracts，不进 generate。

chapters 每一项都要写清：
- key、title、sequence、level、parent：沿用样板编号与层级，不要改。
- describes：章节说明（功能+结构）；有表格时点明固定栏目与可变填报；禁止样板实例。
- extracts：本章须从本项目资料取值的元数据（name/kind/hint/required/style）；
  name 用信息类型（工程名称、设备型号…），禁止填样板里的实例值。
- generate / points：写法指引（结构、语气、须覆盖的信息类型）；表格写清列/行与固定 vs 按项目填；
  禁止工程名、设备型号、投资额、日期等样板实例。
- keywords：可跨项目复用的检索词；禁止工程专名与型号串。
- reference_text：样板原文摘录（可含具体信息，仅供格式对照）。
- content_blocks：文字/表格/图片；表格含 headers、column_roles、fixed_items（栏目名/单位/套话）、
  variable_items（信息类型）。
- paragraph_format：正文输出完整 HTML（p/table/figure），标签成对闭合。
- boundary、depends_on。"""


class StudyScriptModel(ScriptedChatModel):
    role: str = "study"
    template_json: str = ""

    def decide(self, messages: list[BaseMessage]):
        if tool_payloads(messages, "submit_writing_brief"):
            return self.finish("书写要求已提交")
        if not tool_payloads(messages, "list_chapters"):
            return self.call("list_chapters")
        template = json.loads(self.template_json)
        read_keys = [item["key"] for item in tool_payloads(messages, "read_chapter")]
        for chapter in template["chapters"]:
            if chapter["key"] not in read_keys:
                return self.call("read_chapter", {"key": chapter["key"]})
        return self.call("submit_writing_brief", _brief_arguments(template))


def run_study(template: ReportTemplate, model: BaseChatModel | None = None, *, robot=None) -> WritingBrief:
    if model is not None and not isinstance(model, StudyScriptModel):
        return _study_with_model(template, model, robot=robot)
    from report_writing.services.robots import resolve_prompt

    tools = _tools(template)
    chosen = model or StudyScriptModel(template_json=json.dumps(_template_payload(template), ensure_ascii=False))
    result = run_react(
        chosen,
        tools,
        resolve_prompt("study", robot, default=INSTRUCTIONS),
        "请分析这份报告模板并提交书写要求。",
        "study",
        rounds=len(template.chapters) + 3,
    )
    return WritingBrief.from_dict(tool_result(result["messages"], "submit_writing_brief"))


def _tools(template: ReportTemplate):
    payload = _template_payload(template)

    @tool
    def list_chapters() -> str:
        """列出模板中的章节键、标题和层级。"""
        return json.dumps(
            [{"key": item["key"], "sequence": item.get("sequence") or "", "title": item["title"], "level": item["level"]} for item in payload["chapters"]],
            ensure_ascii=False,
        )

    @tool
    def read_chapter(key: str) -> str:
        """阅读某一章的范文段落和已有要点。"""
        title = next((item["title"] for item in payload["chapters"] if item["key"] == key), key)
        _log.info("模型正在阅读 %s %s", key, title)
        for item in payload["chapters"]:
            if item["key"] == key:
                return json.dumps(item, ensure_ascii=False)
        return json.dumps({"error": f"未知章节 {key}"}, ensure_ascii=False)

    @tool
    def submit_writing_brief(
        voice: str,
        layout: str,
        paragraph_format: str,
        chapters: list[dict],
    ) -> str:
        """提交整份书写要求。这是分析要点阶段的结束动作。"""
        brief = _assemble(template, voice, layout, paragraph_format, chapters)
        return json.dumps(brief.to_dict(), ensure_ascii=False)

    return [list_chapters, read_chapter, submit_writing_brief]


_BATCH = 6
_MAX_BATCHES = 5

_BATCH_INSTRUCTIONS = """你是报告章节配置助手。根据每章「sample」原文节选，提取可跨项目复用的写作配置（不要写新报告）。
按输入数组顺序返回等长 JSON 数组；key、sequence 原样照抄。

分两层，不要混：
A. 固定层：版式、栏目名、单位、写法顺序、跨项目套话 → describes / writing_style / generate / content_blocks。
B. 元数据层：随项目而变的工程名、站名、公司、设备型号规格、电压/容量/投资/坐标/日期/文号等
  → 一律放 extracts（name=信息类型，hint=怎么填），禁止写进 generate/describes/keywords/fixed_items。
样板里的具体值只是示例。仅 reference_text 可保留原文摘录。

每项字段：
- describes（80～280 字）：功能与组织；固定栏目 vs 项目填报；不写样板实例。
- writing_style：2～5 条写法特点。
- extracts：本章须从本项目资料取值的元数据清单（name/kind/hint/required/style）；
  name 用类型名（工程名称、主变型号…），不要填样板实例值。
- generate：5～12 条写法要点；提信息类型，不写工程名/型号/数值实例。
- keywords：5～10 个（2～12 字）通用术语；禁止专名与型号串。
- reference_text：摘自 sample（400～3000 字，可含原文具体信息）。
- content_blocks：表格含 headers、column_roles、fixed_items（栏目名/单位/套话）、
  variable_items（信息类型）、cells_sample（××占位）。"""


def _study_with_model(template: ReportTemplate, model: BaseChatModel, *, robot=None) -> WritingBrief:
    """每批只送少量章节的节选。测试阶段最多分析 5 批。"""
    limit = _BATCH * _MAX_BATCHES
    if len(template.chapters) > limit:
        _log.info(
            "测试阶段只分析前 %s 批（%s 章），其余 %s 章本次跳过",
            _MAX_BATCHES,
            limit,
            len(template.chapters) - limit,
        )
        template = ReportTemplate(
            id=template.id,
            version=template.version,
            title=template.title,
            chapters=template.chapters[:limit],
        )
    payload = _template_payload(template)
    base = _brief_arguments(payload)
    refined: dict[str, dict] = {}
    chapters = payload["chapters"]
    total = len(chapters)
    batches = (total + _BATCH - 1) // _BATCH
    for index, start in enumerate(range(0, total, _BATCH), start=1):
        batch = []
        for item in chapters[start : start + _BATCH]:
            sample = "\n".join(item.get("sample_paragraphs") or [])
            parent = item.get("parent") or "无"
            _log.info(
                "分析章节 序号%s 层级%s 上级%s %s\n%s",
                item.get("sequence") or "无",
                item.get("level") or 1,
                parent,
                item["title"],
                sample[:800] or "（无正文）",
            )
            batch.append(
                {
                    "key": item["key"],
                    "sequence": item.get("sequence") or "",
                    "level": item.get("level") or 1,
                    "parent": item.get("parent") or "",
                    "title": item["title"],
                    "sample": sample[:_SAMPLE_LIMIT],
                }
            )
        first = batch[0]
        last = batch[-1]
        _log.info(
            "正在分析第 %s/%s 批，%s %s 至 %s %s",
            index,
            batches,
            first.get("sequence") or first["key"],
            first["title"],
            last.get("sequence") or last["key"],
            last["title"],
        )
        try:
            learned = _ask_batch(model, batch, robot=robot)
        except (ValueError, json.JSONDecodeError) as error:
            _log.warning("第 %s 批没有分析成，沿用标题归纳：%s", index, error)
            continue
        ordered = [row.get("sequence") or row["key"] for row in batch]
        _log.info("第 %s/%s 批已分析完，顺序 %s", index, batches, "、".join(ordered))
        refined.update(learned)
    chapters_out = []
    for item in base["chapters"]:
        extra = refined.get(item["key"]) or {}
        merged = dict(item)
        merged["sequence"] = item.get("sequence") or ""
        merged["level"] = item.get("level") or 1
        merged["parent"] = item.get("parent") or ""
        for field in ("describes", "generate"):
            if str(extra.get(field) or "").strip():
                merged[field] = str(extra[field]).strip()
        if extra.get("writing_style"):
            from report_writing.controller.points import _merge_describes_and_style

            merged["describes"] = _merge_describes_and_style(
                merged.get("describes") or "",
                extra.get("writing_style"),
            )
        if extra.get("extracts"):
            merged["extracts"] = extra["extracts"]
        if extra.get("keywords"):
            from report_writing.controller.points import _polish_keywords

            pts = [line.strip() for line in str(merged.get("generate") or "").splitlines() if line.strip()]
            merged["keywords"] = _polish_keywords(
                str(merged.get("title") or ""),
                pts,
                list(extra["keywords"]),
            )
        if extra.get("content_blocks"):
            merged["content_blocks"] = normalize_content_blocks(extra.get("content_blocks"))
        if str(extra.get("reference_text") or "").strip():
            merged["reference_text"] = str(extra["reference_text"]).strip()[:8000]
        if merged.get("generate"):
            merged["points"] = [line.strip() for line in str(merged["generate"]).splitlines() if line.strip()]
        from report_writing.controller.points import (
            _fold_project_slots,
            _normalize_project_slots,
            audit_chapter_analysis_with_model,
        )

        pts = list(merged.get("points") or [])
        kws = list(merged.get("keywords") or [])
        desc = str(merged.get("describes") or "")
        blocks = list(merged.get("content_blocks") or [])
        slots = _normalize_project_slots(extra.get("extracts") or merged.get("extracts") or [])
        pts, kws, desc, blocks, _, slots = audit_chapter_analysis_with_model(
            model,
            points=pts,
            keywords=kws,
            describes=desc,
            blocks=blocks,
            writing_style=[],
            project_slots=slots,
        )
        if slots:
            # 审核后的槽位写回 extracts，供写作时按项目取值
            merged["extracts"] = [
                {
                    "name": s.get("name") or "",
                    "kind": s.get("kind") or "",
                    "hint": s.get("hint") or "按本项目资料填写",
                    "required": bool(s.get("required", True)),
                    "style": "",
                }
                for s in slots
            ]
        desc = _fold_project_slots(desc, slots)
        merged["points"] = pts
        merged["keywords"] = kws
        merged["describes"] = desc
        merged["content_blocks"] = blocks
        if pts:
            merged["generate"] = "\n".join(pts)
        chapters_out.append(merged)
    brief = _assemble(template, base["voice"], base["layout"], base["paragraph_format"], chapters_out)
    return brief


def _ask_batch(model: BaseChatModel, batch: list[dict], *, robot=None) -> dict[str, dict]:
    from report_writing.services.robots import resolve_prompt

    instructions = resolve_prompt("study", robot, default=_BATCH_INSTRUCTIONS)
    response = model.invoke(
        [
            SystemMessage(content=instructions),
            HumanMessage(
                content=(
                    "按下面数组的顺序返回同样数量的 JSON 数组。sequence 原样照抄，不要重排。\n"
                    + json.dumps(batch, ensure_ascii=False)
                )
            ),
        ]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    text = str(content).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("模型没有返回章节数组")
    rows = json.loads(text[start : end + 1])
    if not isinstance(rows, list):
        raise ValueError("模型返回的不是章节数组")
    return {row["key"]: row for row in rows if isinstance(row, dict) and row.get("key")}


def _template_payload(template: ReportTemplate) -> dict:
    return {
        "id": template.id,
        "version": template.version,
        "chapters": [
            {
                "key": item.key,
                "title": item.title,
                "sequence": item.sequence,
                "level": item.level,
                "parent": item.parent,
                "sample_paragraphs": item.sample_paragraphs,
                "points": item.points,
                "boundary": item.boundary,
                "depends_on": item.depends_on,
            }
            for item in template.chapters
        ],
    }


def _brief_arguments(template_payload: dict) -> dict:
    titles = [item["title"] for item in template_payload["chapters"]]
    chapters = []
    for item in template_payload["chapters"]:
        others = "、".join(title for title in titles if title != item["title"])
        paragraphs = item.get("sample_paragraphs") or []
        points = learn_points(paragraphs) or list(item.get("points") or [])
        extracts = learn_extracts(paragraphs)
        keywords = [entry["hint"] for entry in extracts] or list(points)
        chapters.append(
            {
                "key": item["key"],
                "title": item["title"],
                "sequence": item.get("sequence") or "",
                "level": item.get("level") or 1,
                "parent": item.get("parent") or "",
                "describes": _describes(item["title"], paragraphs),
                "extracts": extracts,
                "generate": "\n".join(points),
                "keywords": keywords,
                "points": points,
                "boundary": item["boundary"] or f"只写{item['title']}。禁止写入：{others}",
                "depends_on": item["depends_on"],
            }
        )
    return {
        "voice": "使用客观陈述，避免宣传用语。数值带单位，名称使用材料中的全称。",
        "layout": "按样板的章节顺序使用一级标题；正文可为文字+表格；图片仅占位。",
        "paragraph_format": "输出完整 HTML：文字用 <p>…</p>；表格用完整 <table>…</table>（含合并单元格）；图片用 <figure class=\"image-placeholder\"> 占位。标签必须成对闭合。",
        "chapters": chapters,
    }


_POINT = re.compile(r"^(?:本)?([\u4e00-\u9fff]{2,8})(?:为|位于|写明|执行)")


def _extract_item(item: dict) -> ExtractItem:
    if isinstance(item, ExtractItem):
        return item
    return ExtractItem.model_validate(item)


def _describes(title: str, paragraphs: list[str]) -> str:
    text = next((item.strip() for item in paragraphs if item.strip()), "")
    if not text:
        return f"说明{title}。"
    if len(text) > 80:
        text = text[:80] + "…"
    return text


_NUMBER = re.compile(r"投资|容量|噪声|限值|面积|长度|电压")
_DATE = re.compile(r"日期|时间|工期")
_TABLE = re.compile(r"表$|清单|一览")


def learn_extracts(paragraphs: list[str]) -> list[dict]:
    items = []
    for name in learn_points(paragraphs):
        if _TABLE.search(name):
            kind = "表格"
        elif _DATE.search(name):
            kind = "日期"
        elif _NUMBER.search(name):
            kind = "数值"
        else:
            kind = "文本"
        items.append(
            {
                "name": name,
                "kind": kind,
                "source": "",
                "hint": name,
                "required": True,
                "style": "归纳",
            }
        )
    return items


def learn_points(paragraphs: list[str]) -> list[str]:
    found: list[str] = []
    for paragraph in paragraphs:
        for sentence in re.split(r"[。；]", paragraph):
            match = _POINT.match(sentence.strip())
            if not match:
                continue
            point = match.group(1)
            if point.startswith("项目名称") or point == "名称":
                point = "项目名称"
            if point not in found:
                found.append(point)
    return found


def _assemble(template: ReportTemplate, voice: str, layout: str, paragraph_format: str, chapters: list[dict]) -> WritingBrief:
    supplied = {item.get("key"): item for item in chapters if item.get("key")}
    requirements = []
    for source in template.chapters:
        extra = supplied.get(source.key) or {}
        others = "、".join(item.title for item in template.chapters if item.key != source.key)
        points = list(extra.get("points") or []) or learn_points(source.sample_paragraphs) or list(source.points)
        generate = str(extra.get("generate") or "").strip() or "\n".join(points)
        if not points and generate:
            points = [line.strip() for line in generate.splitlines() if line.strip()]
        extracts = [_extract_item(item) for item in (extra.get("extracts") or [])] or [
            ExtractItem.model_validate(item) for item in learn_extracts(source.sample_paragraphs)
        ]
        keywords = [item.strip() for item in (extra.get("keywords") or []) if str(item).strip()]
        if not keywords:
            keywords = [item.hint or item.name for item in extracts] or list(points)
        # 表格结构只认大模型给出的 content_blocks，代码不从样本文本猜行列
        content_blocks = [
            ContentBlock.model_validate(item)
            for item in normalize_content_blocks(extra.get("content_blocks"))
        ]
        reference_text = str(extra.get("reference_text") or "").strip()
        if not reference_text:
            reference_text = "\n".join(source.sample_paragraphs or [])[:8000]
        requirements.append(
            ChapterRequirement(
                key=source.key,
                title=extra.get("title") or source.title,
                sequence=source.sequence or str(extra.get("sequence") or ""),
                level=int(extra.get("level") or source.level or 1),
                parent=str(extra.get("parent") or source.parent or ""),
                describes=str(extra.get("describes") or "").strip() or _describes(source.title, source.sample_paragraphs),
                extracts=extracts,
                generate=generate,
                keywords=keywords,
                points=points,
                content_blocks=content_blocks,
                reference_text=reference_text,
                boundary=extra.get("boundary") or source.boundary or f"只写{source.title}。禁止写入：{others}",
                depends_on=list(extra.get("depends_on") or source.depends_on),
            )
        )
    return WritingBrief(
        template_id=template.id,
        template_version=template.version,
        voice=voice or "使用客观陈述，避免宣传用语。",
        layout=layout or "按模板章节顺序书写。",
        paragraph_format=paragraph_format
        or "输出完整 HTML：文字用 <p>，表格用完整 <table>，图片用 figure 占位；标签成对闭合。",
        chapters=requirements,
    )

