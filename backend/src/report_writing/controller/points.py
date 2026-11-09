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
from report_writing.infra.llm import or_live_model
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


_ANALYSIS_ARTIFACT_BLOCK_RE = re.compile(
    r"【写作特点】[\s\S]*?(?=【须按本项目资料填写的元数据|$)"
    r"|【须按本项目资料填写的元数据[^\n]*】[\s\S]*$",
    re.M,
)
_CHAPTER_FOCUS_ASIDE_RE = re.compile(
    r"本章为[^。\n]{2,80}部分[，,、][^。\n]{0,800}[。\n]?",
)
# 章名不含这些专题词时，正文/要点里出现则视为串章杂质
_TOPIC_CONFLICT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "接地",
        (
            "接地装置",
            "水平接地体",
            "垂直接地极",
            "接地引线",
            "接地干线",
            "接地电阻",
            "接触电势",
            "引下线规格",
            "室内接地",
        ),
    ),
)


def _title_tokens(title: str) -> set[str]:
    raw = re.sub(r"[\s\d.．、，,；;：:（）()【】\[\]\-_/]+", " ", str(title or "")).strip()
    parts = [p for p in re.split(r"\s+", raw) if len(p) >= 2]
    # 再切 2 字以上中文片段，便于「评价单元划分」命中「评价」「单元」「划分」
    chars = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
    out: set[str] = set(parts)
    for chunk in chars:
        out.add(chunk)
        if len(chunk) >= 4:
            out.add(chunk[:2])
            out.add(chunk[-2:])
    return {x for x in out if x}


def _aside_conflicts_with_title(aside: str, title: str) -> bool:
    """「本章为评价单元划分的接地部分」相对章名「评价单元划分」仍冲突（残留「接地」）。"""
    m = re.search(r"本章为([^。\n]{2,80}?)部分", str(aside or ""))
    if not m:
        return False
    focus = str(m.group(1) or "").strip()
    rest = focus
    for tok in sorted(_title_tokens(title), key=len, reverse=True):
        if tok:
            rest = rest.replace(tok, "")
    rest = re.sub(r"[\s的之与和及、，,：:]+", "", rest)
    return len(rest) >= 2


def _title_allows_topic(title: str, topic: str) -> bool:
    return topic in str(title or "")


def _text_has_topic_conflict(text: str, title: str) -> bool:
    body = str(text or "")
    if not body.strip():
        return False
    for topic, markers in _TOPIC_CONFLICT_MARKERS:
        if _title_allows_topic(title, topic):
            continue
        if any(m in body for m in markers) or topic in body:
            return True
    return False


def _looks_like_prior_analysis_artifact(text: str) -> bool:
    """识别「上一轮分析输出」被当成原文再贴回来（写作特点 + 元数据清单）。"""
    t = str(text or "")
    if not t.strip():
        return False
    hits = 0
    if "【写作特点】" in t or "写作特点】" in t:
        hits += 1
    if "须按本项目资料填写的元数据" in t:
        hits += 1
    if "固定层为栏目顺序" in t or "具体规格、数值按项目资料填报" in t:
        hits += 1
    if re.search(r"本章为[^。\n]{2,80}部分", t):
        hits += 1
    if t.count("- ") >= 4 and ("规格" in t or "计算值" in t) and "【" in t:
        hits += 1
    return hits >= 2


def prepare_chapter_excerpt_for_analysis(text: str, *, title: str = "") -> str:
    """去掉粘贴里混入的「上一轮分析残留」，避免把串章导语当成本章主题。

    注意：剥离后若所剩无几、且原文与章名冲突，必须返回空串，禁止再回退到原文
   （旧逻辑 ``cleaned or raw`` 会把接地分析残留原样送回模型，造成「怎么分析还是接地」）。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = _ANALYSIS_ARTIFACT_BLOCK_RE.sub("", raw).strip()

    def _drop_conflicting_aside(match: re.Match) -> str:
        aside = match.group(0)
        if title and _aside_conflicts_with_title(aside, title):
            return ""
        return aside

    cleaned = _CHAPTER_FOCUS_ASIDE_RE.sub(_drop_conflicting_aside, cleaned)
    # 章名不含「接地」时，删掉明显接地专题句（防串章残留漏网）
    if title and not _title_allows_topic(title, "接地"):
        kept: list[str] = []
        for para in re.split(r"\n+", cleaned):
            line = para.strip()
            if not line:
                continue
            if _text_has_topic_conflict(line, title):
                continue
            kept.append(line)
        cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    raw_conflicts = bool(title) and _text_has_topic_conflict(raw, title)
    artifact = _looks_like_prior_analysis_artifact(raw)
    # 整段就是「上一轮错误分析」且与章名冲突：全部丢弃，勿留「固定层…」等无主题残句
    if title and artifact and raw_conflicts:
        _log.info(
            "章节分析丢弃与章名冲突的分析残留 title=%s raw_chars=%s",
            title,
            len(raw),
        )
        return ""
    if title and raw_conflicts and len(cleaned) < max(40, len(raw) // 5):
        _log.info(
            "章节分析已剥离与章名冲突的串章杂质（不回退原文） title=%s raw_chars=%s kept_chars=%s",
            title,
            len(raw),
            len(cleaned),
        )
        return cleaned  # 可为 ""，禁止 cleaned or raw
    if cleaned and title and _text_has_topic_conflict(cleaned, title) and not _title_allows_topic(
        title, "接地"
    ):
        return ""
    if not cleaned and (raw_conflicts or artifact):
        return ""
    return cleaned or ("" if (raw_conflicts or artifact) else raw)


def filter_analysis_against_title(
    *,
    title: str,
    points: list[str],
    keywords: list[str],
    describes: str,
    project_slots: list[dict],
    writing_style: list[str] | None = None,
) -> tuple[list[str], list[str], str, list[dict], list[str]]:
    """硬过滤：章名不含专题词时，丢掉串章要点/槽位/简述句。"""
    title = str(title or "").strip()
    pts = [str(p).strip() for p in list(points or []) if str(p).strip()]
    kws = [str(k).strip() for k in list(keywords or []) if str(k).strip()]
    slots = [dict(x) for x in list(project_slots or []) if isinstance(x, dict)]
    styles = [str(s).strip() for s in list(writing_style or []) if str(s).strip()]
    desc = str(describes or "").strip()
    if not title:
        return pts, kws, desc, slots, styles

    def _keep_text(value: str) -> bool:
        return not _text_has_topic_conflict(value, title)

    pts = [p for p in pts if _keep_text(p)]
    kws = [k for k in kws if _keep_text(k)]
    styles = [s for s in styles if _keep_text(s)]
    slots = [
        s
        for s in slots
        if _keep_text(
            " ".join(
                [
                    str(s.get("name") or ""),
                    str(s.get("hint") or ""),
                    str(s.get("kind") or ""),
                ]
            )
        )
    ]
    if desc and _text_has_topic_conflict(desc, title):
        # 简述串到其它专题（如章名「评价单元划分」却写接地）：整段丢弃，后续按章名重写
        _log.info(
            "章节分析简述与章名冲突，已清空 title=%s artifact=%s",
            title,
            _looks_like_prior_analysis_artifact(desc),
        )
        desc = ""
    return pts, kws, desc, slots, styles


def _chapter_analysis_prompt(
    heading: str,
    excerpt: str,
    *,
    compose: str = "",
    presence: str = "",
    compose_locked: bool = False,
    category: str = "",
) -> str:
    from report_writing.domain.compose_analysis import analysis_strategy_block
    from report_writing.domain.doc_genre import analyst_persona, normalize_doc_category

    cat = normalize_doc_category(category)
    strategy = analysis_strategy_block(
        compose=compose, presence=presence, locked=compose_locked, category=cat
    )
    return (
        f"# Role\n{analyst_persona(cat)}。\n"
        "# Task\n精读「本章原文节选」，输出可跨项目复用的写作施工图。"
        "须符合该文档类型的体裁，不要默认按研究报告拆要点；先拆组成部分和格式，再写怎么写和要点。\n"
        "# Output\n只返回 JSON，不要 markdown：\n"
        '{"structure_only":false,"describes":"...","writing_style":["..."],"points":["..."],'
        '"keywords":["..."],"project_slots":[{"name":"...","kind":"...","hint":"...","required":true}],'
        '"compose":"hybrid","boilerplate":"...","content_blocks":[...],"reference_text":"..."}\n\n'
        f"{strategy}\n"
        "# Constraints\n忠于原文 + 对齐章名（最高优先级，违反即错）：\n"
        "- 以「章节名称」为主题锚：describes / points / content_blocks / keywords / project_slots "
        "必须服务该章主题。正文主体（论述、表4.x、评价单元划分等）优先于后文旁白。\n"
        "- 禁止补全、扩写、联想原文没有的评价单元、子单元、表格行、栏目、设备项、作业项。"
        "表只写到原文出现的行与列；半截表保持半截，不要用「典型安评单元」去补齐。\n"
        "- 若出现「本章为…××部分」或接地/电阻/接触电势等专题，但与章节名称明显无关："
        "视为串章或粘贴杂质，忽略，不得据此生成要点或 project_slots。\n"
        "- 禁止把其它章的写法说明、元数据清单误当成本章评价对象。"
        "只有与章名相符的信息类型才进 project_slots。\n"
        "- 反例：章名是「评价单元划分」，却输出水平接地体/垂直接地极/接地电阻等要点。\n"
        "- 正例：章名是「评价单元划分」，要点围绕如何划分评价单元、表栏目、合并说明、"
        "各单元/子单元及主要评价对象（均须原文有据）。\n\n"
        "核心原则（分两层，不要混）：\n"
        "A. 固定层 = 版式与写法：段落/表格结构、栏目名、单位、跨项目套话、写作顺序。\n"
        "B. 元数据层 = 随项目而变：工程/项目名称、站名线路名、建设单位、设备型式与型号规格、"
        "电压/容量/投资/坐标/日期/文号、测点专名、厂家名等。样板里出现的具体值只是示例，"
        "必须写入 project_slots（name=信息类型，hint=怎么填），禁止写进 points/describes/keywords/"
        "fixed_items/cells_sample。\n"
        "反例：points 写「襄阳连云220千伏工程采用 SFSZ-50000」。\n"
        "正例：points 写「交代本工程名称与电压等级；主设备型号与台数按项目资料」；"
        "project_slots 含 {name:工程名称},{name:电压等级},{name:主变型号}。\n"
        "禁止生成 aux_prompt。describes 不要拼元数据清单。\n\n"
        "字段：\n"
        "0) structure_only：纯目录/无正文 → true。\n"
        "1) describes：章节说明=怎么写 + 什么格式。必须分点（1. 2. 3. 换行），覆盖"
        "写作风格、写作特点（组成块先后）、描述方式、排版格式、写作注意事项（邻章边界）；"
        "禁止一段话；不要内容提纲（提纲放 points）；不写样板实例。compose=fixed 时留空。\n"
        "2) writing_style（2～5）：语气/节奏、结论与发现的先后、是否表格为主。\n"
        "3) points（5～12）：一条对应原文一个组成部分，顺序=阅读顺序；"
        "句式「【组成部分】怎么写；版式：段落|条列|表格；须覆盖：信息类型」。"
        "每条都能在「与章名相符的正文」找到依据；不写样板工程名/型号/数值。"
        "缺本项目资料时在该组成块用「（待补充：资料类型名）」（与 project_slots.name 一致）。"
        "compose=fixed 时 points 留空。\n"
        "4) keywords（5～10，2～12 字）：本章主题术语，仅供参考、不用于章节检索；禁止专名与型号串；"
        "禁止塞入原文未涉及或与章名无关的相邻专题词。\n"
        "5) project_slots：本章写作必须从「本项目资料」取值、且与章名相符的元数据清单（可空）。"
        "每项 name/kind/hint/required；name 用类型名，不要填样板实例值。\n"
        "6) content_blocks：文字/表格/图片。表格行/列不得超出原文；含 headers、column_roles(fixed|variable|mixed)、"
        "fixed_items（仅栏目名/单位/套话）、variable_items（信息类型）、cells_sample（××占位）。\n"
        "7) reference_text：必须保留原文语句与语序；勿摘其它章；勿改写补行；优先摘与章名相符的主体。"
        "source_only/template_fill/hybrid 只把实例换成 {{槽名}}；auto_generate/fixed 原文原样。\n"
        "8) compose：source_only=有资料才写、无则整章转人工；"
        "template_fill=固定话术为主、缺槽留空；"
        "hybrid=相关据实写、主题无关转人工；"
        "auto_generate=不依赖资料、按要点自动成文；"
        "fixed=按原文直接输出、不改写。套话槽名须与 project_slots.name 一致。\n"
        "9) boilerplate：source_only/template_fill/hybrid 时与 reference_text 同口径填槽；"
        "auto_generate/fixed 不要填槽。不要扩写。\n\n"
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
    title: str = "",
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
        "chapter_title": title or "",
        "describes": describes or "",
        "writing_style": list(writing_style or [])[:8],
        "points": list(points or [])[:20],
        "keywords": list(keywords or [])[:12],
        "project_slots": list(project_slots or [])[:20],
        "content_blocks": list(blocks or [])[:12],
    }
    system = (
        "# Role\n你是章节写作配置审核员。\n"
        "# Task\n①消除样板实例固化；②删掉超出原文或与章名无关的评价项/表行/专题；"
        "③把空话要点改回「组成部分 + 怎么写 + 版式」。\n"
        "# Inputs\n章节名称与上一轮分析 JSON。\n"
        "# Output\n只返回同结构 JSON（可增补 project_slots），不要 markdown、不要 reference_text。\n"
        "# Rules\n"
        "- 工程名、站名、公司名、设备型号规格、具体电压/容量/投资/坐标/日期/文号等若出现在 "
        "describes/points/keywords/writing_style/fixed_items/cells_sample/note 中："
        "改成类型化说法，并确保 project_slots 有对应 name（类型名，不是实例值）。\n"
        "- describes 只保留怎么写与格式（风格/组成块先后/描述方式/排版/邻章边界），必须 1. 2. 分点；"
        "内容提纲和元数据清单从 describes 挪到 points / project_slots。\n"
        "- points 必须是原文组成部分：保留【开篇】【分项】【表格】【收束】等结构块；"
        "把「交代工程名称」「注意表述方式」这类空话改成对应组成块的可执行写法，或删掉。\n"
        "- keywords 里的专名/型号串删除或换成通用术语。\n"
        "- fixed_items 只保留栏目名/单位/跨项目套话；实例挪到 variable_items 或 project_slots。\n"
        "- cells_sample 只用 ×× / 数值 / 单位 占位。\n"
        "- 与章节名称明显无关的专题（例如章名是评价单元划分，却出现接地体/接地电阻/接触电势）"
        "从 describes/points/keywords/project_slots/content_blocks 中删除。\n"
        "- 删掉像「行业常识补全」的内容：原文未出现的评价单元、子单元、表格行一律去掉。\n"
        "- 不要丢弃真正来自原文且与章名相符的写法结构；不要编造原文没有的版式。\n"
        "- 若输入已经干净，可原样返回。"
    )
    user = (
        f"章节名称：{title or '（未知）'}\n"
        "待审核配置：\n" + json.dumps(payload, ensure_ascii=False)
    )
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
    from report_writing.domain.compose_analysis import format_describes_as_points

    base = (describes or "").strip()
    styles: list[str] = []
    if isinstance(writing_style, str):
        styles = [line.strip() for line in re.split(r"[\n；;]+", writing_style) if line.strip()]
    elif isinstance(writing_style, list):
        styles = [str(item).strip() for item in writing_style if str(item).strip()]
    if not styles:
        return format_describes_as_points(base)
    return format_describes_as_points("\n".join([base, *styles[:6]]))


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


def parse_writing_points(text: str, *, title: str = "", sequence: str = "", model=None) -> list[str]:
    """粘贴原文，让模型抽出本章写作要点；无模型时按行切分。"""
    points, _keywords, _describes, _blocks, _ref, _structure, _slots, _presence, _compose = parse_points_and_keywords(
        text, title=title, sequence=sequence, model=model
    )
    return points


def parse_points_and_keywords(
    text: str,
    *,
    title: str = "",
    sequence: str = "",
    model=None,
    compose: str = "",
    presence: str = "",
    compose_locked: bool = False,
    category: str = "",
) -> tuple[list[str], list[str], str, list[dict], str, bool, list[dict], str, str]:
    """同一次解析：简述 + 写法要点 + 检索词 + 版式块 + 参考正文 + 是否纯目录。

    提示词对齐 anyuan V2 ChapterOutlineAgent：要点写「怎么写」、检索词可跨项目复用，
    禁止固化项目专名/地址/投资额等事实；参考正文保留原文摘录。
    纯目录节点（structure_only）可不写要点/检索词。
    """
    from report_writing.domain.content_blocks import normalize_content_blocks

    cleaned = (text or "").strip()
    if not cleaned:
        raise RuntimeError("请粘贴本章原文")
    from report_writing.domain.chapter_compose import infer_presence_compose

    from report_writing.domain.compose_analysis import (
        align_describes_to_compose,
        default_points_for_compose,
        fallback_describes,
        should_fold_slots,
    )

    hint_compose = compose
    hint_presence = presence
    model = or_live_model(model)
    if model is None:
        from report_writing.domain.chapter_compose import normalize_compose as _norm_c

        mode = _norm_c(hint_compose) if compose_locked else ""
        points = (
            default_points_for_compose(title, mode)
            if mode == "template_fill"
            else _points_from_plain(cleaned)
        )
        keywords = _keywords_from_title_points(title, points)
        presence_out, compose_out = infer_presence_compose(
            describes=fallback_describes(title, points, compose=mode or hint_compose, presence=hint_presence),
            points=points,
            excerpt=cleaned,
            presence=hint_presence,
            compose=hint_compose if compose_locked else "",
        )
        if compose_locked and hint_compose:
            compose_out = _norm_c(hint_compose)
        return (
            points,
            keywords,
            fallback_describes(title, points, compose=compose_out, presence=presence_out),
            [],
            cleaned[:8000],
            False,
            [],
            presence_out,
            compose_out,
        )
    heading = (
        f"章节序号={sequence or '（未知）'}\n"
        f"章节名称={title or '（未知）'}"
    )
    source_raw = cleaned
    cleaned = prepare_chapter_excerpt_for_analysis(cleaned, title=title)
    title_only = False
    if not cleaned.strip():
        if title and (
            _text_has_topic_conflict(source_raw, title)
            or _looks_like_prior_analysis_artifact(source_raw)
        ):
            title_only = True
            tip = (title or "").strip() or "本章"
            cleaned = (
                f"【系统说明】粘贴内容与章节「{tip}」主题不符，或仅为上一轮分析残留（写作特点/元数据清单），"
                f"已全部丢弃。请只依据章节名称生成可跨项目复用的写作配置："
                f"围绕「{tip}」应写清的划分依据、表栏目/单元组织、合并说明与评价对象类型；"
                f"禁止输出接地装置、水平接地体、垂直接地极、接地电阻、接触电势等与章名无关的专题。"
            )
            _log.warning(
                "章节分析改为仅按章名生成（粘贴冲突/分析残留） title=%s raw_chars=%s",
                title,
                len(source_raw),
            )
        else:
            raise RuntimeError("请粘贴本章原文")
    excerpt = cleaned[:_CHAPTER_EXCERPT_LIMIT]
    prompt = _chapter_analysis_prompt(
        heading,
        excerpt,
        compose=hint_compose,
        presence=hint_presence,
        compose_locked=compose_locked,
        category=category,
    )
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
            presence_raw,
            compose_raw,
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
                title=title,
            )
        )
        points, keywords, describes, project_slots, writing_style = filter_analysis_against_title(
            title=title,
            points=points,
            keywords=keywords,
            describes=describes,
            project_slots=project_slots,
            writing_style=writing_style,
        )
        # merge/fold 会再拼出【写作特点】/元数据块：必须二次硬过滤，否则串章结果原样回来
        presence, compose = infer_presence_compose(
            describes=describes,
            points=points,
            slots=project_slots,
            excerpt=reference or excerpt,
            presence=hint_presence or presence_raw,
            compose=(hint_compose if compose_locked else compose_raw) or compose_raw,
            structure_only=structure_only,
        )
        if compose_locked and hint_compose:
            from report_writing.domain.chapter_compose import normalize_compose as _norm_c

            compose = _norm_c(hint_compose)
        if hint_presence:
            from report_writing.domain.chapter_compose import normalize_presence as _norm_p

            presence = _norm_p(hint_presence)
        describes = _merge_describes_and_style(describes, writing_style)
        if should_fold_slots(compose):
            describes = _fold_project_slots(describes, project_slots)
        points, keywords, describes, project_slots, writing_style = filter_analysis_against_title(
            title=title,
            points=points,
            keywords=keywords,
            describes=describes,
            project_slots=project_slots,
            writing_style=writing_style,
        )
        describes = align_describes_to_compose(
            describes, title=title, points=points, compose=compose, presence=presence
        )
        if not describes or _text_has_topic_conflict(describes, title):
            describes = fallback_describes(title, points, compose=compose, presence=presence)
            if should_fold_slots(compose):
                describes = _fold_project_slots(describes, project_slots)
            if title and _text_has_topic_conflict(describes, title):
                describes = fallback_describes(title, points, compose=compose, presence=presence)
        if structure_only:
            return (
                [],
                [],
                describes or f"目录节点「{title or sequence}」，正文由子节展开，本章不单独撰写。",
                [],
                ("" if title_only else (reference or cleaned))[:8000],
                True,
                list(project_slots or []),
                presence,
                compose,
            )
        if not points:
            points = default_points_for_compose(title, compose)
            if title_only and compose != "template_fill":
                points = [
                    f"围绕「{(title or '').strip() or '本章'}」说明评价单元如何划分、表栏目如何组织",
                    "交代划分依据与合并/拆分原则（按本项目资料，不写样板实例）",
                    "逐项列出各评价单元/子单元及主要评价对象类型",
                ]
            elif not title_only and compose != "template_fill":
                points = _points_from_plain(cleaned) or points
        points = [p for p in points if not (title and _text_has_topic_conflict(p, title))]
        if not points:
            points = default_points_for_compose(title, compose)
        if not keywords:
            keywords = _keywords_from_title_points(title, points)
        keywords = _polish_keywords(title, points, keywords)
        keywords = [k for k in keywords if not (title and _text_has_topic_conflict(k, title))]
        if not describes:
            describes = fallback_describes(title, points, compose=compose, presence=presence)
            if should_fold_slots(compose):
                describes = _fold_project_slots(describes, project_slots)
            if title and _text_has_topic_conflict(describes, title):
                describes = fallback_describes(title, points, compose=compose, presence=presence)
        # 冲突粘贴不得把接地原文留作 reference_text
        if title_only or (
            title
            and reference
            and (
                _text_has_topic_conflict(reference, title)
                or _looks_like_prior_analysis_artifact(reference)
            )
        ):
            reference = ""
        if not reference and not title_only:
            reference = excerpt[:_REFERENCE_LIMIT]
        if reference or excerpt:
            from report_writing.domain.boilerplate import prepare_reference_from_analysis

            prepared = prepare_reference_from_analysis(
                reference or excerpt,
                compose=compose,
                slots=project_slots,
                title=title,
                model=model,
            )
            if prepared and not (title and _text_has_topic_conflict(prepared, title)):
                reference = prepared
        from report_writing.domain.chapter_compose import COMPOSE_FIXED, normalize_compose as _norm_fixed

        if _norm_fixed(compose) == COMPOSE_FIXED:
            return [], [], "", [], reference, False, [], presence, compose
        blocks = normalize_content_blocks(blocks)
        return points, keywords, describes, blocks, reference, False, list(project_slots or []), presence, compose
    except Exception as error:
        _log.warning("模型解析写作要点失败，改用文本切分：%s", error)
        if title_only or (
            title
            and (
                _text_has_topic_conflict(source_raw, title)
                or _looks_like_prior_analysis_artifact(source_raw)
            )
        ):
            points = default_points_for_compose(title, hint_compose)
            presence_out, compose_out = infer_presence_compose(
                points=points, excerpt="", presence=hint_presence, compose=hint_compose
            )
            if compose_locked and hint_compose:
                from report_writing.domain.chapter_compose import normalize_compose as _norm_c

                compose_out = _norm_c(hint_compose)
            return (
                points,
                _keywords_from_title_points(title, points),
                fallback_describes(title, points, compose=compose_out, presence=presence_out),
                [],
                "",
                False,
                [],
                presence_out,
                compose_out,
            )
        points = _points_from_plain(cleaned)
        presence_out, compose_out = infer_presence_compose(
            points=points, excerpt=cleaned, presence=hint_presence, compose=hint_compose
        )
        if compose_locked and hint_compose:
            from report_writing.domain.chapter_compose import normalize_compose as _norm_c

            compose_out = _norm_c(hint_compose)
        return (
            points,
            _keywords_from_title_points(title, points),
            fallback_describes(title, points, compose=compose_out, presence=presence_out),
            [],
            cleaned[:8000],
            False,
            [],
            presence_out,
            compose_out,
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
) -> tuple[list[str], list[str], str, list[dict], str, bool, list[str], list[dict], str, str]:
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
            presence = str(payload.get("presence") or "").strip()
            compose = str(payload.get("compose") or "").strip()
            boilerplate = str(payload.get("boilerplate") or "").strip()
            if boilerplate:
                reference = boilerplate
            return (
                points[:20],
                keywords[:12],
                describes[:1200],
                blocks,
                reference[:_REFERENCE_LIMIT],
                structure_only,
                writing_style[:8],
                slots,
                presence,
                compose,
            )
    array_match = re.search(r"\[[\s\S]*\]", content)
    if array_match:
        payload = json.loads(array_match.group(0))
        if isinstance(payload, list):
            points = [str(item).strip() for item in payload if str(item).strip()]
            return points[:20], [], "", [], "", False, [], [], "", ""
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


def _describes_fallback(title: str, points: list[str], *, compose: str = "", presence: str = "") -> str:
    from report_writing.domain.compose_analysis import fallback_describes

    return fallback_describes(title, points, compose=compose, presence=presence)


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


