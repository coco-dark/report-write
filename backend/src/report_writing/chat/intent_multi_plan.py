"""遗留 JSON 计划编排。口语主路径不调用。

`try_run_oral_multi_intent` 仅单测使用；产品对话走 `create_agent`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatSession

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

# 高置信：静默连做
_HIGH_CONFIDENCE = 0.72
# 与计划分类器底线对齐
_MIN_PLAN_CONFIDENCE = 0.55

_ENTRY_PATH_TOOLS = frozenset(
    {
        "ask_entry_path",
        "match_blueprints",
        "begin_blueprint_wizard",
        "skip_blueprint",
        "select_blueprint",
        "start_writing",
    }
)
_META_TOOLS = frozenset({"set_session_meta", "submit_freeform_meta"})

# 静默多意图仅跑安全前缀；开写/向导长链/收录同步交 ReAct 观察闭环
_SAFE_SILENT_MULTI_TOOLS = frozenset(
    {
        "remember_writing_goal",
        "set_session_meta",
        "submit_freeform_meta",
        "ask_intent",
        "ask_entry_path",
        "focus_document",
        "match_blueprints",
        "list_blueprints",
        "skip_blueprint",
        "begin_blueprint_wizard",
        "select_blueprint",
        "materials_pack_skip",
        "materials_pack_use",
        "materials_pack_create",
        "materials_pack_confirm_library",
        "materials_pack_list",
        "materials_pack_view",
        "materials_pack_unbind",
        "materials_pack_add_slot",
        "materials_pack_remove_slot",
        "materials_pack_update_slot",
        "materials_pack_rename",
    }
)

_TOOL_LABELS = {
    "set_session_meta": "定文档名",
    "submit_freeform_meta": "定文档名",
    "ask_entry_path": "选写作路径",
    "match_blueprints": "匹配蓝本",
    "list_blueprints": "列出蓝本",
    "begin_blueprint_wizard": "创建蓝本",
    "skip_blueprint": "不用蓝本",
    "select_blueprint": "选用蓝本",
    "select_write_chapters": "选要写的章",
    "start_writing": "开始写",
    "materials_pack_create": "新建资料包",
    "materials_pack_use": "选用资料包",
    "remember_writing_goal": "记下写作目标",
    "focus_document": "切换文档",
}


def _tool_names(steps: list[dict[str, Any]]) -> list[str]:
    return [str(s.get("tool") or "").strip() for s in steps if str(s.get("tool") or "").strip()]


def _tool_chain_label(steps: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for name in _tool_names(steps):
        labels.append(_TOOL_LABELS.get(name, name))
    return " → ".join(labels) if labels else "这些步骤"


def expand_plan_auto_continue(
    steps: list[dict[str, Any]],
    message: str,
    session: ChatSession,
) -> list[dict[str, Any]]:
    """关键步自动续：定名后必跟入口（按计划工具结构补，不靠「写一份」等关键词）。"""
    from report_writing.chat.intent_action_chain import (
        CHAT_CONTROL_PLAN_POLICY,
        order_planned_tool_steps,
    )
    from report_writing.chat.session_documents import is_writing_document_named
    from report_writing.chat.session_state import user_chose_skip_blueprint

    out = [dict(s) for s in (steps or []) if str(s.get("tool") or "").strip()]
    names = set(_tool_names(out))
    text = (message or "").strip()
    args_seed = {"message": text[:500]} if text else {}

    # 计划里已有定名步、却还没有入口/开写工具 → 补 ask_entry_path
    # 已定名且只是改名/记目标：勿强行入口三选
    if names.intersection(_META_TOOLS) and not names.intersection(_ENTRY_PATH_TOOLS):
        if not user_chose_skip_blueprint(session):
            rename_only = is_writing_document_named(session) and names <= {
                "remember_writing_goal",
                *_META_TOOLS,
            }
            if not rename_only:
                tip = ""
                for s in out:
                    if str(s.get("tool") or "") in _META_TOOLS:
                        tip = str((s.get("args") or {}).get("title") or "").strip()
                        break
                out.append(
                    {
                        "tool": "ask_entry_path",
                        "args": {**args_seed, **({"title_hint": tip} if tip else {})},
                    }
                )
                names.add("ask_entry_path")

    return order_planned_tool_steps(out, CHAT_CONTROL_PLAN_POLICY)


def classify_oral_multi_intent_plan(
    home: Path | None,
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None,
) -> tuple[list[dict[str, Any]], float]:
    """返回 (已排序并自动续步的 steps, confidence)。失败或过低 → ([], conf)。"""
    del home  # 分类不读盘；签名与 execute 对齐
    text = (message or "").strip()
    if not text or invoke is None:
        return [], 0.0

    from report_writing.chat.dialogue import format_full_dialogue
    from report_writing.chat.intent_llm import classify_chat_control_plan_llm_scored
    from report_writing.chat.intent_router import active_blueprint_wizard
    from report_writing.chat.react_speed import control_dialogue_max_turns
    from report_writing.chat.session_documents import focus_document_brief, session_documents_brief
    from report_writing.chat.session_snapshot import session_turn_snapshot
    from report_writing.chat.session_state import user_chose_skip_blueprint

    snap = session_turn_snapshot(session, text)
    wiz = dict(active_blueprint_wizard(session) or {})
    freeform_path = user_chose_skip_blueprint(session)
    steps, confidence = classify_chat_control_plan_llm_scored(
        text,
        phase=str(session.phase or ""),
        task_id=str(session.task_id or ""),
        blueprint_id=str(session.blueprint_id or wiz.get("blueprint_id") or ""),
        writing_brief_tail=str(snap.get("writing_brief_tail") or ""),
        dialogue_tail=format_full_dialogue(session, max_turns=control_dialogue_max_turns()),
        wizard_step=str(wiz.get("step") or ""),
        session_documents_brief=session_documents_brief(session),
        focus_document_brief=focus_document_brief(session),
        freeform_path=freeform_path,
        invoke=invoke,
        min_steps=1,
    )
    if not steps or confidence < _MIN_PLAN_CONFIDENCE:
        return [], confidence
    steps = expand_plan_auto_continue(steps, text, session)
    steps = _upgrade_ask_entry_to_concrete_path(steps, text, session, invoke=invoke)
    return steps, confidence


def _upgrade_ask_entry_to_concrete_path(
    steps: list[dict[str, Any]],
    message: str,
    session: ChatSession,
    *,
    invoke: InvokeFn | None,
) -> list[dict[str, Any]]:
    """一句里已说清匹配/创建/跳过时，把 ask_entry_path 升成具体工具，避免定名后再三选。"""
    from report_writing.chat.intent_action_chain import (
        CHAT_CONTROL_PLAN_POLICY,
        order_planned_tool_steps,
    )

    names = set(_tool_names(steps))
    if "ask_entry_path" not in names or invoke is None:
        return steps
    # 已有具体入口工具则不必再升
    if names.intersection(
        {"match_blueprints", "begin_blueprint_wizard", "skip_blueprint", "select_blueprint"}
    ):
        return steps
    try:
        from report_writing.chat.intent_clarify import _clear_entry_tool_from_message

        tool = _clear_entry_tool_from_message(message, session=session, invoke=invoke)
    except Exception:
        tool = ""
    if not tool:
        return steps
    tip = ""
    for s in steps:
        if str(s.get("tool") or "") in _META_TOOLS:
            tip = str((s.get("args") or {}).get("title") or "").strip()
            break
    text = (message or "").strip()
    args: dict[str, Any] = {"message": text[:500]} if text else {}
    if tip:
        args["title_hint"] = tip
        args["title"] = tip
    if tool == "begin_blueprint_wizard":
        args["confirmed_create"] = True
        args["from_entry_path"] = True
    out: list[dict[str, Any]] = []
    replaced = False
    for s in steps:
        if str(s.get("tool") or "").strip() == "ask_entry_path" and not replaced:
            out.append({"tool": tool, "args": args})
            replaced = True
        else:
            out.append(dict(s))
    return order_planned_tool_steps(out, CHAT_CONTROL_PLAN_POLICY)


def _hang_low_confidence_clarify(
    session: ChatSession,
    message: str,
    steps: list[dict[str, Any]],
    confidence: float,
) -> tuple[str, list[Any], list[dict[str, Any]]]:
    from report_writing.chat.intent_clarify import begin_clarify

    chain = _tool_chain_label(steps)
    question = f"我理解你想依次做：{chain}。是这样吗？"
    reply, cards = begin_clarify(
        session,
        source="multi_intent",
        question=question,
        options=[
            {
                "id": "run_all",
                "label": "对，按这个顺序做",
                "slots": {"confirm_plan": "yes"},
            },
            {
                "id": "rephrase",
                "label": "不对，我再说一遍",
                "slots": {"confirm_plan": "no"},
            },
        ],
        candidate={
            "kind": "intent_plan",
            "plan": steps,
            "payload": {"message": (message or "").strip()[:500]},
            "confidence": confidence,
        },
    )
    return reply, list(cards or []), [{"type": "multi_intent_clarify", "confidence": confidence}]


def try_run_oral_multi_intent(
    home: Path,
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None,
    emit: EmitFn | None = None,
    min_tools: int = 1,
) -> tuple[str, list[Any], list[dict[str, Any]]] | None:
    """遗留 JSON 计划入口。`run_chat_control` 不再调用。

    高置信执行；缺槽挂卡；不确定多轮确认；否则 None。
    """
    from report_writing.infra.config_settings import intent_plan_enabled

    if not intent_plan_enabled():
        return None
    del min_tools
    from report_writing.chat.intent_turn_plan import apply_turn_plan

    hit = apply_turn_plan(
        home, session, message, invoke=invoke, emit=emit
    )
    if hit is None:
        return None
    reply, cards, events = hit
    events = list(events or [])
    if not any(str(e.get("type") or "") == "multi_intent_plan" for e in events):
        events.append(
            {
                "type": "multi_intent_plan",
                "confidence": next(
                    (e.get("confidence") for e in events if e.get("type") == "turn_plan"),
                    0.0,
                ),
                "tools": next(
                    (e.get("tools") for e in events if e.get("type") == "turn_plan"),
                    [],
                ),
            }
        )
    return (reply or "").strip() or "好的。", list(cards or []), events
