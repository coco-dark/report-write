"""选择卡状态机：槽位不清时挂选项，用户点选后再执行。

不是口语「意图分类」。点选结果走 `tool_dispatch.execute_dispatch_tool`；未挂卡的口语交给 Agent。
卡片 `type` 与 session pending 键仍为 `intent_clarify`（产品契约）。
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

from report_writing.domain.models import (
    ChatActionPayload,
    ChatCard,
    ChatPhase,
    ChatSession,
    ClarifyPending,
)

FREEFORM_KEY = "intent_clarify"

_OPTION_ALIASES: dict[str, tuple[str, ...]] = {
    "self": ("只要本章", "仅本章", "就够了", "不要子", "不要下属", "只学这章", "只分析这章", "只写这章"),
    "whole": ("整章", "含下属", "整个", "整棵", "全部下属", "整章含", "一起写"),
    "children": ("只要子章节", "只要子", "下面的子", "只要下面"),
    "confirm_only": ("先确认", "只确认", "确认目录", "先定目录"),
    "confirm_and_study": ("确认并分析", "确认并学习", "确认完分析", "确认完学习", "继续分析", "继续学习", "确认并开始"),
    "match_blueprint": ("用已有", "匹配蓝本", "选蓝本", "已有蓝本"),
    "create_or_freeform": ("创建蓝本", "新建蓝本", "自建蓝本", "依据样本", "上传样板", "自己写依据样本"),
    "freeform": ("不用蓝本", "跳过蓝本", "无蓝本", "自己定大纲", "直接搭目录", "不定蓝本", "自动大纲", "自己写自动大纲"),
    # 定名确认语不靠短语表；有模型时走 classify_doc_name_reply_llm
    "use_suggested_name": ("就叫", "用这个", "这个名字", "就这个"),
    "type_new_name": ("换个名字", "我自己起", "重新起名"),
    "approve": ("通过", "确认本章", "approve", "没问题"),
    "revise": ("重写", "退回", "revise", "再写"),
    "skip": ("跳过", "先跳过", "skip"),
    "generate_outline": ("起草目录", "生成目录", "帮我起草"),
    "confirm_outline": ("确认目录", "目录没问题", "就这个目录"),
    "generate_points": (
        "生成要点",
        "帮我生成要点",
        "配要点",
        "生成写作信息",
        "分析本章",
        "学习本章",
        "依据样本分析",
        "依据样本学习",
        "帮我分析",
        "帮我学",
    ),
    "skip_points": ("跳过要点", "先不配要点", "不要要点"),
    "start_writing": ("开始写作", "开写", "开始写"),
    "pace_batch": ("一口气", "连着写", "批量", "一次写完", "一口气写", "连续写"),
    "pace_step": ("一章一章", "逐章", "写完再确认", "一章确认", "逐步", "一章一章来"),
    "scope_force_new": ("新开一份", "另开一份", "再开一份", "新开一个", "另外写一份"),
    "scope_rewrite_current": (
        "清空重写当前",
        "清空重写",
        "整份重写",
        "重写当前这份",
        "重写当前",
        "全部重写当前",
    ),
    "scope_rename": ("改当前文档名", "改成这个名字", "改名叫", "改名"),
    "scope_continue": ("继续当前这份", "继续写当前", "继续当前", "当写作题材"),
    "source": ("根据原文", "原文内容", "粘贴原文", "粘贴文本", "按原文", "富文本"),
    "sample": ("依据样本", "按样本", "用样本", "样本分析"),
    "auto": ("自动分析", "自动生成", "没有样本", "生成要点"),
}


def get_pending(session: ChatSession) -> ClarifyPending | None:
    raw = (session.freeform or {}).get(FREEFORM_KEY)
    return ClarifyPending.parse(raw)


def clear_pending(session: ChatSession) -> None:
    ff = dict(session.freeform or {})
    if FREEFORM_KEY in ff:
        ff.pop(FREEFORM_KEY, None)
        session.freeform = ff


def _cards_hang_intent_clarify(cards: list | None) -> bool:
    return any(str(getattr(c, "type", "") or "") == "intent_clarify" for c in (cards or []))


def _clear_stale_doc_name_pending_if_safe(
    session: ChatSession, cards: list | None = None
) -> None:
    """已定名后清掉起名 pending；若本轮卡片仍是澄清卡则不动（否则点选会报过期）。"""
    from report_writing.chat.session_documents import is_writing_document_named

    if not is_writing_document_named(session):
        return
    if _cards_hang_intent_clarify(cards):
        return
    pending = get_pending(session)
    if pending and str(pending.get("source") or "") == "doc_name":
        clear_pending(session)


def restore_pending_from_last_clarify(session: ChatSession) -> ClarifyPending | None:
    """pending 被误清时，用 last_intent_clarify 快照恢复，避免点选直接报过期。"""
    if get_pending(session):
        return get_pending(session)
    last = dict((session.freeform or {}).get("last_intent_clarify") or {})
    opts = list(last.get("options") or [])
    if not opts:
        return None
    pending = {
        "id": f"ic_{secrets.token_hex(4)}",
        "source": str(last.get("source") or "control").strip() or "control",
        "question": str(last.get("question") or "").strip()
        or "我没太把握准，你帮我选一下？",
        "options": opts,
        "candidate": dict(last.get("candidate") or {}),
        "slots": {},
        "phase_hint": str(last.get("phase_hint") or "").strip(),
        "ask_count": 0,
    }
    session.freeform = {**(session.freeform or {}), FREEFORM_KEY: pending}
    return ClarifyPending.parse(pending)


def message_skips_clarify(
    message: str,
    *,
    source: str = "",
    invoke=None,
    session: ChatSession | None = None,
) -> bool:
    """「类型｜标题」元信息行可跳过入口澄清。有 invoke 走分类器；否则分隔符结构兜底。"""
    if source not in {"entry", "control", ""}:
        return False
    text = (message or "").strip()
    if not text:
        return False
    if invoke is not None:
        try:
            from report_writing.chat.intent_context import pack_classifier_context
            from report_writing.chat.clarify_llm import classify_doc_meta_line_llm

            ctx = pack_classifier_context(session) if session is not None else {}
            hit = classify_doc_meta_line_llm(
                text,
                dialogue_tail=list(ctx.get("dialogue_tail") or []),
                focus_document_brief=str(ctx.get("focus_document_brief") or ""),
                invoke=invoke,
            ) or {}
            if hit.get("doc_title") or hit.get("doc_category"):
                return True
        except Exception:
            pass
    from report_writing.chat.session_snapshot import looks_like_freeform_meta_line

    return looks_like_freeform_meta_line(text)


def resolve_clear_entry_tool(
    message: str,
    *,
    invoke=None,
    session: ChatSession | None = None,
) -> str | None:
    """元信息行 → skip_blueprint；其它入口意图交给分类器。"""
    text = (message or "").strip()
    if not text:
        return None
    if message_skips_clarify(text, source="entry", invoke=invoke, session=session):
        return "skip_blueprint"
    return None


def begin_clarify(
    session: ChatSession,
    *,
    source: str,
    question: str,
    options: list[dict[str, Any]],
    candidate: dict[str, Any],
    phase_hint: str = "",
) -> tuple[str, list[ChatCard]]:
    """挂起澄清并返回追问回复 + 选项卡片。"""
    clean_opts: list[dict[str, Any]] = []
    for item in options or []:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not oid or not label:
            continue
        clean_opts.append(
            {
                "id": oid,
                "label": label,
                "slots": dict(item.get("slots") or {}),
            }
        )
    pending = ClarifyPending(
        id=f"ic_{secrets.token_hex(4)}",
        source=(source or "control").strip() or "control",
        question=(question or "").strip() or "我没太把握准，你帮我选一下？",
        options=clean_opts,
        candidate=dict(candidate or {}),
        slots={},
        phase_hint=(phase_hint or "").strip(),
        ask_count=0,
    )
    stored = pending.to_dict()
    # 快照供过期后重新挂起（点选旧 clarify_id 时）
    session.freeform = {
        **(session.freeform or {}),
        FREEFORM_KEY: stored,
        "last_intent_clarify": {
            "source": pending.source,
            "question": pending.question,
            "options": clean_opts,
            "candidate": dict(candidate or {}),
            "phase_hint": pending.phase_hint,
        },
    }
    card = ChatCard(
        type="intent_clarify",
        title="请帮我确认一下",
        payload={
            "clarify_id": pending.id,
            "question": pending.question,
            "options": clean_opts,
            "source": pending.source,
        },
    )
    return pending.question, [card]


def rehang_last_clarify(session: ChatSession) -> tuple[str, list[ChatCard]] | None:
    """澄清过期时用上一轮快照再挂一轮；无快照则 None。"""
    last = dict((session.freeform or {}).get("last_intent_clarify") or {})
    opts = list(last.get("options") or [])
    if not opts:
        return None
    question = str(last.get("question") or "").strip() or "刚才那轮确认过期了，请再选一次。"
    return begin_clarify(
        session,
        source=str(last.get("source") or "control"),
        question=f"刚才那轮确认过期了。{question}",
        options=opts,
        candidate=dict(last.get("candidate") or {}),
        phase_hint=str(last.get("phase_hint") or ""),
    )


def build_writing_pace_clarify(
    session: ChatSession,
    *,
    message: str = "",
    chapter_keys: list[str] | None = None,
    task_id: str = "",
) -> tuple[str, list[ChatCard]]:
    """多章开写前：一次写完 vs 写一章看一章（单章由调用方跳过）。"""
    from report_writing.chat.helpers import WRITING_PACE_BATCH, WRITING_PACE_STEP

    keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    tid = str(task_id or session.task_id or "").strip()
    if keys:
        session.freeform = {**(session.freeform or {}), "write_chapter_keys": keys}
    n = len(keys)
    base_args: dict[str, Any] = {"chapter_keys": keys, "task_id": tid}
    if session.blueprint_id:
        base_args["blueprint_id"] = session.blueprint_id
    question = (
        f"这次要写 {n} 章。你更习惯哪种节奏？\n"
        "· 一次写完再统一看：按顺序连着写，全部跑完后再到文档窗逐章确认。\n"
        "· 写一章看一章：每章写完先停一下，你确认或改完我再写下一章。"
    )
    reply = (
        f"收到，就按你勾选的这 {n} 章来写。"
        "接下来定一下节奏就行：想**一次写完再统一看**，还是**写一章看一章**？点下面选项我就开写。"
    )
    session.phase = ChatPhase.READY_TO_WRITE.value
    return begin_clarify(
        session,
        source="control",
        question=question,
        options=[
            {
                "id": "pace_batch",
                "label": "一次写完再统一看",
                "slots": {
                    "tool": "start_writing",
                    "args": {**base_args, "writing_pace": WRITING_PACE_BATCH},
                },
            },
            {
                "id": "pace_step",
                "label": "写一章看一章",
                "slots": {
                    "tool": "start_writing",
                    "args": {**base_args, "writing_pace": WRITING_PACE_STEP},
                },
            },
        ],
        candidate={"kind": "control_tool", "name": "start_writing", "payload": dict(base_args)},
        phase_hint="writing_pace",
    )


def _match_option_exact(message: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """仅 UI 精确点选：option id 或完整 label（不含别名/子串）。"""
    text = (message or "").strip()
    if not text:
        return None
    lowered = text.lower()
    for opt in options:
        oid = str(opt.get("id") or "").strip()
        label = str(opt.get("label") or "").strip()
        if text == oid or text == label:
            return opt
        if oid and lowered == oid.lower():
            return opt
    return None


def _match_option(message: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = (message or "").strip()
    if not text:
        return None
    exact = _match_option_exact(text, options)
    if exact is not None:
        return exact
    # 别名：离线/复述选项用；定名确认语不在此表，口语确认走 LLM
    for opt in options:
        oid = str(opt.get("id") or "").strip()
        label = str(opt.get("label") or "").strip()
        if label and label in text and text != label:
            return opt
        aliases = _OPTION_ALIASES.get(oid) or ()
        if not any(a and a in text for a in aliases):
            continue
        slots = dict(opt.get("slots") or {})
        tool = str(slots.get("tool") or "").strip()
        entry = str(slots.get("entry_path") or "").strip()
        if oid == "freeform" and tool in {"generate_freeform_points", "submit_outline"}:
            # id 叫 freeform 但实际是要点/贴目录，不能被「不用蓝本」别名点中
            continue
        if oid == "freeform" and entry and entry not in {"freeform", "skip_blueprint"}:
            continue
        return opt
    return None


def looks_like_new_intent(message: str) -> bool:
    """无 LLM 时的换话题兜底。不含「创建蓝本」等澄清选项说法。"""
    text = (message or "").strip()
    if not text:
        return False
    if re.search(r"导出\s*Word|换个题目|不用了|算了吧|取消这次", text):
        return True
    if len(text) > 40 and not re.search(r"整章|子章|通过|重写|跳过|蓝本|确认|学习|分析|写", text):
        return True
    return False


def message_is_vacuous_clarify_reply(message: str) -> bool:
    """离线兜底：含糊应付才允许澄清默认；口语确认意图有模型时走 LLM。"""
    t = re.sub(r"[\s。．！!？?，,、~…]+", "", (message or "").strip())
    t = re.sub(r"[吧呀啊呢哦哈]+$", "", t)
    if not t or len(t) > 16:
        return False
    return bool(
        re.fullmatch(
            r"(嗯+|好的?|行|随便|都行|都可以|你定|看着办|随你|ok|OK)",
            t,
            re.I,
        )
    )


_DOC_NAME_CONFIRM_RE = re.compile(
    r"^(是的?|好的?|可以|行|确认|就这个|就这样|用这个|ok|yes)$",
    re.I,
)
# 长的放前面，避免「用蓝本」先吃掉「用蓝本库的」「不用蓝本」
_DOC_NAME_PATH_MARKERS = (
    "自动生成章节目录",
    "生成章节目录",
    "自己生成目录",
    "自动生成目录",
    "自动生成大纲",
    "自己定大纲",
    "自己写目录",
    "生成目录",
    "生成大纲",
    "匹配已有蓝本",
    "匹配蓝本库",
    "匹配蓝本",
    "用蓝本库的",
    "用蓝本库",
    "不用蓝本",
    "用蓝本",
)


def message_confirms_suggested_doc_name(message: str) -> bool:
    """正在回答「文档叫什么」时，口头确认建议名。"""
    t = re.sub(r"[\s。．！!？?，,、~…]+", "", (message or "").strip())
    t = re.sub(r"[吧呀啊呢哦哈]+$", "", t)
    if not t or len(t) > 8:
        return False
    return bool(_DOC_NAME_CONFIRM_RE.fullmatch(t))


def _path_tail_start(text: str) -> int:
    """路径尾巴起点。同一位置取更长的标记，避免「用蓝本」截进「不用蓝本」。"""
    best: tuple[int, int] | None = None
    for marker in _DOC_NAME_PATH_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        score = (idx, -len(marker))
        if best is None or score < best:
            best = score
    return best[0] if best else -1


def doc_name_path_from_residual(residual: str) -> str:
    """定名后的路径尾巴：匹配蓝本 / 不用蓝本自己生成目录。"""
    compact = (residual or "").replace(" ", "")
    if not compact:
        return ""
    if any(
        k in compact
        for k in (
            "不用蓝本",
            "自己生成目录",
            "自己定大纲",
            "自己写目录",
            "自动生成章节目录",
            "生成章节目录",
            "自动生成目录",
            "生成目录",
            "生成大纲",
            "自动大纲",
        )
    ):
        return "freeform"
    if any(k in compact for k in ("用蓝本库", "用蓝本", "匹配蓝本", "蓝本库")):
        return "match"
    return ""


def split_doc_name_answer(message: str) -> tuple[str, str] | None:
    """只在回答「文档叫什么」时：拆出具体文档名和路径尾巴。

    「2026年工程安全报告 用蓝本库的」→ 名字 + 匹配蓝本。
    「新写一份述职报告」是题材，不是文档名，返回 None。
    「是的 / 好」留给建议名确认，不当成标题。
    """
    from report_writing.chat.helpers import is_vague_freeform_meta
    from report_writing.chat.session_documents import (
        clean_document_display_title,
        explicit_doc_title_from_message,
        quick_title_from_write_message,
        strip_write_subject_tail,
        title_contains_action_clause,
        title_is_write_subject_not_doc_name,
    )

    text = (message or "").strip()
    if not text or message_confirms_suggested_doc_name(text):
        return None
    explicit = explicit_doc_title_from_message(text)
    tail_at = _path_tail_start(text)
    residual = ""
    if tail_at >= 0:
        residual = text[tail_at:].strip(" ，,。；;、")
    if explicit:
        title = explicit
    elif tail_at > 0:
        title = text[:tail_at].strip(" ，,。；;、")
    elif tail_at == 0:
        return None
    else:
        title = quick_title_from_write_message(text) or text
    title = strip_write_subject_tail(clean_document_display_title(title))
    if not title or is_vague_freeform_meta(title, field=True):
        return None
    if title_contains_action_clause(title):
        return None
    if message_confirms_suggested_doc_name(title):
        return None
    # 已拆出路径/动作尾巴时，前缀就是文档名，不要再当「写一份」题材丢掉
    if not residual and title_is_write_subject_not_doc_name(text, title):
        return None
    return title, residual


def _doc_name_suggested_tip(pending: dict[str, Any]) -> str:
    tip = str(((pending.get("candidate") or {}).get("payload") or {}).get("title") or "").strip()
    if tip:
        return tip
    for opt in list(pending.get("options") or []):
        if str(opt.get("id") or "") == "use_suggested_name":
            tip = str((opt.get("slots") or {}).get("title") or "").strip()
            if tip:
                return tip
    return ""


def _spec_apply_doc_title(
    pending: dict[str, Any],
    *,
    title: str,
    option_id: str = "typed_doc_name",
) -> dict[str, Any]:
    tip = (title or "").strip()[:160]
    return {
        "candidate": dict(pending.get("candidate") or {})
        or {"kind": "doc_name", "name": "confirm_document_name", "payload": {}},
        "slots": {
            "doc_name_action": "apply",
            "title": tip,
            "message": tip[:500],
        },
        "option_id": option_id,
        "defaulted": False,
        "note": "",
        "source": str(pending.get("source") or "doc_name"),
    }


def _doc_name_message_blocks_suggestion(message: str) -> bool:
    """题材句不能靠安全默认订成建议名。"""
    from report_writing.chat.session_documents import (
        quick_title_from_write_message,
        title_is_write_subject_not_doc_name,
    )

    text = (message or "").strip()
    if not text:
        return False
    if title_is_write_subject_not_doc_name(text, text):
        return True
    quick = quick_title_from_write_message(text)
    return bool(quick and title_is_write_subject_not_doc_name(text, quick))


def _accept_doc_name_pending_answer(
    session: ChatSession,
    pending: dict[str, Any],
    message: str,
    *,
    allow_plain: bool = False,
) -> dict[str, Any] | None:
    """文档名这一问：确认语用建议名；具体名字（可带路径尾巴）直接定名。

    allow_plain=False：只收「名字叫/就叫」或带路径尾巴的句子，避免把「导出 Word」订成文档名。
    模型判不清之后再 allow_plain，用用户刚打出的整句当名字。
    """
    from report_writing.chat.session_documents import explicit_doc_title_from_message

    text = (message or "").strip()
    if not text:
        return None
    if message_confirms_suggested_doc_name(text):
        tip = _doc_name_suggested_tip(pending)
        if not tip:
            return None
        clear_pending(session)
        opt = next(
            (
                o
                for o in list(pending.get("options") or [])
                if str(o.get("id") or "") == "use_suggested_name"
            ),
            None,
        )
        if isinstance(opt, dict):
            return _spec_from_option(pending, opt)
        return _spec_apply_doc_title(pending, title=tip, option_id="use_suggested_name")
    parsed = split_doc_name_answer(text)
    if not parsed:
        return None
    title, residual = parsed
    if (
        not allow_plain
        and not residual
        and not explicit_doc_title_from_message(text)
    ):
        return None
    clear_pending(session)
    return _attach_residual(
        _spec_apply_doc_title(pending, title=title, option_id="typed_doc_name"),
        residual,
    )


# 大脑生成的入口选项常只有 id/label，缺 tool / entry_path；点选后不能再走 ask_intent 空槽。
_ENTRY_PATH_BY_OPTION_ID: dict[str, str] = {
    "match_blueprint": "match_blueprint",
    "match_blueprints": "match_blueprint",
    "match": "match_blueprint",
    "existing": "match_blueprint",
    "library": "match_blueprint",
    "template": "create_blueprint",
    "create_blueprint": "create_blueprint",
    "create_or_freeform": "create_blueprint",
    "create": "create_blueprint",
    "new_blueprint": "create_blueprint",
    "new": "create_blueprint",
    "begin_blueprint_wizard": "create_blueprint",
    "freeform": "freeform",
    "skip_blueprint": "freeform",
    "skip": "freeform",
    "diy": "freeform",
    "no_blueprint": "freeform",
    "outline_only": "freeform",
}

_ENTRY_TOOL_BY_PATH: dict[str, str] = {
    "match_blueprint": "match_blueprints",
    "match_blueprints": "match_blueprints",
    "create_blueprint": "begin_blueprint_wizard",
    "create_or_freeform": "begin_blueprint_wizard",
    "freeform": "skip_blueprint",
    "skip_blueprint": "skip_blueprint",
}

_TOOL_ALIASES: dict[str, str] = {
    "find_blueprints": "match_blueprints",
    "start_blueprint_wizard": "begin_blueprint_wizard",
    "create_blueprint": "begin_blueprint_wizard",
    "freeform": "skip_blueprint",
}


def _infer_entry_path(option_id: str, label: str = "") -> str:
    """优先认 option_id / 模型填的槽；label 仅作极弱兜底（勿当主路由）。"""
    oid = (option_id or "").strip().lower().replace("-", "_")
    if oid in _ENTRY_PATH_BY_OPTION_ID:
        return _ENTRY_PATH_BY_OPTION_ID[oid]
    # 模型偶发把路径写在 id 上
    if oid in _ENTRY_TOOL_BY_PATH:
        return oid if oid in {"match_blueprint", "match_blueprints", "create_blueprint", "freeform", "skip_blueprint"} else ""
    return ""


def enrich_control_option_slots(
    option_id: str,
    label: str,
    slots: dict[str, Any] | None,
) -> dict[str, Any]:
    """补全入口澄清选项的 tool / entry_path，避免点选后掉进「再说一下想做什么」。"""
    out = dict(slots or {})
    tool = str(out.get("tool") or "").strip()
    if tool in _TOOL_ALIASES:
        tool = _TOOL_ALIASES[tool]
        out["tool"] = tool
    if tool and tool not in {"ask_intent", "ask_entry_path"}:
        return out
    path = str(out.get("entry_path") or "").strip()
    if not path:
        path = _infer_entry_path(option_id, label)
    if path:
        out["entry_path"] = path
        mapped = _ENTRY_TOOL_BY_PATH.get(path, "")
        if mapped:
            out["tool"] = mapped
    return out


def _spec_from_option(pending: dict[str, Any], opt: dict[str, Any]) -> dict[str, Any]:
    slots = {**(pending.get("slots") or {}), **dict(opt.get("slots") or {})}
    source = str(pending.get("source") or "")
    oid = str(opt.get("id") or "")
    label = str(opt.get("label") or "")
    if source in {"control", "entry", "entry_branch", "entry_freeform_mode"}:
        slots = enrich_control_option_slots(oid, label, slots)
    return {
        "candidate": dict(pending.get("candidate") or {}),
        "slots": slots,
        "option_id": oid,
        "defaulted": False,
        "note": "",
        "source": source,
    }


def resolve_clarify(
    session: ChatSession,
    message: str,
) -> dict[str, Any] | None:
    """若命中 pending 选项，返回可执行 spec；否则 None（调用方决定是否丢弃）。

    返回：
      {
        "candidate": {...},
        "slots": {...},
        "option_id": "...",
        "defaulted": bool,
        "note": "..."  # 安全默认时的口头说明
      }
    """
    pending = get_pending(session)
    if not pending:
        return None
    options = list(pending.get("options") or [])
    opt = _match_option(message, options)
    if opt is None:
        return None
    clear_pending(session)
    return _spec_from_option(pending, opt)


def apply_safe_default(session: ChatSession, *, message: str = "") -> dict[str, Any] | None:
    """一轮仍不清：按候选 source 给安全默认并清 pending。

    文档名这一问：用户刚打出的具体名字优先于建议名。
    建议名只在「是的 / 好 / 就这个」或含糊应付时使用。
    """
    pending = get_pending(session)
    if not pending:
        return None
    candidate = dict(pending.get("candidate") or {})
    source = str(pending.get("source") or "")
    slots: dict[str, Any] = dict(pending.get("slots") or {})
    note = "我先按比较稳妥的理解继续；要是不对你再说一声就行。"
    if source == "wizard" and str(candidate.get("name") or candidate.get("command") or "") in {
        "re_study_chapters",
        "go_study",
    }:
        slots.setdefault("study_scope", "whole")
        note = "我先按「整章（含下属）」来学；若只要子章节，跟我说「只要子章节」就行。"
    elif source == "write":
        slots.setdefault("write_scope", "self")
        note = "我先按只要本章来写；要含下属一起写直接说就行。"
    elif source == "writing":
        slots.setdefault("writing_decision", "approve")
        note = "我先按「通过本章」理解；要重写或跳过直接说就行。"
    elif source in {"entry", "control"}:
        if source == "control" and not slots.get("tool") and not slots.get("entry_path"):
            options = list(pending.get("options") or [])
            first = next((o for o in options if isinstance(o, dict)), None)
            if first:
                slots.update(dict(first.get("slots") or {}))
        slots.setdefault("entry_path", "match_blueprint")
        note = "我先按「看看已有蓝本」理解；要自建大纲可以说「自己定大纲」。"
    elif source == "freeform":
        from report_writing.domain.models import ChatPhase

        cand_payload = (pending.get("candidate") or {}).get("payload") or {}
        cand_phase = (
            str(cand_payload.get("phase") or "") if isinstance(cand_payload, dict) else ""
        )
        ph = cand_phase or str(pending.get("phase_hint") or "")
        if ph == ChatPhase.FREEFORM_POINTS.value:
            slots.setdefault("freeform_intent", "generate_points")
            note = "我先按「帮你生成要点」理解；要跳过可以说「先跳过要点」。"
        else:
            slots.setdefault("freeform_intent", "generate_outline")
            note = "我先按「帮你起草目录」理解；目录好了可以说「确认目录」。"
    elif source == "document_scope":
        # 禁止静默猜新开/改名/继续
        clear_pending(session)
        return {
            "candidate": {"kind": "document_scope", "name": "document_scope", "payload": {}},
            "slots": {},
            "option_id": "",
            "defaulted": True,
            "note": "还是没听准：是新开一份、改当前文档名，还是继续写当前这份？请点选或再说一次。",
            "source": source,
        }
    elif source == "action_slots":
        # 禁止静默默认章/要素：清 pending，不执行原 Tool
        clear_pending(session)
        return {
            "candidate": {"kind": "control_tool", "name": "", "payload": {}},
            "slots": {},
            "option_id": "",
            "defaulted": True,
            "note": "还是没对上要操作的章节或要素，请再说一次序号，或点选项。",
            "source": source,
        }
    elif source == "materials_name":
        clear_pending(session)
        return {
            "candidate": {"kind": "control_tool", "name": "", "payload": {}},
            "slots": {},
            "option_id": "",
            "defaulted": True,
            "note": "还没听清新名称，你再说一次想叫什么就行。",
            "source": source,
        }
    elif source == "doc_name":
        parsed = split_doc_name_answer(message) if message else None
        if parsed:
            title, residual = parsed
            clear_pending(session)
            return _attach_residual(
                _spec_apply_doc_title(pending, title=title, option_id="typed_doc_name"),
                residual,
            )
        accepts_tip = (not message) or message_confirms_suggested_doc_name(message) or (
            message_is_vacuous_clarify_reply(message)
        )
        if message and not accepts_tip:
            return None
        tip = str(
            slots.get("title")
            or (candidate.get("payload") or {}).get("title")
            or ""
        ).strip() or _doc_name_suggested_tip(pending)
        if tip:
            slots.setdefault("doc_name_action", "apply")
            slots.setdefault("title", tip)
            slots.setdefault("message", tip)
            note = f"我先按「{tip}」作为文档名；要改直接说新名字就行。"
        else:
            clear_pending(session)
            return {
                "candidate": {"kind": "control_tool", "name": "", "payload": {}},
                "slots": {},
                "option_id": "",
                "defaulted": True,
                "note": "还没听清文档名，你再说一次想叫什么就行。",
                "source": source,
            }
    elif source == "session_meta":
        slots.setdefault("meta_action", "keep")
        note = "我先保持现有文档名；要改名直接发「类型｜标题」就行。"
    clear_pending(session)
    return {
        "candidate": candidate,
        "slots": slots,
        "option_id": "",
        "defaulted": True,
        "note": note,
        "source": source,
    }


def _spec_apply_session_meta_title(pending: dict[str, Any], *, text: str) -> dict[str, Any]:
    return {
        "candidate": dict(pending.get("candidate") or {})
        or {"kind": "session_meta", "name": "set_session_meta", "payload": {}},
        "slots": {"meta_action": "apply", "message": (text or "")[:500]},
        "option_id": "typed_meta",
        "defaulted": False,
        "note": "",
        "source": str(pending.get("source") or "session_meta"),
    }


def _spec_apply_materials_name(pending: dict[str, Any], *, name: str) -> dict[str, Any]:
    candidate = dict(pending.get("candidate") or {})
    tool = str(candidate.get("name") or "").strip()
    base = dict(candidate.get("payload") or {})
    text = (name or "").strip()[:80]
    args = {**base, "name": text, "pack_name": text, "slot_name": text}
    return {
        "candidate": candidate,
        "slots": {"tool": tool, "args": args},
        "option_id": "typed_materials_name",
        "defaulted": False,
        "note": "",
        "source": str(pending.get("source") or "materials_name"),
    }


def _attach_residual(spec: dict[str, Any], residual: str) -> dict[str, Any]:
    text = (residual or "").strip()[:500]
    if not text:
        return spec
    slots = dict(spec.get("slots") or {})
    slots["residual_message"] = text
    return {**spec, "slots": slots}


def _apply_freeform_clarify_title(
    pending: ClarifyPending | dict[str, Any],
    *,
    source: str,
    title: str,
    residual: str = "",
) -> dict[str, Any] | None:
    tip = (title or "").strip()
    if not tip:
        return None
    if source == "doc_name":
        return _attach_residual(
            _spec_apply_doc_title(pending, title=tip, option_id="typed_doc_name"),
            residual,
        )
    if source == "session_meta":
        return _attach_residual(_spec_apply_session_meta_title(pending, text=tip), residual)
    if source == "materials_name":
        return _attach_residual(_spec_apply_materials_name(pending, name=tip), residual)
    return None


def _bump_clarify_ask(
    session: ChatSession,
    pending: ClarifyPending | dict[str, Any],
    *,
    follow: str = "",
) -> str:
    data = pending.to_dict() if isinstance(pending, ClarifyPending) else dict(pending or {})
    ask_count = int(data.get("ask_count") or 0)
    data["ask_count"] = ask_count + 1
    session.freeform = {**(session.freeform or {}), FREEFORM_KEY: data}
    if follow:
        return follow
    options = list(data.get("options") or [])
    labels = [str(o.get("label") or "") for o in options[:3] if str(o.get("label") or "")]
    if labels:
        return (
            "我还没听准你要哪一条。你用自己的话说也行，"
            f"大概是：{'；'.join(labels)}。"
        )
    return str(pending.get("question") or "请再说一次，或点选上面的选项。")


def try_handle_pending_message(
    session: ChatSession,
    message: str,
    *,
    invoke=None,
    home=None,
) -> dict[str, Any] | str | None:
    """主路径入口（所有 source 统一：精确点选 / 结构行 → 上下文模型 → 离线兜底）。

    - 返回 dict：已消歧（含 defaulted）
    - 返回 str：仍挂起，应把该字符串当回复（再问一次 / 提示）
    - 返回 None：无 pending 或已丢弃，走正常路由
    """
    pending = get_pending(session)
    if not pending:
        return _spec_from_study_source_pending(session, message)

    source = str(pending.get("source") or "")
    options = list(pending.get("options") or [])
    text = (message or "").strip()

    # 1) UI 精确点选（option id / 完整 label）
    exact = _match_option_exact(message, options)
    if exact is not None:
        clear_pending(session)
        return _spec_from_option(pending, exact)

    # 2) 「类型｜标题」元信息行：有模型走分类器；无模型才用分隔符结构兜底
    meta_hit = False
    meta_title = text
    if invoke is not None and text:
        try:
            from report_writing.chat.intent_context import pack_classifier_context
            from report_writing.chat.clarify_llm import classify_doc_meta_line_llm

            ctx = pack_classifier_context(session)
            judged_meta = classify_doc_meta_line_llm(
                text,
                dialogue_tail=list(ctx.get("dialogue_tail") or []),
                focus_document_brief=str(ctx.get("focus_document_brief") or ""),
                invoke=invoke,
            ) or {}
            if judged_meta.get("doc_title") or judged_meta.get("doc_category"):
                meta_hit = True
                cat = str(judged_meta.get("doc_category") or "").strip()
                title = str(judged_meta.get("doc_title") or "").strip()
                meta_title = f"{cat}｜{title}".strip("｜") if cat or title else text
        except Exception:
            meta_hit = False
    if not meta_hit:
        from report_writing.chat.session_snapshot import looks_like_freeform_meta_line

        meta_hit = looks_like_freeform_meta_line(message)

    if meta_hit:
        if source in {"entry", "control"}:
            clear_pending(session)
            return {
                "candidate": {
                    "kind": "entry_intent",
                    "name": "entry_path",
                    "payload": {"message": (meta_title or text)[:500]},
                },
                "slots": {"entry_path": "freeform", "tool": "skip_blueprint"},
                "option_id": "skip_blueprint",
                "defaulted": False,
                "note": "",
                "source": source,
            }
        if source in {"doc_name", "session_meta"}:
            clear_pending(session)
            applied = _apply_freeform_clarify_title(
                pending, source=source, title=meta_title or text
            )
            if applied is not None:
                return applied

    # 文档名这一问：具体名字（可带「用蓝本库」）优先于模型和建议名
    if source == "doc_name":
        accepted = _accept_doc_name_pending_answer(session, pending, text)
        if accepted is not None:
            return accepted

    # 3) action_slots：章节号/槽位序号属结构补槽（工具已选定）
    if source == "action_slots":
        from report_writing.chat.action_slots import try_resolve_action_slot_message
        from report_writing.chat.helpers import _load_task
        from report_writing.chat.session_documents import resolve_task_id

        cand = dict(pending.get("candidate") or {})
        pay = dict(cand.get("payload") or {})
        load_id = str(
            resolve_task_id(session, pay)
            or pay.get("task_id")
            or pay.get("blueprint_id")
            or session.task_id
            or session.blueprint_id
            or ""
        ).strip()
        resume_task = _load_task(home, load_id) if home is not None and load_id else None
        slot_hit = try_resolve_action_slot_message(session, message, task=resume_task)
        if slot_hit is not None:
            return slot_hit

    # 4) 有模型：一律结合上下文分类（短语别名不抢先）
    judged: dict[str, Any] = {}
    if invoke is not None:
        from report_writing.chat.intent_context import pack_classifier_context
        from report_writing.chat.clarify_llm import classify_clarify_reply_llm

        ctx = pack_classifier_context(session)
        judged = classify_clarify_reply_llm(
            message,
            pending=pending,
            dialogue_tail=list(ctx.get("dialogue_tail") or []),
            focus_document_brief=str(ctx.get("focus_document_brief") or ""),
            session_documents_brief=str(ctx.get("session_documents_brief") or ""),
            invoke=invoke,
        ) or {}
        decision = str(judged.get("decision") or "")
        residual = str(judged.get("residual") or "").strip()
        if decision == "pick":
            oid = str(judged.get("option_id") or "").strip()
            opt = next((o for o in options if str(o.get("id") or "") == oid), None)
            if opt is not None:
                clear_pending(session)
                return _attach_residual(_spec_from_option(pending, opt), residual)
            # 定名 pick 无选项时可能带回 title
            judged_title = str(judged.get("title") or "").strip()
            if judged_title and source in {"doc_name", "session_meta", "materials_name"}:
                clear_pending(session)
                applied = _apply_freeform_clarify_title(
                    pending, source=source, title=judged_title, residual=residual
                )
                if applied is not None:
                    return applied
        if decision == "apply_title":
            judged_title = str(judged.get("title") or "").strip()
            if (
                source == "doc_name"
                and judged_title
                and _doc_name_message_blocks_suggestion(text)
            ):
                judged_title = ""
            if judged_title:
                clear_pending(session)
                applied = _apply_freeform_clarify_title(
                    pending, source=source, title=judged_title, residual=residual
                )
                if applied is not None:
                    return applied
        if decision == "new_intent":
            clear_pending(session)
            return None
        # unclear：先尝试确定性「改名/名字叫」抽取，避免再挂「起个名字」
        if source in {"doc_name", "session_meta", "materials_name"}:
            from report_writing.chat.session_documents import explicit_doc_title_from_message

            extracted = explicit_doc_title_from_message(text)
            if extracted:
                clear_pending(session)
                applied = _apply_freeform_clarify_title(
                    pending, source=source, title=extracted
                )
                if applied is not None:
                    return applied
        # unclear / 空结果：有模型时不靠 looks_like_new_intent 猜换话题
        # 自由填名 / 槽位 / 含糊应付 → 再问；选项类非 vacuous → 交回总控
        if source in {"doc_name", "session_meta", "materials_name", "action_slots"} or (
            message_is_vacuous_clarify_reply(message)
        ):
            if source == "doc_name":
                accepted = _accept_doc_name_pending_answer(
                    session, pending, text, allow_plain=True
                )
                if accepted is not None:
                    return accepted
            ask_count = int(pending.get("ask_count") or 0)
            if ask_count >= 1:
                if source == "doc_name" and _doc_name_message_blocks_suggestion(text):
                    return _bump_clarify_ask(session, pending)
                defaulted = apply_safe_default(session, message=text)
                if defaulted is not None:
                    return defaulted
                return _bump_clarify_ask(session, pending)
            return _bump_clarify_ask(
                session,
                pending,
                follow=str(judged.get("question") or "").strip(),
            )
        clear_pending(session)
        return None

    # 5) 离线兜底：别名复述 / 自由填名仅作参考；looks_like_new_intent 仅此处使用
    if looks_like_new_intent(message):
        clear_pending(session)
        return None

    alias_hit = _match_option(message, options)
    if alias_hit is not None:
        clear_pending(session)
        return _spec_from_option(pending, alias_hit)

    if source == "doc_name":
        accepted = _accept_doc_name_pending_answer(
            session, pending, text, allow_plain=True
        )
        if accepted is not None:
            return accepted

    ask_count = int(pending.get("ask_count") or 0)
    if ask_count >= 1:
        if source == "doc_name" and _doc_name_message_blocks_suggestion(text):
            return _bump_clarify_ask(session, pending)
        defaulted = apply_safe_default(session, message=text)
        if defaulted is not None:
            return defaulted
        return _bump_clarify_ask(session, pending)

    if source in {"doc_name", "session_meta", "materials_name"}:
        from report_writing.chat.helpers import is_vague_freeform_meta
        from report_writing.chat.session_documents import explicit_doc_title_from_message

        if message_is_vacuous_clarify_reply(message):
            return _bump_clarify_ask(session, pending)
        extracted = explicit_doc_title_from_message(text)
        if extracted:
            clear_pending(session)
            applied = _apply_freeform_clarify_title(pending, source=source, title=extracted)
            if applied is not None:
                return applied
        if (
            source == "doc_name"
            and _doc_name_message_blocks_suggestion(text)
        ):
            return _bump_clarify_ask(session, pending)
        if source == "doc_name" and _path_tail_start(text) == 0:
            return _bump_clarify_ask(session, pending)
        if text and not is_vague_freeform_meta(text, field=True):
            clear_pending(session)
            applied = _apply_freeform_clarify_title(pending, source=source, title=text)
            if applied is not None:
                return applied
        # 对不上：有建议名/选项则再问，不丢上下文
        return _bump_clarify_ask(session, pending)

    if source == "action_slots":
        return _bump_clarify_ask(
            session,
            pending,
            follow=str(pending.get("question") or "请点选项，或直接说章节号。"),
        )

    if message_is_vacuous_clarify_reply(message):
        return _bump_clarify_ask(session, pending)

    # 其它 source 离线且对不上：清 pending 交回总控
    clear_pending(session)
    return None


def study_scope_needs_clarify(
    task: Any,
    message: str,
    sequences: list[str],
    *,
    resolved_scope: str = "",
) -> bool:
    """学习范围：大纲该章有下属、且大脑还没给出 whole/children/self。"""
    if not sequences:
        return False
    if str(resolved_scope or "").strip() in {"whole", "children", "self"}:
        return False
    if task is None or not getattr(task, "outline", None):
        return False
    want = {str(s).strip() for s in sequences if str(s).strip()}
    if not want:
        return False
    chapters = list(getattr(task.outline, "chapters", None) or [])
    for top in want:
        has_child = False
        for node in chapters:
            if bool(getattr(node, "structure_only", False)):
                continue
            seq = str(getattr(node, "sequence", "") or "").strip()
            if not seq:
                continue
            if seq.startswith(top + "."):
                has_child = True
                break
        if has_child:
            return True
    return False


def build_study_scope_clarify(
    session: ChatSession,
    *,
    sequences: list[str],
    study_message: str,
) -> tuple[str, list[ChatCard]]:
    tops = [str(s).strip() for s in sequences if str(s).strip()]
    top_hint = "、".join(f"第{t}章" for t in tops[:3]) or "这一章"
    question = (
        f"{top_hint}下面还有子章节。你是只要本章、分析整章（含下属），还是只要下面的子章节？"
    )
    return begin_clarify(
        session,
        source="wizard",
        question=question,
        options=[
            {
                "id": "self",
                "label": "只要本章（不要下属）",
                "slots": {"study_scope": "self"},
            },
            {
                "id": "whole",
                "label": "分析整章（含下属）",
                "slots": {"study_scope": "whole"},
            },
            {
                "id": "children",
                "label": "只要下面的子章节",
                "slots": {"study_scope": "children"},
            },
        ],
        candidate={
            "kind": "wizard_command",
            "name": "re_study_chapters",
            "command": "re_study_chapters",
            "payload": {
                "chapter_sequences": tops,
                "study_message": study_message,
            },
        },
        phase_hint="study",
    )


def _spec_from_study_source_pending(session: ChatSession, message: str) -> dict[str, Any] | None:
    """对话里粘贴了原文：把挂起的「根据原文分析」续上。"""
    pending = dict((session.freeform or {}).get("study_source_pending") or {})
    if not pending:
        return None
    text = (message or "").strip()
    if len(text) < 40:
        return None
    ff = dict(session.freeform or {})
    ff.pop("study_source_pending", None)
    session.freeform = ff
    payload = {
        **pending,
        "study_text": text,
        "study_method": "source",
    }
    return {
        "candidate": {
            "kind": "wizard_command",
            "name": "re_study_chapters",
            "command": "re_study_chapters",
            "payload": payload,
        },
        "slots": {"study_method": "source", "study_text": text},
        "option_id": "source",
        "defaulted": False,
        "note": "",
        "source": "study_source",
    }


def build_study_method_clarify(
    session: ChatSession,
    *,
    chapter_keys: list[str],
    chapter_sequences: list[str],
    study_message: str,
    study_scope: str = "",
    chapter_label: str = "",
    has_sample: bool = False,
) -> tuple[str, list[ChatCard]]:
    label = (chapter_label or "").strip() or "本章"
    bound_id = "sample" if has_sample else "auto"
    bound_label = "依据样本分析" if has_sample else "自动分析"
    question = f"重新分析「{label}」要用哪种方式？"
    return begin_clarify(
        session,
        source="study_method",
        question=question,
        options=[
            {
                "id": "source",
                "label": "根据原文内容分析",
                "slots": {"study_method": "source"},
            },
            {
                "id": bound_id,
                "label": bound_label,
                "slots": {"study_method": bound_id},
            },
        ],
        candidate={
            "kind": "wizard_command",
            "name": "re_study_chapters",
            "command": "re_study_chapters",
            "payload": {
                "chapter_keys": [str(k).strip() for k in (chapter_keys or []) if str(k).strip()],
                "chapter_sequences": [str(s).strip() for s in (chapter_sequences or []) if str(s).strip()],
                "study_message": study_message,
                "study_scope": str(study_scope or "").strip(),
                "chapter_label": label,
            },
        },
        phase_hint="study",
    )


def build_write_scope_clarify(
    session: ChatSession,
    *,
    sequences: list[str],
    write_message: str,
) -> tuple[str, list[ChatCard]]:
    tops = [str(s).strip() for s in sequences if str(s).strip()]
    hint = "、".join(tops[:3]) or "这一章"
    question = f"写 {hint} 是只要本章，还是连下属子章节一起写？"
    return begin_clarify(
        session,
        source="write",
        question=question,
        options=[
            {
                "id": "self",
                "label": "只要本章",
                "slots": {"write_scope": "self"},
            },
            {
                "id": "whole",
                "label": "含下属一起写",
                "slots": {"write_scope": "whole"},
            },
        ],
        candidate={
            "kind": "write_command",
            "name": "select_write_chapters",
            "payload": {
                "chapter_sequences": tops,
                "write_message": write_message,
            },
        },
        phase_hint="write",
    )


def build_ask_intent_clarify(
    session: ChatSession,
    *,
    message: str = "",
    invoke=None,
    question: str = "",
    options: list[dict[str, Any]] | None = None,
    home: Path | None = None,
    emit=None,
) -> tuple[str, list[ChatCard]]:
    """大脑意图不清：用模型结合对话生成追问；失败则按会话状态给有限选项。"""
    text = (message or "").strip()
    from report_writing.chat.session_documents import (
        focus_bound_blueprint_id,
        focus_needs_doc_name,
        focus_needs_entry_path,
        focus_needs_outline,
        focus_onboarding,
        message_requests_list_blueprints,
    )

    if message_requests_list_blueprints(text) and home is not None:
        from report_writing.chat_orchestrator import _action_list_blueprints

        return _action_list_blueprints(home, session, {"q": ""})

    # 入口未齐时 ask_intent 仍应能问「有蓝本还是自己写」；定名由 hang_missing / 选路卡负责。
    if focus_needs_entry_path(session) and not focus_needs_doc_name(session):
        return build_entry_path_clarify(
            session,
            message=text,
            invoke=invoke,
            home=home,
            emit=emit,
        )
    ob = focus_onboarding(session)
    ep = str(ob.get("entry_path") or "").strip()
    if ep == "match" and not focus_bound_blueprint_id(session):
        return begin_clarify(
            session,
            source="control",
            question="要用哪份蓝本？可以说名字，或先让我列出可选蓝本。",
            options=[
                {
                    "id": "match_blueprints",
                    "label": "帮我匹配蓝本",
                    "slots": {"tool": "match_blueprints"},
                },
                {
                    "id": "list_blueprints",
                    "label": "列出可选蓝本",
                    "slots": {"tool": "list_blueprints"},
                },
            ],
            candidate={
                "kind": "entry_intent",
                "name": "match_blueprints",
                "payload": {"message": text[:500]},
            },
            phase_hint="blueprint_select",
        )
    from report_writing.chat.session_documents import (
        message_wants_template_new_document,
        quick_title_from_write_message,
    )

    if message_wants_template_new_document(session, text):
        from report_writing.chat.session_documents import _template_new_doc_topic

        tip = _template_new_doc_topic(text) or quick_title_from_write_message(text)
        return _start_template_wizard_now(
            session,
            home=home,
            message=text,
            title_hint=tip,
            invoke=invoke,
        )
    if focus_needs_outline(session, home):
        if ep == "template":
            return (
                "目录还没定。请在上方的依据样本向导里上传样板并提取目录，或跟我说「提取目录」。",
                [],
            )
        return begin_clarify(
            session,
            source="control",
            question="目录还没定。要我按题目生成章节大纲，还是你直接粘贴目录？",
            options=[
                {
                    "id": "generate_outline",
                    "label": "帮我生成目录大纲",
                    "slots": {"tool": "submit_outline", "args": {"regenerate": True}},
                },
                {
                    "id": "paste_outline",
                    "label": "我来粘贴目录",
                    "slots": {"tool": "ask_intent"},
                },
            ],
            candidate={
                "kind": "control_tool",
                "name": "ask_intent",
                "payload": {"message": text[:500]},
            },
            phase_hint="control",
        )

    judged: dict[str, Any] = {}
    if invoke is not None and not (question and options):
        from report_writing.chat.dialogue import format_full_dialogue
        from report_writing.chat.clarify_llm import classify_unclear_intent_llm
        from report_writing.chat.intent_router import active_blueprint_wizard
        from report_writing.chat.react_speed import control_dialogue_max_turns

        wiz = dict(active_blueprint_wizard(session) or {})
        from report_writing.chat.intent_context import pack_classifier_context

        ctx = pack_classifier_context(
            session, max_turns=control_dialogue_max_turns()
        )
        focus_bid = focus_bound_blueprint_id(session)
        judged = classify_unclear_intent_llm(
            text,
            phase=str(ctx.get("phase") or session.phase or ""),
            blueprint_id=str(
                focus_bid or wiz.get("blueprint_id") or ctx.get("blueprint_id") or ""
            ),
            wizard_step=str(wiz.get("step") or ""),
            dialogue_tail=list(ctx.get("dialogue_tail") or [])
            or format_full_dialogue(
                session, max_turns=control_dialogue_max_turns(), focus_only=True
            ),
            focus_document_brief=str(ctx.get("focus_document_brief") or ""),
            session_documents_brief=str(ctx.get("session_documents_brief") or ""),
            session_goal_brief=str(ctx.get("session_goal_brief") or ""),
            task_id=str(ctx.get("task_id") or session.task_id or ""),
            invoke=invoke,
        ) or {}
    q = (question or str(judged.get("question") or "")).strip()
    raw_opts = options if options is not None else list(judged.get("options") or [])
    clean: list[dict[str, Any]] = []
    for item in raw_opts:
        if not isinstance(item, dict):
            continue
        oid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not oid or not label:
            continue
        slots = dict(item.get("slots") or {})
        tool = str(item.get("tool") or slots.get("tool") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else slots.get("args")
        if not isinstance(args, dict):
            args = {}
        if tool:
            slots["tool"] = tool
        if args:
            slots["args"] = args
        slots = enrich_control_option_slots(oid, label, slots)
        clean.append({"id": oid, "label": label, "slots": slots})
    if q and len(clean) >= 2:
        return begin_clarify(
            session,
            source="control",
            question=q,
            options=clean,
            candidate={
                "kind": "control_tool",
                "name": "ask_intent",
                "payload": {"message": text[:500]},
            },
            phase_hint="control",
        )
    bid = focus_bound_blueprint_id(session)
    if not bid:
        # 无蓝本线路已有目录：给写作向引导，不再误挂入口
        return begin_clarify(
            session,
            source="control",
            question="这句我还没听准。要继续完善目录/要点，还是开始写？",
            options=[
                {
                    "id": "outline",
                    "label": "调整或确认目录",
                    "slots": {"tool": "submit_outline"},
                },
                {
                    "id": "points",
                    "label": "生成写作要点",
                    "slots": {"tool": "generate_freeform_points"},
                },
                {
                    "id": "write",
                    "label": "开始写作",
                    "slots": {"tool": "start_writing"},
                },
            ],
            candidate={
                "kind": "control_tool",
                "name": "ask_intent",
                "payload": {"message": text[:500]},
            },
            phase_hint="control",
        )
    return begin_clarify(
        session,
        source="control",
        question="这句我还没听准。你是想开写、再分析一章，还是给某章加要素？",
        options=[
            {
                "id": "write",
                "label": "先写已分析的章",
                "slots": {"tool": "write_analyzed_only"},
            },
            {
                "id": "study",
                "label": "分析 / 再分析章节",
                "slots": {"tool": "re_study_chapters"},
            },
            {
                "id": "add_element",
                "label": "给某章加要素",
                "slots": {"tool": "add_chapter_element"},
            },
        ],
        candidate={
            "kind": "control_tool",
            "name": "ask_intent",
            "payload": {"message": text[:500]},
        },
        phase_hint="control",
    )


def build_document_name_clarify(
    session: ChatSession,
    *,
    message: str = "",
    title_hint: str = "",
    force_typed: bool = False,
    invoke=None,
    home: Path | None = None,
    emit=None,
    follow_entry: bool = True,
) -> tuple[str, list[ChatCard]]:
    """各路径开写前：先确认写作文档名称。"""
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        explicit_doc_title_from_message,
        focus_needs_doc_name,
        is_writing_document_named,
        message_wants_own_template,
        peek_pending_entry,
        quick_title_from_write_message,
        stash_focus_preferred_entry_path,
        stash_pending_entry,
        try_confirm_document_name_from_message,
    )

    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    already_named = is_writing_document_named(session)
    # 已定名：禁止再挂「先给这份文档起个名字」；仅显式改名才更新
    if already_named and not force_typed and not focus_needs_doc_name(session):
        confirmed = try_confirm_document_name_from_message(
            session, msg, title_hint=tip, home=home
        )
        if confirmed:
            confirm_writing_document_name(
                session, confirmed, home=home, message=msg or confirmed
            )
            if follow_entry and home is not None:
                return build_entry_path_clarify(
                    session,
                    message=msg,
                    title_hint=confirmed,
                    invoke=invoke,
                    home=home,
                    emit=emit,
                    after_doc_name_confirm=True,
                )
            return f"好，文档名已更新为「{confirmed}」。", []
        from report_writing.chat.session_documents import clean_document_display_title

        current = clean_document_display_title(
            str((session.freeform or {}).get("title") or tip or "")
        )
        if follow_entry and home is not None:
            return build_entry_path_clarify(
                session,
                message=msg,
                title_hint=current or tip,
                invoke=invoke,
                home=home,
                emit=emit,
                after_doc_name_confirm=True,
            )
        if current:
            return f"好，文档就叫「{current}」。", []
        return "文档名已经定好了，接下来选写作方式或继续开写就行。", []
    if not force_typed:
        confirmed = try_confirm_document_name_from_message(
            session, msg, title_hint=tip, home=home
        )
        if confirmed:
            if not focus_needs_doc_name(session):
                if follow_entry and home is not None:
                    return build_entry_path_clarify(
                        session,
                        message=msg,
                        title_hint=confirmed,
                        invoke=invoke,
                        home=home,
                        emit=emit,
                        after_doc_name_confirm=True,
                    )
                return f"好，文档就叫「{confirmed}」。", []
    if message_wants_own_template(msg):
        stash_focus_preferred_entry_path(session, "template")
        if home is not None:
            return _start_template_wizard_now(
                session,
                home=home,
                message=msg,
                title_hint=tip,
                invoke=invoke,
            )
    if not tip and msg and invoke is not None:
        try:
            from report_writing.chat.clarify_llm import extract_write_subject_title_llm

            tip = extract_write_subject_title_llm(msg, invoke=invoke) or tip
        except Exception:
            tip = tip
    if not tip:
        tip = quick_title_from_write_message(msg)
    # 「用我的模版」未起名：题材不是文档名；清掉「就叫××」建议，改为开放起名
    if tip and message_wants_own_template(msg) and not explicit_doc_title_from_message(msg):
        tip = ""
    if msg or tip:
        stash_pending_entry(session, message=msg, title=tip)
    pending = peek_pending_entry(session)
    tip = tip or str(pending.get("title") or "").strip()
    if tip and message_wants_own_template(msg) and not explicit_doc_title_from_message(msg):
        tip = ""
    options: list[dict[str, Any]] = []
    if tip and not force_typed:
        options.append(
            {
                "id": "use_suggested_name",
                "label": f"就叫「{tip}」",
                "slots": {
                    "doc_name_action": "apply",
                    "title": tip,
                    "message": tip,
                },
            }
        )
        options.append(
            {
                "id": "type_new_name",
                "label": "我换个名字",
                "slots": {"doc_name_action": "ask_again"},
            }
        )
        question = (
            f"先给这份文档起个名字吧。听你的意思可以叫「{tip}」——"
            "点选确认，或直接发你想用的名字。"
        )
    else:
        if message_wants_own_template(msg):
            question = (
                "可以，按你的模版来写。先给这份文档起个名字吧（直接说题目即可）；"
                "定名后请上传样板 Word 或 PDF。"
            )
        else:
            question = "先给这份文档起个名字吧。直接告诉我文档叫什么就行（口语或「类型｜标题」都可以）。"
    return begin_clarify(
        session,
        source="doc_name",
        question=question,
        options=options,
        candidate={
            "kind": "doc_name",
            "name": "confirm_document_name",
            "payload": {
                "message": (msg or pending.get("message") or "")[:500],
                "title": tip,
            },
        },
        phase_hint="doc_name",
    )


def build_entry_branch_clarify(
    session: ChatSession,
    *,
    message: str = "",
    title_hint: str = "",
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.session_documents import peek_pending_entry, stash_pending_entry

    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    if msg or tip:
        stash_pending_entry(session, message=msg, title=tip)
    pending = peek_pending_entry(session)
    tip = tip or pending.get("title") or str((session.freeform or {}).get("title") or "").strip()
    question = (
        f"「{tip}」打算走哪条路？"
        if tip
        else "这份文档打算走哪条路？"
    ) + "可以选用已有蓝本，或无蓝本自己写。"
    return begin_clarify(
        session,
        source="entry_branch",
        question=question,
        options=[
            {
                "id": "with_blueprint",
                "label": "有蓝本 · 选用已有",
                "slots": {"entry_branch": "with_blueprint"},
            },
            {
                "id": "without_blueprint",
                "label": "无蓝本 · 自己写",
                "slots": {"entry_branch": "without_blueprint"},
            },
        ],
        candidate={
            "kind": "entry_branch",
            "name": "entry_branch",
            "payload": {
                "message": (msg or pending.get("message") or "")[:500],
                "title": tip,
            },
        },
        phase_hint="entry",
    )


def pending_document_scope_reply(session: ChatSession) -> tuple[str, list[ChatCard]] | None:
    """已有 document_scope 澄清挂起时复用，避免同轮重复问句。"""
    pending = get_pending(session)
    if not pending or str(pending.get("source") or "") != "document_scope":
        return None
    question = str(pending.get("question") or "").strip()
    opts = list(pending.get("options") or [])
    if not question or not opts:
        return None
    card = ChatCard(
        type="intent_clarify",
        title="请帮我确认一下",
        payload={
            "clarify_id": pending.get("id") or "",
            "question": question,
            "options": opts,
            "source": "document_scope",
        },
    )
    return question, [card]


def build_document_scope_clarify(
    session: ChatSession,
    *,
    message: str = "",
    topic_hint: str = "",
) -> tuple[str, list[ChatCard]]:
    """已定名焦点遇上新写/弱重写：追问新开 / 整份重写当前 / 改名 / 继续，禁止静默改名。"""
    existing = pending_document_scope_reply(session)
    if existing is not None:
        return existing
    from report_writing.chat.session_documents import (
        _focus_display_title,
        message_looks_like_weak_rewrite,
        peek_pending_entry,
        record_new_document_thread_anchor,
        stash_pending_entry,
    )

    record_new_document_thread_anchor(session)
    msg = (message or "").strip()
    topic = (topic_hint or "").strip()
    # 弱「重新写」先判定，勿被 pending/quick_title 误填题材后冲掉 rewrite_current 选项
    weak_msg = message_looks_like_weak_rewrite(msg)
    if msg or topic:
        stash_pending_entry(session, message=msg, title=topic)
    pending = peek_pending_entry(session)
    if not topic and not weak_msg:
        topic = pending.get("title") or ""
    cur = _focus_display_title(session) or str((session.freeform or {}).get("title") or "").strip()
    weak = weak_msg and not topic
    if weak and cur:
        question = (
            f"你是想新开一份文档，"
            f"还是清空重写当前的「{cur}」，"
            f"还是继续写「{cur}」？"
        )
    elif topic and cur:
        question = (
            f"你是想新开一份「{topic}」，"
            f"还是把当前的「{cur}」改成这个名字，"
            f"还是继续写「{cur}」、把这句话当写作题材？"
        )
    elif topic:
        question = f"关于「{topic}」：是新开一份文档、改当前文档名，还是继续写当前这份？"
    else:
        question = "你是想新开一份文档、改当前文档的名字，还是继续写当前这份？"
    options: list[dict[str, Any]] = [
        {
            "id": "scope_force_new",
            "label": f"新开一份{(' · ' + topic) if topic else ''}".strip(),
            "slots": {"document_scope_action": "force_new", "title": topic},
        },
    ]
    if weak_msg or weak or not topic:
        options.append(
            {
                "id": "scope_rewrite_current",
                "label": f"清空重写当前这份{(' · ' + cur) if cur else ''}".strip(),
                "slots": {"document_scope_action": "rewrite_current"},
            }
        )
    if (topic and not weak_msg) or (not weak and not weak_msg):
        options.append(
            {
                "id": "scope_rename",
                "label": f"改当前文档名{(' · ' + topic) if topic else ''}".strip(),
                "slots": {"document_scope_action": "rename", "title": topic},
            }
        )
    options.append(
        {
            "id": "scope_continue",
            "label": f"继续当前这份{(' · ' + cur) if cur else ''}".strip(),
            "slots": {"document_scope_action": "continue"},
        }
    )
    return begin_clarify(
        session,
        source="document_scope",
        question=question,
        options=options,
        candidate={
            "kind": "document_scope",
            "name": "document_scope",
            "payload": {
                "message": (msg or pending.get("message") or "")[:500],
                "title": topic,
                "current_title": cur,
            },
        },
        phase_hint="document_scope",
    )


def _start_template_wizard_now(
    session: ChatSession,
    *,
    home: Path | None,
    message: str = "",
    title_hint: str = "",
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    """已声明依据模版：直接下发向导名称卡（meta），不再另挂 doc_name 澄清。"""
    from report_writing.chat.resource_actions import _begin_blueprint_wizard
    from report_writing.chat.session_documents import (
        drop_foreign_blueprint_wizard,
        mark_focus_entry_branch,
        mark_focus_entry_path,
        session_documents_map,
        stash_focus_preferred_entry_path,
    )

    # 禁止 continue 上一份 selected/study：先丢掉异文档向导，再开本文档 fresh_meta
    drop_foreign_blueprint_wizard(session)
    wiz_now = dict((session.freeform or {}).get("blueprint_wizard") or {})
    mode_now = str(wiz_now.get("mode") or "").strip()
    step_now = str(wiz_now.get("step") or "").strip()
    if mode_now == "selected" or step_now == "study":
        ff_drop = dict(session.freeform or {})
        ff_drop.pop("blueprint_wizard", None)
        session.freeform = ff_drop
        session.blueprint_id = ""
        try:
            from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

            workflow_set(session, WORKFLOW_BLUEPRINT, {})
        except Exception:
            pass
    stash_focus_preferred_entry_path(session, "template", residual=message)
    mark_focus_entry_branch(session, "without_blueprint")
    mark_focus_entry_path(session, "template")
    tid = str(getattr(session, "task_id", "") or "").strip()
    have_shell = False
    try:
        from report_writing.services.task_ids import is_writing_task_id

        have_shell = bool(tid and is_writing_task_id(tid))
    except Exception:
        have_shell = False
    force_new = (not have_shell) and bool(session_documents_map(session))
    return _begin_blueprint_wizard(
        session,
        title_hint=title_hint,
        home=home,
        force_new=force_new,
        user_message=message,
        invoke=invoke,
    )


def _template_path_declared(session: ChatSession, message: str = "") -> bool:
    from report_writing.chat.session_documents import (
        focus_onboarding,
        message_wants_own_template,
    )

    pep = str(focus_onboarding(session).get("preferred_entry_path") or "").strip()
    ep = str(focus_onboarding(session).get("entry_path") or "").strip()
    return pep == "template" or ep == "template" or message_wants_own_template(message)


def hang_focus_onboarding_gap(
    session: ChatSession,
    *,
    message: str = "",
    title_hint: str = "",
    invoke=None,
    home: Path | None = None,
    emit=None,
) -> tuple[str, list[ChatCard]] | None:
    """按当前车道缺口只回一张卡：未定名→定名；已定名未选路→选路。禁止整机重入。"""
    from report_writing.chat.session_documents import (
        focus_needs_doc_name,
        focus_needs_entry_path,
        message_skips_onboarding_hang,
        should_hang_onboarding_gap,
    )

    if message_skips_onboarding_hang(message):
        return None
    if not should_hang_onboarding_gap(session, message, invoke=invoke, tool=""):
        return None

    if focus_needs_doc_name(session):
        if _template_path_declared(session, message):
            return _start_template_wizard_now(
                session,
                home=home,
                message=message,
                title_hint=title_hint,
                invoke=invoke,
            )
        return build_document_name_clarify(
            session,
            message=message,
            title_hint=title_hint,
            invoke=invoke,
            home=home,
            emit=emit,
            follow_entry=False,
        )
    if focus_needs_entry_path(session):
        return build_entry_path_clarify(
            session,
            message=message,
            title_hint=title_hint,
            invoke=invoke,
            home=home,
            emit=emit,
            after_doc_name_confirm=True,
            apply_plan=False,
        )
    return None


def _auto_freeform_outline_ready(
    session: ChatSession,
    message: str = "",
    *,
    invoke=None,
) -> bool:
    """用户已选无蓝本·自动大纲且 entry_path=freeform；勿在只定名/未选支线时链式 submit_outline。"""
    from report_writing.chat.session_documents import focus_onboarding, observe_new_document_gaps

    ob = focus_onboarding(session)
    if str(ob.get("entry_branch") or "").strip() != "without_blueprint":
        return False
    if str(ob.get("entry_path") or "").strip() != "freeform":
        return False
    msg = (message or "").strip()
    gaps = observe_new_document_gaps(session, msg, invoke=invoke)
    if str(gaps.get("declared_freeform_mode") or "").strip() == "auto_outline":
        return True
    if "generate_outline" in list(gaps.get("residual_intents") or []):
        return True
    return False


def _observe_after_entry_marks(
    session: ChatSession,
    *,
    message: str = "",
    title_hint: str = "",
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    """入口槽已落：只刷新 gaps / suggested_next_tools，不执行 skip / outline / match / wizard。"""
    from report_writing.chat.session_documents import (
        observe_new_document_gaps,
        peek_pending_entry,
        stash_onboarding_observation,
        suggest_next_tools_from_gaps,
    )

    msg = (message or "").strip()
    pending = peek_pending_entry(session)
    tip = (
        (title_hint or "").strip()
        or str(pending.get("title") or "").strip()
        or str((session.freeform or {}).get("title") or "").strip()
    )
    gaps = observe_new_document_gaps(session, msg, title_hint=tip, invoke=invoke)
    suggested = suggest_next_tools_from_gaps(
        gaps, message=msg, title=tip, session=session
    )
    stash_onboarding_observation(session, gaps, suggested)
    title_now = tip or "这份文档"
    tools = [str(t.get("tool") or "").strip() for t in suggested]
    if "submit_outline" in tools or "skip_blueprint" in tools:
        reply = f"好，按自动生成大纲来写「{title_now}」。"
    elif "begin_blueprint_wizard" in tools:
        reply = f"好，按你的模版来写「{title_now}」。"
    elif "match_blueprints" in tools:
        reply = f"好，按有蓝本来匹配「{title_now}」。"
    else:
        reply = f"好，文档「{title_now}」和写作方式都对齐了，我继续帮你往下准备。"
    return reply, []


def _maybe_dispatch_suggested_onboarding(
    home: Path | None,
    session: ChatSession,
    *,
    apply_plan: bool,
    message: str = "",
    title: str = "",
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard]] | None:
    """仅遗留 apply_plan=True 路径链式执行 suggested；生产路径禁止。"""
    if not apply_plan or home is None:
        return None
    hit = dispatch_suggested_onboarding_tools(
        home,
        session,
        message=message,
        title=title,
        invoke=invoke,
        emit=emit,
    )
    if hit is None:
        return None
    reply, cards, _events = hit
    return reply, list(cards or [])


def _apply_auto_freeform_and_dispatch(
    home: Path | None,
    session: ChatSession,
    *,
    message: str = "",
    title_hint: str = "",
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard]] | None:
    """落 freeform + auto_outline 槽位并观察下一跳；不链式执行 skip / submit_outline。"""
    from report_writing.chat.session_documents import (
        _focus_display_title,
        _titles_conflict,
        begin_document_onboarding,
        ensure_named_freeform_document,
        focus_needs_doc_name,
        focus_ready_for_auto_outline,
        mark_focus_entry_path,
        mark_focus_freeform_mode,
        peek_pending_entry,
        stash_focus_preferred_entry_path,
        stash_pending_entry,
    )

    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    from report_writing.chat.session_documents import (
        apply_working_title as _apply_working,
        focus_has_display_title,
        quick_title_from_write_message as _quick_working,
    )

    if focus_needs_doc_name(session) and not focus_has_display_title(session):
        working = tip or _quick_working(msg)
        if working:
            _apply_working(session, working, home=home, message=msg)
    if focus_needs_doc_name(session) and not focus_has_display_title(session):
        return build_document_name_clarify(
            session,
            message=msg,
            title_hint=tip,
            invoke=invoke,
            home=home,
            emit=emit,
            follow_entry=False,
        )
    if msg or tip:
        stash_pending_entry(session, message=msg, title=tip)
    stash_focus_preferred_entry_path(session, "freeform", residual=msg)
    mark_focus_entry_path(session, "freeform")
    mark_focus_freeform_mode(session, "auto_outline")
    if not focus_ready_for_auto_outline(session):
        return build_entry_path_clarify(
            session,
            message=msg,
            title_hint=tip,
            invoke=invoke,
            home=home,
            emit=emit,
            apply_plan=False,
        )
    pending = peek_pending_entry(session)
    probe = msg or str(pending.get("message") or "").strip()
    title_for_tools = tip or str(pending.get("title") or "").strip()
    cur = _focus_display_title(session)
    if title_for_tools and cur and _titles_conflict(cur, title_for_tools):
        from report_writing.chat.session_documents import (
            is_writing_document_named,
            should_mint_new_document_slot,
            title_is_write_subject_not_doc_name,
        )

        hint_is_subject = title_is_write_subject_not_doc_name(
            probe, title_for_tools
        )
        # 当前车道已定名：题材 hint 不得再 force_new，否则定名/大纲横跳
        if (
            should_mint_new_document_slot(
                session, cur, title_for_tools, message=probe
            )
            and not hint_is_subject
            and not is_writing_document_named(session)
        ):
            begin_document_onboarding(
                session,
                home,
                message=probe,
                title_hint=title_for_tools,
                force_new=True,
            )
            ensure_named_freeform_document(
                session,
                home,
                title=title_for_tools,
                confirm_name=True,
            )
            stash_focus_preferred_entry_path(session, "freeform", residual=probe)
            mark_focus_entry_path(session, "freeform")
            mark_focus_freeform_mode(session, "auto_outline")
    elif title_for_tools and not cur and focus_needs_doc_name(session):
        ensure_named_freeform_document(
            session,
            home,
            title=title_for_tools,
            confirm_name=False,
        )

    del emit  # 观察路径不再链式 emit skip/outline
    return _observe_after_entry_marks(
        session, message=probe, title_hint=title_for_tools, invoke=invoke
    )


def build_entry_freeform_mode_clarify(
    session: ChatSession,
    *,
    message: str = "",
    title_hint: str = "",
    home: Path | None = None,
    invoke=None,
    emit=None,
    apply_plan: bool = False,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.session_documents import peek_pending_entry, stash_pending_entry

    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    if _auto_freeform_outline_ready(session, msg, invoke=invoke):
        auto = _apply_auto_freeform_and_dispatch(
            home,
            session,
            message=msg,
            title_hint=tip,
            invoke=invoke,
            emit=emit,
        )
        if auto is not None:
            dispatched = _maybe_dispatch_suggested_onboarding(
                home,
                session,
                apply_plan=apply_plan,
                message=msg,
                title=tip,
                invoke=invoke,
                emit=emit,
            )
            if dispatched is not None:
                return dispatched
            return auto
    if msg or tip:
        stash_pending_entry(session, message=msg, title=tip)
    pending = peek_pending_entry(session)
    tip = tip or pending.get("title") or str((session.freeform or {}).get("title") or "").strip()
    question = (
        f"无蓝本写「{tip}」："
        if tip
        else "无蓝本写作："
    ) + "可以依据模版上传提取，或直接自动生成大纲。"
    return begin_clarify(
        session,
        source="entry_freeform_mode",
        question=question,
        options=[
            {
                "id": "template",
                "label": "依据模版编写（上传提取）",
                "slots": {"entry_path": "create_blueprint", "freeform_mode": "template"},
            },
            {
                "id": "freeform",
                "label": "自动生成大纲",
                "slots": {"entry_path": "freeform", "freeform_mode": "auto_outline"},
            },
        ],
        candidate={
            "kind": "entry_freeform_mode",
            "name": "entry_freeform_mode",
            "payload": {
                "message": (msg or pending.get("message") or "")[:500],
                "title": tip,
            },
        },
        phase_hint="entry",
    )


def dispatch_suggested_onboarding_tools(
    home: Path,
    session: ChatSession,
    *,
    message: str = "",
    title: str = "",
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard], list[dict[str, Any]]] | None:
    """入口要素已齐时，按 suggested_next_tools 链式执行（非仅提示总控）。"""
    suggested = list((session.freeform or {}).get("suggested_next_tools") or [])
    if not suggested:
        return None
    from report_writing.chat.helpers import _emit_process
    from report_writing.chat.tool_dispatch import execute_dispatch_tool
    from report_writing.chat.intent_router import user_chose_skip_blueprint
    from report_writing.chat.session_documents import focus_onboarding, peek_pending_entry, session_blueprint_already_selected

    probe = (message or "").strip()
    pending = peek_pending_entry(session)
    if not probe:
        probe = str(pending.get("message") or "").strip()
    tip = (title or "").strip() or str(pending.get("title") or "").strip()
    ob = focus_onboarding(session)
    bp_bound = session_blueprint_already_selected(session)
    skip_match = (
        bp_bound
        or user_chose_skip_blueprint(session)
        or str(ob.get("entry_branch") or "").strip() == "without_blueprint"
        or str(ob.get("entry_path") or "").strip() == "freeform"
    )

    replies: list[str] = []
    cards: list[ChatCard] = []
    events: list[dict[str, Any]] = []
    executed = 0
    _emit_process(emit, "好，我按你刚才说的方式继续往下准备…")
    for step in suggested:
        tool = str(step.get("tool") or "").strip()
        if not tool:
            continue
        if tool == "match_blueprints" and skip_match:
            executed += 1
            continue
        args = dict(step.get("args") or {})
        if tip:
            args.setdefault("title_hint", tip)
            args.setdefault("title", tip)
        if probe and not str(args.get("message") or "").strip():
            args["message"] = probe[:500]
        hit = execute_dispatch_tool(
            home,
            session,
            {"tool": tool, "args": args},
            probe or tip,
            emit=emit,
            invoke=invoke,
        )
        if hit is None:
            break
        reply, step_cards, step_events = hit
        if reply and str(reply).strip():
            r = str(reply).strip()
            if not replies or replies[-1] != r:
                replies.append(r)
        cards.extend(list(step_cards or []))
        events.extend(list(step_events or []))
        executed += 1

    if executed <= 0:
        return None

    ff = dict(session.freeform or {})
    remainder = suggested[executed:]
    if remainder:
        ff["suggested_next_tools"] = remainder
    else:
        ff.pop("suggested_next_tools", None)
    session.freeform = ff
    return "\n".join(replies).strip(), cards, events


def continue_after_new_document(
    home: Path,
    session: ChatSession,
    *,
    message: str = "",
    title: str = "",
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard], list[dict[str, Any]]]:
    """force_new 后只 prepare + 挂缺槽卡 / 写 suggested_next_tools，不代跑 skip/match/wizard。"""
    from report_writing.chat.freeform_outline import remember_outline_constraints_from_message
    from report_writing.chat.session_documents import prepare_observed_onboarding

    probe = (message or "").strip()
    remember_outline_constraints_from_message(session, probe)
    prepare_observed_onboarding(
        session, home, message=probe, title_hint=title, invoke=invoke
    )
    reply, cards = build_entry_path_clarify(
        session,
        message=probe,
        title_hint=title,
        invoke=invoke,
        home=home,
        emit=emit,
        after_doc_name_confirm=True,
        apply_plan=False,
    )
    return reply, list(cards or []), []


def build_entry_path_clarify(
    session: ChatSession,
    *,
    message: str = "",
    title_hint: str = "",
    invoke=None,
    home: Path | None = None,
    emit=None,
    after_doc_name_confirm: bool = False,
    apply_plan: bool = False,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.session_documents import (
        clear_writing_document_named,
        confirm_writing_document_name,
        ensure_write_onboarding_started,
        explicit_doc_title_from_message,
        focus_needs_doc_name,
        focus_needs_entry_path,
        focus_onboarding,
        is_writing_document_named,
        message_confirms_write_document_intent,
        message_wants_new_document_strong,
        peek_pending_entry,
        quick_title_from_write_message,
        resolve_document_scope,
        stash_pending_entry,
        ambiguous_named_doc_new_topic,
    )

    msg = (message or "").strip()
    tip = (title_hint or "").strip()
    if home is not None and msg:
        from report_writing.chat.session_documents import (
            begin_document_onboarding,
            onboarding_lane_active,
        )

        if message_confirms_write_document_intent(
            msg, session=session, invoke=invoke
        ):
            ensure_write_onboarding_started(
                session, home, msg, invoke=invoke, emit=emit
            )
        elif not onboarding_lane_active(session):
            begin_document_onboarding(
                session, home, message=msg, title_hint=tip, force_new=True, emit=emit
            )
    explicit = ""
    # 已定名后又来新写句：先判作用域（追问 / 强制新开），勿把题材当续写当前。
    # 只缺选路时（生成目录 / 用模版）不要整段重开 document_scope。
    if (
        msg
        and is_writing_document_named(session)
        and not after_doc_name_confirm
        and not (
            focus_needs_entry_path(session)
            and not message_wants_new_document_strong(msg)
        )
    ):
        pending_scope = pending_document_scope_reply(session)
        if pending_scope is not None:
            return pending_scope
        from report_writing.chat.session_documents import begin_document_onboarding

        scope = resolve_document_scope(session, msg, title_hint=tip, invoke=invoke)
        if scope.get("need_clarify") or str(scope.get("action") or "") == "ask":
            return build_document_scope_clarify(
                session,
                message=msg,
                topic_hint=str(scope.get("title") or tip or "").strip(),
            )
        if scope.get("force_new"):
            begin_document_onboarding(
                session,
                home,
                message=msg,
                title_hint=str(scope.get("title") or tip or "").strip(),
                force_new=True,
                emit=emit,
            )
            tip = str(scope.get("title") or tip or "").strip() or tip
            # 已清 named，下面按未定名新开继续
        else:
            obvious = ambiguous_named_doc_new_topic(session, msg, title_hint=tip)
            if obvious:
                begin_document_onboarding(
                    session,
                    home,
                    message=msg,
                    title_hint=obvious,
                    force_new=True,
                    emit=emit,
                )
                tip = obvious or tip
    # 已定名，或定名刚成功（after_doc_name_confirm）：勿用旧 write 句再 force_new / 清 named / 重挂 doc_name
    already_named = bool(is_writing_document_named(session) or after_doc_name_confirm)
    if already_named:
        # 若标记尚未写上但调用方声明刚定名，以 title_hint 补齐，避免又走进起名卡
        if after_doc_name_confirm and not is_writing_document_named(session) and tip:
            confirm_writing_document_name(session, tip, home=home, message=msg or tip)
        confirmed = tip or str((session.freeform or {}).get("title") or "").strip()
        tip = confirmed
        pending = peek_pending_entry(session)
        residual = str(
            (session.freeform or {}).get("clarify_residual_message") or ""
        ).strip()
        original_write = str(pending.get("message") or msg or "").strip()
        if tip:
            # 保留原 write 句供 path 识别，但 title 以已确认名为准
            stash_pending_entry(
                session,
                message=original_write if original_write and original_write != tip else tip,
                title=tip,
            )
            pending = peek_pending_entry(session)
        scope_msg = residual or original_write
        # 路径识别优先残余，否则用原「创建蓝本/匹配」句；勿把纯定名短句当路径
        msg_for_path = residual or original_write
    else:
        # 定名/新开：优先 LLM（说法不固定）；正则只作无模型兜底
        explicit = ""
        residual_from_name = ""
        if invoke is not None and msg:
            from report_writing.chat.intent_context import pack_classifier_context
            from report_writing.chat.clarify_llm import (
                classify_explicit_doc_name_given_llm,
                extract_write_subject_title_llm,
            )

            ctx = pack_classifier_context(session)
            judged_name = classify_explicit_doc_name_given_llm(
                msg,
                dialogue_tail=list(ctx.get("dialogue_tail") or []),
                invoke=invoke,
            ) or {}
            if judged_name.get("has_explicit_name") and str(judged_name.get("title") or "").strip():
                explicit = str(judged_name.get("title") or "").strip()
                residual_from_name = str(judged_name.get("residual") or "").strip()
            if not tip and not explicit:
                tip = extract_write_subject_title_llm(msg, invoke=invoke) or tip
        if not explicit:
            explicit = explicit_doc_title_from_message(msg)
        if not tip and not explicit:
            tip = quick_title_from_write_message(msg)
        tip = tip or explicit
        scope = resolve_document_scope(session, msg, title_hint=tip, invoke=invoke)
        if scope.get("title") and not tip:
            tip = str(scope.get("title") or tip).strip() or tip
        elif scope.get("title") and not is_writing_document_named(session):
            tip = str(scope.get("title") or tip).strip() or tip
        # 新开一份：统一 onboarding，禁止静默定名
        strong_new = bool(scope.get("force_new"))
        if msg and strong_new:
            from report_writing.chat.session_documents import begin_document_onboarding

            begin_document_onboarding(
                session,
                home,
                message=msg,
                title_hint=tip,
                force_new=True,
                emit=emit,
            )
            if invoke is not None and msg:
                route_hit = _clear_entry_tool_from_message(msg, session=session, invoke=invoke)
                if route_hit == "begin_blueprint_wizard":
                    from report_writing.chat.session_documents import stash_focus_preferred_entry_path

                    stash_focus_preferred_entry_path(
                        session,
                        "template",
                        residual=residual_from_name or "",
                    )
                elif route_hit == "match_blueprints":
                    from report_writing.chat.session_documents import stash_focus_preferred_entry_path

                    stash_focus_preferred_entry_path(
                        session,
                        "match",
                        residual=residual_from_name or "",
                    )
                elif route_hit == "skip_blueprint":
                    from report_writing.chat.session_documents import stash_focus_preferred_entry_path

                    stash_focus_preferred_entry_path(
                        session,
                        "freeform",
                        residual=residual_from_name or "",
                    )
        elif msg or tip:
            stash_pending_entry(session, message=msg, title=tip)
        if residual_from_name:
            ff = {**(session.freeform or {})}
            ff["clarify_residual_message"] = residual_from_name[:500]
            session.freeform = ff
            from report_writing.chat.session_documents import stash_focus_preferred_entry_path

            if not str(focus_onboarding(session).get("preferred_entry_path") or "").strip():
                route_hit = _clear_entry_tool_from_message(
                    residual_from_name, session=session, invoke=invoke
                )
                if route_hit == "begin_blueprint_wizard":
                    stash_focus_preferred_entry_path(session, "template", residual=residual_from_name)
                elif route_hit == "match_blueprints":
                    stash_focus_preferred_entry_path(session, "match", residual=residual_from_name)
                elif route_hit == "skip_blueprint":
                    stash_focus_preferred_entry_path(session, "freeform", residual=residual_from_name)
        pending = peek_pending_entry(session)
        tip = tip or pending.get("title") or str((session.freeform or {}).get("title") or "").strip()
        scope_msg = msg
        msg_for_path = msg

    probe_msg = (msg or scope_msg or msg_for_path or "").strip()
    from report_writing.chat.session_documents import (
        focus_needs_entry_branch,
        focus_needs_freeform_mode,
        prepare_observed_onboarding,
    )

    # 生产路径禁止 apply_turn_plan。apply_plan=True 仅遗留链式 suggested 观察，不跑 JSON 计划。

    prepare_observed_onboarding(
        session,
        home,
        message=probe_msg,
        title_hint=tip,
        explicit=explicit,
        invoke=invoke,
    )
    if (
        after_doc_name_confirm
        and home is not None
        and is_writing_document_named(session)
    ):
        from report_writing.chat.session_documents import (
            focus_onboarding,
            mark_focus_entry_branch,
            mark_focus_entry_path,
            message_wants_own_template,
        )

        ob = focus_onboarding(session)
        pep = str(ob.get("preferred_entry_path") or "").strip()
        ep_done = str(ob.get("entry_path") or "").strip()
        tail = probe_msg or msg or tip
        if pep == "template" or message_wants_own_template(tail):
            mark_focus_entry_branch(session, "without_blueprint")
            mark_focus_entry_path(session, "template")
            observed = _observe_after_entry_marks(
                session, message=tail, title_hint=tip, invoke=invoke
            )
            dispatched = _maybe_dispatch_suggested_onboarding(
                home,
                session,
                apply_plan=apply_plan,
                message=tail,
                title=tip,
                invoke=invoke,
                emit=emit,
            )
            if dispatched is not None:
                return dispatched
            if apply_plan:
                from report_writing.chat.resource_actions import _begin_blueprint_wizard

                return _begin_blueprint_wizard(
                    session,
                    title_hint=tip,
                    home=home,
                    force_new=False,
                    user_message=tail,
                    invoke=invoke,
                )
            return observed
        if ep_done == "freeform" or pep == "freeform":
            from report_writing.chat.session_documents import mark_focus_freeform_mode

            mark_focus_entry_branch(session, "without_blueprint")
            mark_focus_entry_path(session, "freeform")
            mark_focus_freeform_mode(session, "auto_outline")
            from report_writing.chat.session_documents import (
                apply_working_title as _aw,
                focus_has_display_title as _has_title,
            )

            if focus_needs_doc_name(session) and not after_doc_name_confirm and not _has_title(
                session
            ):
                working = tip or quick_title_from_write_message(tail or msg)
                if working:
                    _aw(session, working, home=home, message=tail or msg)
            auto = _apply_auto_freeform_and_dispatch(
                home,
                session,
                message=tail,
                title_hint=tip,
                invoke=invoke,
                emit=emit,
            )
            if auto is not None:
                dispatched = _maybe_dispatch_suggested_onboarding(
                    home,
                    session,
                    apply_plan=apply_plan,
                    message=tail,
                    title=tip,
                    invoke=invoke,
                    emit=emit,
                )
                if dispatched is not None:
                    return dispatched
                return auto
    suggested = list((session.freeform or {}).get("suggested_next_tools") or [])
    # 无工作名、也未正式定名时才问起名；有工作名则直接问写作方式
    from report_writing.chat.session_documents import (
        apply_working_title as _aw_entry,
        focus_has_display_title as _has_display,
        message_confirms_write_document_intent as _write_intent,
    )

    if (
        not after_doc_name_confirm
        and not is_writing_document_named(session)
        and not _has_display(session)
    ):
        working = tip or quick_title_from_write_message(msg or probe_msg)
        if working:
            _aw_entry(session, working, home=home, message=msg or probe_msg)
    wiz_now = dict((session.freeform or {}).get("blueprint_wizard") or {})
    wiz_step_now = str(wiz_now.get("step") or "").strip()
    wizard_in_progress = bool(wiz_step_now)
    if (
        (not wizard_in_progress)
        and _template_path_declared(session, msg or probe_msg)
        and (
            not is_writing_document_named(session)
            or after_doc_name_confirm
            or focus_needs_entry_path(session)
        )
    ):
        from report_writing.chat.session_documents import (
            mark_focus_entry_branch,
            mark_focus_entry_path,
            stash_focus_preferred_entry_path,
        )

        tail_tpl = msg or probe_msg
        stash_focus_preferred_entry_path(session, "template", residual=tail_tpl)
        mark_focus_entry_branch(session, "without_blueprint")
        mark_focus_entry_path(session, "template")
        observed = _observe_after_entry_marks(
            session,
            message=tail_tpl,
            title_hint=tip or quick_title_from_write_message(tail_tpl),
            invoke=invoke,
        )
        dispatched = _maybe_dispatch_suggested_onboarding(
            home,
            session,
            apply_plan=apply_plan,
            message=tail_tpl,
            title=tip,
            invoke=invoke,
            emit=emit,
        )
        if dispatched is not None:
            return dispatched
        if apply_plan:
            return _start_template_wizard_now(
                session,
                home=home,
                message=tail_tpl,
                title_hint=tip or quick_title_from_write_message(tail_tpl),
                invoke=invoke,
            )
        return observed
    if (
        not after_doc_name_confirm
        and not is_writing_document_named(session)
        and not _has_display(session)
        and (
            focus_needs_doc_name(session)
            or _write_intent(msg or probe_msg, session=session, invoke=invoke)
        )
    ):
        return build_document_name_clarify(
            session,
            message=msg or probe_msg,
            title_hint=tip,
            invoke=invoke,
            home=home,
            emit=emit,
            follow_entry=False,
        )
    if focus_needs_entry_branch(session):
        return build_entry_branch_clarify(
            session, message=probe_msg, title_hint=tip
        )
    if focus_needs_freeform_mode(session):
        if _auto_freeform_outline_ready(session, probe_msg, invoke=invoke):
            auto = _apply_auto_freeform_and_dispatch(
                home,
                session,
                message=probe_msg,
                title_hint=tip,
                invoke=invoke,
                emit=emit,
            )
            if auto is not None:
                dispatched = _maybe_dispatch_suggested_onboarding(
                    home,
                    session,
                    apply_plan=apply_plan,
                    message=probe_msg,
                    title=tip,
                    invoke=invoke,
                    emit=emit,
                )
                if dispatched is not None:
                    return dispatched
                return auto
        return build_entry_freeform_mode_clarify(
            session,
            message=probe_msg,
            title_hint=tip,
            home=home,
            invoke=invoke,
            emit=emit,
            apply_plan=apply_plan,
        )
    if suggested:
        title_now = tip or str((session.freeform or {}).get("title") or "").strip()
        _clear_stale_doc_name_pending_if_safe(session, [])
        from report_writing.chat.session_documents import session_blueprint_already_selected

        if session_blueprint_already_selected(session):
            suggested = [
                t for t in suggested if str(t.get("tool") or "") != "match_blueprints"
            ]
        if not suggested and after_doc_name_confirm and session_blueprint_already_selected(
            session
        ):
            name_show = title_now or "—"
            return (
                f"好，文档「{name_show}」名称已确认。蓝本已选好，请在右侧文档窗口选章或开始分析。",
                [],
            )
        if apply_plan and home is not None:
            from report_writing.domain.models import ChatPhase

            if str(session.phase or "") == ChatPhase.FREEFORM_OUTLINE.value:
                suggested = [
                    t
                    for t in suggested
                    if str(t.get("tool") or "") != "submit_outline"
                ]
            hit = dispatch_suggested_onboarding_tools(
                home,
                session,
                message=probe_msg or msg_for_path or msg,
                title=title_now,
                invoke=invoke,
                emit=emit,
            )
            if hit is not None:
                d_reply, d_cards, _ = hit
                return d_reply, list(d_cards or [])
        return (
            f"好，文档「{title_now or '—'}」和写作方式都对齐了，我继续帮你往下准备。",
            [],
        )
    return ("好，入口信息齐了，我继续帮你往下走。", [])


def entry_path_from_routing_message(
    message: str,
    *,
    session: ChatSession | None = None,
    invoke=None,
) -> str:
    """入口路由（不含「生成目录」→ freeform，那由 preferred_entry_path_resolved 处理）。"""
    tool = _clear_entry_tool_from_message(message, session=session, invoke=invoke)
    if tool == "skip_blueprint":
        return "freeform"
    if tool == "begin_blueprint_wizard":
        return "template"
    if tool == "match_blueprints":
        return "match"
    return ""


def _dispatch_observed_entry_path(
    home: Path,
    session: ChatSession,
    entry_path: str,
    *,
    message: str,
    title: str,
    invoke=None,
    emit=None,
) -> tuple[str, list[Any]] | None:
    from report_writing.chat.tool_dispatch import execute_dispatch_tool

    ep = (entry_path or "").strip()
    tool_map = {
        "match": "match_blueprints",
        "template": "begin_blueprint_wizard",
        "freeform": "skip_blueprint",
    }
    tool = tool_map.get(ep, "")
    if not tool:
        return None
    from report_writing.chat.session_documents import mark_focus_entry_path

    mark_focus_entry_path(session, ep)
    args: dict[str, Any] = {
        "message": (message or "")[:500],
        **({"title_hint": title, "title": title} if title else {}),
    }
    if tool == "begin_blueprint_wizard":
        args["confirmed_create"] = True
        args["from_entry_path"] = True
    hit = execute_dispatch_tool(
        home,
        session,
        {"tool": tool, "args": args},
        message,
        emit=emit,
        invoke=invoke,
    )
    if hit is None:
        return None
    reply, cards, _ = hit
    return str(reply or ""), list(cards or [])


def try_apply_observed_onboarding(
    session: ChatSession,
    home: Path | None,
    *,
    message: str,
    title_hint: str = "",
    explicit: str = "",
    invoke=None,
    emit=None,
) -> tuple[str, list[Any]] | None:
    """兼容旧调用：仅 prepare 观察快照，不在 clarify 内 dispatch Tool。"""
    from report_writing.chat.session_documents import prepare_observed_onboarding

    prepare_observed_onboarding(
        session,
        home,
        message=message,
        title_hint=title_hint,
        explicit=explicit,
        invoke=invoke,
    )
    return None


def _clear_entry_tool_from_message(
    message: str,
    *,
    session: ChatSession | None = None,
    invoke=None,
) -> str:
    """本句是否已明确入口工具；有则返回 match_blueprints / begin_blueprint_wizard / skip_blueprint。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return ""
    # 「生成目录 / 起草大纲」不是创建蓝本；交给入口三选或 submit_outline
    try:
        from report_writing.chat.freeform_intent import utterance_mentions_generate_outline

        if utterance_mentions_generate_outline(text):
            return ""
    except Exception:
        pass
    try:
        from report_writing.chat.intent_context import pack_classifier_context
        from report_writing.chat.clarify_llm import classify_entry_routing_llm

        ctx = pack_classifier_context(session) if session is not None else {}
        hit = classify_entry_routing_llm(
            text,
            phase=str(ctx.get("phase") or (session.phase if session else "") or ""),
            session_goal_brief=str(ctx.get("session_goal_brief") or ""),
            dialogue_tail=list(ctx.get("dialogue_tail") or []),
            focus_document_brief=str(ctx.get("focus_document_brief") or ""),
            session_documents_brief=str(ctx.get("session_documents_brief") or ""),
            invoke=invoke,
        ) or {}
    except Exception:
        return ""
    intent = str(hit.get("intent") or "").strip()
    if intent in {"match_blueprints", "explicit_select"}:
        return "match_blueprints"
    if intent == "create_blueprint":
        return "begin_blueprint_wizard"
    if intent in {"skip_blueprint", "path_correction"}:
        return "skip_blueprint"
    return ""


def _consume_clarify_residual(
    session: ChatSession,
    reply: str,
    cards: list[ChatCard],
    *,
    invoke=None,
    home=None,
    emit=None,
) -> tuple[str, list[ChatCard]]:
    """若 freeform 中有残余路径，在已挂 entry 澄清后继续消歧（不跑 JSON 计划）。"""
    ff = dict(session.freeform or {})
    residual = str(ff.pop("clarify_residual_message", "") or "").strip()
    if not residual:
        return reply, cards
    session.freeform = ff
    follow = try_handle_pending_message(session, residual, invoke=invoke, home=home)
    if isinstance(follow, dict):
        if home is not None:
            reply2, cards2, _ = execute_clarify_spec(
                home, session, follow, message=residual, emit=emit, invoke=invoke
            )
            if reply2:
                reply = f"{reply}\n{reply2}".strip()
            return reply, list(cards or []) + list(cards2 or [])
        # 无 home 时：把 follow 暂存，由 ask_entry_path / 上层执行
        ff2 = {**(session.freeform or {})}
        ff2["clarify_follow_spec"] = follow
        session.freeform = ff2
        return reply, cards
    if isinstance(follow, str) and follow.strip():
        return f"{reply}\n{follow}".strip(), cards
    return reply, cards


def build_writing_decision_clarify(
    session: ChatSession,
    *,
    task_id: str = "",
    chapter_key: str = "",
) -> tuple[str, list[ChatCard]]:
    question = "本章你是想通过确认、退回重写，还是先跳过？"
    return begin_clarify(
        session,
        source="writing",
        question=question,
        options=[
            {"id": "approve", "label": "通过本章", "slots": {"writing_decision": "approve"}},
            {"id": "revise", "label": "退回重写", "slots": {"writing_decision": "revise"}},
            {"id": "skip", "label": "先跳过", "slots": {"writing_decision": "skip"}},
        ],
        candidate={
            "kind": "writing_decision",
            "name": "confirm_chapter",
            "payload": {
                "task_id": task_id or str(session.task_id or ""),
                "chapter_key": chapter_key,
            },
        },
        phase_hint="writing",
    )


def build_session_meta_clarify(
    session: ChatSession,
    *,
    current_title: str = "",
    current_category: str = "",
    message: str = "",
) -> tuple[str, list[ChatCard]]:
    """改名/定题听不清时挂起：保持现名或示例，也可直接发题目或「类型｜标题」。"""
    title = str(current_title or "").strip()
    category = str(current_category or "").strip()
    options: list[dict[str, Any]] = []
    if title:
        keep_line = f"{category}｜{title}" if category else title
        options.append(
            {
                "id": "keep_name",
                "label": f"保持「{title}」",
                "slots": {"meta_action": "keep", "message": keep_line},
            }
        )
    options.append(
        {
            "id": "example_name",
            "label": "例：述职报告｜2026年度个人述职",
            "slots": {
                "meta_action": "apply",
                "message": "述职报告｜2026年度个人述职",
            },
        }
    )
    return begin_clarify(
        session,
        source="session_meta",
        question="没听清新的文档名。点一项，或直接说新题目（口语或「类型｜标题」都行）。",
        options=options,
        candidate={
            "kind": "session_meta",
            "name": "set_session_meta",
            "payload": {"message": (message or "")[:500]},
        },
        phase_hint="session_meta",
    )


def build_freeform_next_clarify(
    session: ChatSession,
    *,
    phase: str,
    message: str = "",
    home: Path | None = None,
) -> tuple[str, list[ChatCard]]:
    """无蓝本大纲/要点阶段：分类落空时的有限选项澄清（按任务真实进度出选项）。"""
    from report_writing.domain.models import ChatPhase

    ph = (phase or "").strip()
    ff = dict(session.freeform or {})
    chapter_n = 0
    tid = (session.task_id or "").strip()
    if home is not None and tid:
        try:
            from report_writing.chat.helpers import _load_task

            task = _load_task(home, tid)
            if task and task.outline:
                chapter_n = len(list(task.outline.chapters or []))
        except Exception:
            chapter_n = 0
    outline_confirmed = bool(ff.get("outline_confirmed")) and not bool(
        ff.get("outline_pending_edit")
    )
    points_step = str(ff.get("points_step") or "").strip()

    if ph == ChatPhase.FREEFORM_OUTLINE.value or (
        chapter_n >= 1 and not outline_confirmed and ph in {"", ChatPhase.FREEFORM_ASK.value}
    ):
        if chapter_n >= 1:
            question = (
                f"当前目录已有 {chapter_n} 章。是确认大纲继续，还是重新生成目录？"
            )
            options = [
                {
                    "id": "confirm_outline",
                    "label": "确认大纲并继续",
                    "slots": {"freeform_intent": "confirm_outline"},
                },
                {
                    "id": "generate_outline",
                    "label": "重新生成目录",
                    "slots": {"freeform_intent": "generate_outline"},
                },
            ]
        else:
            question = "你是想让我帮你起草/调整目录，还是目录已经可以、直接确认？"
            options = [
                {
                    "id": "generate_outline",
                    "label": "帮我起草目录",
                    "slots": {"freeform_intent": "generate_outline"},
                },
                {
                    "id": "confirm_outline",
                    "label": "确认目录",
                    "slots": {"freeform_intent": "confirm_outline"},
                },
            ]
    elif ph == ChatPhase.FREEFORM_POINTS.value or (
        outline_confirmed and points_step not in {"generated", "skipped"}
    ):
        question = "要点这边你想怎么走：生成写作信息、跳过，还是直接开写？"
        options = [
            {
                "id": "generate_points",
                "label": "生成写作要点",
                "slots": {"freeform_intent": "generate_points"},
            },
            {
                "id": "skip_points",
                "label": "先跳过要点",
                "slots": {"freeform_intent": "skip_points"},
            },
            {
                "id": "start_writing",
                "label": "开始写作",
                "slots": {"freeform_intent": "start_writing"},
            },
        ]
    elif ph == ChatPhase.READY_TO_WRITE.value or points_step in {"generated", "skipped"}:
        question = "目录和要点都齐了。你想开写，还是先改写作要点？"
        options = [
            {
                "id": "start_writing",
                "label": "开始写作",
                "slots": {"freeform_intent": "start_writing"},
            },
            {
                "id": "generate_points",
                "label": "重新生成写作要点",
                "slots": {"freeform_intent": "generate_points"},
            },
        ]
    else:
        question = "你想继续配大纲，还是生成写作要点？"
        options = [
            {
                "id": "generate_outline",
                "label": "继续配目录",
                "slots": {"freeform_intent": "generate_outline"},
            },
            {
                "id": "generate_points",
                "label": "生成写作要点",
                "slots": {"freeform_intent": "generate_points"},
            },
        ]
    return begin_clarify(
        session,
        source="freeform",
        question=question,
        options=options,
        candidate={
            "kind": "freeform_intent",
            "name": "freeform_next",
            "payload": {"message": (message or "")[:500], "phase": ph},
        },
        phase_hint="freeform",
    )


def merge_study_message_with_scope(study_message: str, scope: str) -> str:
    base = (study_message or "").strip() or "分析指定章节"
    if scope == "self":
        if "只要本章" in base:
            return base
        return f"{base} 只要本章"
    if scope == "children":
        if "下面的所有子章节" in base:
            return base
        return f"{base} 下面的所有子章节"
    if scope == "whole":
        if "整章" in base:
            return base
        return f"{base} 整章"
    return base


def execute_clarify_spec(
    home,
    session: ChatSession,
    spec: dict[str, Any],
    *,
    message: str = "",
    emit=None,
    invoke=None,
) -> tuple[str, list[ChatCard], list[dict[str, Any]]]:
    """把消歧结果落到具体命令/动作。"""
    from report_writing.chat.helpers import _emit_process

    candidate = dict(spec.get("candidate") or {})
    slots = dict(spec.get("slots") or {})
    note = str(spec.get("note") or "").strip()
    kind = str(candidate.get("kind") or spec.get("source") or "").strip()
    name = str(
        candidate.get("name")
        or candidate.get("command")
        or candidate.get("tool")
        or spec.get("source")
        or ""
    ).strip()
    payload = ChatActionPayload.parse(candidate.get("payload"))

    reply = ""
    cards: list[ChatCard] = []

    if kind == "doc_name" or name == "confirm_document_name":
        action = str(slots.get("doc_name_action") or "").strip() or "apply"
        if action == "ask_again":
            reply, cards = build_document_name_clarify(
                session,
                message=str(payload.get("message") or message or ""),
                title_hint="",
                force_typed=True,
            )
            if note:
                reply = f"{note}\n{reply}".strip() if reply else note
            return reply, list(cards or []), []
        from report_writing.chat.session_documents import (
            confirm_writing_document_name,
            peek_pending_entry,
        )
        raw_title = str(
            slots.get("title") or slots.get("message") or message or payload.get("title") or ""
        ).strip()
        if "｜" in raw_title or "|" in raw_title:
            parts = re.split(r"[｜|]", raw_title, maxsplit=1)
            category = (parts[0] or "").strip()
            title = (parts[1] if len(parts) > 1 else "").strip() or raw_title
        else:
            category = ""
            title = raw_title
        if not title:
            reply, cards = build_document_name_clarify(
                session,
                message=str(payload.get("message") or ""),
                title_hint=str(payload.get("title") or ""),
            )
            return reply, list(cards or []), []
        _emit_process(emit, "正在记下文档名称…")
        pending = peek_pending_entry(session)
        cat = category or str((session.freeform or {}).get("category") or "").strip()
        residual = str(slots.get("residual_message") or "").strip()
        from report_writing.chat.intent_router import user_chose_skip_blueprint
        from report_writing.chat.session_documents import (
            _titles_conflict,
            clean_document_display_title,
            clear_writing_document_named,
            ensure_named_freeform_document,
            focus_document_key,
            focus_has_active_writing_context,
            is_writing_document_named,
            resolve_document_scope,
            session_documents_map,
            should_mint_new_document_slot,
        )

        prefix = f"好，文档就叫「{title}」。"
        if note:
            prefix = f"{note}\n{prefix}"
        msg_for_name = str(
            pending.get("message") or payload.get("message") or message or title
        )
        scope_invoke = (
            invoke
            if (
                is_writing_document_named(session)
                or focus_has_active_writing_context(session)
            )
            else None
        )
        # 第二份文档：原 write 句 / 标题冲突 → 新开槽，勿改旧文档章节
        scope = resolve_document_scope(
            session, msg_for_name, title_hint=title, invoke=scope_invoke
        )
        if (
            is_writing_document_named(session) or focus_has_active_writing_context(session)
        ) and (
            scope.get("need_clarify")
            or str(scope.get("action") or "").strip() == "ask"
        ):
            pending_hit = pending_document_scope_reply(session)
            if pending_hit is not None:
                reply, cards = pending_hit
            else:
                reply, cards = build_document_scope_clarify(
                    session, message=msg_for_name, topic_hint=title
                )
            return reply, list(cards or []), []
        docs = session_documents_map(session)
        focus_key = focus_document_key(session)
        entry = docs.get(focus_key) or {}
        entry_title = clean_document_display_title(str(entry.get("title") or ""))
        need_new_slot = bool(scope.get("force_new")) or (
            bool(entry_title)
            and _titles_conflict(entry_title, title)
            and should_mint_new_document_slot(
                session, entry_title, title, message=msg_for_name or title
            )
        )
        original_write = str(pending.get("message") or payload.get("message") or "").strip()
        clarify_residual = str(
            (session.freeform or {}).get("clarify_residual_message") or ""
        ).strip()
        # 定名后续跑：residual → 澄清残余 → pending 原话 → payload → 本轮 message
        # 勿在仍有完整首句时用纯标题截断，否则会丢「生成大纲」并重挂模版卡
        continuation = (
            residual
            or clarify_residual
            or original_write
            or str(payload.get("message") or "").strip()
            or str(message or "").strip()
        ).strip()
        if continuation == title:
            continuation = residual or clarify_residual or original_write or ""
        if need_new_slot:
            from report_writing.chat.session_documents import begin_document_onboarding

            begin_document_onboarding(
                session,
                home,
                message=continuation or original_write or msg_for_name,
                title_hint=title,
                force_new=True,
                emit=emit,
            )
        confirm_writing_document_name(
            session,
            title,
            home=home,
            category=cat,
            message=continuation or original_write or title,
        )
        path_ep = doc_name_path_from_residual(residual)
        if path_ep:
            from report_writing.chat.session_documents import stash_focus_preferred_entry_path

            stash_focus_preferred_entry_path(session, path_ep, residual=residual)
        # 定名刚成功：走入口引导（after_doc_name_confirm），禁止再用原「写一份…」句
        # 跑 apply_turn_plan / force_new，否则会清掉 named、或 focus_document 切回上一份文档。
        cards: list[ChatCard] = []
        events: list[dict[str, Any]] = []
        reply2, cards = build_entry_path_clarify(
            session,
            message=continuation,
            title_hint=title,
            invoke=invoke,
            home=home,
            emit=emit,
            after_doc_name_confirm=True,
            apply_plan=False,
        )
        # 若下游又挂回起名卡，禁止再断言「好，文档就叫」造成自相矛盾
        name_ask_again = any(
            str((getattr(c, "payload", None) or {}).get("source") or "") == "doc_name"
            for c in (cards or [])
        ) or ("先给这份文档起个名字" in str(reply2 or ""))
        if name_ask_again:
            reply = str(reply2 or "").strip() or prefix
        else:
            reply = f"{prefix}\n{reply2}".strip() if reply2 else prefix
        return reply, list(cards or []), events

    if kind == "session_meta":
        action = str(slots.get("meta_action") or "").strip()
        if action == "keep":
            return "好，文档名保持不变。需要改名时再说「类型｜标题」就行。", [], []
        meta_msg = str(slots.get("message") or message or payload.get("message") or "").strip()
        if not meta_msg:
            return "跟我说一下文档叫什么、写哪一类就行（口语或「类型｜标题」都可以）。", [], []
        from report_writing.chat.session_goal_actions import run_session_goal_action

        _emit_process(emit, "正在更新文档名称…")
        reply, cards = run_session_goal_action(
            home,
            session,
            "set_session_meta",
            {"message": meta_msg},
            invoke=invoke,
            emit=emit,
        )
        if note:
            reply = f"{note}\n{reply}".strip() if reply else note
        return reply, list(cards or []), []

    if kind in {"wizard_command", ""} and name in {
        "re_study_chapters",
        "go_study",
        "confirm_outline",
        "re_extract_outline",
        "go_outline_confirm",
        "abandon_wizard",
        "mark_upload_done",
        "re_upload_sample",
    }:
        if slots.get("wizard_defer"):
            return (
                "好，你先在文档窗口核对目录；满意后说「确认全部目录」或点卡片确认，再分析不迟。",
                [],
                [],
            )
        from report_writing.chat.resource_actions import execute_wizard_command

        study_message = str(payload.get("study_message") or message or "").strip()
        scope = str(slots.get("study_scope") or "").strip()
        study_method = str(slots.get("study_method") or payload.get("study_method") or "").strip()
        study_text = str(payload.get("study_text") or slots.get("study_text") or "").strip()
        _emit_process(emit, "好，按你刚确认的来…")
        reply, cards = execute_wizard_command(
            home,
            session,
            name,
            study_message=study_message,
            chapter_sequences=list(payload.get("chapter_sequences") or []),
            study_scope=scope,
            study_method=study_method,
            study_text=study_text,
            chapter_keys=[
                str(k).strip() for k in list(payload.get("chapter_keys") or []) if str(k).strip()
            ],
            invoke=invoke,
        )
    elif kind == "write_command":
        from report_writing.chat.write_chapter_intent import try_write_chapters_from_message

        scope = str(slots.get("write_scope") or "").strip()
        msg = str(payload.get("write_message") or message or "").strip()
        _emit_process(emit, "好，按你刚确认的范围开写…")
        hit = try_write_chapters_from_message(
            home,
            session,
            msg,
            emit=emit,
            invoke=invoke,
            write_scope=scope,
            chapter_sequences=list(payload.get("chapter_sequences") or []),
            chapter_key=str(payload.get("chapter_key") or "").strip(),
            chapter_key_hint=str(
                payload.get("chapter_key_hint") or payload.get("chapter_key") or ""
            ).strip(),
            chapter_keys=[
                str(k).strip()
                for k in list(payload.get("chapter_keys") or [])
                if str(k).strip()
            ],
            write_analyzed_only=bool(payload.get("write_analyzed_only")),
        )
        if hit is not None:
            reply, cards, _events = hit
        else:
            # 澄清后仍对不上：再 hang 一轮选章，勿硬断
            from report_writing.chat.action_slots import build_action_slot_clarify
            from report_writing.chat.helpers import _load_task

            bid = str(
                payload.get("blueprint_id")
                or session.blueprint_id
                or session.task_id
                or ""
            ).strip()
            task = _load_task(home, bid) if bid else None
            reply, cards = build_action_slot_clarify(
                session,
                "select_write_chapters",
                {"blueprint_id": bid, "task_id": bid},
                ["chapter_sequences"],
                task=task,
                message=message,
            )
    elif kind in {"writing_tool", "writing_decision"}:
        decision = str(slots.get("writing_decision") or name or "").strip()
        # approve / revise / skip → confirm_chapter
        from report_writing.domain.models import HumanDecision
        from report_writing.chat_orchestrator import _action_confirm_chapter

        mapping = {
            "approve": HumanDecision.APPROVE.value,
            "revise": HumanDecision.RETURN.value,
            "skip": HumanDecision.SKIP.value,
            "edit": HumanDecision.EDIT.value,
        }
        human = mapping.get(decision, HumanDecision.APPROVE.value)
        _emit_process(emit, "好，按你的确认处理本章…")
        reply, cards, _events = _action_confirm_chapter(
            home,
            session,
            {
                **payload,
                "decision": human,
                "task_id": payload.get("task_id") or session.task_id,
            },
        )
    elif kind == "intent_plan":
        confirm = str(slots.get("confirm_plan") or "").strip().lower()
        if confirm in {"no", "rephrase", "false", "0"}:
            reply = "好的，你再用自己的话说明一下想先做哪一步就行。"
            cards = []
        else:
            from report_writing.chat.tool_dispatch import execute_intent_plan

            plan = list(candidate.get("plan") or [])
            plan_msg = str(payload.get("message") or message or "").strip()
            _emit_process(emit, "好，按你确认的顺序依次办理…")
            hit = execute_intent_plan(
                home,
                session,
                plan,
                plan_msg,
                emit=emit,
                invoke=invoke,
            )
            if hit is None:
                reply = "好的，你再说一下想怎么做就行。"
            else:
                reply, cards, _events = hit
    elif kind == "control_tool":
        source = str(spec.get("source") or "")
        if source != "action_slots":
            enriched = enrich_control_option_slots(
                str(spec.get("option_id") or ""),
                str(message or ""),
                slots,
            )
            slots = enriched
        tool = str(slots.get("tool") or name or "").strip()
        args = slots.get("args") if isinstance(slots.get("args"), dict) else {}
        args = dict(args)
        # action_slots：把章节/要素等槽位合并进 args，并带上挂起时的 payload
        if source == "action_slots" or any(
            k in slots
            for k in (
                "chapter_key",
                "element_id",
                "blueprint_id",
                "chapter_sequences",
                "pack_id",
                "slot_no",
            )
        ):
            merged = {**payload, **args}
            for key in (
                "chapter_key",
                "chapter_key_hint",
                "element_id",
                "element_name",
                "blueprint_id",
                "chapter_sequences",
                "sequences",
                "chapter_keys",
                "pack_id",
                "materials_pack_id",
                "slot_no",
                "no",
                "name",
                "pack_name",
                "slot_name",
                "task_id",
            ):
                if slots.get(key) not in (None, "", [], {}):
                    merged[key] = slots[key]
            args = merged
            if not tool:
                tool = str(name or "").strip()
        if tool in {"ask_intent", "ask_entry_path", ""}:
            path = str(slots.get("entry_path") or "").strip()
            if path:
                tool = _ENTRY_TOOL_BY_PATH.get(path, path)
            else:
                reply = "好的，你再说一下想做什么就行。"
                tool = ""
        if tool:
            from report_writing.chat.tool_dispatch import execute_dispatch_tool
            from report_writing.chat.session_documents import peek_pending_entry, pop_pending_entry

            _emit_process(emit, "好，按你刚确认的来…")
            # 优先用澄清挂起时记下的原话（如「写一份述职报告」），不要用选项文案当写作目标。
            # 但若本轮已是明确路径/元信息指令，用本轮话，避免旧旁白把 skip 带偏。
            current = str(message or "").strip()
            stored = str(payload.get("message") or "").strip()
            if tool == "skip_blueprint" or message_skips_clarify(
                current, source="entry", invoke=invoke, session=session
            ):
                dispatch_msg = current or stored
            else:
                dispatch_msg = stored or current
            dispatch_args = dict(args)
            # 用户已点选入口路径：创建蓝本不再二次门禁；带上挂起的标题
            if tool == "begin_blueprint_wizard":
                pending = pop_pending_entry(session)
                dispatch_args["confirmed_create"] = True
                dispatch_args["from_entry_path"] = True
                if pending.get("title") and not dispatch_args.get("title_hint"):
                    dispatch_args["title_hint"] = pending["title"]
                if pending.get("message") and not dispatch_args.get("user_message"):
                    dispatch_args["user_message"] = pending["message"]
                    if not dispatch_msg:
                        dispatch_msg = pending["message"]
            elif tool == "skip_blueprint":
                pending = peek_pending_entry(session)
                if pending.get("title") and not dispatch_args.get("title_hint"):
                    dispatch_args["title_hint"] = pending["title"]
                if pending.get("message") and not dispatch_args.get("message"):
                    dispatch_args["message"] = pending["message"]
            hit = execute_dispatch_tool(
                home,
                session,
                {"tool": tool, "args": dispatch_args},
                dispatch_msg,
                emit=emit,
                invoke=invoke,
            )
            if hit is None:
                reply = "好的，你再说一下想怎么做就行。"
            else:
                reply, cards, _events = hit
    elif kind == "document_scope" or name == "document_scope":
        from report_writing.chat.session_documents import (
            begin_document_onboarding,
            confirm_writing_document_name,
            peek_pending_entry,
            stash_pending_entry,
            _topic_matches_current_doc_title,
        )
        from report_writing.chat.session_goal_actions import run_session_goal_action

        action = str(slots.get("document_scope_action") or "").strip()
        pending = peek_pending_entry(session)
        pending_msg = (
            pending.get("message")
            or str(payload.get("message") or message or "").strip()
        )
        topic = str(
            slots.get("title")
            or payload.get("title")
            or pending.get("title")
            or ""
        ).strip()
        # 先清掉 document_scope 卡，避免下游无新卡时旧澄清残留
        clear_pending(session)
        _emit_process(emit, "好，按你确认的文档范围来…")
        if action == "force_new":
            begin_document_onboarding(
                session,
                home,
                message=pending_msg,
                title_hint=topic,
                force_new=True,
                emit=emit,
            )
            # 点选「新开一份 · 标题」即已选定新文档名，勿再挂范围卡/勿用旧写句 apply_plan
            if topic:
                confirm_writing_document_name(
                    session, topic, home=home, message=pending_msg or topic
                )
            stash_pending_entry(session, message=pending_msg, title=topic)
            reply2, cards, _extra = continue_after_new_document(
                home,
                session,
                message=pending_msg,
                title=topic,
                invoke=invoke,
                emit=emit,
            )
            prefix = f"好，新开一份「{topic}」。" if topic else "好，新开一份文档。"
            reply = f"{prefix}\n{reply2}".strip() if reply2 else prefix
        elif action == "rename":
            if topic:
                reply, cards = run_session_goal_action(
                    home,
                    session,
                    "set_session_meta",
                    {"message": f"改名叫{topic}", "title": topic},
                    invoke=invoke,
                    emit=emit,
                )
            else:
                reply, cards = build_document_name_clarify(
                    session,
                    message=pending_msg,
                    title_hint="",
                    force_typed=True,
                    invoke=invoke,
                    home=home,
                    emit=emit,
                    follow_entry=False,
                )
        elif action == "rewrite_current":
            # 整份重写当前：保留 entry_path / 定名，标记待勾章后再写，禁止直接开写
            ff = dict(session.freeform or {})
            ff["document_scope_resolved"] = "rewrite_current"
            ff["rewrite_current_pending"] = True
            if pending_msg:
                ff["clarify_residual_message"] = pending_msg[:500]
            session.freeform = ff
            cur = str(
                payload.get("current_title")
                or (session.freeform or {}).get("title")
                or ""
            ).strip()
            prefix = (
                f"好，按当前「{cur}」整份重写。"
                if cur
                else "好，按当前这份文档整份重写。"
            )
            # 已有入口则挂勾章/下一写作步；缺入口仍走选路
            reply2, cards = build_entry_path_clarify(
                session,
                message=pending_msg,
                title_hint=cur or topic,
                invoke=invoke,
                home=home,
                emit=emit,
                after_doc_name_confirm=True,
                apply_plan=False,
            )
            if not cards:
                reply3, cards = build_freeform_next_clarify(
                    session,
                    message=pending_msg or "整份重写",
                    home=home,
                    phase=str(session.phase or ""),
                )
                if reply3:
                    reply2 = f"{reply2}\n{reply3}".strip() if reply2 else reply3
            if not cards:
                # 入口已齐：提醒勾章，勿静默 start_writing
                reply2 = (
                    f"{reply2}\n请在右侧文档窗勾选要重写的章节，或告诉我写哪些章。"
                    if reply2
                    else "请在右侧文档窗勾选要重写的章节，或告诉我写哪些章。"
                )
            ff2 = dict(session.freeform or {})
            ff2.pop("document_scope_resolved", None)
            session.freeform = ff2
            reply = f"{prefix}\n{reply2}".strip() if reply2 else prefix
        elif action == "continue":
            ff = dict(session.freeform or {})
            if pending_msg:
                ff["clarify_residual_message"] = pending_msg[:500]
                ff["writing_topic_residual"] = (topic or pending_msg)[:200]
            ff["document_scope_resolved"] = "continue"
            session.freeform = ff
            cur = str(
                payload.get("current_title")
                or (session.freeform or {}).get("title")
                or ""
            ).strip()
            prefix = (
                f"好，继续写「{cur}」。"
                if cur
                else "好，继续写当前这份文档。"
            )
            if topic and not _topic_matches_current_doc_title(session, topic):
                prefix = f"{prefix}我会把「{topic}」当作本轮写作题材，不改文档名。"
            reply2, cards = build_entry_path_clarify(
                session,
                message=pending_msg,
                title_hint=cur or topic,
                invoke=invoke,
                home=home,
                emit=emit,
                after_doc_name_confirm=True,
                apply_plan=False,
            )
            if not cards:
                reply3, cards = build_freeform_next_clarify(
                    session,
                    message=pending_msg,
                    home=home,
                    phase=str(session.phase or ""),
                )
                if reply3:
                    reply2 = f"{reply2}\n{reply3}".strip() if reply2 else reply3
            ff2 = dict(session.freeform or {})
            ff2.pop("document_scope_resolved", None)
            session.freeform = ff2
            reply = f"{prefix}\n{reply2}".strip() if reply2 else prefix
        else:
            reply, cards = build_document_scope_clarify(
                session, message=pending_msg, topic_hint=topic
            )
    elif kind == "entry_branch" or name == "entry_branch":
        from report_writing.chat.session_documents import (
            mark_focus_entry_branch,
            mark_focus_entry_path,
            pop_pending_entry,
        )

        branch = str(slots.get("entry_branch") or "").strip()
        pending = pop_pending_entry(session)
        pending_msg = (
            pending.get("message")
            or str(payload.get("message") or message or "").strip()
        )
        pending_title = pending.get("title") or str(payload.get("title") or "").strip()
        _emit_process(emit, "好，按你选的主路来…")
        if branch == "with_blueprint":
            mark_focus_entry_branch(session, "with_blueprint")
            mark_focus_entry_path(session, "match")
            reply, cards = _observe_after_entry_marks(
                session,
                message=pending_msg,
                title_hint=pending_title,
                invoke=invoke,
            )
        elif branch == "without_blueprint":
            from report_writing.chat.session_documents import (
                focus_onboarding,
                mark_focus_freeform_mode,
            )

            mark_focus_entry_branch(session, "without_blueprint")
            pep = str(focus_onboarding(session).get("preferred_entry_path") or "").strip()
            if pep == "freeform":
                mark_focus_entry_path(session, "freeform")
                mark_focus_freeform_mode(session, "auto_outline")
                auto = _apply_auto_freeform_and_dispatch(
                    home,
                    session,
                    message=pending_msg,
                    title_hint=pending_title,
                    invoke=invoke,
                    emit=emit,
                )
                if auto is not None:
                    reply, cards = auto
                    return reply, list(cards or []), []
                reply, cards = ("好，按自动大纲来写。", [])
            elif pep == "template":
                mark_focus_entry_path(session, "template")
                reply, cards = _observe_after_entry_marks(
                    session,
                    message=pending_msg,
                    title_hint=pending_title,
                    invoke=invoke,
                )
            else:
                reply, cards = build_entry_freeform_mode_clarify(
                    session,
                    message=pending_msg,
                    title_hint=pending_title,
                    home=home,
                    invoke=invoke,
                    emit=emit,
                )
        else:
            reply = "好的，你再说一下选有蓝本还是无蓝本。"
    elif kind == "entry_freeform_mode" or name == "entry_freeform_mode":
        from report_writing.chat.session_documents import mark_focus_entry_path, pop_pending_entry

        enriched = enrich_control_option_slots(
            str(spec.get("option_id") or ""),
            str(message or ""),
            slots,
        )
        path = str(enriched.get("entry_path") or "").strip()
        pending = pop_pending_entry(session)
        pending_msg = (
            pending.get("message")
            or str(payload.get("message") or message or "").strip()
        )
        pending_title = pending.get("title") or str(payload.get("title") or "").strip()
        _emit_process(emit, "好，按你选的无蓝本方式…")
        if path in {"create_or_freeform", "create_blueprint", "template"}:
            from report_writing.chat.session_documents import mark_focus_entry_branch

            mark_focus_entry_branch(session, "without_blueprint")
            mark_focus_entry_path(session, "template")
            reply, cards = _observe_after_entry_marks(
                session,
                message=pending_msg,
                title_hint=pending_title,
                invoke=invoke,
            )
        elif path in {"freeform", "skip_blueprint"}:
            freeform_mode = str(enriched.get("freeform_mode") or slots.get("freeform_mode") or "").strip()
            if freeform_mode == "auto_outline" or str(spec.get("option_id") or "") == "freeform":
                from report_writing.chat.session_documents import (
                    focus_needs_doc_name,
                    mark_focus_freeform_mode,
                    stash_focus_preferred_entry_path,
                )

                stash_focus_preferred_entry_path(session, "freeform", residual=pending_msg)
                mark_focus_entry_path(session, "freeform")
                mark_focus_freeform_mode(session, "auto_outline")
                if focus_needs_doc_name(session):
                    reply, cards = build_document_name_clarify(
                        session,
                        message=pending_msg,
                        title_hint=pending_title,
                        invoke=invoke,
                        home=home,
                        emit=emit,
                        follow_entry=False,
                    )
                    return reply, list(cards or []), []
                auto = _apply_auto_freeform_and_dispatch(
                    home,
                    session,
                    message=pending_msg,
                    title_hint=pending_title,
                    invoke=invoke,
                    emit=emit,
                )
                if auto is not None:
                    reply, cards = auto
                else:
                    reply, cards = _observe_after_entry_marks(
                        session,
                        message=pending_msg,
                        title_hint=pending_title,
                        invoke=invoke,
                    )
            else:
                mark_focus_entry_path(session, "freeform")
                reply, cards = _observe_after_entry_marks(
                    session,
                    message=pending_msg,
                    title_hint=pending_title,
                    invoke=invoke,
                )
        else:
            reply = "好的，你再说一下用模版还是自动大纲。"
    elif kind == "entry_intent" and name:
        from report_writing.chat.session_documents import pop_pending_entry

        enriched = enrich_control_option_slots(
            str(spec.get("option_id") or ""),
            str(message or ""),
            slots,
        )
        path = str(enriched.get("entry_path") or name or "").strip()
        pending = pop_pending_entry(session)
        pending_msg = (
            pending.get("message")
            or str(payload.get("message") or message or "").strip()
        )
        pending_title = pending.get("title") or str(payload.get("title") or "").strip()
        _emit_process(emit, "好，按你选的路径来…")
        from report_writing.chat.session_documents import mark_focus_entry_path

        if path in {"match_blueprint", "match_blueprints"}:
            from report_writing.chat.session_documents import mark_focus_entry_branch

            mark_focus_entry_branch(session, "with_blueprint")
            mark_focus_entry_path(session, "match")
            reply, cards = _observe_after_entry_marks(
                session,
                message=pending_msg,
                title_hint=pending_title,
                invoke=invoke,
            )
        elif path in {"create_or_freeform", "create_blueprint", "template"}:
            from report_writing.chat.session_documents import mark_focus_entry_branch

            mark_focus_entry_branch(session, "without_blueprint")
            mark_focus_entry_path(session, "template")
            reply, cards = _observe_after_entry_marks(
                session,
                message=pending_msg,
                title_hint=pending_title,
                invoke=invoke,
            )
        elif path in {"freeform", "skip_blueprint"}:
            from report_writing.chat.session_documents import mark_focus_entry_branch

            mark_focus_entry_branch(session, "without_blueprint")
            mark_focus_entry_path(session, "freeform")
            reply, cards = _observe_after_entry_marks(
                session,
                message=pending_msg,
                title_hint=pending_title,
                invoke=invoke,
            )
        else:
            reply = "好的，你再说一下想怎么走就行。"
    elif kind == "freeform_intent":
        intent = str(slots.get("freeform_intent") or name or "").strip()
        phase = str(payload.get("phase") or session.phase or "").strip()
        tid = str(session.task_id or "").strip()
        from report_writing.chat.freeform_route import _apply_single_freeform_intent
        from report_writing.chat.session_documents import peek_pending_entry

        _emit_process(emit, "好，按你刚确认的来…")
        # 重新生成目录：带上用户原话/待办消息，勿只传 intent id
        pending_msg = str(peek_pending_entry(session).get("message") or "").strip()
        oral = str(payload.get("message") or message or pending_msg or "").strip()
        if oral in {"generate_outline", "generate_points", "confirm_outline", intent}:
            oral = pending_msg or oral
        hit = _apply_single_freeform_intent(
            home,
            session,
            oral or intent,
            intent,
            phase=phase,
            tid=tid,
            invoke=invoke,
            llm=invoke,
            emit=emit,
        )
        if hit is None:
            reply = "好的，你再说一下想怎么处理大纲/要点就行。"
        else:
            reply, cards, _events = hit
    else:
        reply = "好的，我记下了。"

    if note:
        reply = f"{note}\n{reply}".strip() if reply else note
    return reply or "好的。", list(cards or []), []
