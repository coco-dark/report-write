"""口语「帮我写 2.3.1 / 写第3章」→ 选章开写，勿误判为蓝本学习。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatCard, ChatSession, WritingTask

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

# 2.3.1 / 第2.3.1节 / 2.3章（结构序号，非意图）
_DOTTED_SEQ_RE = re.compile(
    r"(?:第\s*)?(\d+(?:\.\d+)+)\s*(?:章节|章|节)?",
    re.I,
)
# 第3章 / 第三章（顶层）
_TOP_SEQ_RE = re.compile(
    r"第\s*(\d+|[一二三四五六七八九十百]+)\s*章(?:节)?",
    re.I,
)
_CN_NUM: dict[str, int] = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _cn_to_num(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    if t.isdigit():
        return t
    if len(t) == 1 and t in _CN_NUM:
        return str(_CN_NUM[t])
    if t.startswith("十") and len(t) == 2 and t[1] in _CN_NUM:
        return str(10 + _CN_NUM[t[1]])
    if t.endswith("十") and len(t) == 2 and t[0] in _CN_NUM:
        return str(_CN_NUM[t[0]] * 10)
    if "十" in t and len(t) == 3 and t[1] == "十" and t[0] in _CN_NUM and t[2] in _CN_NUM:
        return str(_CN_NUM[t[0]] * 10 + _CN_NUM[t[2]])
    return ""


def parse_write_sequences_from_message(message: str) -> list[str]:
    text = (message or "").strip()
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _DOTTED_SEQ_RE.finditer(text):
        seq = str(m.group(1) or "").strip()
        if seq and seq not in seen:
            seen.add(seq)
            out.append(seq)
    for m in _TOP_SEQ_RE.finditer(text):
        seq = _cn_to_num(str(m.group(1) or ""))
        if seq and seq not in seen:
            # 若已有更细序号（2.3.1）则不再加顶层「2」以免整棵误选
            if any(s == seq or s.startswith(seq + ".") for s in out):
                continue
            seen.add(seq)
            out.append(seq)
    return out


WRITE_SCOPES = frozenset({"self", "whole", "children"})


def write_sequences_have_descendants(task: WritingTask | None, sequences: list[str]) -> bool:
    if task is None or not getattr(task, "outline", None):
        return False
    want = [str(s).strip() for s in sequences if str(s).strip()]
    if not want:
        return False
    for node in list(getattr(task.outline, "chapters", None) or []):
        if bool(getattr(node, "structure_only", False)):
            continue
        ns = str(getattr(node, "sequence", "") or "").strip()
        if any(ns.startswith(w + ".") for w in want):
            return True
    return False


def keys_for_write_scope(
    task: WritingTask | None,
    sequences: list[str],
    *,
    scope: str = "self",
) -> list[str]:
    """写作范围：self=该序号本节点；whole=本节点+下属；children=只要下属。"""
    if task is None or not getattr(task, "outline", None):
        return []
    want = [str(s).strip() for s in sequences if str(s).strip()]
    if not want:
        return []
    mode = str(scope or "self").strip()
    if mode not in WRITE_SCOPES:
        mode = "self"
    keys: list[str] = []
    seen: set[str] = set()
    for node in list(getattr(task.outline, "chapters", None) or []):
        if bool(getattr(node, "structure_only", False)):
            continue
        ns = str(getattr(node, "sequence", "") or "").strip()
        key = str(getattr(node, "key", "") or "").strip()
        if not ns or not key or key in seen:
            continue
        hit = False
        for w in want:
            is_self = ns == w
            is_desc = ns.startswith(w + ".")
            if mode == "self" and is_self:
                hit = True
            elif mode == "whole" and (is_self or is_desc):
                hit = True
            elif mode == "children" and is_desc:
                hit = True
            if hit:
                break
        if hit:
            seen.add(key)
            keys.append(key)
    if keys:
        return keys
    # 只要本章但大纲没有该节点：退回第一个下属，避免点了 2.3 却空手
    if mode == "self":
        return keys_for_write_scope(task, sequences, scope="children")[:1]
    return []


def _chapter_key_on_task(task: WritingTask | None, key: str) -> bool:
    k = (key or "").strip()
    if not k or task is None or not getattr(task, "outline", None):
        return False
    return any(str(getattr(n, "key", "") or "") == k for n in (task.outline.chapters or []))


def resolve_write_chapter_keys(
    task: WritingTask | None,
    message: str,
    *,
    llm_sequences: list[str] | None = None,
    chapter_key: str = "",
    chapter_key_hint: str = "",
    chapter_keys: list[str] | None = None,
    scope: str = "",
) -> list[str]:
    """按 slots / 序号 / 配置同路解析大纲 key。scope 由大脑给出。"""
    from report_writing.chat.chapter_resolve import (
        resolve_chapter_key_by_sequence,
        resolve_chapter_key_from_message,
    )

    mode = str(scope or "").strip() or "self"
    explicit: list[str] = []
    for k in list(chapter_keys or []):
        kk = str(k or "").strip()
        if kk and _chapter_key_on_task(task, kk) and kk not in explicit:
            explicit.append(kk)
    raw_key = str(chapter_key or "").strip()
    if raw_key and _chapter_key_on_task(task, raw_key) and raw_key not in explicit:
        explicit.append(raw_key)
    if explicit:
        return explicit

    seqs = [str(s).strip() for s in (llm_sequences or []) if str(s).strip()]
    hint = str(chapter_key_hint or "").strip()
    if hint and hint not in seqs:
        # hint 可能是序号或已是 key
        if _chapter_key_on_task(task, hint):
            return [hint]
        by_seq = resolve_chapter_key_by_sequence(task, hint)
        if by_seq:
            return keys_for_write_scope(task, [hint], scope=mode) or [by_seq]
        seqs = [hint, *seqs]

    if not seqs:
        seqs = parse_write_sequences_from_message(message)
    if seqs:
        keys = keys_for_write_scope(task, seqs, scope=mode)
        if keys:
            return keys

    # 与 configure/associate 同路：study 序号解析 + 子孙回落（含 structure_only 父节点）
    from_msg = resolve_chapter_key_from_message(task, message)
    if from_msg:
        return [from_msg]
    return []


def _load_write_outline_task(home: Path, session: ChatSession) -> tuple[str, WritingTask | None]:
    """优先焦点写作副本，再会话蓝本 / 向导 id。"""
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.session_documents import resolve_task_id

    tid = str(resolve_task_id(session, {}) or "").strip()
    bid = str(session.blueprint_id or "").strip()
    if not bid:
        ff = dict(session.freeform or {})
        wiz = dict(ff.get("blueprint_wizard") or {})
        bid = str(wiz.get("blueprint_id") or "").strip()
    # 写作大纲：w* 焦点优先；库蓝本 id 作绑定身份
    load_id = tid or bid
    if not load_id:
        return "", None
    task = _load_task(home, load_id)
    if task is None and bid and bid != load_id:
        task = _load_task(home, bid)
        load_id = bid if task is not None else load_id
    return (bid or load_id), task


def remember_last_study_chapter_keys(session: ChatSession, keys: list[str] | None) -> None:
    """会话备份最近分析批，防切换副本后 task.study_chapter_keys 对不上。"""
    clean = [str(k).strip() for k in list(keys or []) if str(k).strip()]
    if not clean:
        return
    session.freeform = {**(session.freeform or {}), "last_study_chapter_keys": clean}


def resolve_recent_studied_write_keys(
    task: WritingTask | None,
    session: ChatSession | None = None,
) -> list[str]:
    """最近分析批 ∩ 已分析章；优先 task.study_chapter_keys，再 pending_study，再会话备份。"""
    from report_writing.chat.gates import partition_blueprint_chapters

    if task is None:
        return []
    analyzed = {n.key for n in partition_blueprint_chapters(task).get("analyzed") or []}
    if not analyzed:
        return []

    def _take(raw: list[str] | None) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in list(raw or []):
            key = str(item or "").strip()
            if key and key in analyzed and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    hit = _take(list(getattr(task, "study_chapter_keys", None) or []))
    if hit:
        return hit
    pending_keys: list[str] = []
    for row in list(getattr(task, "pending_study", None) or []):
        if isinstance(row, dict):
            pending_keys.append(str(row.get("chapter_key") or "").strip())
        else:
            pending_keys.append(str(getattr(row, "chapter_key", "") or "").strip())
    hit = _take(pending_keys)
    if hit:
        return hit
    if session is not None:
        return _take(list((session.freeform or {}).get("last_study_chapter_keys") or []))
    return []


def _last_study_brief(task: WritingTask | None, keys: list[str]) -> str:
    if task is None or not keys:
        return ""
    labels: list[str] = []
    by_key = {
        str(getattr(n, "key", "") or ""): n
        for n in list(getattr(getattr(task, "outline", None), "chapters", None) or [])
    }
    for key in keys[:6]:
        node = by_key.get(key)
        if node is None:
            labels.append(key)
            continue
        labels.append(
            f"{getattr(node, 'sequence', '')} {getattr(node, 'title', '')}".strip() or key
        )
    return "、".join(labels)


def utterance_requests_study(message: str) -> bool:
    """用户这句是在要分析/重学要点，不是打招呼或开写。"""
    text = (message or "").strip()
    if not text:
        return False
    return any(
        tok in text
        for tok in (
            "分析",
            "学习",
            "重学",
            "再学",
            "生成要点",
            "写作要点",
            "依据样本",
            "根据原文",
            "自动分析",
        )
    )


def message_asks_to_write_not_study(message: str) -> bool:
    """用户在要写正文，而不是分析要点。不含「分析/学习」时的「写/开写」。"""
    text = (message or "").strip()
    if not text:
        return False
    if utterance_requests_study(text):
        return False
    return any(tok in text for tok in ("写", "开写", "撰稿"))


def reroute_if_write_not_study(
    home: Path,
    session: ChatSession,
    message: str,
    *,
    emit: EmitFn | None = None,
    invoke: InvokeFn | None = None,
) -> tuple[str, list[ChatCard], list[dict[str, Any]]] | None:
    """分析类工具被误调时改走开写；对不上任务则 None。"""
    if not message_asks_to_write_not_study(message):
        return None
    return try_write_chapters_from_message(
        home, session, message, emit=emit, invoke=invoke
    )


def try_write_chapters_from_message(
    home: Path,
    session: ChatSession,
    message: str,
    *,
    emit: EmitFn | None = None,
    invoke: InvokeFn | None = None,
    write_scope: str = "",
    chapter_sequences: list[str] | None = None,
    chapter_key: str = "",
    chapter_key_hint: str = "",
    chapter_keys: list[str] | None = None,
    write_analyzed_only: bool = False,
) -> tuple[str, list[ChatCard], list[dict[str, Any]]] | None:
    """已由意图模型选中开写工具后执行；序号仅作结构解析。"""
    text = (message or "").strip()

    from report_writing.chat.actions import _action_select_write_chapters
    from report_writing.chat.cards import _offer_chapter_picker_cards
    from report_writing.chat.gates import partition_blueprint_chapters
    from report_writing.chat.helpers import _emit_process

    bid, task = _load_write_outline_task(home, session)
    if not bid or task is None:
        return None

    _emit_process(emit, "正在匹配章节…")
    session.blueprint_id = bid
    parts = partition_blueprint_chapters(task)
    analyzed_keys = {n.key for n in parts.get("analyzed") or []}

    seqs = [str(s).strip() for s in (chapter_sequences or []) if str(s).strip()]
    hint = str(chapter_key_hint or "").strip()
    raw_key = str(chapter_key or "").strip()
    explicit_keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    if not seqs:
        seqs = parse_write_sequences_from_message(text)

    # 无显式章时：LLM 判开写目标（最近分析批 / 进度 / 序号），不用固定句式表
    if not seqs and not hint and not raw_key and not explicit_keys and not write_analyzed_only:
        recent_keys = resolve_recent_studied_write_keys(task, session)
        judged: dict[str, Any] = {}
        if invoke is not None and text:
            from report_writing.chat.classifier_context import pack_classifier_context
            from report_writing.chat.clarify_llm import classify_write_target_llm

            ctx = pack_classifier_context(session)
            judged = classify_write_target_llm(
                text,
                dialogue_tail=list(ctx.get("dialogue_tail") or []),
                focus_document_brief=str(ctx.get("focus_document_brief") or ""),
                last_study_brief=_last_study_brief(task, recent_keys),
                invoke=invoke,
            )
        target = str(judged.get("target") or "").strip()
        # 闲聊 / 问能力：不挂写作进度卡，交回总控口头答
        if target == "none" or (invoke is not None and text and not judged):
            return None
        if target == "status":
            # 查进度不走选章开写；交回总控按 status_facts 说话，禁止套话
            return None
        if target == "recent_study":
            if recent_keys:
                _emit_process(emit, "正在按刚才分析的章节准备开写…")
                remember_last_study_chapter_keys(session, recent_keys)
                try:
                    reply, cards = _action_select_write_chapters(
                        home,
                        session,
                        {"blueprint_id": bid, "chapter_keys": recent_keys},
                        emit=emit,
                        invoke=invoke,
                    )
                except ValueError as error:
                    return str(error), [], []
                return reply, list(cards or []), []
            cards = _offer_chapter_picker_cards(session, task)
            return (
                "刚才那批分析章还没法开写（可能还没落盘或未完成分析）。"
                "请在右侧文档窗口确认已分析章节后再开写，或直接点名章节号。",
                cards,
                [],
            )
        if target == "all_analyzed":
            write_analyzed_only = True
        elif target == "sequences":
            seqs = [
                str(s).strip()
                for s in list(judged.get("chapter_sequences") or [])
                if str(s).strip()
            ]

    # 分类器选 write_analyzed_only 且无具体章 → 仅已分析章选章卡
    if write_analyzed_only and not seqs and not hint and not raw_key and not explicit_keys:
        if not parse_write_sequences_from_message(text):
            from report_writing.chat.actions import _action_write_analyzed_only

            try:
                reply, cards = _action_write_analyzed_only(
                    home, session, {"blueprint_id": bid}, emit=emit, invoke=invoke
                )
            except ValueError as error:
                return str(error), [], []
            return reply, list(cards or []), []
    scope = str(write_scope or "").strip()
    probe_seqs = seqs or ([hint] if hint else []) or parse_write_sequences_from_message(text)
    if probe_seqs and write_sequences_have_descendants(task, probe_seqs) and scope not in WRITE_SCOPES:
        if invoke is not None:
            from report_writing.chat.classifier_context import pack_classifier_context
            from report_writing.chat.clarify_llm import classify_write_scope_llm

            ctx = pack_classifier_context(session)
            judged = classify_write_scope_llm(
                text,
                dialogue_tail=list(ctx.get("dialogue_tail") or []),
                focus_document_brief=str(ctx.get("focus_document_brief") or ""),
                invoke=invoke,
            )
            if judged in WRITE_SCOPES:
                scope = judged
        if scope not in WRITE_SCOPES:
            from report_writing.chat.clarify_flow import build_write_scope_clarify

            reply, cards = build_write_scope_clarify(
                session, sequences=probe_seqs, write_message=text
            )
            return reply, list(cards or []), []
    keys = resolve_write_chapter_keys(
        task,
        text,
        llm_sequences=seqs,
        chapter_key=raw_key,
        chapter_key_hint=hint,
        chapter_keys=explicit_keys,
        scope=scope,
    )
    if keys:
        _emit_process(emit, "正在按你说的章节准备开写…")
        ready = [k for k in keys if k in analyzed_keys]
        if ready:
            try:
                reply, cards = _action_select_write_chapters(
                    home,
                    session,
                    {"blueprint_id": bid, "chapter_keys": ready},
                    emit=emit,
                    invoke=invoke,
                )
            except ValueError as error:
                return str(error), [], []
            return reply, list(cards or []), []
        # 认出了章但还没分析：引导去分析，而不是误报「没认出要学的章节」
        labels = []
        for node in list(getattr(task.outline, "chapters", None) or []):
            if str(getattr(node, "key", "") or "") in keys:
                labels.append(
                    f"{getattr(node, 'sequence', '')} {getattr(node, 'title', '')}".strip()
                )
        tip = "、".join(labels[:3]) or "你点的章节"
        cards = _offer_chapter_picker_cards(session, task)
        return (
            f"「{tip}」还没完成蓝本分析，暂时开不了写。"
            "请在右侧文档窗口分析这一章，或选已分析章节开写。",
            cards,
            [],
        )

    # 对不上章：挂多轮澄清，禁止只丢一句硬提示就断线
    from report_writing.chat.action_slots import build_action_slot_clarify

    reply, cards = build_action_slot_clarify(
        session,
        "select_write_chapters",
        {"blueprint_id": bid, "task_id": bid},
        ["chapter_sequences"],
        task=task,
        message=text,
    )
    picker = _offer_chapter_picker_cards(session, task)
    return reply, list(cards or []) + list(picker or []), []
