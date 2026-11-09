"""窄 JSON：澄清卡回复、写/分析范围槽、向导 command、要素 payload。

口语选 Tool / 入口 / 定名 / 无蓝本动作只走 LangChain `create_agent`。
详见 chat/AGENT_ARCHITECTURE.md。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger(__name__)

from report_writing.domain.models import ClarifyPending

InvokeFn = Callable[[str, str], str]


WIZARD_COMMANDS: dict[str, str] = {
    "submit_meta": "当前在填写名称步，用户给出了蓝本名称，提交并创建",
    "mark_upload_done": "用户表示样板已上传完成，进入提取目录（与点「上传完成」同类）",
    "re_extract_outline": "从已上传样板识别、生成或修正章节目录",
    "re_upload_sample": "重新上传或更换 Word/PDF 样板",
    "go_outline_confirm": "进入确认目录、在拓扑图核对（尚未最终确认）",
    "confirm_outline": "确认目录无误（只确认目录，不自动学习）",
    "go_study": "进入分析要点并开始分析全部章节（「开始学习/开始分析全部」）；指定范围用 re_study_chapters",
    "re_study_chapters": "对指定章节重新分析写作要点。未指定方式时后端会追问：根据原文 / 依据样本 / 自动分析",
    "abandon_wizard": "不要当前这份、退出向导、换别的蓝本或改用不绑蓝本写（不是跳过某一步）",
    "none": "与当前向导无关、闲聊，或信息不足需要追问",
}

_MIN_CONFIDENCE = 0.52


def _intent_lines(catalog: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in catalog.items())


def _uncertain_line(*, field: str = "intent", none_value: str = "none") -> str:
    return f"不确定：{field}={none_value}，confidence < {_MIN_CONFIDENCE}。"


def _parse_classifier_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _classifier_confidence(data: dict[str, Any]) -> float:
    try:
        return float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def wizard_action_for_command(command: str, data: dict[str, Any]) -> dict[str, Any] | None:
    allowed = [k for k in WIZARD_COMMANDS if k != "none"]
    cmd = (command or "").strip()
    if cmd == "none" or cmd not in allowed:
        return None
    title = str(data.get("title") or "").strip()
    if cmd == "submit_meta":
        if not title:
            return None
        return {"type": "payload", "payload": {"step": "meta", "title": title, "blueprint_id": ""}}
    if cmd == "mark_upload_done":
        return {"type": "payload", "payload": {"step": "upload_done"}}
    if cmd == "confirm_outline":
        return {"type": "payload", "payload": {"step": "outline_confirm"}}
    if cmd == "re_study_chapters":
        seqs = data.get("chapter_sequences") or data.get("sequences") or []
        if isinstance(seqs, str):
            seqs = [s.strip() for s in seqs.replace("，", ",").split(",") if s.strip()]
        if not isinstance(seqs, list):
            seqs = []
        scope = str(data.get("study_scope") or data.get("scope") or "").strip()
        action: dict[str, Any] = {
            "type": "command",
            "command": cmd,
            "chapter_sequences": [str(s).strip() for s in seqs if str(s).strip()],
        }
        if scope in {"whole", "children", "self"}:
            action["study_scope"] = scope
        return action
    if cmd in {"re_extract_outline", "re_upload_sample", "go_outline_confirm", "go_study", "abandon_wizard"}:
        return {"type": "command", "command": cmd}
    return None


def _wizard_actions_from_classifier(data: dict[str, Any]) -> list[dict[str, Any]]:
    if _classifier_confidence(data) < _MIN_CONFIDENCE:
        return []
    from report_writing.chat.classifier_action_chain import (
        WIZARD_POLICY,
        order_action_ids,
        parse_id_list_from_classifier,
        reorder_by_ids,
    )

    allowed = [k for k in WIZARD_COMMANDS if k != "none"]
    ids = parse_id_list_from_classifier(
        data,
        plural_keys=("commands",),
        single_keys=("command", "action"),
    )
    ids = [i for i in order_action_ids(ids, WIZARD_POLICY) if i in allowed]
    if not ids:
        return []
    hits: list[dict[str, Any]] = []
    for cmd in ids:
        hit = wizard_action_for_command(cmd, data)
        if hit:
            hits.append(hit)

    def _cmd_id(action: dict[str, Any]) -> str:
        if action.get("type") == "command":
            return str(action.get("command") or "")
        payload = action.get("payload") or {}
        step = str(payload.get("step") or "")
        if step == "outline_confirm":
            return "confirm_outline"
        if step == "upload_done":
            return "mark_upload_done"
        if step == "meta":
            return "submit_meta"
        return step

    return reorder_by_ids(hits, ids, _cmd_id)


def _wizard_action_from_classifier(data: dict[str, Any]) -> dict[str, Any] | None:
    hits = _wizard_actions_from_classifier(data)
    return hits[0] if hits else None


def classify_wizard_action_llm(
    message: str,
    *,
    wizard_step: str = "",
    blueprint_title: str = "",
    blueprint_id: str = "",
    wizard_context: dict[str, Any] | None = None,
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any] | None:
    """将口语映射为向导 action（command 或 step payload）；失败返回 None。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return None

    ctx = dict(wizard_context or {})
    from report_writing.chat.wizard_loop_guard import normalized_wizard_step
    from report_writing.chat.classifier_context import format_dialogue_lines

    step_label = normalized_wizard_step(str(wizard_step or ctx.get("step") or ""))
    has_outline = bool(ctx.get("chapters"))
    study_mode = str(ctx.get("study_mode") or "").strip() or "—"

    allowed = [k for k in WIZARD_COMMANDS if k != "none"]
    from report_writing.chat.classifier_action_chain import MULTI_ACTION_JSON_HINT

    system = (
        "# Role\n你是蓝本向导的意图分类器。\n"
        "# Task\n结合近期对话、焦点文档与当前步骤选出向导命令；"
        "说法不固定，按意思判断；指代（继续/那个）要落到上下文。\n"
        "# Inputs\nclassifier context JSON（对话、焦点文档、当前步骤）。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON，不要 Markdown：\n"
        '{"command":"<单动作>","commands":["<动作1>","<动作2>"],"confidence":0.0,'
        '"title":"","chapter_sequences":[],"study_scope":""}\n'
        f"command / commands ∈ {{{', '.join(allowed + ['none'])}}}\n"
        f"{MULTI_ACTION_JSON_HINT}\n"
        "后端按向导流水线校正顺序；不自动补 confirm_outline。\n"
        "\n"
        "## 命令含义\n"
        + _intent_lines(WIZARD_COMMANDS)
        + "\n\n"
        "## 规则\n"
        "1) 确认目录与学习分开：仅确认 → confirm_outline；全部学习 → go_study；"
        "指定章 → re_study_chapters。「就这个目录，开始分析」→ [confirm_outline, go_study]。\n"
        "2) 目录未确认时只说学习/重学 → 只返回 go_study 或 re_study_chapters（后端提示先确认）。\n"
        "3) meta 步只发名称 → submit_meta，title=名称。\n"
        "4) 不要这份蓝本/换方式写 → abandon_wizard。\n"
        "5) outline_confirm「重新提取」→ re_extract_outline（不是 go_outline_confirm）。\n"
        "6) study_scope：整章/全部 → whole；只要本章/不要子章 → self；"
        "只要下属 → children；否定「子章节」=self；未说勿填。\n"
        "7) 「写第×章/开写」→ command=none（另有开写路由）。\n"
        "8) 加要素/检索词 → command=none（另有 add_chapter_element）。\n"
        "\n"
        "## 示例\n"
        "蓝本叫安全预评价 → submit_meta；上传好了 → mark_upload_done；"
        "分析第1到2章 → re_study_chapters + chapter_sequences=[\"1\",\"2\"]；"
        "学习第一章就够了不要子章节 → re_study_chapters + study_scope=self。\n"
        "\n"
        f"## {_uncertain_line(field='command')}"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"当前向导步骤：{step_label}\n"
        f"蓝本 ID：{(blueprint_id or '—').strip()}\n"
        f"蓝本名称：{(blueprint_title or '—').strip()}\n"
        f"任务是否已有目录章节：{'是' if has_outline else '否'}\n"
        f"学习阶段：{study_mode}\n"
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"本会话已登记文档：\n{(session_documents_brief or '（无）').strip() or '（无）'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户说：{text[:1500]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("wizard intent llm failed: %s", error)
        return None

    data = _parse_classifier_json(raw)
    hits = _wizard_actions_from_classifier(data)
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    return {"type": "chain", "actions": hits}


def resolve_wizard_action(
    message: str,
    wizard: dict[str, Any],
    *,
    invoke: InvokeFn | None,
    home: Path | None = None,
    session: Any | None = None,
) -> dict[str, Any] | None:
    """蓝本向导口语 → action；规则兜底 + 意图模型。"""
    text = (message or "").strip()
    if not text:
        return None

    wizard = dict(wizard or {})
    if not str(wizard.get("step") or "").strip() and not str(
        wizard.get("blueprint_id") or ""
    ).strip():
        return None

    if invoke is None:
        return None

    from report_writing.chat.classifier_context import pack_classifier_context

    ctx = pack_classifier_context(session) if session is not None else {}
    hit = classify_wizard_action_llm(
        text,
        wizard_step=str(wizard.get("step") or ""),
        blueprint_title=str(wizard.get("title") or ""),
        blueprint_id=str(wizard.get("blueprint_id") or ""),
        wizard_context=wizard,
        dialogue_tail=list(ctx.get("dialogue_tail") or []) or None,
        focus_document_brief=str(ctx.get("focus_document_brief") or ""),
        session_documents_brief=str(ctx.get("session_documents_brief") or ""),
        invoke=invoke,
    )
    if not hit:
        return None

    def enrich(one: dict[str, Any]) -> dict[str, Any] | None:
        action = dict(one)
        if action.get("type") == "payload":
            payload = dict(action.get("payload") or {})
            bid = str(wizard.get("blueprint_id") or "").strip()
            if bid and not str(payload.get("blueprint_id") or "").strip():
                payload["blueprint_id"] = bid
            if str(payload.get("step") or "") == "meta":
                title = str(payload.get("title") or "").strip()
                if not title and str(wizard.get("step") or "") == "meta":
                    title = text.strip()[:48]
                if title:
                    payload["title"] = title
                else:
                    return None
            action["payload"] = payload
        if action.get("type") == "command" and str(action.get("command") or "") == "re_study_chapters":
            seqs = list(action.get("chapter_sequences") or [])
            if not seqs:
                from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message

                seqs = parse_study_sequences_from_message(text)
            action["chapter_sequences"] = seqs
            action["study_message"] = text
        return action

    if hit.get("type") == "chain":
        actions = []
        for sub in list(hit.get("actions") or []):
            enriched = enrich(dict(sub))
            if enriched:
                actions.append(enriched)
        if not actions:
            return None
        if len(actions) == 1:
            return actions[0]
        return {"type": "chain", "actions": actions}

    return enrich(hit)



def classify_study_scope_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    invoke: InvokeFn | None,
) -> str:
    """学习范围：结合上下文判整章还是只要子章。不靠固定句式。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return ""
    from report_writing.chat.classifier_context import format_dialogue_lines

    system = (
        "角色：学习范围分类器。用户要分析某章写作要点；「学习」与「分析」同义；"
        "结合近期对话与焦点文档按意思判断，不靠固定句式。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"scope":"whole"|"children"|"self"|"unclear","confidence":0.0}\n'
        "\n"
        "## 取值\n"
        "- whole：整章含下属（所有/全部/整章/整个/都学；且非「只要/不要子章节」）。\n"
        "- self：只要本章节点（就够了/只要本章/不要子章节/不要下属；否定子章=self）。\n"
        "- children：只要下属子章、不要顶层本章（须肯定要子章）。\n"
        "- unclear：只点了第几章，未说范围。\n"
        "\n"
        "## 示例\n"
        "学习第一章就够了不要子章节 → self。\n"
        "\n"
        f"## 不确定：scope=unclear，confidence < {_MIN_CONFIDENCE}。"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户：{text[:1500]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("study scope llm failed: %s", error)
        return ""
    data = _parse_classifier_json(raw)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    scope = str(data.get("scope") or "").strip()
    if confidence < _MIN_CONFIDENCE or scope not in {"whole", "children", "self"}:
        return ""
    return scope


WRITE_TARGET_INTENTS: dict[str, str] = {
    "recent_study": (
        "用户要写刚才/刚分析完的那一批章节（指代最近分析批；"
        "可有可无「写」字，如「刚才分析的那几个章节」）"
    ),
    "sequences": "用户点名了具体章节序号或标题要开写",
    "all_analyzed": "用户要写全部已分析章或「写已分析的」",
    "status": "用户明确问写作进度/写到哪了/写了几章/哪章待确认",
    "none": (
        "与指定开写目标无关：闲聊、问现在能干什么/可以看什么、问怎么用、"
        "只想浏览右侧目录或配置，没有要开写或查进度"
    ),
}


def classify_write_target_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    last_study_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """开写目标：最近分析批 / 序号 / 已分析全集 / 查进度。按语义分类，不用固定句式。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    from report_writing.chat.classifier_context import format_dialogue_lines

    allowed = [k for k in WRITE_TARGET_INTENTS if k != "none"]
    system = (
        "# Role\n你是写作对话的「开写目标」分类器。\n"
        "# Task\n结合近期对话与焦点文档判断用户要写哪批章、查进度，还是只是闲聊/问能力；"
        "按意思判断，不要靠固定关键词表。\n"
        "# Inputs\n用户原话、近期对话、焦点文档、最近分析批摘要。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"target":"<id>","chapter_sequences":[],"confidence":0.0}\n'
        f"target ∈ {{{', '.join(allowed + ['none'])}}}\n"
        "点名章节时 chapter_sequences 填序号字符串（如 \"1.2\"）；否则 []。\n"
        "\n"
        "## 意图含义\n"
        + _intent_lines(WRITE_TARGET_INTENTS)
        + "\n\n"
        "## 规则\n"
        "指代「刚才/刚分析完/那几章」且语境是要处理这批章 → recent_study"
        "（即使没说「写」字）。\n"
        "仅当明确问进度/写到哪/待确认 → status；"
        "「现在可以看什么 / 现在能干什么 / 怎么办」→ none，禁止判成 status。\n"
        "点名第×章/×.× → sequences。\n"
        "「写已分析的 / 先写分析好的」→ all_analyzed。\n"
        "\n"
        f"## {_uncertain_line(field='target')}"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"最近分析批：{(last_study_brief or '—').strip() or '—'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户：{text[:1500]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("write target llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    confidence = _classifier_confidence(data)
    target = str(data.get("target") or "").strip()
    if confidence < _MIN_CONFIDENCE:
        return {}
    if target == "none":
        return {"target": "none", "chapter_sequences": [], "confidence": confidence}
    if target not in allowed:
        return {}
    seqs: list[str] = []
    raw_seqs = data.get("chapter_sequences") or data.get("sequences") or []
    if isinstance(raw_seqs, list):
        for item in raw_seqs:
            tok = str(item or "").strip()
            if tok and tok not in seqs:
                seqs.append(tok)
    return {"target": target, "chapter_sequences": seqs, "confidence": confidence}


def classify_write_scope_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    invoke: InvokeFn | None,
) -> str:
    """写作范围：结合上下文判只要本章还是含下属。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return ""
    from report_writing.chat.classifier_context import format_dialogue_lines

    system = (
        "角色：写作范围分类器。用户要写某章正文；结合近期对话与焦点文档按意思判断，不靠固定句式。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"scope":"whole"|"self"|"unclear","confidence":0.0}\n'
        "\n"
        "## 取值\n"
        "- self：只要本章节点（只要本章/就够了/不要子章节）。\n"
        "- whole：含下属一起写（整章/含下属/下面的也写）。\n"
        "- unclear：只点章节号，未说范围。\n"
        "\n"
        "## 示例\n"
        "帮我写2.3章节（未说范围）→ unclear；写2.3整章 → whole；只写2.3本章 → self。\n"
        "\n"
        f"## 不确定：scope=unclear，confidence < {_MIN_CONFIDENCE}。"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户：{text[:1500]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("write scope llm failed: %s", error)
        return ""
    data = _parse_classifier_json(raw)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    scope = str(data.get("scope") or "").strip()
    if confidence < _MIN_CONFIDENCE or scope not in {"whole", "self"}:
        return ""
    return scope


# 口语选 Tool / 入口路径 / 定名 / 无蓝本动作 / 记住目标：一律总控 create_agent。
# 本文件只保留：解卡回复、写/分析范围槽、向导 command、要素 payload 抽取。


def extract_collect_element_name_llm(
    message: str,
    *,
    candidate_names: list[str] | None = None,
    invoke: InvokeFn | None,
) -> str:
    """从口语抽取要收入个人要素库的要素名（说法不固定，不靠正则）。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return ""
    names = [str(n).strip() for n in (candidate_names or []) if str(n).strip()]
    catalog = "、".join(names[:40]) if names else "（未提供候选，按原话抽取）"
    system = (
        "角色：要素名抽取器。用户要把任务里某个已有要素收入个人要素库，"
        "从原话抽出要素名称（不是新建要素、也不是打开要素库）。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"element_name":"","confidence":0.0}\n'
        "\n"
        "## 规则\n"
        "1) 优先对齐候选列表中的原名；没有候选则抽用户点名的短名。\n"
        "2) 「这个/该要素/全部」等代词且无法对齐 → element_name 留空。\n"
        "3) 勿编造未说的名字。\n"
        "\n"
        f"## {_uncertain_line(field='element_name', none_value='')}"
    )
    user = f"候选要素：{catalog}\n用户：{text[:2000]}"
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("collect element name llm failed: %s", error)
        return ""
    data = _parse_classifier_json(raw)
    if _classifier_confidence(data) < _MIN_CONFIDENCE:
        return ""
    return str(data.get("element_name") or "").strip()


def infer_element_payload_llm(
    message: str,
    *,
    chapter_title: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """从对话提取 add_chapter_element 的 payload 字段（供总控或补全草稿）。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    system = (
        "角色：章节要素 payload 抽取器。从对话提取新增章节要素配置。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON：\n"
        '{"kind":"kv|fixed|file|file_slot|llm_generate|node_ref","name":"","category":"","purpose":"","tags":[],"'
        '"kv_pairs":[{"key":"","value":""}],"query":"","value":"","prompt":"","ref_element_name":""}\n'
        "\n"
        "## 规则\n"
        "键值类 → kind=kv，从原话填全 kv_pairs；勿编造未说的值。\n"
        "文件要素/资料/检索词 → kind=file，query=检索词原文。\n"
        "引用资料包槽位 → kind=file_slot；旧称「知识库要素」按 file。"
    )
    ctx = ""
    for item in (dialogue_tail or [])[-6:]:
        ctx += f"{item.get('role')}: {str(item.get('text') or '')[:300]}\n"
    user = f"本章：{chapter_title or '—'}\n近期：\n{ctx}\n用户：{text[:2000]}"
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("element payload llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    return data if isinstance(data, dict) else {}


def classify_doc_name_reply_llm(
    message: str,
    *,
    pending: ClarifyPending | dict[str, Any],
    suggested_title: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """定名澄清回复：结合上文判断确认建议名 / 新题目 / 换话题。失败返回 {}。

    返回：
      decision: pick | apply_title | new_intent | unclear
      option_id: pick 时的选项 id
      title: apply_title 时抽出的文档名
      question / confidence
    """
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    pending = ClarifyPending.from_any(pending)
    options = [o for o in (pending.get("options") or []) if isinstance(o, dict)]
    catalog = []
    allowed_ids: list[str] = []
    for item in options:
        oid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not oid or not label:
            continue
        allowed_ids.append(oid)
        catalog.append({"id": oid, "label": label})
    tip = (suggested_title or "").strip()
    if not tip:
        tip = str(((pending.get("candidate") or {}).get("payload") or {}).get("title") or "").strip()
    if not tip:
        for item in options:
            if str(item.get("id") or "") == "use_suggested_name":
                tip = str((item.get("slots") or {}).get("title") or "").strip()
                if tip:
                    break

    from report_writing.chat.classifier_context import format_dialogue_lines

    ids_hint = ", ".join(allowed_ids) if allowed_ids else "（无按钮选项）"
    system = (
        "你在写报告对话里处理「文档起名」澄清。必须结合刚才的问题、建议名与近期对话判断；"
        "用户说法不固定（肯定、指代、改名、一句里夹多种信息），勿靠固定短语字面匹配。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON，不要 Markdown：\n"
        '{"decision":"pick|apply_title|new_intent|unclear","option_id":"","title":"",'
        '"residual":"","question":"","confidence":0.0}\n'
        "\n"
        "## decision\n"
        "- pick：在回答起名问题并点选已有选项；option_id 必填。\n"
        f"  option_id ∈ {{{ids_hint}}}。\n"
        "- apply_title：用户给出了要用的文档名（可夹在长句里）；title=抽出的短标题，"
        "不要把整句、路径意图、确认语本身当成 title。\n"
        "- new_intent：本句主要是另下指令（匹配蓝本/开写/加要素/导出/取消等），不是在定名。\n"
        "- unclear：看不出是确认建议名还是在起新名。\n"
        "\n"
        "## residual\n"
        "一句里定名之外还有后续意图（匹配蓝本/创建蓝本/跳过蓝本等）时，把剩余原话写入 residual；"
        "没有则留空。\n"
        "\n"
        "## 规则\n"
        f"- 建议名（若有）：「{tip or '（无）'}」。用户用任意肯定/指代确认该名"
        "（如同意、就这个、用你说的那个等）→ pick use_suggested_name（若有该选项），title 留空。\n"
        "- 禁止把确认语、嗯、随便等当作 title。\n"
        "- 用户明确换名或直接给出另一题目 → apply_title，title=新名。\n"
        "- 一句多信息：定名决策（pick/apply_title）与路径/match 意图可同句；剩余路径放 residual。\n"
        "\n"
        "## 示例\n"
        f"建议名「{tip or '1101安全预评价'}」，用户说「是的」→ "
        '{"decision":"pick","option_id":"use_suggested_name","title":"","residual":"","confidence":0.9}\n'
        "用户说「就叫1101安全预评价，然后匹配蓝本」→ "
        '{"decision":"apply_title","title":"1101安全预评价","residual":"然后匹配蓝本","confidence":0.9}\n'
        "\n"
        f"## 不确定：decision=unclear，confidence < {_MIN_CONFIDENCE}。"
    )
    opt_lines = "\n".join(f"- {o['id']}: {o['label']}" for o in catalog) if catalog else "（无）"
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"刚才问的：{str(pending.get('question') or '')[:400]}\n"
        f"建议文档名：{tip or '（无）'}\n"
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"本会话已登记文档：\n{(session_documents_brief or '（无）').strip() or '（无）'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"选项：\n{opt_lines}\n"
        f"用户这句：{text[:1500]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("doc name reply llm failed: %s", error)
        return {}

    data = _parse_classifier_json(raw)
    if not data:
        return {}
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    decision = str(data.get("decision") or "").strip()
    option_id = str(data.get("option_id") or "").strip()
    title = str(data.get("title") or "").strip()[:160]
    residual = str(data.get("residual") or "").strip()[:500]
    question = str(data.get("question") or "").strip()

    def _pack(**kwargs: Any) -> dict[str, Any]:
        out = {
            "decision": "",
            "option_id": "",
            "title": "",
            "residual": "",
            "question": "",
            "confidence": confidence,
        }
        out.update(kwargs)
        if "residual" not in kwargs and residual and out.get("decision") in {
            "pick",
            "apply_title",
        }:
            out["residual"] = residual
        return out

    if decision == "pick":
        if allowed_ids and (option_id not in allowed_ids or confidence < _MIN_CONFIDENCE):
            return _pack(decision="unclear", question=question)
        # 无按钮但有建议名时，把「确认」收成 apply tip
        if not allowed_ids and tip and confidence >= _MIN_CONFIDENCE:
            return _pack(decision="apply_title", title=tip, residual=residual)
        if option_id == "use_suggested_name" and tip and not title:
            return _pack(
                decision="pick", option_id=option_id, title=tip, residual=residual
            )
        return _pack(
            decision="pick", option_id=option_id, title=title, residual=residual
        )
    if decision == "apply_title" and title and confidence >= _MIN_CONFIDENCE:
        # 确认语不得落成标题
        low = title.lower().strip()
        if low in {"是的", "是", "对", "对的", "好的", "好", "可以", "确认", "行", "ok", "okay", "yes", "y", "没错"}:
            if tip:
                return _pack(decision="apply_title", title=tip, residual=residual)
            return _pack(
                decision="unclear",
                question=question or "请直接发文档名，或确认建议名。",
            )
        return _pack(decision="apply_title", title=title, residual=residual)
    if decision == "new_intent" and confidence >= _MIN_CONFIDENCE:
        return _pack(decision="new_intent")
    return _pack(decision="unclear", question=question)


_FREEFORM_NAME_CLARIFY_SOURCES = frozenset({"doc_name", "session_meta", "materials_name"})


def classify_clarify_reply_llm(
    message: str,
    *,
    pending: ClarifyPending | dict[str, Any],
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """澄清挂起后的用户回复：结合对话上下文判定。失败返回 {}。

    返回：
      decision: pick | apply_title | new_intent | unclear
      option_id: pick 时的选项 id
      title: apply_title 时抽出的名称（定名/改名/资料包名）
      question / confidence
    """
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    pending = ClarifyPending.from_any(pending)
    source = str(pending.get("source") or "").strip()
    # 文档定名：专用规则（建议名确认）
    if source == "doc_name":
        return classify_doc_name_reply_llm(
            message,
            pending=pending,
            dialogue_tail=dialogue_tail,
            focus_document_brief=focus_document_brief,
            session_documents_brief=session_documents_brief,
            invoke=invoke,
        )

    options = [o for o in (pending.get("options") or []) if isinstance(o, dict)]
    catalog = []
    allowed_ids: list[str] = []
    for item in options:
        oid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not oid or not label:
            continue
        allowed_ids.append(oid)
        catalog.append({"id": oid, "label": label})
    allow_apply_title = source in _FREEFORM_NAME_CLARIFY_SOURCES
    # 无选项且不能自由填名 → 无法分类
    if not catalog and not allow_apply_title:
        return {}

    from report_writing.chat.classifier_context import format_dialogue_lines

    ids_hint = ", ".join(allowed_ids) if allowed_ids else "（无按钮选项）"
    apply_line = ""
    if allow_apply_title:
        apply_line = (
            "- apply_title：用户直接给出要用的名称（可夹在长句里）；title=抽出的短名，"
            "勿把确认语/整句路径意图当成 title。\n"
        )
    decisions = "pick|apply_title|new_intent|unclear" if allow_apply_title else "pick|new_intent|unclear"
    system = (
        "你在写报告对话里帮用户确认刚才没说清的意图。必须结合刚才的问题、选项与近期对话；"
        "用户说法不固定（同义、指代、一句多信息），勿靠固定短语字面匹配。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON，不要 Markdown：\n"
        f'{{"decision":"{decisions}","option_id":"","title":"","residual":"","question":"","confidence":0.0}}\n'
        "\n"
        "## decision\n"
        "- pick：在回答刚才那问并点选已有选项；option_id 必填。\n"
        f"  option_id ∈ {{{ids_hint}}}。\n"
        + apply_line
        + "- new_intent：另下指令（加要素、开写、学另一章、改目录、导出、取消等），不是在回答本问；"
        "出现章节号 ≠ 在选「整章/子章节」。\n"
        "- unclear：看不出选哪个或名称是什么。\n"
        "unclear 时 question 写一句短口语追问；勿编号、勿说「作为AI」。\n"
        "\n"
        "## residual\n"
        "本句在回答当前澄清之外还有后续意图时，写入 residual（剩余原话）；没有则留空。\n"
        "\n"
        "## 示例\n"
        "问整章还是子章节，用户说「为2.2添加知识库要素」→ new_intent。\n"
        "问新文档名，用户说「2026年度个人述职」→ apply_title，title=2026年度个人述职。\n"
        "问资料包新名，用户说「就叫项目资料包」→ apply_title，title=项目资料包。\n"
        "\n"
        f"## 不确定：decision=unclear，confidence < {_MIN_CONFIDENCE}。"
    )
    opt_lines = "\n".join(f"- {o['id']}: {o['label']}" for o in catalog) if catalog else "（无）"
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"刚才问的：{str(pending.get('question') or '')[:400]}\n"
        f"来源：{source or '—'}\n"
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"本会话已登记文档：\n{(session_documents_brief or '（无）').strip() or '（无）'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"选项：\n{opt_lines}\n"
        f"用户这句：{text[:1500]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("clarify reply llm failed: %s", error)
        return {}

    data = _parse_classifier_json(raw)
    if not data:
        return {}
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    decision = str(data.get("decision") or "").strip()
    option_id = str(data.get("option_id") or "").strip()
    title = str(data.get("title") or "").strip()[:160]
    residual = str(data.get("residual") or "").strip()[:500]
    question = str(data.get("question") or "").strip()
    if decision == "pick":
        if not allowed_ids or option_id not in allowed_ids or confidence < _MIN_CONFIDENCE:
            return {
                "decision": "unclear",
                "option_id": "",
                "title": "",
                "residual": "",
                "question": question,
                "confidence": confidence,
            }
        return {
            "decision": "pick",
            "option_id": option_id,
            "title": "",
            "residual": residual,
            "question": "",
            "confidence": confidence,
        }
    if (
        allow_apply_title
        and decision == "apply_title"
        and title
        and confidence >= _MIN_CONFIDENCE
    ):
        return {
            "decision": "apply_title",
            "option_id": "",
            "title": title,
            "residual": residual,
            "question": "",
            "confidence": confidence,
        }
    if decision == "new_intent" and confidence >= _MIN_CONFIDENCE:
        return {
            "decision": "new_intent",
            "option_id": "",
            "title": "",
            "residual": "",
            "question": "",
            "confidence": confidence,
        }
    return {
        "decision": "unclear",
        "option_id": "",
        "title": "",
        "residual": "",
        "question": question,
        "confidence": confidence,
    }


__all__ = [
    "InvokeFn",
    "WIZARD_COMMANDS",
    "classify_clarify_reply_llm",
    "classify_doc_name_reply_llm",
    "classify_study_scope_llm",
    "classify_wizard_action_llm",
    "classify_write_scope_llm",
    "classify_write_target_llm",
    "extract_collect_element_name_llm",
    "infer_element_payload_llm",
    "resolve_wizard_action",
    "wizard_action_for_command",
    "WRITE_TARGET_INTENTS",
]
