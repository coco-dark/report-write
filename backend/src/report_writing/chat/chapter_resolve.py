"""写作任务：从大纲解析焦点章（供 turn_context）；不做口语意图路由。"""

from __future__ import annotations

import re

from report_writing.domain.models import ChatPhase, ChatSession, WritingTask

_SEQ_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _norm_seq(value: str) -> str:
    return str(value or "").strip().replace("．", ".").replace("。", ".")


def message_has_chapter_sequence(message: str) -> bool:
    """原话是否点名了章节序号（如 2.2 / 第2章）。"""
    from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message

    return bool(parse_study_sequences_from_message(message or ""))


def resolve_chapter_key_by_title(task: WritingTask | None, message: str) -> str:
    """用户点了章节名（如「为建设规模添加…」）时按最长标题匹配。"""
    if task is None or not task.outline or not task.outline.chapters:
        return ""
    text = (message or "").strip()
    if not text:
        return ""
    hits: list[tuple[int, str]] = []
    for node in list(task.outline.chapters or []):
        title = str(getattr(node, "title", "") or "").strip()
        key = str(getattr(node, "key", "") or "").strip()
        if not title or not key or len(title) < 2:
            continue
        if title in text:
            hits.append((len(title), key))
    if not hits:
        return ""
    hits.sort(reverse=True)
    return hits[0][1]


def resolve_chapter_key_by_sequence(task: WritingTask | None, sequence: str) -> str:
    """按序号精确命中；没有精确章时不回落到无关章节。"""
    if task is None or not task.outline or not task.outline.chapters:
        return ""
    want = _norm_seq(sequence)
    if not want or not _SEQ_TOKEN_RE.fullmatch(want):
        return ""
    nodes = list(task.outline.chapters or [])
    for node in nodes:
        ns = _norm_seq(getattr(node, "sequence", "") or "")
        if ns == want:
            return str(getattr(node, "key", "") or "")
    # 无精确节点时，取该序号下第一个子孙（目录节点常 structure_only）
    descendants: list[tuple[str, str]] = []
    for node in nodes:
        ns = _norm_seq(getattr(node, "sequence", "") or "")
        key = str(getattr(node, "key", "") or "").strip()
        if not ns or not key or not (ns == want or ns.startswith(want + ".")):
            continue
        if ns == want:
            return key
        descendants.append((ns, key))
    if descendants:
        descendants.sort(key=lambda item: [int(p) if p.isdigit() else p for p in item[0].split(".")])
        return descendants[0][1]
    return ""


def resolve_chapter_key_from_message(task: WritingTask | None, message: str) -> str:
    """从原话解析章节：序号优先且必须准确；有序号却对不上时不瞎匹配标题。"""
    if task is None or not task.outline or not task.outline.chapters:
        return ""
    text = (message or "").strip()
    if not text:
        return ""
    from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message

    seqs = [_norm_seq(s) for s in parse_study_sequences_from_message(text) if _norm_seq(s)]
    for want in seqs:
        key = resolve_chapter_key_by_sequence(task, want)
        if key:
            return key
    if seqs:
        # 用户已点名序号，但对不上大纲：宁可空，也不要落到上一焦点章
        return ""
    return resolve_chapter_key_by_title(task, text)


def pick_awaiting_confirm_chapter_key(task: WritingTask | None) -> str:
    if task is None:
        return ""
    from report_writing.domain.models import ChapterStatus

    for ch in reversed(task.chapters or []):
        st = ch.status.value if hasattr(ch.status, "value") else ch.status
        if st in {ChapterStatus.AWAITING_CONFIRM.value, ChapterStatus.NEEDS_HUMAN.value}:
            return str(ch.key or "")
    return ""


def pick_focus_chapter_key(task: WritingTask | None, message: str) -> str:
    """用户话里的序号优先，否则取待确认章。"""
    if task is None:
        return ""
    key = resolve_chapter_key_from_message(task, message)
    if key:
        return key
    if message_has_chapter_sequence(message):
        return ""
    return pick_awaiting_confirm_chapter_key(task)


def _chapter_key_on_task(task: WritingTask, key: str) -> bool:
    k = (key or "").strip()
    if not k or task.outline is None:
        return False
    return any(str(n.key or "") == k for n in (task.outline.chapters or []))


def normalize_chapter_key_tokens(task: WritingTask | None, tokens: list[str] | None) -> list[str]:
    """把大纲 key 或章节序号统一成 outline key（去重、保序）。

    模型常把 ``1.2`` / ``2.1`` 塞进 chapter_keys；若不解析，开写会误判「没有已分析的」。
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(tokens or []):
        token = str(raw or "").strip()
        if not token:
            continue
        key = ""
        if task is not None and _chapter_key_on_task(task, token):
            key = token
        else:
            key = resolve_chapter_key_by_sequence(task, token)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def pick_focus_chapter_key_extended(
    session: ChatSession,
    task: WritingTask | None,
    message: str,
    *,
    allow_outline_default: bool = True,
) -> str:
    """消息序号 / 待确认章 / 上轮焦点 / 前端章节编辑 / 要素待确认 / 首个可写章。

    allow_outline_default=False：不加「落到大纲第一章」的回退（加要素等需明确章节时用）。
    """
    if task is None:
        return ""
    key = resolve_chapter_key_from_message(task, message)
    if key:
        return key
    # 原话已点名 2.2 等序号却对不上：禁止回落到 1.5.6 这类上一焦点
    if message_has_chapter_sequence(message):
        return ""
    awaiting = pick_awaiting_confirm_chapter_key(task)
    if awaiting:
        return awaiting
    ff = dict(session.freeform or {})
    snap = dict(ff.get("last_turn_snapshot") or {})
    wt = dict(snap.get("writing_task") or {})
    prev = str(wt.get("focus_chapter_key") or "").strip()
    if prev and _chapter_key_on_task(task, prev):
        return prev
    from report_writing.chat.element_configure_pending import list_configure_pending

    pending = list_configure_pending(session)
    if pending:
        pk = str(pending[-1].get("chapter_key") or "").strip()
        if pk and _chapter_key_on_task(task, pk):
            return pk
    edits = ff.get("chapter_edits")
    if isinstance(edits, dict):
        touched = [
            str(k).strip()
            for k, v in edits.items()
            if isinstance(v, dict) and v.get("touched") and _chapter_key_on_task(task, str(k))
        ]
        if len(touched) == 1:
            return touched[0]
    if not allow_outline_default:
        return ""
    if task.outline and task.outline.chapters:
        for node in task.outline.chapters:
            if not bool(getattr(node, "structure_only", False)):
                return str(node.key or "")
        return str(task.outline.chapters[0].key or "")
    return ""


def writing_phase_allows_chapter_tools(session: ChatSession) -> bool:
    phase = str(session.phase or "")
    if not (session.task_id or "").strip():
        return False
    return phase in {
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
        ChatPhase.READY_TO_WRITE.value,
    }
