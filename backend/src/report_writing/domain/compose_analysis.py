"""按写作策略区分学要点与写作口径：要点 / 章节说明 / 参考正文。补充提示词不生成。"""

from __future__ import annotations

import re
from typing import Any

_DESCRIBE_LEAD = re.compile(r"^\s*(?:\d+[\.．、\)）]|[-•*、])\s*")

from report_writing.domain.chapter_compose import (
    COMPOSE_AUTO_GENERATE,
    COMPOSE_FIXED,
    COMPOSE_HYBRID,
    COMPOSE_SOURCE_ONLY,
    COMPOSE_TEMPLATE_FILL,
    normalize_compose,
)

COMPOSE_LABELS = {
    COMPOSE_SOURCE_ONLY: "资料驱动 (SOURCE_ONLY)：有资料才写，无则整章转人工",
    COMPOSE_TEMPLATE_FILL: "模板填槽 (TEMPLATE_FILL)：固定话术为主，缺槽留空",
    COMPOSE_HYBRID: "混合 (HYBRID)：部分相关据实写，主题无关才转人工",
    COMPOSE_AUTO_GENERATE: "自动生成 (AUTO_GENERATE)：不依赖资料，按要点自动成文",
    COMPOSE_FIXED: "固定内容输出 (FIXED)：按原文直接输出，不改写",
}


def chapter_anatomy_block() -> str:
    """资深文档写作专家：把样板章节拆成组成部分 / 格式 / 怎么写 / 要点。"""
    return (
        "# Anatomy\n"
        "把这一章还原成下一份同类文档的施工图，不是缩写原文。禁止先摘要再空写要点。\n"
        "精读顺序：组成部分 → 格式 → 怎么写 → 写作要点。四者分开写进不同字段。\n"
        "\n"
        "1. 组成部分：按原文出现顺序列 3～8 块。块名用功能（开篇定位、依据与范围、分项论述、表格/清单、归纳结论、向下章过渡等）。"
        "原文没有的块不要造：无表不写表、无对策专段不写对策、无分条不写分条。\n"
        "\n"
        "2. 格式（版式）：每一块在纸面上长什么样。标明段落 / 编号条列 / 表格（列名、表题「表x.x」）/ 图注。"
        "记下先后、条列编号习惯、是否「见表」。排版写进 describes 的「排版格式」，不要只说「条理清晰」。\n"
        "\n"
        "3. 怎么写：结论在前还是发现在前；每条是「对象+现象+判断」还是「定义+列举」；是否引标准；与邻章边界。"
        "写入 describes 的风格/特点/描述方式/注意事项。\n"
        "\n"
        "4. 写作要点 points：一条对应一个组成部分，顺序=原文阅读顺序。\n"
        "推荐句式：「【组成部分】怎么写；版式：…；须覆盖：信息类型」。\n"
        "正例：【分因素评价】按原文出现的危险/有害因素类型分条；版式：条列或短段；须覆盖：因素类型、评价结论口径。\n"
        "反例：交代工程名称与电压等级（元数据，进 project_slots）。\n"
        "反例：注意表述方式、层次清楚（这是 describes，不要当 points）。\n"
        "反例：把邻章「对策措施」「单元划分」写进本章要点。\n"
    )


def analysis_strategy_block(
    *,
    compose: str = "",
    presence: str = "",
    locked: bool = False,
    category: str = "",
) -> str:
    """注入学要点主提示：先定策略，再填 points / describes / reference_text。不生成 aux。"""
    from report_writing.domain.doc_genre import genre_writing_hint, normalize_doc_category

    mode = normalize_compose(compose, presence=presence) if (locked and compose) else ""
    cat = normalize_doc_category(category)
    head = (
        f"# Role\n文档写作专家。当前文档类型：{cat}。{genre_writing_hint(cat)}\n"
        "# Task\n先判定 compose，再按该策略填写 describes / points / writing_style / project_slots / "
        "boilerplate / reference_text。五种策略不可混用。\n"
        "# Constraints\n"
        "compose 只允许：source_only | template_fill | hybrid | auto_generate | fixed。\n"
        "禁止生成 aux_prompt / 补充提示词（留给用户手填）。\n"
        "keywords 仅作参考术语，不作为章节级检索词。\n"
    )
    if locked and mode:
        head += (
            f"【已锁定】本章写作策略 = {mode}（{COMPOSE_LABELS.get(mode, mode)}）。"
            "必须按此策略分析，禁止改成其它 compose。\n"
        )
    else:
        head += (
            "判定 compose：\n"
            "- 固定公文套话、仅项目名/文号/数值会变 → template_fill。\n"
            "- 必须有对应项目资料才写、没有则整章交人工 → source_only。\n"
            "- 部分可据资料现写、与章名/主题无关才转人工 → hybrid。\n"
            "- 无需项目资料、按要点由模型直接成文 → auto_generate。\n"
            "- 本章应整段沿用原文、不改写 → fixed。\n"
        )
    head += (
        "\n" + chapter_anatomy_block() + "\n"
        "## 章节说明 describes\n只写怎么写与什么格式，不写内容提纲。\n"
        "必须分点，每行一条：\\n1. …\\n2. …；3～8 条。建议覆盖：\n"
        "写作风格（语气/人称/正式度）、写作特点（组成块先后）、描述方式（结论先行还是分项展开）、"
        "排版格式（段落/条列/表格/表号）、写作注意事项（与邻章边界、勿把样板实例当事实）。\n"
        "禁止一段话，禁止「本章要写什么」提纲，禁止样板实例，禁止把元数据清单拼进 describes。\n"
        "\n## 参考正文 reference_text\n必须保留原文语句与语序，禁止概括改写、禁止补行。\n"
        "- source_only / template_fill / hybrid：只把项目实例换成 {{槽名}}，骨架不动。\n"
        "- auto_generate / fixed：原文原样，不填槽。\n"
        "\n## Mode: source_only\n"
        "- points：按组成部分 5～12 条；缺资料的块用（待补充：类型名）。\n"
        "\n## Mode: template_fill\n"
        "- points：3～8 条，一条对应一句套话或一个槽位块。boilerplate 与 reference_text 同口径填槽。\n"
        "\n## Mode: hybrid\n"
        "- points：按组成部分 5～12 条可执行写法；主题无关块不要硬写成要点。\n"
        "\n## Mode: auto_generate\n"
        "- points：按组成部分 5～12 条，供无资料时仍按该结构成文。\n"
        "\n## Mode: fixed\n"
        "- points、describes 均留空。reference_text=原文原样，写作时整段照抄。\n"
    )
    return head


def write_craft_block() -> str:
    """资深文档写作专家：按组成部分组稿，按格式落笔。"""
    return (
        "## 成稿章法\n"
        "目标：写这一章的正式正文，不是扩写提纲，也不是复述要点列表。\n"
        "步骤：\n"
        "1. 读 describes：定语气、组成块先后、描述方式、排版、邻章边界。\n"
        "2. 按 points / point_write_plan 顺序铺块。一条要点 = 一个组成部分，写完再写下一条；"
        "不得漏条或改用样板目录顶替；有 element_value 则写入该块事实。\n"
        "3. 版式跟要点与 content_blocks：表格用 <table>，条列用列表或（1）（2），段落用 <p>。"
        "不要整章糊成一段，也不要把每条要点做成「标题+套话」。\n"
        "4. 把证据中的名称/数值/结论嵌进对应组成块，不要先堆事实再空写结构。\n"
        "禁止：\n"
        "- 把 points 原文当小标题或「应说明……」提纲句贴进正文。\n"
        "- 开篇空帽子（「本章主要介绍/将从以下几方面」），除非 describes 要求结论先行且开篇即总判断。\n"
        "- 打乱 points 顺序，或写入邻章专题。\n"
        "- 把 describes 整段贴进正文。\n"
        "- 用「首先、其次、最后」硬套，除非样板就是这种节奏。\n"
        "- 把所有数字堆在第一段。\n"
    )


def write_strategy_block(*, compose: str = "", presence: str = "") -> str:
    """注入写作主提示：按策略使用 describes / 参考正文 / 补充提示词。"""
    mode = normalize_compose(compose, presence=presence)
    label = COMPOSE_LABELS.get(mode, COMPOSE_LABELS[COMPOSE_HYBRID])
    return (
        f"# Strategy\n"
        f"writing_strategy = `{mode}`（{label}）。禁止混用其它策略。\n"
        + write_craft_block()
        + "\n## Field usage\n"
        "- describes：1. 2. 分点的写法与格式约束，逐条遵守，不要当内容提纲扩写。\n"
        "- points / point_write_plan：组成部分权威清单，按顺序完整覆盖；正文写该块内容，"
        "不要把要点原文当小标题回显，不得漏条、不得用样板结构替换要点清单。\n"
        "- aux_prompt：仅用户手填时生效；非空则高于 describes / points / reference_text。"
        "系统不会自动生成补充提示词。\n"
        "\n## Mode: source_only\n"
        "只写已采纳资料支撑的组成块；无资料不要编段落，整章应由上游转人工。\n"
        "参考正文是填槽骨架：学写法与格式，事实只来自证据；禁止照抄样板实例。\n"
        "\n## Mode: template_fill\n"
        "套话骨架不得改写，只把 {{槽名}} 换成已采纳证据/要素中的实际值；"
        "缺槽留空，不要删句、写待补充或扩写。必填缺槽可进 uncovered_points。\n"
        "\n## Mode: hybrid\n"
        "与本章主题相关的组成块据实写；主题无关放入 uncovered_points，不要硬写无关专题。\n"
        "参考正文是填槽骨架，只学写法与格式，禁止整句照抄（填槽替换除外）。\n"
        "\n## Mode: auto_generate\n"
        "不依赖项目资料，按 points 的组成顺序与 describes 的写法约束自动成文；"
        "无证据也必须交出完整章节，不要整章转人工、不要只列缺口、不要（待补充）。\n"
        "reference_text 是原文对照，只学写法与版式，严禁整段整句照抄。\n"
        "\n## Mode: fixed\n"
        "不要改写：按 reference_text 原文直接输出。不要 invent 要点、不要检索、不要补充。\n"
    )


def reference_usage_hint(compose: str = "", *, has_reference: bool = False) -> str:
    if not has_reference:
        return ""
    mode = normalize_compose(compose)
    if mode == COMPOSE_FIXED:
        return "verbatim_copy：按参考正文原文输出，不改写"
    if mode == COMPOSE_AUTO_GENERATE:
        return "style_format_only：高权重模仿描述方式与排版；禁止照抄原文句子或样板实例"
    if mode == COMPOSE_TEMPLATE_FILL:
        return "slot_fill_leave_empty：只替换 {{槽}}，缺槽留空，骨架不改"
    return "slot_fill_style：保留填槽骨架学写法；事实来自证据，禁止照搬样板实例"


def meta_system_prompt(*, compose: str = "", presence: str = "", category: str = "") -> str:
    from report_writing.domain.doc_genre import coach_persona, genre_writing_hint, normalize_doc_category

    mode = normalize_compose(compose, presence=presence)
    label = COMPOSE_LABELS.get(mode, COMPOSE_LABELS[COMPOSE_HYBRID])
    cat = normalize_doc_category(category)
    return (
        f"# Role\n{coach_persona(cat)}。{genre_writing_hint(cat)}\n"
        "# Task\n根据本章写作要点、参考正文与策略，只重写「章节说明」。\n"
        "# Constraints\n"
        "章节说明只描述怎么写与什么格式：写作风格、写作特点（组成块先后）、描述方式、排版格式、写作注意事项（与邻章边界）。\n"
        "必须分点，用换行分隔，例如：\\n1. 写作风格：…\\n2. 写作特点：先…后…\\n3. 描述方式：…\n"
        "分点写全，不要截短用户已写的章节说明；不要把通例改成具体项目条目。\n"
        "不要写成一段话，不要把内容提纲或组成部分清单写进 describes（那些属于 points）。\n"
        "禁止把工程名、地址、投资额等项目实例写进 describes。\n"
        "不要生成补充提示词，不要输出 aux_prompt 字段。\n"
        f"当前写作策略：{mode}（{label}）。\n"
        "# Output\n只返回 JSON，不要 Markdown：\n"
        '{"describes":"1. …\\n2. …"}\n'
    )


def format_describes_as_points(text: str, *, max_items: int = 32) -> str:
    """把章节说明规范成「1. …\\n2. …」分点。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = raw.replace("【写作特点】", "\n").replace("\\n", "\n")
    chunks: list[str] = []
    for part in re.split(r"[\n]+", raw):
        part = str(part or "").strip()
        if not part:
            continue
        part = _DESCRIBE_LEAD.sub("", part).strip().rstrip("；;。")
        if not part:
            continue
        bits = [x.strip().rstrip("。") for x in re.split(r"[；;]", part) if x.strip()]
        if len(bits) >= 2 and all(len(b) <= 80 for b in bits):
            chunks.extend(bits)
        else:
            chunks.append(part)
    out: list[str] = []
    seen: set[str] = set()
    for item in chunks:
        key = re.sub(r"\s+", "", item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    if not out:
        return ""
    return "\n".join(f"{i}. {item}" for i, item in enumerate(out, 1))


def fallback_describes(
    title: str,
    points: list[str],
    *,
    compose: str = "",
    presence: str = "",
) -> str:
    name = (title or "").strip() or "本章"
    mode = normalize_compose(compose, presence=presence)
    if mode == COMPOSE_FIXED:
        return ""
    del points
    return format_describes_as_points(
        "\n".join(
            [
                f"写作风格：语气与正式度对齐「{name}」所属体裁",
                "写作特点：按原文组成块依次展开，先交代本章定位再写主体，最后收束",
                "描述方式：客观陈述；结论与发现的先后跟样板走，不评价、不扩写无关专题",
                "排版格式：段落/条列/表格及表号层级与样板一致，不要改成另一种版式",
                "写作注意事项：不要把样板实例当本项目事实；不要把邻章内容写进本章",
            ]
        )
    )


def fallback_aux(
    *,
    compose: str = "",
    presence: str = "",
    points: list[str] | None = None,
    items: list[Any] | None = None,
) -> str:
    mode = normalize_compose(compose, presence=presence)
    if mode == COMPOSE_SOURCE_ONLY:
        return (
            "只写已有项目资料支撑的内容；无对应资料则整章转人工，禁止编造、禁止用待补充硬凑正文。"
        )[:800]
    if mode == COMPOSE_AUTO_GENERATE:
        body = "\n".join(str(p).strip() for p in (points or [])[:8] if str(p).strip())
        core = "不依赖项目资料，按要点自动成文；无资料也要写出完整章节，不要整章转人工、不要留空。"
        return f"{core}\n{body}".strip()[:800]
    if mode == COMPOSE_TEMPLATE_FILL:
        body = "\n".join(str(p).strip() for p in (points or [])[:8] if str(p).strip())
        core = (
            "只将套话中的 {{槽名}} 替换为本项目资料中的实际值；禁止改写套话骨架；"
            "未取得资料的槽位留空，不要删句、不要写待补充、不要编造。"
        )
        text = core
        if body:
            text = f"{text}\n{body}"
        return text.strip()[:800]
    if items:
        from report_writing.domain.point_items_sync import (
            chapter_has_pending_supplements,
            pending_supplement_slots,
            writing_text_for_point_row,
        )

        pending = chapter_has_pending_supplements(items)
        lines = [
            writing_text_for_point_row(r)
            for r in items
            if isinstance(r, dict)
        ]
        body = "\n".join(t for t in lines if t).strip()
        prefix = "与本章主题无关的内容不要写入正文，标为转人工。\n"
        if pending:
            slot_lines = [f"资料·{s}" for s in pending_supplement_slots(items)]
            if slot_lines:
                prefix = "【建议补充资料】\n" + "\n".join(slot_lines) + "\n" + prefix
        return f"{prefix}{body}".strip()[:800]
    body = "\n".join(str(p).strip() for p in (points or [])[:8] if str(p).strip())
    core = "相关部分据实写；主题无关转人工，不要硬写。"
    return f"{core}\n{body}".strip()[:800]


def default_points_for_compose(title: str, compose: str) -> list[str]:
    name = (title or "").strip() or "本章"
    mode = normalize_compose(compose)
    if mode == COMPOSE_SOURCE_ONLY:
        return [
            f"仅依据「{name}」相关项目资料撰写，无资料则整章转人工",
            "不编造名称、数值、结论；资料缺口不写正文",
        ]
    if mode == COMPOSE_TEMPLATE_FILL:
        return [
            f"按「{name}」固定话术成文，只替换资料槽位",
            "未取得资料的槽位留空，不得编造、不得删句",
        ]
    if mode == COMPOSE_AUTO_GENERATE:
        return [
            f"围绕「{name}」按要点自动成文，不依赖项目资料",
            "无资料也要写完整章节，不要留空或转人工",
        ]
    if mode == COMPOSE_FIXED:
        return []
    return [
        f"围绕「{name}」据实写与主题相关的内容",
        "与本章主题无关的条目转人工，不要硬写",
    ]


def align_describes_to_compose(
    describes: str,
    *,
    title: str,
    points: list[str],
    compose: str,
    presence: str = "",
) -> str:
    text = str(describes or "").strip()
    mode = normalize_compose(compose, presence=presence)
    fb = fallback_describes(title, points, compose=mode, presence=presence)
    if mode == COMPOSE_FIXED:
        return ""
    if not text:
        return fb
    return format_describes_as_points(text) or fb


def should_fold_slots(compose: str) -> bool:
    """章节说明不再拼元数据清单；槽位只留在 project_slots / extracts。"""
    return False
