"""无蓝本：目录起草与粘贴解析（结构识别保留正则；口语意图见 freeform_intent）。"""

from __future__ import annotations

import re
from typing import Callable

from report_writing.chat.dialogue import format_full_dialogue
from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]


_CN_CHAPTER_COUNT = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_BARE_GENERATE_PHRASES = frozenset(
    {
        "生成目录",
        "生成大纲",
        "帮我生成目录",
        "帮我生成大纲",
        "起草目录",
        "自动生成目录",
        "generate",
        "generate_outline",
        "help_draft",
        "starter_fallback",
    }
)


def _parse_cn_or_int(token: str) -> int:
    raw = (token or "").strip()
    if raw in _CN_CHAPTER_COUNT:
        return _CN_CHAPTER_COUNT[raw]
    try:
        n = int(raw)
    except ValueError:
        return 0
    return n if 1 <= n <= 20 else 0


def outline_top_chapter_count(text: str) -> int:
    """从「三个章节 / 只要三章 / 3个章节」抽出一级章数量；不认「第3章」。"""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0
    patterns = (
        r"(\d+|[一二两三四五六七八九十])个大章节",
        r"(\d+|[一二两三四五六七八九十])大章节",
        r"(\d+|[一二两三四五六七八九十])个章节",
        r"(\d+|[一二两三四五六七八九十])个大章",
        r"(\d+|[一二两三四五六七八九十])个章",
        r"(\d+|[一二两三四五六七八九十])章节",
        r"(?:只要|仅要|一共|共)(\d+|[一二两三四五六七八九十])个?大?章",
    )
    for pat in patterns:
        m = re.search(pat, compact)
        if m:
            n = _parse_cn_or_int(m.group(1))
            if n:
                return n
    return 0


def is_bare_generate_outline_phrase(text: str) -> bool:
    compact = re.sub(r"\s+", "", (text or "").strip()).strip("。.!！")
    return compact in _BARE_GENERATE_PHRASES


def merge_outline_constraints(existing: str, incoming: str) -> str:
    inc = (incoming or "").strip()
    old = (existing or "").strip()
    if not inc or is_bare_generate_outline_phrase(inc):
        return old
    if not old:
        return inc[:2000]
    if inc in old:
        return old
    if old in inc:
        return inc[:2000]
    return f"{old}；{inc}"[:2000]


def remember_outline_constraints_from_message(session: ChatSession, message: str) -> None:
    text = (message or "").strip()
    if not text or is_bare_generate_outline_phrase(text):
        return
    structural = bool(outline_top_chapter_count(text)) or any(
        k in text for k in ("小节", "小点", "只要", "不要二级", "扁平")
    )
    if not structural:
        return
    ff = dict(session.freeform or {})
    ff["outline_constraints"] = merge_outline_constraints(
        str(ff.get("outline_constraints") or ""), text[:500]
    )
    session.freeform = ff


def trim_outline_directory_text(text: str, keep_top: int) -> str:
    if keep_top <= 0:
        return text
    kept: list[str] = []
    for line in (text or "").splitlines():
        ln = line.strip()
        if not ln:
            continue
        body = re.sub(r"^[-*•]\s*", "", ln)
        m = re.match(r"^(\d+(?:\.\d+)*)[\.．、\s]+", body)
        if not m:
            kept.append(ln)
            continue
        top = int(m.group(1).split(".")[0])
        if top <= keep_top:
            kept.append(ln)
    return "\n".join(kept)


_OUTLINE_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(\d+(?:[\.．、]\d+)*)[\.．、]?\s+\S",
    re.M,
)


def looks_like_outline_directory(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    lines = [re.sub(r"^[-*•]\s*", "", ln.strip()) for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    numbered = sum(1 for ln in lines if _OUTLINE_LINE_RE.match(ln))
    if numbered >= 2:
        return True
    if numbered >= 1 and len(lines) >= 3:
        return True
    if lines and re.search(r"[｜|]", lines[0]) and numbered >= 1:
        return True
    return False


def normalize_pasted_outline(text: str) -> tuple[str, str, str]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    category = ""
    title = ""
    body_start = 0
    if lines and re.search(r"[｜|]", lines[0]) and not _OUTLINE_LINE_RE.match(
        re.sub(r"^[-*•]\s*", "", lines[0])
    ):
        parts = re.split(r"[｜|]", lines[0], maxsplit=1)
        category = (parts[0] or "").strip()
        title = (parts[1] if len(parts) > 1 else "").strip()
        body_start = 1
    body: list[str] = []
    for ln in lines[body_start:]:
        ln = re.sub(r"^[-*•]\s*", "", ln).strip()
        if ln:
            body.append(ln)
    return category, title, "\n".join(body)


def starter_outline_for_category(category: str, title: str = "") -> str | None:
    """目录模型失败时的兜底骨架。"""
    cat = (category or "").strip()
    templates: dict[str, str] = {
        "对策建议": """1 引言
2 现状与形势分析
3 对策建议
4 保障措施""",
        "研究报告": """1 绪论
2 相关理论与现状
3 现状调研与分析
4 结论与建议""",
        "工作说明": """1 总则
2 项目概况
3 工作内容与技术路线
4 组织与进度安排""",
        "述职报告": """1 述职概述
2 全年主要工作完成情况
3 存在问题与改进计划
4 结语""",
    }
    for key, body in templates.items():
        if key in cat:
            return body
    return None


def llm_generate_outline_text(
    session: ChatSession,
    *,
    category: str,
    title: str,
    invoke: InvokeFn,
    revision_instructions: str = "",
    current_outline_text: str = "",
    regenerate: bool = False,
) -> str:
    dialogue = format_full_dialogue(session, max_turns=12)
    context = "\n".join(
        f"{row.get('role', 'user')}: {row.get('text', '')}" for row in dialogue[-8:]
    )
    rev = (revision_instructions or "").strip()
    cur = (current_outline_text or "").strip()
    ff = dict(session.freeform or {})
    if not rev:
        rev = str(ff.get("outline_constraints") or "").strip()
    keep_top = outline_top_chapter_count(rev)
    system = (
        "# Role\n你是文档目录编辑。\n"
        "# Task\n根据文档类型（函件/方案/说明书/纪要/合同/述职/报告等）、标题与用户修改要求，"
        "生成符合该体裁的完整章节目录草案，不要默认套研究报告目录。\n"
        "# Rules\n格式：每行一章，使用 1、1.1、1.1.1 或「1.1 标题」等常见编号。\n"
        "必须尊重修改要求：例如「每个点下面有三小点 / 每章下三个小节」"
        "→ 每个一级章下写出三个二级小节（如 1.1 / 1.2 / 1.3），勿只输出扁平一级章。\n"
        "若修改要求指定了一级章数量（如「三个章节」「只要三章」），一级章必须严格是该数量，"
        "不要为了凑篇幅再加第4、5、6章。\n"
        "重新生成时以当前目录为底稿修订，不要无视用户约束另起炉灶。\n"
        + (
            f"本次一级章数量必须为 {keep_top}。\n"
            if keep_top
            else "未指定数量时，章节条目总数通常约 8～40 行（含二、三级）；优先满足层级结构要求。\n"
        )
        + "# Output\n只输出目录正文，不要解释、不要 Markdown 代码块。"
    )
    parts = [
        f"文档类型：{category or '通用'}",
        f"标题：{title or '未命名'}",
    ]
    if regenerate or cur:
        parts.append(f"【当前目录】\n{(cur or '（无）')[:6000]}")
    if rev:
        parts.append(f"【修改要求】\n{rev[:2000]}")
    parts.append(f"最近对话：\n{context[:4000]}")
    parts.append("请生成目录大纲：")
    user = "\n".join(parts)
    raw = str(invoke(system, user) or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.I)
        raw = re.sub(r"\n?```\s*$", "", raw)
    return raw.strip()


def chapters_to_outline_text(chapters: list[dict]) -> str:
    lines: list[str] = []
    for ch in chapters or []:
        seq = str(ch.get("sequence") or "").strip()
        title = str(ch.get("title") or "").strip()
        if seq and title:
            lines.append(f"{seq} {title}")
        elif title:
            lines.append(title)
    return "\n".join(lines)
