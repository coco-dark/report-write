"""澄清/调度/可选遗留计划用的 LLM 分类器（非 LangChain 口语主路由）。

- 默认配置下口语不经过本模块的 turn_plan；总控见 agents/react.run_react。
- 仍用于：澄清回复、wizard command、writing_control 窄分类、intent_plan=true 时的 JSON 计划。
详见 chat/AGENT_ARCHITECTURE.md。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger(__name__)

from report_writing.chat.tool_registry import WIZARD_COMMAND_HINTS, WRITING_CONTROL_TOOLS

InvokeFn = Callable[[str, str], str]

WIZARD_COMMANDS: dict[str, str] = WIZARD_COMMAND_HINTS

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


def _wizard_action_for_command(command: str, data: dict[str, Any]) -> dict[str, Any] | None:
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
    from report_writing.chat.intent_action_chain import (
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
        hit = _wizard_action_for_command(cmd, data)
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
    from report_writing.chat.intent_context import format_dialogue_lines

    step_label = normalized_wizard_step(str(wizard_step or ctx.get("step") or ""))
    has_outline = bool(ctx.get("chapters"))
    study_mode = str(ctx.get("study_mode") or "").strip() or "—"

    allowed = [k for k in WIZARD_COMMANDS if k != "none"]
    from report_writing.chat.intent_action_chain import MULTI_ACTION_JSON_HINT

    system = (
        "你是蓝本向导的意图分类器。结合近期对话、焦点文档与当前步骤选出向导命令；"
        "说法不固定，按意思判断；指代（继续/那个）要落到上下文。\n"
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


def classify_wizard_command_llm(
    message: str,
    *,
    wizard_step: str = "",
    blueprint_title: str = "",
    blueprint_id: str = "",
    invoke: InvokeFn | None,
) -> str | None:
    """兼容旧接口：仅返回 wizard_command 字符串。"""
    hit = classify_wizard_action_llm(
        message,
        wizard_step=wizard_step,
        blueprint_title=blueprint_title,
        blueprint_id=blueprint_id,
        invoke=invoke,
    )
    if not hit:
        return None
    if hit.get("type") == "command":
        return str(hit.get("command") or "").strip() or None
    return None


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

    if invoke is None:
        return None

    from report_writing.chat.intent_context import pack_classifier_context

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


def resolve_wizard_command(
    message: str,
    wizard: dict[str, Any],
    *,
    invoke: InvokeFn | None,
) -> str | None:
    hit = resolve_wizard_action(message, wizard, invoke=invoke)
    if not hit:
        return None
    if hit.get("type") == "chain":
        for sub in hit.get("actions") or []:
            if isinstance(sub, dict) and sub.get("type") == "command":
                return str(sub.get("command") or "").strip() or None
        return None
    if hit.get("type") != "command":
        return None
    return str(hit.get("command") or "").strip() or None


def classify_writing_control_llm(
    message: str,
    *,
    phase: str = "",
    task_id: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    writing_task: dict[str, Any] | None = None,
    session_goal_brief: str = "",
    focus_document_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """根据对话语境判断本轮更合适的总控工具（非正则）。失败返回 {}。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}

    allowed = [k for k in WRITING_CONTROL_TOOLS if k != "none"]
    from report_writing.chat.intent_action_chain import MULTI_ACTION_JSON_HINT
    from report_writing.infra.config_settings import intent_tool_catalog_in_prompt

    dialogue_lines = []
    for item in (dialogue_tail or [])[-8:]:
        role = str(item.get("role") or "?")
        line = str(item.get("text") or "").strip().replace("\n", " ")[:400]
        if line:
            dialogue_lines.append(f"{role}: {line}")
    wt = writing_task or {}
    focus = ""
    if wt.get("focus_chapter_title") or wt.get("focus_chapter_key"):
        focus = f"{wt.get('focus_chapter_title') or ''} (key={wt.get('focus_chapter_key') or '—'})"

    system = (
        "你是写作对话总控的意图分类器。结合近期对话、阶段与任务选工具；"
        "默认针对当前焦点文档，用户点名其它文档时才切换。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON，不要 Markdown：\n"
        '{"tool":"<id>","tools":[{"tool":"<id>","payload":{}}],"confidence":0.0,"payload":{}}\n'
        f"tool / tools ∈ {{{', '.join(allowed + ['none'])}}}\n"
        f"{MULTI_ACTION_JSON_HINT}\n"
        "多工具时后端按 remember_writing_goal → add_chapter_element → submit_points 校正；"
        "open_* 可与其它工具并列。\n"
        "\n"
    )
    if intent_tool_catalog_in_prompt():
        system += "## 工具含义\n" + _intent_lines(WRITING_CONTROL_TOOLS) + "\n\n"
    else:
        system += "各 tool 的调用语义以 LangChain 总控 Tool 的 description 为准；此处只列 id。\n\n"
    system += (
        "## payload 规则\n"
        "- add_chapter_element：从原话提取 kind/name/category/tags/purpose/kv_pairs/query/value/prompt/"
        "ref_element_name/chapter_key_hint；缺省 kind=kv。"
        "文件/资料/检索词 → kind=file；引用资料包槽位 → kind=file_slot。\n"
        "- associate_elements：chapter_key_hint 可选。\n"
        "- dissociate_chapter_element：取消关联；element_name / element_id、chapter_key。\n"
        "- remove_document_element：仅删本文档要素；element_name / element_id；勿删要素库。\n"
        "- collect_to_element_library：element_name 可选（收入个人库，不是新建）。\n"
        "- open_element_library / open_knowledge：payload 可 {}；open_knowledge=资料库（兼容旧称知识库）。\n"
        "- remember_writing_goal：notes=原话摘要。\n"
        "- submit_points / none：payload={}。\n"
        "\n"
        "## 示例\n"
        "查看/打开要素库 → open_element_library（不是蓝本列表）。\n"
        "查看/浏览蓝本库 → open_blueprint_library（不是 match_blueprints / list_blueprints 选型）。\n"
        "查看资料库/知识库 → open_knowledge。\n"
        "给2.2章关联要素 → associate_elements，chapter_key_hint=2.2。\n"
        "将文件要素收入要素库 → collect_to_element_library（绝不要 add_chapter_element）。\n"
        "加个个人信息要素，姓名 nico → add_chapter_element + kv_pairs。\n"
        "为建设规模添加文件要素，检索词为… → add_chapter_element，kind=file。\n"
        "\n"
        f"## {_uncertain_line(field='tool')}"
    )
    user = (
        f"阶段：{(phase or '—').strip()}\n"
        f"task_id：{(task_id or '—').strip()}\n"
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"写作焦点章：{focus or '—'}\n"
        f"已记住目标：{(session_goal_brief or '—')[:400]}\n"
        f"近期对话：\n" + ("\n".join(dialogue_lines) if dialogue_lines else "（无）") + "\n"
        f"用户本轮：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("writing control intent llm failed: %s", error)
        return {}

    data = _parse_classifier_json(raw)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return {}

    from report_writing.chat.intent_action_chain import (
        WRITING_CONTROL_POLICY,
        order_action_ids,
        parse_id_list_from_classifier,
    )

    tool_ids = [
        t
        for t in order_action_ids(
            parse_id_list_from_classifier(data, plural_keys=("tools",), single_keys=("tool",)),
            WRITING_CONTROL_POLICY,
        )
        if t in allowed
    ]
    if len(tool_ids) > 1:
        steps: list[dict[str, Any]] = []
        raw_tools = data.get("tools")
        by_tool: dict[str, dict[str, Any]] = {}
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("tool") or "").strip()
                pl = item.get("payload")
                if tid and isinstance(pl, dict):
                    by_tool[tid] = pl
        top_payload = data.get("payload")
        if not isinstance(top_payload, dict):
            top_payload = {}
        for tid in tool_ids:
            pl = dict(by_tool.get(tid) or top_payload if tid == str(data.get("tool") or "") else by_tool.get(tid) or {})
            steps.append({"tool": tid, "confidence": confidence, "payload": pl})
        return {"tools": steps, "confidence": confidence}

    tool = str(data.get("tool") or tool_ids[0] if tool_ids else "none").strip()
    payload = data.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    if tool == "none" or tool not in allowed:
        return {}
    return {"tool": tool, "confidence": confidence, "payload": payload}


_PLAN_MIN_CONFIDENCE = 0.55


def _invoke_control_plan_json(
    message: str,
    *,
    phase: str = "",
    task_id: str = "",
    blueprint_id: str = "",
    writing_brief_tail: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    wizard_step: str = "",
    session_documents_brief: str = "",
    focus_document_brief: str = "",
    freeform_path: bool = False,
    invoke: InvokeFn | None,
    gaps_context_line: str = "",
) -> tuple[dict[str, Any], set[str]]:
    """调用统一调度分类器，返回 (JSON, allowed_tools)。失败 → ({}, allowed)。"""
    text = (message or "").strip()
    from report_writing.chat.intent_action_chain import (
        CHAT_CONTROL_PLAN_BLOCKLIST,
        CHAT_CONTROL_PLAN_POLICY,
        MULTI_ACTION_JSON_HINT,
    )

    allowed = sorted(k for k in CHAT_CONTROL_PLAN_POLICY.pipeline_rank if k not in CHAT_CONTROL_PLAN_BLOCKLIST)
    if freeform_path:
        _ff_block = {
            "blueprint_wizard",
            "begin_blueprint_wizard",
            "match_blueprints",
            "list_blueprints",
            "select_blueprint",
            "re_study_chapters",
        }
        allowed = [k for k in allowed if k not in _ff_block]
    allowed_set = set(allowed)
    if not text or invoke is None:
        return {}, allowed_set
    dialogue_lines = []
    for item in dialogue_tail or []:
        role = str(item.get("role") or "?")
        line = str(item.get("text") or "").strip().replace("\n", " ")[:300]
        if line:
            dialogue_lines.append(f"{role}: {line}")

    from report_writing.infra.config_settings import intent_tool_catalog_in_prompt
    from report_writing.chat.plan_classifier_prompt import turn_plan_system_prompt

    if not intent_tool_catalog_in_prompt():
        system = turn_plan_system_prompt(
            allowed,
            freeform_path=freeform_path,
            include_tool_catalog=False,
            multi_action_hint=MULTI_ACTION_JSON_HINT,
        )
    if intent_tool_catalog_in_prompt():
        system = (
        "你是对话写文档的统一意图调度器（唯一大脑）。根据本轮、近期对话与会话快照选出 1～4 个 Tool；"
        "说法不固定，按意思判断，不要靠固定句式。"
        "一句可含多个意图（定名+匹配+资料包+开写等），须全部列入 tools，不要只返回第一个。\n"
        "会话中途同样适用：改名并开写、新建资料包再继续写，都要输出完整链。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON，不要 Markdown：\n"
        '{"confidence":0.0,"uncertain":false,'
        '"slots":{"doc_name":null,"writing_path":null},"tools":[{"tool":"<id>","args":{}}],'
        '"missing":[]}\n'
        f"tool ∈ {{{', '.join(allowed)}}}\n"
        "slots：本轮已确定的要素，没有就 null。"
        "writing_path ∈ {match, template, freeform, null}。"
        "missing：本轮目标还缺的槽，∈ {doc_name, writing_path, freeform_mode, chapter_key, blueprint_id}。\n"
        "若 user 块含「规则观察」行，仅为后端只读快照（与 focus_needs_* 对齐），勿机械覆盖你的 missing/tools。\n"
        f"{MULTI_ACTION_JSON_HINT}\n"
        "tools 是唯一执行清单；后端按 JSON 的 tools 执行，不靠从标题/原话截取工具名。\n"
        "后端可能按依赖重排，并可能在定名后补 ask_entry_path。不自动注入 confirm_outline。不要填子 Agent 名。\n"
        "args 从原话提取：title / doc_name、blueprint_id、chapter_keys、chapter_key_hint、kind、query、"
        "chapter_sequences、document_title / blueprint_title 等。\n"
        "确定的槽位 → 列入 tools 并执行；缺的 → missing 并引导；说不清 → uncertain=true，降低 confidence，不要猜开写/选蓝本。\n"
        "\n"
        "## 同会话多文档\n"
        "默认操作当前焦点文档；未点名其它文档时勿切换。\n"
        "仅切换到会话里已有文档（「切到职业病那份」「看安全预评价」）→ focus_document，"
        "args 可带 document_title；文档窗口会跟随。\n"
        "「写/分析/配置 XX 文档的某章」→ document_title=XX + chapter_key_hint/sequences；"
        "除非明确另写一份，勿当成全新创建。\n"
        "「重写帮我写一份×× / 重新写一份×× / 另写一份××」= 新开文档，→ ask_entry_path，"
        "args 带 title=××；不要当成退回重写本章，也不要继续改当前焦点文档。\n"
        "正在写 A 时「另外写一份 B」且路径未清 → ask_entry_path（有蓝本 / 无蓝本·模版或自动大纲），"
        "args 带 title=B；勿直接 begin_blueprint_wizard，勿继续改 A。"
        "仅明确依据模版编写/上传样板时才 begin_blueprint_wizard。\n"
        "焦点已定名文档 A，用户又说「帮我写一份关于×× / 写一份××」且题材与 A 不同、"
        "又未说「另写/再写一份」也未说「改名叫/名字叫」→ uncertain=true 或 document_scope ask，"
        "tools=[]；禁止 set_session_meta(title=题材) 把 A 改名。\n"
        "焦点已在写（已选蓝本/有 w* 副本/导出阶段）即使用户尚未点选「定名」，"
        "「写一份 B」且 B 与当前展示名不同 → 同样先澄清或 uncertain，禁止静默改当前文档标题。\n"
        "只有用户明确「改名叫/名字改一下叫做」才 set_session_meta；"
        "只有明确「另写/再写一份」才按新开文档走。\n"
        "槽位不够准就不要猜硬动作：多文档歧义、写还是学未定、指代不明 → "
        "uncertain / missing / ask_intent，多轮问清再执行。\n"
        "\n"
        "## 向导与写作可并行\n"
        "向导只管创建步骤（名称/上传/提取/确认目录/分析要点）。"
        "向导未结束仍可调度写作、选章、加要素、再分析。\n"
        "\n"
        "## 写 vs 学\n"
        "已绑蓝本、写某章正文 → select_write_chapters 或 write_analyzed_only（不是 re_study）。\n"
        "write_scope：只要本章 → self；含下属/整章 → whole；只点章节号未说范围 → 勿填。\n"
        "分析/再分析/学习某章 → re_study_chapters；chapter_sequences 填顶层序号。\n"
        "study_scope：全部/整章 → whole；只要本章/不要子章 → self；只要下属 → children；"
        "否定子章节=self；未说勿填。\n"
        "\n"
        "## 要素 ≠ 蓝本\n"
        "要素=写作物料；蓝本=报告模板。查看/打开要素 → open_element_library；"
        "查看/浏览蓝本库 → open_blueprint_library；选型写作 → match_blueprints / list_blueprints。"
        "关联已有 → associate_elements；新建 → add_chapter_element；"
        "取消关联 → dissociate_chapter_element；删文档要素 → remove_document_element（不删库）；"
        "收入要素库 → collect_to_element_library。"
        "绝不要把「要素」当成蓝本去 match/list。\n"
        "新建文件要素+检索词 → add_chapter_element；「关联知识库」勿虚构旧目录绑定。\n"
        "\n"
        "## 资料库与资料包\n"
        "打开资料库页面 → open_knowledge；管当前文档绑定的资料包 → materials_pack_*"
        "（list/view/use/create/unbind/add_slot/remove_slot/update_slot/rename）。\n"
        "\n"
        "## 未绑蓝本时的入口（灵活收集，非固定流水线）\n"
        "必要槽：doc_name + writing_path（有蓝本 match / 无蓝本模版 template / 自动大纲 freeform）。\n"
        "一句全齐 → slots 填齐 + tools 列出全部（如 set_session_meta + match_blueprints）。\n"
        "只给了名字 → slots.doc_name + tools=[set_session_meta]，missing=[writing_path]。\n"
        "只给了写作方式 → tools 走 match/skip/wizard，missing=[doc_name]（「写一份××」题材不算已定名）。\n"
        "「用已有蓝本写××」且未 explicit 定名 → 仅 match_blueprints，missing=[doc_name]；同轮禁止 set_session_meta 静默定名。\n"
        "blueprint_id 已非空（已选用蓝本）且用户仅「名字叫…/改名叫…」→ tools=[set_session_meta]，missing=[]；禁止 match_blueprints。\n"
        "两槽都没有 → tools=[]，missing=[doc_name, writing_path]，uncertain 仅在真说不清时为 true。\n"
        "「帮我生成一份述职报告 名字叫lzb述职报告」→ "
        'slots.doc_name=lzb述职报告，tools=[set_session_meta]，missing=[writing_path]。\n'
        "「作lzb述职报告 自动生成章节目录 三个大章节就可以」→ "
        "slots.doc_name 不要填整句；工作名=lzb述职报告，writing_path=freeform，"
        "tools=[skip_blueprint, submit_outline]，missing=[]。\n"
        "「再给我写一份述职报告 三个章节 名字叫做lzb述职报告」→ "
        "slots.doc_name=lzb述职报告，writing_path=freeform，"
        "tools=[set_session_meta, skip_blueprint, submit_outline]；禁止 begin_blueprint_wizard。\n"
        "「名字叫lzb述职报告，用现有蓝本」→ set_session_meta + match_blueprints，missing=[]。\n"
        "「名字叫做lzb的述职报告 你帮我生成大纲」→ "
        "slots.doc_name=lzb的述职报告，writing_path=freeform，"
        "tools=[set_session_meta, skip_blueprint, submit_outline]，missing=[]；"
        "不要再要 freeform_mode，不要只挂模版/自动大纲点选卡。\n"
        "焦点已是「述职报告」定名后「帮我写一份关于ai应用对于市场的影响」→ "
        "uncertain=true 或 ask_intent（新开/改名/继续），tools=[]，"
        "禁止 slots.doc_name=ai题材，禁止 set_session_meta 改当前名。\n"
        "「帮我写一份报告」→ missing=[doc_name, writing_path]，不要猜 skip_blueprint。\n"
        "「你好」「谢谢」「在吗」等纯寒暄 → tools=[]，missing=[]，slots 全 null；"
        "禁止 set_session_meta、ask_entry_path、begin_blueprint_wizard；勿把寒暄词当 doc_name。\n"
        "「我有模版写一份安全报告 / 根据我的模版写安全报告」→ "
        "writing_path=template，tools=[begin_blueprint_wizard]，missing=[doc_name]；"
        "禁止 slots.doc_name=安全报告，禁止 set_session_meta(title=安全报告)。\n"
        "明确找库内蓝本 → match_blueprints / list_blueprints。\n"
        "点名某份蓝本 → match_blueprints 或 select_blueprint。\n"
        "明确创建/上传样板 → begin_blueprint_wizard + explicit_create=true。\n"
        "不要这份蓝本/换方式 → skip_blueprint。\n"
        "\n"
        "## 意图不清（开场与中途同一原则）\n"
        "指代不明、写还是学未定、多文档歧义 → uncertain=true 或 ask_intent（question + 2～3 options）。\n"
        "「写这一章」没说哪一章 → missing=[chapter_key]，不要猜第一章。\n"
        "范围未说清 → missing=[write_scope] 或 study_scope；要素/资料包未点名 → missing=[element_id]/[pack_id]。\n"
        "禁止低置信静默 start_writing / 选蓝本 / 删要素 / 收录入库。\n"
        "写作中途同样适用：改名、开写、确认章、加要素、资料包，都按 slots/tools/missing 输出，勿只服务开场。\n"
        )
        if freeform_path:
            system += (
                "\n"
                "## 当前已走无蓝本\n"
                "禁止 match/list/select/begin 蓝本与 re_study_chapters；"
                "学习/分析本章/生成写作信息 → generate_freeform_points；"
                "定题目/改名 → set_session_meta（必须 args.title=新名，可选 message=原话）；"
                "目录 → submit_outline。\n"
            )
        system += (
            "\n"
            "## 示例\n"
            "帮我写2.3章节 → select_write_chapters，chapter_sequences=[\"2.3\"]。\n"
            "为2.2添加文件要素 检索词为… → add_chapter_element，kind=file。\n"
            "查看资料库 → open_knowledge；列出资料包 → materials_pack_list；"
            "新建资料包叫变电站资料包 → materials_pack_create。\n"
            "改个名字 2026个人述职报告 / 改名为年度总结 → set_session_meta(title=新名)，"
            "args 带 message=原话、title=新名；不要 focus_document。\n"
            "改名叫年度总结然后开始写 → set_session_meta + select_write_chapters/start_writing（已绑蓝本时）。\n"
            "开写前查待补要素 → check_writing_supplements；用户说先写再说/不补了 → skip_writing_supplement_check。\n"
            "新建资料包叫XX再继续写 → materials_pack_create + 后续写作 Tool。\n"
            "已绑蓝本「把刚才那章写了」→ select_write_chapters；"
            "未绑「帮我出个安全预评价」→ missing 含 writing_path 或 ask_intent；"
            "「这一章」没说写还是学 → uncertain=true 或 ask_intent；"
            "看看有哪些要素 → open_element_library；"
            "收入要素库 → collect_to_element_library；"
            "看看有哪些蓝本 → list_blueprints；"
            "切到职业病那份 / 打开安全预评价文档 → focus_document；"
            "用这份蓝本，选前两章开始写 → select_blueprint + select_write_chapters + start_writing。"
        )
    docs_block = (session_documents_brief or "").strip() or "（尚无其它文档登记）"
    focus_line = (focus_document_brief or "").strip() or "（尚无焦点文档）"
    observe_line = (gaps_context_line or "").strip()
    user = (
        f"阶段：{phase or '—'}\ntask_id：{task_id or '—'}\nblueprint_id：{blueprint_id or '—'}\n"
        f"写作路径：{'无蓝本 freeform' if freeform_path else '未锁 / 蓝本可用'}\n"
        f"当前焦点文档：{focus_line}\n"
        f"当前向导步骤：{(wizard_step or '—').strip() or '—'}\n"
        f"写作摘要：{(writing_brief_tail or '—')[:400]}\n"
        f"本会话已登记文档：\n{docs_block}\n"
    )
    if observe_line:
        user += f"规则观察（只读参考）：{observe_line[:500]}\n"
    user += (
        f"近期对话：\n" + ("\n".join(dialogue_lines) if dialogue_lines else "（无）") + "\n"
        f"用户本轮：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("chat control plan llm failed: %s", error)
        return {}, allowed_set
    return _parse_classifier_json(raw), allowed_set


def classify_chat_control_plan_llm_scored(
    message: str,
    *,
    phase: str = "",
    task_id: str = "",
    blueprint_id: str = "",
    writing_brief_tail: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    wizard_step: str = "",
    session_documents_brief: str = "",
    focus_document_brief: str = "",
    freeform_path: bool = False,
    invoke: InvokeFn | None,
    min_steps: int = 1,
) -> tuple[list[dict[str, Any]], float]:
    """统一调度计划（含置信度）：返回 (已排序 steps, confidence)。失败或过低 → ([], conf)。"""
    from report_writing.chat.intent_turn_plan import parse_turn_plan

    data, allowed = _invoke_control_plan_json(
        message,
        phase=phase,
        task_id=task_id,
        blueprint_id=blueprint_id,
        writing_brief_tail=writing_brief_tail,
        dialogue_tail=dialogue_tail,
        wizard_step=wizard_step,
        session_documents_brief=session_documents_brief,
        focus_document_brief=focus_document_brief,
        freeform_path=freeform_path,
        invoke=invoke,
    )
    plan = parse_turn_plan(data, allowed_tools=allowed)
    if plan.confidence < _PLAN_MIN_CONFIDENCE:
        return [], plan.confidence
    need = max(1, int(min_steps or 1))
    if len(plan.tools) < need:
        return [], plan.confidence
    return plan.tools, plan.confidence


def classify_turn_plan_llm(
    message: str,
    *,
    session: Any = None,
    invoke: InvokeFn | None,
    freeform_path: bool | None = None,
    session_gaps: Any = None,
):
    """整轮 JSON 计划（含 slots / missing / uncertain）。失败返回空 TurnPlan。"""
    from report_writing.chat.intent_turn_plan import TurnPlan, parse_turn_plan
    from report_writing.domain.models import ChatSession as _ChatSession

    text = (message or "").strip()
    if not text or invoke is None:
        return TurnPlan()
    sess = session if session is not None else _ChatSession(id="anon")
    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.intent_router import active_blueprint_wizard
    from report_writing.chat.react_speed import control_dialogue_max_turns
    from report_writing.chat.session_documents import focus_document_brief, session_documents_brief
    from report_writing.chat.session_snapshot import session_turn_snapshot
    from report_writing.chat.session_state import user_chose_skip_blueprint

    snap = session_turn_snapshot(sess, text)
    wiz = dict(active_blueprint_wizard(sess) or {})
    ff = user_chose_skip_blueprint(sess) if freeform_path is None else bool(freeform_path)
    gaps_line = ""
    if session_gaps is not None:
        gaps_line = str(getattr(session_gaps, "context_line", "") or "").strip()
        if not gaps_line and isinstance(session_gaps, dict):
            gaps_line = str(session_gaps.get("context_line") or "").strip()
    data, allowed = _invoke_control_plan_json(
        text,
        phase=str(sess.phase or ""),
        task_id=str(sess.task_id or ""),
        blueprint_id=str(sess.blueprint_id or wiz.get("blueprint_id") or ""),
        writing_brief_tail=str(snap.get("writing_brief_tail") or ""),
        dialogue_tail=format_full_dialogue(sess, max_turns=control_dialogue_max_turns()),
        wizard_step=str(wiz.get("step") or ""),
        session_documents_brief=session_documents_brief(sess),
        focus_document_brief=focus_document_brief(sess),
        freeform_path=ff,
        invoke=invoke,
        gaps_context_line=gaps_line,
    )
    return parse_turn_plan(data, allowed_tools=allowed)


def classify_chat_control_plan_llm(
    message: str,
    *,
    phase: str = "",
    task_id: str = "",
    blueprint_id: str = "",
    writing_brief_tail: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    wizard_step: str = "",
    session_documents_brief: str = "",
    focus_document_brief: str = "",
    freeform_path: bool = False,
    invoke: InvokeFn | None,
    min_steps: int = 1,
) -> list[dict[str, Any]]:
    """统一调度计划：返回已按 CHAT_CONTROL_PLAN_POLICY 排序的 [{tool, args}, …]；不足 min_steps 或失败返回 []。"""
    steps, _confidence = classify_chat_control_plan_llm_scored(
        message,
        phase=phase,
        task_id=task_id,
        blueprint_id=blueprint_id,
        writing_brief_tail=writing_brief_tail,
        dialogue_tail=dialogue_tail,
        wizard_step=wizard_step,
        session_documents_brief=session_documents_brief,
        focus_document_brief=focus_document_brief,
        freeform_path=freeform_path,
        invoke=invoke,
        min_steps=min_steps,
    )
    return steps


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
    from report_writing.chat.intent_context import format_dialogue_lines

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
    from report_writing.chat.intent_context import format_dialogue_lines

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


def classify_unclear_intent_llm(
    message: str,
    *,
    phase: str = "",
    blueprint_id: str = "",
    wizard_step: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    session_goal_brief: str = "",
    task_id: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """意图不清时生成多轮追问。失败返回 {}。须先结合上下文，勿凭空追问。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    from report_writing.chat.intent_action_chain import (
        CHAT_CONTROL_PLAN_BLOCKLIST,
        CHAT_CONTROL_PLAN_POLICY,
    )
    from report_writing.chat.intent_context import classifier_context_user_block

    allowed = sorted(
        k
        for k in CHAT_CONTROL_PLAN_POLICY.pipeline_rank
        if k not in CHAT_CONTROL_PLAN_BLOCKLIST and k not in {"ask_intent", "ask_entry_path"}
    )
    system = (
        "你在写报告对话里做多轮意图澄清。先结合焦点文档、已登记文档与近期对话收集语料，"
        "再决定还缺什么；能从上下文推断的不要再问。说法不固定，按意思分析。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON，不要 Markdown：\n"
        '{"question":"","options":[{"id":"","label":"","tool":"","args":{}}],"confidence":0.0}\n'
        "question：一句短口语追问；勿编号、勿说「作为AI」。\n"
        "options：2～3 个；id=英文短词，label=人话。\n"
        f"tool ∈ {{{', '.join(allowed)}}} 或留空。\n"
        "\n"
        "## 规则\n"
        "1) 未绑蓝本、像要写文档 → 先区分有蓝本 / 无蓝本；无蓝本再区分依据模版 / 自动大纲"
        "（tool=match_blueprints / begin_blueprint_wizard / skip_blueprint）；label 用人话。\n"
        "2) 题目未清 → 追问写什么、叫什么；勿强迫「类型｜标题」。\n"
        "3) 已有蓝本但未说写还是学 → 开写 / 学习 / 加要素。\n"
        "4) 闲聊也先问想写什么，仍给入口两步（有蓝本｜无蓝本）。\n"
        "5) 多文档时选项/追问须点明文档（用标题或 doc_id），避免串文档。\n"
        "\n"
        f"## 不确定：confidence < {_MIN_CONFIDENCE}。"
    )
    user = classifier_context_user_block(
        text=text,
        ctx={
            "phase": phase,
            "task_id": task_id,
            "blueprint_id": blueprint_id,
            "focus_document_brief": focus_document_brief,
            "session_documents_brief": session_documents_brief,
            "session_goal_brief": session_goal_brief,
            "dialogue_tail": dialogue_tail or [],
        },
        extra_lines=[f"当前向导步骤：{(wizard_step or '—').strip() or '—'}"],
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("unclear intent llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    if not isinstance(data, dict):
        return {}
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    question = str(data.get("question") or "").strip()
    raw_opts = data.get("options")
    options: list[dict[str, Any]] = []
    if isinstance(raw_opts, list):
        allowed_set = set(allowed)
        for item in raw_opts:
            if not isinstance(item, dict):
                continue
            oid = str(item.get("id") or "").strip()
            label = str(item.get("label") or "").strip()
            if not oid or not label:
                continue
            tool = str(item.get("tool") or "").strip()
            if tool and tool not in allowed_set:
                tool = ""
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            # 入口三条：模型常留空 tool，按 id/label 补全，避免点选后无槽位
            if not tool:
                from report_writing.chat.intent_clarify import enrich_control_option_slots

                enriched = enrich_control_option_slots(oid, label, {})
                tool = str(enriched.get("tool") or "").strip()
                if tool and tool not in allowed_set and tool not in {
                    "match_blueprints",
                    "begin_blueprint_wizard",
                    "skip_blueprint",
                }:
                    tool = ""
            options.append({"id": oid, "label": label, "tool": tool, "args": dict(args)})
    if not question or len(options) < 2 or confidence < _MIN_CONFIDENCE:
        return {}
    return {"question": question, "options": options[:3], "confidence": confidence}


SESSION_GOAL_SIGNAL_INTENTS: dict[str, str] = {
    "remember_goal": "用户明确要求记住/今后都按/别忘了某写作目标或项目背景（不是单纯报一行类型｜标题）",
    "none": "不是显式「记住」类话术",
}


def classify_explicit_remember_goal_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    invoke: InvokeFn | None,
) -> bool:
    """是否「记住/今后按…」类显式写作目标话术（替代 session_goal 正则表）。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return False
    from report_writing.chat.intent_context import format_dialogue_lines

    allowed = [k for k in SESSION_GOAL_SIGNAL_INTENTS if k != "none"]
    system = (
        "角色：显式「记住目标」信号分类器。结合近期对话判断本句是否在要求记住写作目标。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"intent":"<id>","confidence":0.0}\n'
        f"intent ∈ {{{', '.join(allowed + ['none'])}}}\n"
        "\n"
        "## 意图含义\n"
        + _intent_lines(SESSION_GOAL_SIGNAL_INTENTS)
        + "\n\n"
        "## 规则\n"
        "一行「类型｜标题」不算 remember_goal → none。\n"
        "\n"
        f"## {_uncertain_line()}"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=4, max_chars=200)
    user = (
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("remember goal signal llm failed: %s", error)
        return False
    data = _parse_classifier_json(raw)
    intent = str(data.get("intent") or "none").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return intent == "remember_goal" and confidence >= _MIN_CONFIDENCE


def classify_doc_meta_line_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, str]:
    """判断是否为「文档类型｜标题」元信息行，并抽取字段（替代 looks_like_freeform_meta 正则路由）。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    from report_writing.chat.intent_context import format_dialogue_lines

    system = (
        "角色：文档元信息行分类器。结合近期对话判断是否在定题/改名，并抽取类型与标题。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"is_meta_line":false,"confidence":0.0,"doc_category":"","doc_title":""}\n'
        "\n"
        "## 规则\n"
        "粘贴章节目录（多行或 1.1 序号开头）→ is_meta_line=false。\n"
        "上文在改名/定题，本句只给具体题目（可无「类型｜标题」）→ is_meta_line=true，"
        "doc_title=题目；类型可沿用焦点文档或留空。\n"
        "\n"
        "## 示例\n"
        "工作说明｜城南变电站工程 → is_meta_line=true。\n"
        "上文问新文档名，用户回「2026年度工作汇报」→ is_meta_line=true，doc_title=2026年度工作汇报。\n"
        "\n"
        f"## 不确定：is_meta_line=false，confidence < {_MIN_CONFIDENCE}。"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("doc meta line llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not data.get("is_meta_line") or confidence < _MIN_CONFIDENCE:
        return {}
    category = str(data.get("doc_category") or "").strip()
    title = str(data.get("doc_title") or "").strip()
    if not title and not category:
        return {}
    out: dict[str, str] = {}
    if category:
        out["doc_category"] = category
    if title:
        out["doc_title"] = title
    if category or title:
        out["summary"] = f"{category or '文档'}｜{title}".strip("｜")
    return out


SESSION_GOAL_ACTION_INTENTS: dict[str, str] = {
    "set_session_meta": "用户在定题或改名（含「改个名字 XXX」「改名为 XXX」）；payload 必须填 title=新名，可选 category、message",
    "remember_writing_goal": "用户明确要求记住身份、岗位、项目或写作背景，后续都要遵守",
    "none": "不是定题目也不是显式记住目标",
}


def classify_session_goal_action_llm(
    message: str,
    *,
    phase: str = "",
    session_goal_brief: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    task_id: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """口语 → set_session_meta / remember_writing_goal；失败返回 {}。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}

    allowed = [k for k in SESSION_GOAL_ACTION_INTENTS if k != "none"]
    from report_writing.chat.intent_context import classifier_context_user_block

    system = (
        "你是写作对话的「会话目标」动作分类器。结合近期对话与焦点文档判断本句操作；"
        "改名/定题常承接上文，不要只看孤立一句。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"action":"<id>","confidence":0.0,"payload":{}}\n'
        f"action ∈ {{{', '.join(allowed + ['none'])}}}\n"
        "\n"
        "## 动作含义\n"
        + _intent_lines(SESSION_GOAL_ACTION_INTENTS)
        + "\n\n"
        "## payload 规则\n"
        "- set_session_meta：必须填结构化 title（改名/定名的新名）；可选 category、message=原话。禁止只丢整句指望执行层正则剥标题。\n"
        "- remember_writing_goal：notes=原话摘要。\n"
        "- none：payload={}。\n"
        "\n"
        "## 规则\n"
        "找蓝本、生成目录、加要素、确认章节 → action=none（交给其它分类器）。\n"
        "「改个名字 2026个人述职报告」→ set_session_meta，title=2026个人述职报告（务必带 title）。\n"
        "上文在问文档名，本句给出具体题目 → set_session_meta，title=题目。\n"
        "\n"
        f"## {_uncertain_line(field='action')}"
    )
    user = classifier_context_user_block(
        text=text,
        ctx={
            "phase": phase,
            "task_id": task_id,
            "session_goal_brief": session_goal_brief,
            "focus_document_brief": focus_document_brief,
            "session_documents_brief": session_documents_brief,
            "dialogue_tail": dialogue_tail or [],
        },
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("session goal action llm failed: %s", error)
        return {}

    data = _parse_classifier_json(raw)
    action = str(data.get("action") or "none").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    payload = data.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    if action == "none" or confidence < _MIN_CONFIDENCE or action not in allowed:
        meta = classify_doc_meta_line_llm(
            text,
            dialogue_tail=dialogue_tail,
            focus_document_brief=focus_document_brief,
            invoke=invoke,
        )
        if not meta:
            # 离线/解析失败：仅认「类型｜标题」分隔结构，不靠口语正则猜意图
            from report_writing.chat.session_goal import _parse_meta_line_structural

            meta = _parse_meta_line_structural(text)
        if meta:
            return {
                "action": "set_session_meta",
                "confidence": 0.85,
                "payload": {
                    "message": text,
                    "category": meta.get("doc_category") or "",
                    "title": meta.get("doc_title") or "",
                },
            }
        if classify_explicit_remember_goal_llm(text, invoke=invoke):
            return {"action": "remember_writing_goal", "confidence": 0.85, "payload": {"notes": text}}
        return {}
    return {"action": action, "confidence": confidence, "payload": payload}


def classify_freeform_dispatch_route_llm(
    message: str,
    *,
    phase: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    invoke: InvokeFn | None,
) -> str:
    """无蓝本快车道：react 交给总控 vs freeform_fast 继续专用路由 vs none。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return "none"
    from report_writing.chat.intent_context import format_dialogue_lines

    system = (
        "角色：无蓝本路径分派器。结合近期对话与焦点文档判断本句走快车道还是总控。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"route":"freeform_fast"|"react"|"none","confidence":0.0}\n'
        "\n"
        "## 取值\n"
        "- freeform_fast：生成/确认目录、要点、跳过要点、开写、学习/生成写作信息、托管起草、定题目、记住目标。\n"
        "- react：多句复杂诉求、开放问答、需总控多步推理、跨文档切换后复杂操作。\n"
        "- none：不确定。\n"
        "\n"
        f"## 不确定：route=none，confidence < {_MIN_CONFIDENCE}。"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=6, max_chars=240)
    user = (
        f"阶段：{phase or '—'}\n"
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("freeform dispatch route llm failed: %s", error)
        return "none"
    data = _parse_classifier_json(raw)
    route = str(data.get("route") or "none").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE or route not in {"freeform_fast", "react"}:
        return "none"
    return route


FREEFORM_ACTION_INTENTS: dict[str, str] = {
    "generate_outline": "请系统起草/生成章节目录或大纲",
    "confirm_outline": "确认当前目录/大纲可以定稿继续",
    "generate_points": (
        "请系统生成/重写各章写作要点或写作信息；"
        "含「学习本章」「依据样本学习」「帮我学一下」「生成写作信息」等口语"
        "（无蓝本没有样本学习，一律按生成要点理解）"
    ),
    "skip_points": "跳过要点配置，直接开写",
    "start_writing": "开始写作/开写/写正文/先写已分析的章",
    "help_draft": "用户不会写，希望系统先帮忙起草（目录或要点）",
    "none": "不属于无蓝本阶段动作",
}


def classify_freeform_action_llm(
    message: str,
    *,
    phase: str = "",
    task_id: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    session_goal_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    allowed = [k for k in FREEFORM_ACTION_INTENTS if k != "none"]
    from report_writing.chat.intent_action_chain import MULTI_ACTION_JSON_HINT
    from report_writing.chat.intent_context import classifier_context_user_block

    system = (
        "你是无蓝本写作流程的动作分类器。结合近期对话、焦点文档与阶段判断操作；"
        "不要只看本句孤立措辞，指代（继续/那个/刚才）要落到上下文文档。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"intent":"<id>","intents":["<动作1>","<动作2>"],"confidence":0.0}\n'
        f"intent / intents ∈ {{{', '.join(allowed + ['none'])}}}\n"
        f"{MULTI_ACTION_JSON_HINT}\n"
        "后端按 生成目录 → 确认目录 → 生成要点 → 开写 校正顺序。\n"
        "\n"
        "## 意图含义\n"
        + _intent_lines(FREEFORM_ACTION_INTENTS)
        + "\n\n"
        "## 规则\n"
        "无蓝本没有「依据样本学习」：学习/分析本章/生成写作信息 → generate_points。\n"
        "「生成目录」「起草目录」「目录大纲」「搭个目录」「重新生成目录」→ generate_outline；"
        "起草「大纲 / 目录 / 章节结构」语义相同（如「自动生成大纲」「帮我起草章节结构」）"
        "→ generate_outline；绝不要判成 generate_points（要点是各章怎么写，目录是章节结构）。\n"
        "「每个点下面有三小点 / 每章下三个小节 / 加子节」若语境是目录/大纲/章节结构"
        "→ generate_outline（嵌套编号），不要判成 generate_points。\n"
        "只有明确提到「要点 / 写作信息 / 配要点」才用 generate_points。\n"
        "默认针对当前焦点文档（见 doc_id）；用户点名其它文档时再切换。\n"
        "\n"
        f"## {_uncertain_line()}"
    )
    user = classifier_context_user_block(
        text=text,
        ctx={
            "phase": phase,
            "task_id": task_id,
            "focus_document_brief": focus_document_brief,
            "session_documents_brief": session_documents_brief,
            "session_goal_brief": session_goal_brief,
            "dialogue_tail": dialogue_tail or [],
        },
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("freeform action llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return {}

    from report_writing.chat.intent_action_chain import FREEFORM_POLICY, order_action_ids, parse_id_list_from_classifier

    parsed = [
        i
        for i in parse_id_list_from_classifier(
            data, plural_keys=("intents",), single_keys=("intent",)
        )
        if i in allowed
    ]
    # 单意图：勿 inject 前置（否则「生成要点」会被扩成 confirm_outline 链）
    if len(parsed) == 1:
        return {"intent": parsed[0], "confidence": confidence}
    intent_ids = [i for i in order_action_ids(parsed, FREEFORM_POLICY) if i in allowed]
    if len(intent_ids) > 1:
        return {"intents": intent_ids, "confidence": confidence}
    intent = str(data.get("intent") or (intent_ids[0] if intent_ids else "none")).strip()
    if intent == "none" or intent not in allowed:
        return {}
    return {"intent": intent, "confidence": confidence}


USER_OWN_SAMPLE_INTENTS: dict[str, str] = {
    "owns_sample": "用户表示手头有 Word/PDF 样板、样本或参照文档，要上传后按样板提取目录/学习再写",
    "none": "未强调自带样本文件，或只是要用系统里已有蓝本/无蓝本写",
}


def classify_user_own_sample_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    invoke: InvokeFn | None,
) -> bool:
    """是否「我有样本/按样板写」类（入口纠偏，不用正则表）。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return False
    from report_writing.chat.intent_context import format_dialogue_lines

    allowed = [k for k in USER_OWN_SAMPLE_INTENTS if k != "none"]
    system = (
        "角色：自带样板信号分类器。结合近期对话判断是否在说自己有样板/样本文件需上传"
        "（不是仅找库内蓝本）。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"intent":"<id>","confidence":0.0}\n'
        f"intent ∈ {{{', '.join(allowed + ['none'])}}}\n"
        "\n"
        "## 意图含义\n"
        + _intent_lines(USER_OWN_SAMPLE_INTENTS)
        + "\n\n"
        "## 示例\n"
        "我有样本帮我写安评 → owns_sample；写一份环评报告 → none。\n"
        "\n"
        f"## {_uncertain_line()}"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=4, max_chars=200)
    user = (
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("user own sample llm failed: %s", error)
        return False
    data = _parse_classifier_json(raw)
    intent = str(data.get("intent") or "none").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return intent == "owns_sample" and confidence >= _MIN_CONFIDENCE


def extract_write_subject_title_llm(
    message: str,
    *,
    invoke: InvokeFn | None,
) -> str:
    """从口语里抽要写的主体题名（供 create_blueprint 的 blueprint_title 兜底）。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return ""
    system = (
        "角色：写作用题目抽取器。从用户话提取文档/报告题目或类型名，供蓝本命名。\n"
        "说法不固定，按意思抽取，不要靠固定句式。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"doc_title":"","confidence":0.0}\n'
        "\n"
        "## 规则\n"
        "缺明确题目 → doc_title 留空；勿编造用户没说的内容。\n"
        "一句含多个动作时，doc_title 只填短文档名，不要把「生成目录 / 三个章节 / 用蓝本 / 就可以」写进去。\n"
        "「作lzb述职报告 自动生成章节目录 三个大章节就可以」→ "
        '{"doc_title":"lzb述职报告","confidence":0.9}\n'
        "「再给我写一份述职报告 三个章节 名字叫做lzb述职报告」→ "
        '{"doc_title":"lzb述职报告","confidence":0.92}\n'
        "\n"
        f"## 不确定：doc_title 留空，confidence < {_MIN_CONFIDENCE}。"
    )
    user = f"用户：{text[:2000]}"
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("write subject title llm failed: %s", error)
        return ""
    data = _parse_classifier_json(raw)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return ""
    return str(data.get("doc_title") or data.get("blueprint_title") or "").strip()


def classify_document_scope_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """同会话多文档：本句是续写当前、新开一份、切已有、退回重写本章，或需追问。

    用户说法不固定，按意思判断；失败返回 {}。
    """
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    from report_writing.chat.intent_context import format_dialogue_lines

    allowed = (
        "continue",
        "force_new",
        "switch_existing",
        "chapter_revise",
        "rewrite_current",
        "ask",
    )
    system = (
        "你是「文档作用域」分类器。用户说法不固定，按意思判断，不要靠固定句式或关键词表。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"action":"continue|force_new|switch_existing|chapter_revise|rewrite_current|ask",'
        '"title":"","confidence":0.0}\n'
        "\n"
        "## 含义\n"
        "- continue：继续当前焦点文档（分析/配置/写某章、把这份写完等），且题材与当前文档一致或用户明确续写\n"
        "- force_new：另开/再开一份新文档（与当前并行），或题材与当前标题明显不同的新写请求\n"
        "- switch_existing：点名会话里另一份已有文档并切换过去\n"
        "- chapter_revise：退回重写某一章正文，不是新开文档\n"
        "- rewrite_current：清空/整份重写当前这份文档正文，不新开、不改名\n"
        "- ask：说不清是「新开 / 改名 / 整份重写当前 / 继续」→ 必须追问；禁止猜改名、禁止默认 continue\n"
        "\n"
        "## 规则\n"
        "1) 「另外写一份」「再来一份」「再帮我写一份」「从头再写一份」「重新写一份××」→ force_new，"
        "title=题材短名。\n"
        "2) 「这一章退回重写」「重写第2章」「按意见改写本章」→ chapter_revise。\n"
        "3) 焦点已在写（已定名或已选蓝本/有写作副本），用户「写一份×× / 帮我写一份××」"
        "且题材与当前展示名明显不同、又未说「另写/再写/新写一份」也未显式「改名叫」→ "
        "优先 action=ask（新开/改名/继续三选一），confidence 约 0.55～0.75；"
        "仅当用户有强新开词（另写/再写一份等）或焦点几乎是空壳待首次定名且题材即文档名时 → force_new。\n"
        "4) 仅「帮我写一份××」且与当前焦点同题、用户未说另开 → continue；点名另一份已有 → switch_existing。\n"
        "5) 「切到职业病那份」「看安全预评价那份」→ switch_existing，title=那份标题。\n"
        "6) 裸「重新写」「重写」「全部重写」且无章节锚点、无新题材 → ask 或 rewrite_current；"
        "不要 continue。\n"
        "7) title：新开/切换/题材的短标题；ask 时也尽量填题材短名；没有则留空。\n"
        "8) 不确定时 action=ask 且 confidence 降低；禁止默认 continue 去改当前文档名。\n"
        "9) 结合近期对话与本轮话判断，综合判断。判断不准时 action=ask 且 confidence 降低。"
        "\n"
        "## 示例\n"
        "重写帮我写一份安全报告 → "
        '{"action":"force_new","title":"安全报告","confidence":0.9}\n'
        "第1.4章退回重写 → "
        '{"action":"chapter_revise","title":"","confidence":0.9}\n'
        "继续分析这一章 → "
        '{"action":"continue","title":"","confidence":0.88}\n'
        "焦点已是「述职报告」，用户「帮我写一份关于ai应用对于市场的影响」→ "
        '{"action":"ask","title":"AI应用对市场的影响","confidence":0.68}\n'
        "焦点已定名，用户只说「重新写」→ "
        '{"action":"ask","title":"","confidence":0.55}\n'
        "\n"
        f"## {_uncertain_line(field='action', none_value='ask')}"
    )
    lines = format_dialogue_lines(dialogue_tail, limit=6, max_chars=280)
    user = (
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"本会话已登记文档：\n{(session_documents_brief or '（无）').strip() or '（无）'}\n"
        f"近期对话：\n" + ("\n".join(lines) if lines else "（无）") + "\n"
        f"用户：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("document scope llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    action = str(data.get("action") or "").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    # 低置信或空动作 → ask，勿默认 continue
    if action not in allowed or confidence < _MIN_CONFIDENCE:
        if action in {"", "none", "unclear", "ask"} or confidence > 0:
            return {
                "action": "ask",
                "title": str(data.get("title") or "").strip(),
                "confidence": confidence,
            }
        return {}
    return {
        "action": action,
        "title": str(data.get("title") or "").strip(),
        "confidence": confidence,
    }


def classify_explicit_doc_name_given_llm(
    message: str,
    *,
    dialogue_tail: list[dict[str, str]] | None = None,
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """判断本句是否已明确给出文档名（可跳过二次确认）。失败返回 {}。

    返回：has_explicit_name / title / residual / confidence
    """
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    from report_writing.chat.intent_context import format_dialogue_lines

    system = (
        "判断用户这句是否已经明确给出「写作文档名称」（可据此直接定名，无需再确认）。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"has_explicit_name":false,"title":"","residual":"","confidence":0.0}\n'
        "\n"
        "## 规则\n"
        "- has_explicit_name=true：用户明确起了或改了文档名"
        "（名字叫/名字改一下叫做/改名叫/标题是/就叫某某），title=短标题。\n"
        "- 「帮我写一份安全报告」「用我的模版」仅题材/路径 → has_explicit_name=false"
        "（仍需确认文档名；勿把「安全报告」当已定名）。\n"
        "- residual：定名/改名之外的后续意图原话（匹配蓝本、生成目录、三个章节等）；没有留空。\n"
        "- title 必须是短文档名；禁止把整句或多动作短语当作 title。\n"
        "\n"
        "## 示例\n"
        "「帮我写一份安全报告，名字叫做1101安全预评价」→ "
        '{"has_explicit_name":true,"title":"1101安全预评价","residual":"","confidence":0.9}\n'
        "「名字改一下叫做 lzb 安全报告」→ "
        '{"has_explicit_name":true,"title":"lzb安全报告","residual":"","confidence":0.9}\n'
        "「帮我写一份安全报告 用我的模版」→ "
        '{"has_explicit_name":false,"title":"","residual":"用我的模版","confidence":0.9}\n'
        "「帮我写一份述职报告」→ "
        '{"has_explicit_name":false,"title":"","residual":"","confidence":0.85}\n'
        "「再给我写一份述职报告 三个章节 名字叫做lzb述职报告」→ "
        '{"has_explicit_name":true,"title":"lzb述职报告","residual":"三个章节","confidence":0.92}\n'
        "「作lzb述职报告 自动生成章节目录 三个大章节就可以」→ "
        '{"has_explicit_name":false,"title":"","residual":"自动生成章节目录 三个大章节就可以","confidence":0.88}\n'
        "\n"
        f"## 不确定：has_explicit_name=false，confidence < {_MIN_CONFIDENCE}。"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=4, max_chars=200)
    user = (
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户这句：{text[:1500]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("explicit doc name llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    if not data:
        return {}
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return {}
    title = str(data.get("title") or "").strip()[:160]
    has_name = bool(data.get("has_explicit_name")) and bool(title)
    return {
        "has_explicit_name": has_name,
        "title": title if has_name else "",
        "residual": str(data.get("residual") or "").strip()[:500],
        "confidence": confidence,
    }


NO_MATCH_PATH_INTENTS: dict[str, str] = {
    "use_own_sample": "用户要用自己的 Word/PDF 样板建蓝本、从样本提取目录",
    "help_build_outline": "用户没有样板，希望先帮忙起草/生成可调的大纲目录（无蓝本）",
    "skip_blueprint_freeform": "用户明确跳过蓝本，自己定题目或已有目录想法",
    "match_blueprints_again": "用户还想在库里再找/换一批蓝本",
    "none": "尚未选择或需要澄清",
}


def classify_no_match_path_llm(
    message: str,
    *,
    topic: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """蓝本未匹配后的追问轮：用户选择后续路径。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}
    from report_writing.chat.intent_context import format_dialogue_lines

    allowed = [k for k in NO_MATCH_PATH_INTENTS if k != "none"]
    system = (
        "角色：蓝本未匹配后的路径分类器。上一轮已告知库里没有特别对口的蓝本，"
        "正在问用户接下来怎么走；结合近期对话与本轮话判断选择。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"intent":"<id>","confidence":0.0}\n'
        f"intent ∈ {{{', '.join(allowed + ['none'])}}}\n"
        "\n"
        "## 意图含义\n"
        + _intent_lines(NO_MATCH_PATH_INTENTS)
        + "\n\n"
        f"## {_uncertain_line()}"
    )
    dlg = format_dialogue_lines(dialogue_tail, limit=4, max_chars=200)
    user = (
        f"最初题目：{(topic or '—')[:300]}\n"
        f"近期对话：\n" + ("\n".join(dlg) if dlg else "（无）") + "\n"
        f"用户本轮：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("no match path llm failed: %s", error)
        return {}
    data = _parse_classifier_json(raw)
    intent = str(data.get("intent") or "none").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if intent == "none" or confidence < _MIN_CONFIDENCE or intent not in allowed:
        return {}
    return {"intent": intent, "confidence": confidence}


ENTRY_ROUTING_INTENTS: dict[str, str] = {
    "skip_blueprint": "不要/跳过蓝本，或直接写、无蓝本、自己定大纲；含「不用这个蓝本」「不要当前这份」",
    "create_blueprint": (
        "依据模版编写：对话里为当前文档上传样板、提取目录、分析要点（w* 副本，收录前不入蓝本库）；"
        "含「我有样本/样板」「按样本写」「依据样本」；"
        "不含仅说「生成目录/起草目录/搭个目录」（那是自动大纲，intent=none）"
    ),
    "save_freeform_blueprint": "把当前无蓝本写作收录到蓝本库/保存为蓝本（需已确认大纲）",
    "match_blueprints": "找系统里已有蓝本、列蓝本、用库内模板写（用户未强调自带样本文件，也未点名要用哪一份）",
    "explicit_select": "明确点名/指定要用哪一份蓝本（就用这份、选用「××」）；不是泛泛写某类报告",
    "path_correction": "纠正理解：不是蓝本、走无蓝本、别匹配蓝本、不要当前这份蓝本",
    "general_write": "描述要写什么但未提蓝本/模板、未点名选用哪一份",
    "none": "与入口路由无关",
}


def classify_entry_routing_llm(
    message: str,
    *,
    phase: str = "",
    session_goal_brief: str = "",
    dialogue_tail: list[dict[str, str]] | None = None,
    focus_document_brief: str = "",
    session_documents_brief: str = "",
    invoke: InvokeFn | None,
) -> dict[str, Any]:
    """会话入口：跳过/创建蓝本/match 等，统一意图模型。"""
    text = (message or "").strip()
    if not text or invoke is None:
        return {}

    allowed = [k for k in ENTRY_ROUTING_INTENTS if k != "none"]
    from report_writing.chat.intent_context import format_dialogue_lines

    lines = format_dialogue_lines(dialogue_tail, limit=6, max_chars=300)

    system = (
        "你是报告写作对话的「入口意图」分类器。结合近期对话与已有文档判断本句入口意图。\n"
        "\n"
        "## 输出\n"
        "只返回 JSON：\n"
        '{"intent":"<id>","confidence":0.0,"blueprint_title":"","agent_may_select":false,"is_vague_write":false}\n'
        f"intent ∈ {{{', '.join(allowed + ['none'])}}}\n"
        "\n"
        "## 意图含义\n"
        + _intent_lines(ENTRY_ROUTING_INTENTS)
        + "\n\n"
        "## 规则\n"
        "1) create_blueprint → blueprint_title 填报告/蓝本名（若有）。\n"
        "2) explicit_select 仅当明确选用某份蓝本；此时 agent_may_select=true。\n"
        "3) 「帮我写一份××报告」主题碰巧像蓝本标题 → match_blueprints 或 general_write，"
        "is_vague_write=true，agent_may_select=false（系统不得代选）。\n"
        "4) 配蓝本时「不用这个蓝本」→ skip_blueprint 或 path_correction。\n"
        "5) 要素 ≠ 蓝本：只说查看/打开要素库 → none（交给总控）；"
        "仅明确说蓝本/模板/样板库才 match。\n"
        "6) 「生成目录」「起草目录」「目录大纲」「搭个目录」「重新生成目录」→ none"
        "（不是 create_blueprint；未提上传样板时不要当创建蓝本）。\n"
        "7) 「我有模版/参考模版写作/按模版写」→ create_blueprint（依据模版上传提取，不是选用库蓝本 match）。\n"
        "8) 焦点文档 onboarding 待定：按 gaps 补缺的要素（定名/选路/match 顺序不固定）；开写前须 writing_doc_named。\n"
        "\n"
        "## 示例\n"
        "我有样本，帮我写安全预评价报告 → create_blueprint（不是 match）。\n"
        "我有模版参考模版写作 → create_blueprint。\n"
        "帮我写一份安全报告 → general_write 或 match_blueprints，is_vague_write=true。\n"
        "就用「安全报告」这份蓝本 → explicit_select，agent_may_select=true。\n"
        "生成目录 → none。\n"
        "看看有哪些要素 → none。\n"
        "\n"
        f"## {_uncertain_line()}"
    )
    user = (
        f"阶段：{(phase or '—').strip()}\n"
        f"已记住：{(session_goal_brief or '—')[:300]}\n"
        f"当前焦点文档：{(focus_document_brief or '—').strip() or '—'}\n"
        f"本会话已登记文档：\n{(session_documents_brief or '（无）').strip() or '（无）'}\n"
        f"近期对话：\n" + ("\n".join(lines) if lines else "（无）") + "\n"
        f"用户：{text[:2000]}"
    )
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("entry routing llm failed: %s", error)
        return {}

    data = _parse_classifier_json(raw)
    intent = str(data.get("intent") or "none").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if intent == "none" or confidence < _MIN_CONFIDENCE or intent not in allowed:
        return {}

    blueprint_title = str(data.get("blueprint_title") or "").strip()

    if intent in {"match_blueprints", "general_write"} and classify_user_own_sample_llm(
        text, dialogue_tail=dialogue_tail, invoke=invoke
    ):
        intent = "create_blueprint"
        confidence = max(confidence, 0.86)
        if not blueprint_title:
            blueprint_title = extract_write_subject_title_llm(text, invoke=invoke)

    return {
        "intent": intent,
        "confidence": confidence,
        "blueprint_title": blueprint_title,
        "agent_may_select": bool(data.get("agent_may_select")),
        "is_vague_write": bool(data.get("is_vague_write")),
    }


def pick_blueprint_id_llm(
    message: str,
    matches: list,
    *,
    invoke: InvokeFn | None,
) -> str:
    """在 match 结果中由意图模型解析唯一 blueprint_id；无法确定返回空。"""
    text = (message or "").strip()
    if not text or not matches or invoke is None:
        return ""

    catalog = []
    for item in matches[:20]:
        catalog.append(
            {
                "id": str(getattr(item, "id", "") or "").strip(),
                "title": str(getattr(item, "title", None) or getattr(item, "id", "") or "").strip(),
                "description": str(getattr(item, "description", "") or "").strip()[:200],
            }
        )
    catalog = [c for c in catalog if c["id"]]
    if not catalog:
        return ""

    system = (
        "角色：蓝本点名解析器。判断用户是否明确指定要用候选中的哪一份。\n"
        "\n"
        "## 输出\n"
        '只返回 JSON：{"blueprint_id":"<id或空>","confidence":0.0}\n'
        "\n"
        "## 规则\n"
        "仅点名候选标题或「就用这份/选用××」时填对应 id。\n"
        "只说要写某类报告、主题碰巧接近 → blueprint_id 必须为空（不得代选）。\n"
        f"\n候选：{json.dumps(catalog, ensure_ascii=False)}\n"
        "\n"
        f"## 不确定：blueprint_id 留空，confidence < {_MIN_CONFIDENCE}。"
    )
    user = f"用户说：{text[:1500]}"
    try:
        raw = str(invoke(system, user) or "").strip()
    except Exception as error:
        _log.debug("pick blueprint llm failed: %s", error)
        return ""

    data = _parse_classifier_json(raw)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return ""
    bid = str(data.get("blueprint_id") or "").strip()
    valid = {c["id"] for c in catalog}
    return bid if bid in valid else ""


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
    pending: dict[str, Any],
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

    from report_writing.chat.intent_context import format_dialogue_lines

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
    pending: dict[str, Any],
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

    from report_writing.chat.intent_context import format_dialogue_lines

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
