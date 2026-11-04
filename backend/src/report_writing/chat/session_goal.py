"""会话级写作目标：跨轮记住类型/项目/要点，供意图与总控共用。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from report_writing.domain.models import ChatSession

InvokeFn = Callable[[str, str], str]

_META_SPLIT_RE = re.compile(r"[｜|]")
_GOAL_AUX_MARKER = "【会话写作背景】"


def get_session_goal(session: ChatSession) -> dict[str, Any]:
    raw = dict((session.freeform or {}).get("session_goal") or {})
    return {
        "summary": str(raw.get("summary") or "").strip(),
        "doc_category": str(raw.get("doc_category") or "").strip(),
        "doc_title": str(raw.get("doc_title") or "").strip(),
        "project": str(raw.get("project") or "").strip(),
        "notes": str(raw.get("notes") or "").strip(),
        "updated_at": str(raw.get("updated_at") or "").strip(),
    }


def merge_session_goal(session: ChatSession, patch: dict[str, Any]) -> dict[str, Any]:
    goal = get_session_goal(session)
    for key, value in (patch or {}).items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            goal[key] = text
    if any(goal.get(k) for k in ("summary", "doc_category", "doc_title", "project", "notes")):
        goal["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.freeform = {**(session.freeform or {}), "session_goal": goal}
    try:
        from report_writing.chat.session_documents import (
            focus_document_key,
            session_documents_map,
        )

        key = focus_document_key(session)
        if key:
            docs = session_documents_map(session)
            entry = dict(docs.get(key) or {})
            lane = dict(entry.get("lane") or {})
            lane["session_goal"] = dict(goal)
            entry["lane"] = lane
            docs[key] = entry
            session.freeform = {**(session.freeform or {}), "session_documents": docs}
    except Exception:
        pass
    from report_writing.chat.intent_router import touch_session_plan

    touch_session_plan(
        session,
        goal_summary=goal.get("summary") or "",
        doc_category=goal.get("doc_category") or "",
        doc_title=goal.get("doc_title") or "",
    )
    return goal


def session_goal_brief(session: ChatSession) -> str:
    g = get_session_goal(session)
    parts: list[str] = []
    cat_title = ""
    if g.get("doc_category") or g.get("doc_title"):
        cat_title = f"{g.get('doc_category') or '文档'}｜{g.get('doc_title') or '未命名'}"
        parts.append(cat_title)
    if g.get("project"):
        parts.append(f"项目：{g['project']}")
    summary = str(g.get("summary") or "").strip().replace("|", "｜")
    # summary 常与「类型｜标题」同文，勿拼成「A｜B；A｜B」
    if summary and summary != cat_title and summary not in parts:
        parts.append(summary)
    elif not summary and g.get("notes"):
        parts.append(str(g.get("notes") or "")[:200])
    return "；".join(parts)


def is_session_goal_message(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> bool:
    """显式「记住/今后按…」类；纯「类型｜标题」由无蓝本 meta 流程处理。"""
    from report_writing.chat.session_goal_intent import is_explicit_remember_goal_message

    text = (message or "").strip()
    if not text:
        return False
    return is_explicit_remember_goal_message(text, invoke=invoke, session=session)


def _parse_meta_line_structural(message: str) -> dict[str, str]:
    """仅解析「类型｜标题」分隔符结构（非意图路由）；无 invoke 时的兜底。"""
    text = (message or "").strip()
    if not text or not _META_SPLIT_RE.search(text):
        return {}
    from report_writing.chat.session_snapshot import looks_like_freeform_meta_line

    if not looks_like_freeform_meta_line(text):
        return {}
    parts = re.split(r"[｜|]", text, maxsplit=1)
    cat = (parts[0] or "").strip()
    title = (parts[1] if len(parts) > 1 else "").strip()
    patch: dict[str, str] = {}
    if cat:
        patch["doc_category"] = cat
    if title:
        patch["doc_title"] = title
    if patch:
        patch["summary"] = f"{patch.get('doc_category', cat)}｜{patch.get('doc_title', title)}".strip("｜")
    return patch


def parse_goal_patch_from_message(
    message: str,
    *,
    invoke: InvokeFn | None = None,
    session: ChatSession | None = None,
) -> dict[str, str]:
    text = (message or "").strip()
    if not text:
        return {}
    from report_writing.chat.session_goal_intent import doc_meta_patch_from_message

    if invoke is not None:
        hit = doc_meta_patch_from_message(text, invoke=invoke, session=session)
        if hit:
            return {k: v for k, v in hit.items() if v}
    return _parse_meta_line_structural(text)


def freeform_meta_from_session(session: ChatSession) -> tuple[str, str] | None:
    """已写入 session_goal / freeform 的类型与标题（不经正则推断）。"""
    from report_writing.chat.helpers import is_vague_freeform_meta

    g = get_session_goal(session)
    title = str(g.get("doc_title") or "").strip()
    category = str(g.get("doc_category") or "").strip()
    if not title:
        ff = dict(session.freeform or {})
        title = str(ff.get("title") or "").strip()
        category = str(ff.get("category") or category).strip()
    if not title:
        summary = str(g.get("summary") or "").strip()
        if _META_SPLIT_RE.search(summary):
            parts = re.split(r"[｜|]", summary, maxsplit=1)
            category = (parts[0] or "").strip() or category
            title = (parts[1] if len(parts) > 1 else "").strip()
    if title and not is_vague_freeform_meta(title, field=True):
        if category and is_vague_freeform_meta(category, field=True):
            category = "文档"
        return (category or "文档", title)
    return None


def infer_freeform_meta_from_dialogue(
    session: ChatSession,
    invoke: InvokeFn,
    *,
    extra: str = "",
) -> tuple[str, str] | None:
    """用模型结合近期对话理解要写什么（类型｜标题），不用关键词表路由。"""
    hit = freeform_meta_from_session(session)
    if hit:
        return hit
    message = (extra or "").strip()
    if not message:
        for msg in reversed(list(session.messages or [])[-8:]):
            if str(getattr(msg, "role", "") or "") != "user":
                continue
            message = str(getattr(msg, "text", "") or "").strip()
            if message:
                break
    if not message:
        return None
    patch = _llm_extract_goal_patch(session, message, invoke=invoke)
    if not patch:
        return None
    title = str(patch.get("doc_title") or "").strip()
    category = str(patch.get("doc_category") or "").strip()
    if not title and patch.get("summary"):
        summary = str(patch["summary"]).strip()
        if _META_SPLIT_RE.search(summary):
            parts = re.split(r"[｜|]", summary, maxsplit=1)
            category = (parts[0] or "").strip() or category
            title = (parts[1] if len(parts) > 1 else "").strip()
        else:
            title = summary[:160]
    if not title:
        return None
    from report_writing.chat.helpers import is_vague_freeform_meta

    # 拒绝把「不用蓝本 / 尚未说明题目」等旁白当成标题
    if is_vague_freeform_meta(title, field=True) or is_vague_freeform_meta(
        category or "", field=True
    ):
        return None
    merge_session_goal(session, {**patch, "doc_category": category or "文档", "doc_title": title})
    sync_goal_into_freeform(session)
    return (category or "文档", title)


def session_goal_writing_context(session: ChatSession) -> str:
    """供各章写作 aux_prompt 注入的跨章背景。

    不注入项目名等元数据：补充提示词只保留文档类型/标题与写法向备注，
    具体工程事实应由检索证据提供，避免模型照搬样板或会话里的项目实例。
    """
    g = get_session_goal(session)
    parts: list[str] = []
    if g.get("notes"):
        parts.append(str(g["notes"]).strip())
    elif g.get("summary"):
        parts.append(str(g["summary"]).strip())
    # 故意不写入 g["project"]：项目元数据不得进入补充提示词
    if g.get("doc_category") or g.get("doc_title"):
        head = f"{g.get('doc_category') or ''}｜{g.get('doc_title') or ''}".strip("｜")
        if head and head not in "\n".join(parts):
            parts.insert(0, head)
    return "\n".join(p for p in parts if p).strip()


def _merge_goal_into_aux(existing: str, ctx: str, *, force: bool = False) -> str:
    """合并会话背景到补充提示词。

    - force=True：开写/显式记住目标时写入或刷新标记块
    - force=False：仅当原文本已含标记时刷新内容；用户已清空或改掉标记则不再强行塞回
    """
    block = f"{_GOAL_AUX_MARKER}\n{ctx}".strip()
    old = (existing or "").strip()
    if not ctx:
        return old
    if block in old:
        return old
    if _GOAL_AUX_MARKER in old:
        after = old.split(_GOAL_AUX_MARKER, 1)[1].lstrip("\n")
        tail = after.split("\n\n", 1)[-1].strip() if "\n\n" in after else ""
        return f"{block}\n\n{tail}".strip() if tail else block
    if not force:
        # 用户删掉补充提示词后不应在重试/拉目录时再写回来
        return old
    return f"{block}\n\n{old}".strip() if old else block


def _goal_matches_task(session: ChatSession, task: Any) -> bool:
    """session_goal 的文档标题/类型与任务自身明显不一致时，禁止刷新 aux。"""
    g = get_session_goal(session)
    goal_title = str(g.get("doc_title") or "").strip()
    goal_cat = str(g.get("doc_category") or "").strip()
    if not goal_title and not goal_cat:
        return True
    task_title = str(getattr(task, "title", "") or "").strip()
    task_cat = str(getattr(task, "category", "") or "").strip()
    if goal_title and task_title and goal_title != task_title:
        # 一方包含另一方仍视为同文档（如「述职」vs「2026个人述职」）
        if goal_title not in task_title and task_title not in goal_title:
            return False
    if goal_cat and task_cat and goal_cat != task_cat:
        if goal_cat not in task_cat and task_cat not in goal_cat:
            return False
    return True


def apply_session_goal_to_task(session: ChatSession, task: Any, *, force: bool = False) -> bool:
    """把已记住的会话目标写入任务 brief/大纲 aux_prompt，后续各章写作都会遵守。"""
    from report_writing.domain.models import WritingTask

    if not isinstance(task, WritingTask):
        return False
    if not _goal_matches_task(session, task):
        return False
    ctx = session_goal_writing_context(session)
    if not ctx:
        return False
    changed = False
    if task.outline and task.outline.chapters:
        for node in task.outline.chapters:
            if getattr(node, "structure_only", False):
                continue
            merged = _merge_goal_into_aux(getattr(node, "aux_prompt", "") or "", ctx, force=force)
            if merged != (getattr(node, "aux_prompt", "") or "").strip():
                node.aux_prompt = merged
                changed = True
    if task.brief is not None:
        for req in task.brief.chapters or []:
            if getattr(req, "structure_only", False):
                continue
            merged = _merge_goal_into_aux(getattr(req, "aux_prompt", "") or "", ctx, force=force)
            if merged != (getattr(req, "aux_prompt", "") or "").strip():
                req.aux_prompt = merged
                changed = True
    return changed


def sync_goal_into_freeform(session: ChatSession) -> None:
    """把已记住的目标写入 freeform 字段，供无蓝本流程复用。"""
    g = get_session_goal(session)
    if not any(g.get(k) for k in ("doc_category", "doc_title", "project", "summary")):
        return
    ff = dict(session.freeform or {})
    if g.get("doc_category"):
        ff["category"] = g["doc_category"]
    if g.get("doc_title"):
        ff["title"] = g["doc_title"]
    session.freeform = ff


def ingest_user_message_goal(
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None = None,
) -> bool:
    """每轮用户话后静默合并目标；有更新返回 True。"""
    text = (message or "").strip()
    if not text:
        return False
    patch = parse_goal_patch_from_message(text, invoke=invoke)
    if patch.get("doc_category") or patch.get("doc_title"):
        merge_session_goal(session, patch)
        sync_goal_into_freeform(session)
        return True
    if invoke is not None and len(text) >= 4:
        extra = _llm_extract_goal_patch(session, text, invoke=invoke)
        if extra:
            patch = {**patch, **{k: v for k, v in extra.items() if v}}
    if not patch:
        return False
    merge_session_goal(session, patch)
    sync_goal_into_freeform(session)
    return True


def remember_from_user_message(
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None = None,
) -> str:
    """显式记住目标并返回口语确认。"""
    text = (message or "").strip()
    if not text:
        return "你想让我记住什么？可以说：类型｜标题，或「记住，后面都按某某项目工作说明写」。"
    ingest_user_message_goal(session, text, invoke=invoke)
    brief = session_goal_brief(session)
    if not brief:
        return "我还没抓准要写什么，你可以发：工作说明｜某某项目工作说明。"
    return f"好，我记下了：{brief}。后面选蓝本、生成目录都会按这个来。"


def _llm_extract_goal_patch(
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn,
) -> dict[str, str] | None:
    from report_writing.chat.dialogue import format_full_dialogue

    system = (
        "你是写作会话目标提取器。结合 dialogue 与当前 message，理解用户真正要写的文档。"
        "若本轮只是「不用蓝本/跳过蓝本」等路径选择，应保留对话里更早的写作题目，不要清空。"
        "只返回 JSON："
        '{"summary":"","doc_category":"","doc_title":"","project":"","notes":""} '
        "doc_category 为简短类型（如技术方案、工作说明），doc_title 为具体题目；缺项留空，不要编造。"
    )
    payload = {
        "dialogue": format_full_dialogue(session, max_turns=10),
        "message": message[:1500],
        "existing": get_session_goal(session),
    }
    try:
        raw = str(invoke(system, json.dumps(payload, ensure_ascii=False)) or "").strip()
    except Exception:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.I)
        raw = re.sub(r"\n?```\s*$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    out: dict[str, str] = {}
    for key in ("summary", "doc_category", "doc_title", "project", "notes"):
        val = str(data.get(key) or "").strip()
        if val:
            out[key] = val
    return out or None


def should_prefer_react_over_freeform_dispatch(
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None = None,
) -> bool:
    """无蓝本快车道：复杂句交给总控 ReAct。

    有模型时交给 dispatch 路由分类器（可识别「生成目录 + 看要素库」等复合句）；
    不再用单动作 wants_* 抢先锁死 freeform。
    """
    text = (message or "").strip()
    if not text:
        return False
    if is_session_goal_message(text, invoke=invoke, session=session) and not _META_SPLIT_RE.search(
        text
    ):
        return False
    if invoke is None:
        return False
    from report_writing.chat.intent_context import pack_classifier_context
    from report_writing.chat.intent_llm import classify_freeform_dispatch_route_llm

    phase = str(session.phase or "")
    ctx = pack_classifier_context(session)
    route = classify_freeform_dispatch_route_llm(
        text,
        phase=phase or str(ctx.get("phase") or ""),
        dialogue_tail=list(ctx.get("dialogue_tail") or []),
        focus_document_brief=str(ctx.get("focus_document_brief") or ""),
        invoke=invoke,
    )
    return route == "react"
