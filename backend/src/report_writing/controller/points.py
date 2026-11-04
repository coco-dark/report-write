"""Task controller helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from report_writing.agents.supervisor import run_supervisor
from report_writing.services.corpus import load_template, normalize_biz_type
from report_writing.infra.llm import live_model
from report_writing.domain.models import (
    ChapterRequirement,
    ChapterState,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.domain.outline import (
    finalize_outline,
    load_outline_json,
    outline_as_text,
    outline_from_brief_chapters,
    outline_from_payload,
    outline_from_text,
    read_template_text,
    slice_toc_region,
    template_from_outline,
)
from report_writing.domain.chapter_locator import CHAPTER_EXCERPT_MAX_CHARS
from report_writing.infra.postgres import open_library
from report_writing.services.store import BriefCache, TaskStore

_log = logging.getLogger("uvicorn.error")

# 单章分析送入模型的原文上限（与定位切段上限对齐）
_CHAPTER_EXCERPT_LIMIT = CHAPTER_EXCERPT_MAX_CHARS
_REFERENCE_LIMIT = 12_000


def _chapter_analysis_prompt(heading: str, excerpt: str) -> str:
    return (
        "你是报告章节配置助手。精读「本章原文节选」，输出可跨项目复用的写作配置。\n"
        "只返回 JSON，不要 markdown：\n"
        '{"structure_only":false,"describes":"...","writing_style":["..."],"points":["..."],'
        '"keywords":["..."],"project_slots":[{"name":"...","kind":"...","hint":"...","required":true}],'
        '"content_blocks":[...],"reference_text":"..."}\n\n'
        "核心原则（分两层，不要混）：\n"
        "A. 固定层 = 版式与写法：章节功能、段落/表格结构、栏目名、单位、跨项目套话、写作顺序。\n"
        "B. 元数据层 = 随项目而变：工程/项目名称、站名线路名、建设单位、设备型式与型号规格、"
        "电压/容量/投资/坐标/日期/文号、测点专名、厂家名等。样板里出现的具体值只是示例，"
        "必须写入 project_slots（name=信息类型，hint=怎么填），禁止写进 points/describes/keywords/"
        "fixed_items/cells_sample。\n"
        "反例：points 写「襄阳连云220千伏工程采用 SFSZ-50000」。\n"
        "正例：points 写「交代本工程名称与电压等级；主设备型号与台数按项目资料」；"
        "project_slots 含 {name:工程名称},{name:电压等级},{name:主变型号}。\n"
        "仅 reference_text 可保留样板原文（格式对照）。\n\n"
        "字段：\n"
        "0) structure_only：纯目录/无正文 → true。\n"
        "1) describes（80～280 字）：功能与组织；点明固定栏目 vs 项目填报；不写样板实例。\n"
        "2) writing_style（2～5）：语气/节奏/是否表格为主。\n"
        "3) points（5～12）：可执行写法；提「填哪类信息」，不写样板里的具体工程名/型号/数值。"
        "缺本项目资料时在对应条目中用「（待补充：资料类型名）」标记（类型名与 project_slots.name 一致）。\n"
        "4) keywords（5～10，2～12 字）：规范/方法/专题术语；禁止专名与型号串。\n"
        "5) project_slots：本章写作必须从「本项目资料」取值的元数据清单（可空）。"
        "每项 name/kind/hint/required；name 用类型名（工程名称、设备型号…），不要填样板实例值。\n"
        "6) content_blocks：文字/表格/图片。表格含 headers、column_roles(fixed|variable|mixed)、"
        "fixed_items（仅栏目名/单位/套话）、variable_items（信息类型）、cells_sample（××占位）。\n"
        "7) reference_text：原文摘录 400～3000 字；勿摘其它章。\n\n"
        f"目标章节锚点：\n{heading}\n"
        f"本章原文节选：\n{excerpt}"
    )


def _fold_project_slots(describes: str, slots: list[dict]) -> str:
    """把元数据槽位并入简述，写作时明确「按本项目填、勿照搬样板」。"""
    lines: list[str] = []
    seen: set[str] = set()
    for item in slots or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        hint = str(item.get("hint") or "").strip()
        kind = str(item.get("kind") or "").strip()
        bit = name
        if kind and kind not in name:
            bit = f"{name}（{kind}）"
        if hint:
            bit = f"{bit}：{hint}"
        else:
            bit = f"{bit}：按本项目资料填写"
        lines.append(f"- {bit}")
        if len(lines) >= 16:
            break
    if not lines:
        return (describes or "").strip()
    block = "【须按本项目资料填写的元数据·勿照搬样板实例】\n" + "\n".join(lines)
    base = (describes or "").strip()
    if "须按本项目资料填写的元数据" in base:
        return base
    return f"{base}\n{block}".strip() if base else block


def audit_chapter_analysis_with_model(
    model,
    *,
    points: list[str],
    keywords: list[str],
    describes: str,
    blocks: list[dict],
    writing_style: list[str] | None = None,
    project_slots: list[dict] | None = None,
) -> tuple[list[str], list[str], str, list[dict], list[str], list[dict]]:
    """第二遍模型审核：把误写入配置的样板实例改成类型化表述 / project_slots。不用正则。"""
    if model is None:
        return (
            list(points or []),
            list(keywords or []),
            str(describes or ""),
            list(blocks or []),
            list(writing_style or []),
            list(project_slots or []),
        )
    payload = {
        "describes": describes or "",
        "writing_style": list(writing_style or [])[:8],
        "points": list(points or [])[:20],
        "keywords": list(keywords or [])[:12],
        "project_slots": list(project_slots or [])[:20],
        "content_blocks": list(blocks or [])[:12],
    }
    system = (
        "你是章节写作配置审核员。输入是上一轮分析 JSON。"
        "任务：消除「把样板项目实例当成固定写法」的问题，提高可跨项目复用性。\n"
        "只返回同结构 JSON（可增补 project_slots），不要 markdown、不要 reference_text。\n"
        "规则：\n"
        "- 工程名、站名、公司名、设备型号规格、具体电压/容量/投资/坐标/日期/文号等若出现在 "
        "describes/points/keywords/writing_style/fixed_items/cells_sample/note 中："
        "改成类型化说法，并确保 project_slots 有对应 name（类型名，不是实例值）。\n"
        "- keywords 里的专名/型号串删除或换成通用术语。\n"
        "- fixed_items 只保留栏目名/单位/跨项目套话；实例挪到 variable_items 或 project_slots。\n"
        "- cells_sample 只用 ×× / 数值 / 单位 占位。\n"
        "- 不要丢弃真正的写法结构信息；不要编造原文没有的版式。\n"
        "- 若输入已经干净，可原样返回。"
    )
    user = "待审核配置：\n" + json.dumps(payload, ensure_ascii=False)
    try:
        message = model.invoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=user),
            ]
        )
        raw = getattr(message, "content", None) or str(message)
        data = _parse_analysis_object(raw)
        if not data:
            return (
                list(points or []),
                list(keywords or []),
                str(describes or ""),
                list(blocks or []),
                list(writing_style or []),
                list(project_slots or []),
            )
        out_points = [str(x).strip() for x in (data.get("points") or points or []) if str(x).strip()][:20]
        out_kw = [str(x).strip() for x in (data.get("keywords") or keywords or []) if str(x).strip()][:12]
        out_desc = str(data.get("describes") or describes or "").strip()
        style_raw = data.get("writing_style")
        if isinstance(style_raw, list):
            out_style = [str(x).strip() for x in style_raw if str(x).strip()][:8]
        else:
            out_style = list(writing_style or [])
        out_blocks = data.get("content_blocks") if isinstance(data.get("content_blocks"), list) else list(blocks or [])
        slots_raw = data.get("project_slots")
        if isinstance(slots_raw, list):
            out_slots = [dict(x) for x in slots_raw if isinstance(x, dict)][:20]
        else:
            out_slots = list(project_slots or [])
        return out_points, out_kw, out_desc, out_blocks, out_style, out_slots
    except Exception as error:
        _log.warning("章节分析审核未成功，沿用首轮结果：%s", error)
        return (
            list(points or []),
            list(keywords or []),
            str(describes or ""),
            list(blocks or []),
            list(writing_style or []),
            list(project_slots or []),
        )


def _parse_analysis_object(content: str) -> dict:
    object_match = re.search(r"\{[\s\S]*\}", content or "")
    if not object_match:
        return {}
    try:
        data = json.loads(object_match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _merge_describes_and_style(describes: str, writing_style: list[str] | str | None) -> str:
    base = (describes or "").strip()
    styles: list[str] = []
    if isinstance(writing_style, str):
        styles = [line.strip() for line in re.split(r"[\n；;]+", writing_style) if line.strip()]
    elif isinstance(writing_style, list):
        styles = [str(item).strip() for item in writing_style if str(item).strip()]
    if not styles:
        return base
    style_block = "；".join(styles[:6])
    if "写作特点" in base:
        return base
    if base:
        return f"{base}\n【写作特点】{style_block}"
    return f"【写作特点】{style_block}"


def _polish_keywords(title: str, points: list[str], keywords: list[str]) -> list[str]:
    """去掉像整句要点一样的检索词，并补主题词。"""
    title = (title or "").strip()
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        t = re.sub(r"\s+", "", (term or "").strip())
        if len(t) < 2 or len(t) > 16:
            return
        if t in seen:
            return
        if any(t in p or p in t for p in points if len(p) > 20):
            return
        seen.add(t)
        out.append(t)

    for item in keywords:
        piece = (item or "").strip()
        if not piece:
            continue
        if len(piece) > 18 or "。" in piece or "；" in piece:
            for part in re.split(r"[,，、/\\s]+", piece):
                add(part)
            continue
        add(piece)
    if title:
        add(title[:12])
    for point in points:
        for token in re.findall(r"[\u4e00-\u9fff]{2,8}", point):
            if token in {"本章", "围绕", "说明", "交代", "内容", "相关", "应当", "需要"}:
                continue
            add(token)
            if len(out) >= 10:
                break
        if len(out) >= 10:
            break
    return out[:10]


def parse_writing_points(text: str, *, title: str = "", sequence: str = "") -> list[str]:
    """粘贴原文，让模型抽出本章写作要点；无模型时按行切分。"""
    points, _keywords, _describes, _blocks, _ref, _structure, _slots = parse_points_and_keywords(
        text, title=title, sequence=sequence
    )
    return points


def parse_points_and_keywords(
    text: str, *, title: str = "", sequence: str = ""
) -> tuple[list[str], list[str], str, list[dict], str, bool, list[dict]]:
    """同一次解析：简述 + 写法要点 + 检索词 + 版式块 + 参考正文 + 是否纯目录。

    提示词对齐 anyuan V2 ChapterOutlineAgent：要点写「怎么写」、检索词可跨项目复用，
    禁止固化项目专名/地址/投资额等事实；参考正文保留原文摘录。
    纯目录节点（structure_only）可不写要点/检索词。
    """
    from report_writing.domain.content_blocks import normalize_content_blocks

    cleaned = (text or "").strip()
    if not cleaned:
        raise RuntimeError("请粘贴本章原文")
    model = live_model()
    if model is None:
        points = _points_from_plain(cleaned)
        keywords = _keywords_from_title_points(title, points)
        return (
            points,
            keywords,
            _describes_fallback(title, points),
            [],
            cleaned[:8000],
            False,
            [],
        )
    heading = (
        f"章节序号={sequence or '（未知）'}\n"
        f"章节名称={title or '（未知）'}"
    )
    excerpt = cleaned[:_CHAPTER_EXCERPT_LIMIT]
    prompt = _chapter_analysis_prompt(heading, excerpt)
    try:
        message = model.invoke(prompt)
        content = getattr(message, "content", None) or str(message)
        (
            points,
            keywords,
            describes,
            blocks,
            reference,
            structure_only,
            writing_style,
            project_slots,
        ) = _parse_points_keywords_payload(content)
        points, keywords, describes, blocks, writing_style, project_slots = (
            audit_chapter_analysis_with_model(
                model,
                points=points,
                keywords=keywords,
                describes=describes,
                blocks=blocks,
                writing_style=writing_style,
                project_slots=project_slots,
            )
        )
        describes = _merge_describes_and_style(describes, writing_style)
        describes = _fold_project_slots(describes, project_slots)
        if structure_only:
            return (
                [],
                [],
                describes or f"目录节点「{title or sequence}」，正文由子节展开，本章不单独撰写。",
                [],
                (reference or cleaned)[:8000],
                True,
                list(project_slots or []),
            )
        if not points:
            points = _points_from_plain(cleaned)
        if not keywords:
            keywords = _keywords_from_title_points(title, points)
        keywords = _polish_keywords(title, points, keywords)
        if not describes:
            describes = _describes_fallback(title, points)
            describes = _fold_project_slots(describes, project_slots)
        if not reference:
            reference = excerpt[:_REFERENCE_LIMIT]
        blocks = normalize_content_blocks(blocks)
        return points, keywords, describes, blocks, reference, False, list(project_slots or [])
    except Exception as error:
        _log.warning("模型解析写作要点失败，改用文本切分：%s", error)
        points = _points_from_plain(cleaned)
        return (
            points,
            _keywords_from_title_points(title, points),
            _describes_fallback(title, points),
            [],
            cleaned[:8000],
            False,
            [],
        )


def _normalize_project_slots(raw) -> list[dict]:
    """接受 project_slots 或 extracts；name 用类型名，去掉明显像实例值的超长串。"""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        # 名称过长或像整句事实 → 仍保留但截断，交给审核模型处理
        if len(name) > 40:
            name = name[:40]
        seen.add(name)
        slot = {
            "name": name,
            "kind": str(item.get("kind") or "").strip(),
            "hint": str(item.get("hint") or "").strip() or "按本项目资料填写",
            "required": bool(item.get("required", True)),
        }
        out.append(slot)
        if len(out) >= 20:
            break
    return out


def _parse_points_keywords_payload(
    content: str,
) -> tuple[list[str], list[str], str, list[dict], str, bool, list[str], list[dict]]:
    object_match = re.search(r"\{[\s\S]*\}", content)
    if object_match:
        payload = json.loads(object_match.group(0))
        if isinstance(payload, dict):
            structure_only = bool(payload.get("structure_only") or payload.get("structureOnly"))
            points = [str(item).strip() for item in (payload.get("points") or []) if str(item).strip()]
            # 兼容 V2 contentPoints 整段文本
            if not points:
                raw_points = str(payload.get("contentPoints") or "").strip()
                if raw_points:
                    points = [
                        line.strip(" -•\t")
                        for line in re.split(r"[\n；;]+", raw_points)
                        if line.strip(" -•\t")
                    ]
            keywords = [str(item).strip() for item in (payload.get("keywords") or []) if str(item).strip()]
            if not keywords:
                raw_kw = str(payload.get("extractKeywords") or "").strip()
                if raw_kw:
                    keywords = [item.strip() for item in re.split(r"[,，、]+", raw_kw) if item.strip()]
            describes = str(payload.get("describes") or "").strip()
            style_raw = payload.get("writing_style") or payload.get("writing_traits") or []
            writing_style: list[str] = []
            if isinstance(style_raw, str):
                writing_style = [line.strip() for line in re.split(r"[\n；;]+", style_raw) if line.strip()]
            elif isinstance(style_raw, list):
                writing_style = [str(item).strip() for item in style_raw if str(item).strip()]
            blocks = payload.get("content_blocks") if isinstance(payload.get("content_blocks"), list) else []
            reference = str(
                payload.get("reference_text") or payload.get("retainedSourceText") or ""
            ).strip()
            slots = _normalize_project_slots(
                payload.get("project_slots") or payload.get("extracts") or []
            )
            return (
                points[:20],
                keywords[:12],
                describes[:1200],
                blocks,
                reference[:_REFERENCE_LIMIT],
                structure_only,
                writing_style[:8],
                slots,
            )
    array_match = re.search(r"\[[\s\S]*\]", content)
    if array_match:
        payload = json.loads(array_match.group(0))
        if isinstance(payload, list):
            points = [str(item).strip() for item in payload if str(item).strip()]
            return points[:20], [], "", [], "", False, [], []
    raise ValueError("模型没有返回可用 JSON")


def _looks_like_structure_only(excerpt: str, title: str, sequence: str, has_children: bool) -> bool:
    """正文几乎只有标题、且常为父级目录时，视为纯目录节点。"""
    body_lines = []
    title = (title or "").strip()
    sequence = (sequence or "").strip()
    for line in (excerpt or "").splitlines():
        item = line.strip()
        if not item:
            continue
        if sequence and (item == sequence or item.startswith(sequence + " ") or item.startswith(sequence + "　")):
            rest = item[len(sequence) :].strip(" .．、　")
            if not rest or rest == title:
                continue
        if title and (item == title or item.endswith(title)):
            continue
        body_lines.append(item)
    body = "\n".join(body_lines).strip()
    if len(body) < 24:
        return True
    if has_children and len(body) < 80:
        return True
    return False


def _points_from_plain(text: str) -> list[str]:
    """无模型时：把原文行改写成简短写法提示，避免直接当要点。"""
    points = []
    for line in re.split(r"[\n；;]", text):
        item = re.sub(r"^[\d一二三四五六七八九十]+[、.．）)\s]+", "", line).strip()
        if 8 <= len(item) <= 80:
            points.append(f"围绕「{item[:28]}」交代清楚相关内容与依据")
        if len(points) >= 8:
            break
    return points[:20] or ["按本章标题组织段落：先总述再分点说明，语气客观"]


def _describes_fallback(title: str, points: list[str]) -> str:
    name = (title or "").strip() or "本章"
    if points:
        return f"围绕「{name}」说明应写清的内容与写法侧重点，避免空泛概述。"
    return f"围绕「{name}」客观交代核心内容、写法结构与本章特点。"


def _keywords_from_title_points(title: str, points: list[str]) -> list[str]:
    terms: list[str] = []
    cleaned_title = (title or "").strip()
    if cleaned_title:
        terms.append(cleaned_title)
    for item in points:
        piece = (item or "").strip()
        if piece and piece not in terms:
            terms.append(piece[:40])
        if len(terms) >= 8:
            break
    return terms[:8]


