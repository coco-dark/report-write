"""Chat orchestration helpers."""

from __future__ import annotations

import copy
import json
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from report_writing.services.chat_session import load_or_create, save_session
from report_writing.controller import (
    confirm_outline,
    ensure_task,
    outline_from_pasted_text,
    save_outline,
    save_spec_meta,
)
from report_writing.domain.models import (
    ChapterStatus,
    ChatCard,
    ChatMessage,
    ChatPhase,
    ChatRequest,
    ChatResponse,
    ChatSession,
    HumanDecision,
    OutlineNode,
    WritingTask,
)
from report_writing.services.robots import (
    DEFAULT_CHAT_PROMPT,
    get_robot,
    match_blueprints_with_agent,
    resolve_prompt,
    search_blueprints,
)
from report_writing.services.store import TaskStore

_log = logging.getLogger("report_writing.chat")

InvokeFn = Callable[[str, str], str]
EmitFn = Callable[[dict[str, Any]], None]
StreamModelFn = Callable[[str, str], Iterable[str]]

_log = logging.getLogger(__name__)
InvokeFn = Callable[[str, str], str]
EmitFn = Callable[[dict[str, Any]], None]
StreamModelFn = Callable[[str, str], Iterable[str]]
_VAGUE_FREEFORM_META_RE = re.compile(
    r"^(直接写|开始写|开写|写报告|写一份|继续写|继续|好的?|是的?|对的?|没错|确认|可以|行|嗯+|ok|okay|yes|y)$",
    re.I,
)
# 路径意图 / 模型旁白 / 改名指令词，不能当类型或标题（结构校验，非意图路由）
_VAGUE_FREEFORM_FIELD_MARKERS = (
    "不用蓝本",
    "跳过蓝本",
    "直接搭目录",
    "直接写",
    "用户希望",
    "尚未说明",
    "具体写作题目",
    "还没定",
    "请先发",
    "类型｜标题",
    "类型|标题",
    "改个名字",
    "改一下名",
    "改名为",
    "改名叫",
    "重命名",
    "标题改成",
    "标题改为",
    "名字改成",
    "名字改为",
    "名字改一下",
    "文档名改成",
    "自动生成章节目录",
    "生成章节目录",
    "自动生成目录",
    "生成目录",
    "生成大纲",
    "大章节",
    "个章节",
    "就可以",
)


def is_vague_freeform_meta(text: str, *, field: bool = False) -> bool:
    """无蓝本路径：不能把「直接写」等意图词当成文档标题。"""
    t = (text or "").strip()
    if not t:
        return True
    if _VAGUE_FREEFORM_META_RE.match(t):
        return True
    if field:
        if len(t) < 2:
            return True
        if "占位" in t or t in {"大纲", "目录", "章节目录"}:
            return True
        # 模型旁白/路径说明：过长或含意图词，不当标题
        if any(m in t for m in _VAGUE_FREEFORM_FIELD_MARKERS):
            return True
        if len(t) > 48 or "。" in t or t.count("，") >= 2:
            return True
        return False
    if len(t) < 6 and "｜" not in t and "|" not in t and " / " not in t:
        return True
    return False


WRITING_PACE_BATCH = "batch"
WRITING_PACE_STEP = "step"
_ACTION_USER_LABELS = {
    "select_blueprint": "选用蓝本",
    "skip_blueprint": "跳过蓝本，自己定大纲",
    "begin_blueprint_wizard": "无蓝本 · 依据模版编写",
    "list_blueprints": "看看有哪些蓝本",
    "match_blueprints": "寻找合适的蓝本",
    "write_analyzed_only": "先写已分析的章节",
    "select_write_chapters": "确认要写的章节",
    "start_writing": "开始写作",
    "check_writing_supplements": "开写前待补充检查",
    "skip_writing_supplement_check": "先这样开写",
    "dismiss_writing_supplement": "忽略待补充项",
    "resume": "从蓝本页回来继续",
    "confirm_chapter": "确认本章",
    "confirm_all_chapters": "统一确认章节",
    "configure_chapter": "保存章节配置",
    "add_chapter_element": "添加章节要素",
    "collect_to_element_library": "收入要素库",
    "associate_elements": "关联要素",
    "dissociate_chapter_element": "取消要素关联",
    "remove_document_element": "删除文档要素",
    "open_element_library": "查看要素",
    "open_blueprint_library": "查看蓝本库",
    "retry_chapter": "重试本章写作",
    "retry_search_chapter": "仅重试检索",
    "submit_outline": "确认大纲",
    "save_freeform_as_blueprint": "收录到蓝本库",
    "generate_freeform_points": "帮我生成写作要点",
    "skip_freeform_points": "先不配要点，直接开写",
    "handoff": "去完善蓝本",
    "pause_writing": "暂停写作",
    "resource_check": "检查机器人配置",
    "resource_choice": "资源配置选择",
    "blueprint_wizard": "蓝本创建向导",
    "template_wizard": "模版配置向导",
    "materials_pack_skip": "不用资料包",
    "materials_pack_use": "换绑资料包",
    "materials_pack_create": "新建资料包",
    "materials_pack_confirm_library": "确认资料包入库",
    "materials_pack_list": "查看全部资料包",
    "materials_pack_view": "查看资料包配置",
    "materials_pack_unbind": "解绑资料包",
    "materials_pack_add_slot": "添加资料包槽位",
    "materials_pack_remove_slot": "删除资料包槽位",
    "materials_pack_update_slot": "修改资料包槽位",
    "materials_pack_rename": "重命名资料包",
}
_WIZARD_STEP_USER_LABELS = {
    "meta": "填写蓝本名称",
    "upload": "上传样板文件",
    "upload_done": "完成上传样板",
    "outline": "提取章节目录",
    "outline_done": "完成目录提取",
    "outline_confirm": "确认章节目录",
    "study": "开始分析要点",
    "study_done": "完成分析要点",
    "done": "完成分析要点",
}
_WIZARD_COMMAND_USER_LABELS = {
    "re_extract_outline": "重新提取目录",
    "re_upload_sample": "重新上传样板",
    "go_outline_confirm": "进入确认目录",
    "go_study": "进入分析要点",
    "re_study_chapters": "指定章节重新分析要点",
    "confirm_outline": "确认目录",
    "submit_meta": "提交蓝本名称",
    "mark_upload_done": "完成上传样板",
}


def blueprint_wizard_user_label(payload: dict | None) -> str:
    data = payload or {}
    command = str(data.get("wizard_command") or "").strip()
    if command and command in _WIZARD_COMMAND_USER_LABELS:
        return _WIZARD_COMMAND_USER_LABELS[command]
    step = str(data.get("step") or "").strip()
    title = str(data.get("title") or "").strip()
    if step == "meta" and title:
        return f"创建蓝本「{title}」"
    if step and step in _WIZARD_STEP_USER_LABELS:
        return _WIZARD_STEP_USER_LABELS[step]
    return _ACTION_USER_LABELS["blueprint_wizard"]


_TAG_RE = re.compile(r"<[^>]+>")
_STEP_LABEL = {
    "upload": "上传",
    "outline": "目录",
    "study": "分析",
    "search": "检索",
    "write": "写作",
    "check": "检查",
}
_CHAPTER_STATUS_LABEL = {
    "pending": "未开写",
    "searching": "检索中",
    "writing": "写作中",
    "checking": "检查中",
    "awaiting_confirm": "已写作",
    "confirmed": "已写作",
    "needs_human": "已写作",
    "skipped": "已跳过",
}
_TASK_STATUS_LABEL = {
    "studying": "分析中",
    "running": "进行中",
    "awaiting_confirm": "已写作",
    "completed": "已完成",
}


def _user_action_label(action: str, payload: dict | None = None) -> str:
    key = (action or "").strip()
    label = _ACTION_USER_LABELS.get(key, "继续")
    payload = payload or {}
    if key == "select_blueprint":
        title = str(payload.get("title") or payload.get("blueprint_title") or "").strip()
        return f"选用「{title}」" if title else label
    if key == "select_write_chapters":
        keys = payload.get("chapter_keys") or payload.get("write_chapter_keys") or []
        n = len(keys) if isinstance(keys, list) else 0
        return f"确认要写的章节（{n} 章）" if n else label
    if key == "start_writing":
        pace = str(payload.get("writing_pace") or "").strip().lower()
        if pace in {"batch", "all", "full"}:
            return "全部写完再确认"
        if pace in {"step", "chapter", "one"}:
            return "写一章确认一章"
        return label
    if key == "submit_outline":
        if bool(payload.get("regenerate")):
            rev = str(payload.get("revision_instructions") or "").strip()
            return f"重新生成章节目录：{rev[:40]}" if rev else "重新生成章节目录"
        if bool(payload.get("reopen_edit")):
            return "重新编辑目录"
        if bool(payload.get("confirm")):
            return "确认大纲"
        return label
    if key == "blueprint_wizard":
        return blueprint_wizard_user_label(payload)
    return label


def _emit(emit: EmitFn | None, event: dict[str, Any]) -> None:
    if emit is not None:
        emit(event)


def _emit_tokens(emit: EmitFn | None, text: str) -> str:
    """下发可见回复。整段一次推送，避免长文逐字 SSE 拖慢 done。"""
    value = text or ""
    if emit is not None and value:
        emit({"type": "token", "text": value})
    return value


def _emit_process(emit: EmitFn | None, message: str, **extra: Any) -> None:
    if emit is None or not message:
        return
    payload = {"type": "process", "message": message}
    payload.update(extra)
    emit(payload)


def emit_search_element_lines(emit: EmitFn | None, result: dict[str, Any] | None) -> None:
    """检索完成后把要素/证据摘要刷到过程条，方便用户看到用了哪些资料。"""
    payload = result if isinstance(result, dict) else {}
    note = str(payload.get("note") or "").strip()
    cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
    slot_missing = payload.get("slot_missing")
    if isinstance(slot_missing, list) and slot_missing:
        gap_txt = "、".join(str(n) for n in slot_missing if str(n).strip())
        if gap_txt:
            _emit_process(emit, f"　槽位 {gap_txt} 未对齐/未上传，已跳过这些槽的检索")
    if note and (payload.get("fallback_write") or payload.get("element_driven")):
        _emit_process(emit, f"　检索说明：{note}")
    if not cards:
        return
    _emit_process(emit, f"　要素检索 · 采纳 {len(cards)} 条")
    for card in cards[:8]:
        if not isinstance(card, dict):
            continue
        name = str(card.get("point") or card.get("source_file") or "要素").strip() or "要素"
        kind = str(card.get("location") or "").strip()
        kind_label = {
            "file_slot": "引用槽位",
            "file": "文件",
            "rag_search": "文件",
            "kv": "键值",
            "fixed": "固定",
            "llm_generate": "LLM生成",
            "node_ref": "引用结果",
        }.get(kind, kind)
        query = str(card.get("query") or "").strip()
        excerpt = " ".join(str(card.get("excerpt") or "").split()).strip()
        line = f"　· {name}"
        if kind_label:
            line += f"（{kind_label}）"
        if query:
            line += f" · 检索词：{query[:80]}"
        if excerpt:
            clipped = excerpt[:180] + ("…" if len(excerpt) > 180 else "")
            line += f"：{clipped}"
        _emit_process(emit, line)


def emit_write_points_lines(emit: EmitFn | None, result: dict[str, Any] | None) -> None:
    """写作开始时把本章写作要点刷到过程条。"""
    payload = result if isinstance(result, dict) else {}
    points = [str(p).strip() for p in (payload.get("points") or []) if str(p).strip()]
    describes = str(payload.get("describes") or "").strip()
    aux = str(payload.get("aux_prompt") or "").strip()
    if not points and not describes and not aux:
        return
    if points:
        _emit_process(emit, f"　写作要点 · {len(points)} 条")
        for idx, point in enumerate(points[:12], start=1):
            _emit_process(emit, f"　{idx}. {point[:160]}")
    if describes:
        _emit_process(emit, f"　章节说明：{describes[:160]}")
    if aux:
        _emit_process(emit, f"　补充提示：{aux[:160]}")


def _llm_reply(system: str, message: str, *, invoke: InvokeFn) -> str:
    if not (message or "").strip():
        return ""
    try:
        return str(invoke(system, message) or "").strip()
    except Exception as error:
        _log.warning("chat invoke failed: %s", error)
        return ""


def should_skip_reply_polish(
    session: ChatSession | None,
    cards: list | None,
    *,
    action: str = "",
) -> bool:
    """工具已给出卡片/进度时少润色，避免改味或推销蓝本/导出。"""
    act = (action or "").strip()
    if act:
        try:
            from report_writing.agents.chat_control import UI_SHORTCUT_ACTIONS

            if act in UI_SHORTCUT_ACTIONS:
                return True
        except Exception:
            pass
    if session is not None:
        phase = str(session.phase or "")
        if phase.startswith("freeform") or phase in {
            ChatPhase.INTENT.value,
            ChatPhase.BLUEPRINT_SELECT.value,
            "ready_to_write",
            "writing",
            "confirm_export",
        }:
            return True
    if cards:
        return True
    return False


def _reply_polish_disabled() -> bool:
    import os

    from report_writing.infra.config_settings import llm_mode, reply_polish_enabled, scripted_chat

    if not reply_polish_enabled():
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if scripted_chat():
        return True
    if llm_mode() == "scripted":
        return True
    return False


def compose_polish_situation(session: ChatSession | None, cards: list | None = None) -> str:
    """给润色模型用的场景约束，避免工单腔、和当前流程打架。"""
    if session is None:
        return ""
    from report_writing.chat.intent_router import user_chose_skip_blueprint

    parts: list[str] = []
    ff = dict(session.freeform or {})
    phase = str(session.phase or "").strip()

    if user_chose_skip_blueprint(session):
        parts.append(
            "用户已选择不用蓝本：语气像陪写同事，别提匹配蓝本、上传样板、"
            "「还没配好蓝本」；引导在卡片里起草/改目录，不要让用户整段粘贴章节目录。"
        )
    if phase == ChatPhase.FREEFORM_ASK.value:
        parts.append(
            "在问要写什么、文档叫什么：口语追问即可，短、亲切；"
            "不要强迫「类型｜标题」固定句式，用户用习惯说法说清就行。"
        )
    elif phase == ChatPhase.FREEFORM_OUTLINE.value or ff.get("outline_pending_edit"):
        parts.append(
            "目录在下方拓扑卡片里生成/修改：说「我帮你在卡片里搭好了/你点开调一下」，"
            "禁止「每行一章」「把目录发我」「粘贴大纲」等话术。"
        )
    elif phase == ChatPhase.FREEFORM_POINTS.value:
        parts.append("在配写作要点：可帮生成或跳过，别让用户重复贴目录当要点。")
    elif phase in {ChatPhase.WRITING.value, ChatPhase.CONFIRM_EXPORT.value}:
        parts.append("写作进行中：少说流程术语，多指向进度卡片里哪一章要处理。")

    if ff.get("meta_confirmed") and ff.get("category") and ff.get("title"):
        parts.append(
            f"已定文档【{ff.get('category')}】{ff.get('title')}，不要重复让用户再报一遍标题。"
        )

    for card in cards or []:
        ctype = str(getattr(card, "type", "") or "")
        payload = getattr(card, "payload", None) or {}
        if ctype == "outline_preview" and payload.get("editable"):
            parts.append("界面已出可编辑章节目录卡片：只引导在卡片里改并点确认，不要文本粘贴目录。")
            break
        if ctype == "freeform_points_help":
            parts.append("界面有「生成写作要点」按钮：引导点按钮或说一声帮你生成。")
            break

    return " ".join(parts).strip()


def polish_chat_reply(
    *,
    invoke: InvokeFn | None,
    user_message: str,
    draft: str,
    phase: str = "",
    action: str = "",
    situation: str = "",
    session: ChatSession | None = None,
    cards: list | None = None,
    force: bool = False,
) -> str:
    """将工具/模板草稿改写成自然口语；失败则返回草稿。单测与脚本模式默认跳过。

    force=True 时跳过阶段/快捷操作跳过逻辑（仍尊重全局关闭开关），用于开写前资料包软提醒等。
    """
    text = (draft or "").strip()
    if not text or _reply_polish_disabled():
        return draft or ""
    if not force and should_skip_reply_polish(session, cards, action=action):
        return text
    if not invoke:
        return text
    system = (
        "你是智能文档协作助手「小墨」，像坐在对面的靠谱同事：有温度、会接话，但不油腻、不卖萌。"
        "把「草稿」改成 1～3 句口语（必要时 4 句），必须保留全部事实："
        "蓝本名、章数、按钮/卡片怎么点、错误原因、下一步是什么；禁止编造或删掉关键信息。"
        "风格：先轻轻接住用户上一步（半句即可），再说接下来怎么做；用「咱/你」自然交替。"
        "禁止：公文腔（敬请、您好）、工单腔（请粘贴、每行一章、这边还没配好）、"
        "机械复读草稿里的生硬套话；可把套话改成人话，意思不变。"
        "不要 Markdown，不要编号清单；只输出给用户看的正文。"
    )
    user = (
        f"用户刚说：{(user_message or '').strip()[:800]}\n"
        f"会话阶段：{(phase or '').strip() or '—'}\n"
        f"界面操作：{(action or '').strip() or '无'}\n"
    )
    ctx = (situation or "").strip() or compose_polish_situation(session, cards)
    if ctx:
        user += f"场景与禁忌：{ctx[:900]}\n"
    user += f"\n草稿：\n{text[:2400]}\n\n请直接回复用户。"
    try:
        polished = str(invoke(system, user) or "").strip()
        if polished and len(polished) <= 1600:
            return polished
    except Exception as error:
        _log.debug("polish_chat_reply failed: %s", error)
    return text


def _plain_message(text: str) -> str:
    """富文本消息去标签，供意图/大纲等逻辑使用。"""
    raw = (text or "").strip()
    if not raw or "<" not in raw:
        return raw
    plain = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    plain = re.sub(r"</(?:p|div|h[1-6]|li|tr)\s*>", "\n", plain, flags=re.I)
    plain = _TAG_RE.sub("", plain)
    plain = (
        plain.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
    )
    return "\n".join(line.strip() for line in plain.splitlines()).strip()


def _chapter_label(node) -> str:
    seq = str(getattr(node, "sequence", "") or "").strip()
    title = str(getattr(node, "title", "") or getattr(node, "key", "") or "").strip()
    return f"{seq} {title}".strip() if seq else title


def _join_labels(labels: list[str]) -> str:
    return "、".join(labels[:8]) if labels else "—"


def _split_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "")
    parts = re.split(r"[\n,，;；]+", text)
    return [item.strip() for item in parts if item.strip()]


def _load_task(home: Path, task_id: str) -> WritingTask | None:
    tid = (task_id or "").strip()
    if not tid:
        return None
    try:
        from report_writing.services.task_ids import is_blueprint_task_id

        if is_blueprint_task_id(tid):
            from report_writing.services.blueprint_catalog import load_blueprint_task

            task = load_blueprint_task(tid)
        else:
            from report_writing.services.writing_task_catalog import load_writing_task

            task = load_writing_task(tid)
        if task is not None:
            return task
    except Exception:
        pass
    path = home / "tasks" / tid / "task.json"
    if not path.is_file():
        return None
    return WritingTask.from_dict(json.loads(path.read_text(encoding="utf-8")))


