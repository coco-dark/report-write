"""文档类型（体裁）：写作角色不默认「报告」，按当前文档类型调整口径。"""

from __future__ import annotations

from typing import Any

# 会话/任务未填类型时用中性「文档」，禁止默认成「报告」
DEFAULT_DOC_CATEGORY = "文档"

_REPORT_ALIASES = {"", "报告", "通用", "document", "doc", "general"}


def normalize_doc_category(value: Any, *, default: str = DEFAULT_DOC_CATEGORY) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() in _REPORT_ALIASES or raw in {"报告", "通用"}:
        return default
    return raw


def category_from_task(task: Any = None, session: Any = None) -> str:
    ff = {}
    if session is not None:
        ff = dict(getattr(session, "freeform", None) or {})
        goal = dict(ff.get("goal") or {}) if isinstance(ff.get("goal"), dict) else {}
        for key in ("category",):
            got = str(ff.get(key) or "").strip()
            if got:
                return normalize_doc_category(got)
        got = str(goal.get("doc_category") or "").strip()
        if got:
            return normalize_doc_category(got)
    if task is not None:
        got = str(getattr(task, "category", "") or "").strip()
        if got:
            return normalize_doc_category(got)
    return DEFAULT_DOC_CATEGORY


def _match(cat: str, *needles: str) -> bool:
    return any(n and n in cat for n in needles)


def genre_writing_hint(category: str) -> str:
    """体裁习惯：函件≠研究报告。只给写作约束，不改组稿方式。"""
    cat = normalize_doc_category(category)
    if _match(cat, "函", "通知", "通报", "批复", "请示", "意见", "决定", "通告", "公告"):
        return (
            f"本文档是「{cat}」。按公文/往来函件写：标题、主送/抄送（若有）、事由、事项、结尾称谓与落款；"
            "语气得体、一事一段；不要写成调研报告或评价专篇。"
        )
    if _match(cat, "方案", "策划", "计划", "规划"):
        return (
            f"本文档是「{cat}」。按方案结构写：背景与目标、范围、路径/措施、进度与资源、风险与保障；"
            "可执行、可验收，不要空泛倡议，也不要写成检测评价报告。"
        )
    if _match(cat, "说明书", "手册", "指南", "规程", "作业指导"):
        return (
            f"本文档是「{cat}」。按说明/操作文体写：对象、步骤、注意与禁忌；祈使或说明语气；"
            "步骤可编号，不要写成论文式论证。"
        )
    if _match(cat, "纪要", "记录", "备忘"):
        return (
            f"本文档是「{cat}」。按会议/事项纪要写：时间地点、出席、议题、结论与待办责任人；"
            "据实记录，不扩写成分析报告。"
        )
    if _match(cat, "合同", "协议", "条款"):
        return (
            f"本文档是「{cat}」。按条款体写：定义、权利义务、期限、违约与签署；编号清晰；"
            "禁止抒情和评价性扩写。"
        )
    if _match(cat, "述职", "总结", "汇报"):
        return (
            f"本文档是「{cat}」。按履职/工作总结写：职责、进展与实绩、问题、下一步；"
            "用第一人称或本单位口径，不要套成工程安评章节。"
        )
    if _match(cat, "演讲", "致辞", "讲话"):
        return (
            f"本文档是「{cat}」。按口头表达写：称谓、开场、分层要点、收束；可有节奏感，但事实仍须有据。"
        )
    if _match(cat, "报告", "评价", "安评", "环评"):
        return (
            f"本文档是「{cat}」。按该类专业报告的章节习惯写：依据、对象、分析、结论层次清楚；"
            "仍须紧扣本章标题，不要串到其它专篇。"
        )
    return (
        f"本文档类型是「{cat}」，不是默认的研究报告。"
        "文风、称谓、章节功能须符合该类型常见写法；不要无故套用安评/工程报告体例。"
    )


def writer_persona(category: str) -> str:
    cat = normalize_doc_category(category)
    return f"你是资深「{cat}」文档写作专家，按组成部分与格式写成正式章节正文"


def analyst_persona(category: str) -> str:
    cat = normalize_doc_category(category)
    return (
        f"你是资深「{cat}」文档写作专家，负责把样板章节拆成可复用施工图"
        "（组成部分、格式、怎么写、写作要点）"
    )


def coach_persona(category: str) -> str:
    cat = normalize_doc_category(category)
    return f"你是「{cat}」写作教练"


def checker_persona(category: str) -> str:
    cat = normalize_doc_category(category)
    return f"你是「{cat}」文稿检查员"


def searcher_persona(category: str) -> str:
    cat = normalize_doc_category(category)
    return f"你是「{cat}」证据检索员"


def instructions_with_genre(base: str, category: str) -> str:
    cat = normalize_doc_category(category)
    hint = genre_writing_hint(cat)
    return (
        f"{base.rstrip()}\n\n"
        f"## Document type\n"
        f"{cat}\n"
        f"{hint}\n"
        "payload.doc_category 若有值，以它为准；禁止在未指定时默认写成「研究报告」。"
    )
