"""多意图 / 多工具：解析分类器 JSON、按领域流水线与依赖重排、串行执行。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

MULTI_ACTION_JSON_HINT = (
    "多动作：用户一句含多个步骤时，用数组字段 commands / intents / tools（按业务顺序）；"
    "单动作：只填 command / intent / tool 即可。后端会再校正顺序。"
)


@dataclass(frozen=True)
class ActionOrderPolicy:
    """领域动作顺序：pipeline_rank 越小越先执行；requires 表示 (前置, 后置) 必须先于后置。"""

    pipeline_rank: dict[str, int]
    requires: tuple[tuple[str, str], ...] = ()
    inject_if_missing: tuple[tuple[str, str], ...] = ()


# 蓝本向导：与 meta → upload → outline → confirm → study 一致
WIZARD_POLICY = ActionOrderPolicy(
    pipeline_rank={
        "abandon_wizard": 1,
        "submit_meta": 10,
        "re_upload_sample": 15,
        "mark_upload_done": 20,
        "re_extract_outline": 30,
        "go_outline_confirm": 40,
        "confirm_outline": 50,
        "go_study": 60,
        "re_study_chapters": 65,
    },
    requires=(
        # 用户显式「确认并学习」时再排序；勿自动注入 confirm，否则未确认也会被静默确认
        ("confirm_outline", "go_study"),
        ("mark_upload_done", "re_extract_outline"),
    ),
    inject_if_missing=(),
)

WRITING_CONTROL_POLICY = ActionOrderPolicy(
    pipeline_rank={
        "remember_writing_goal": 10,
        "open_element_library": 15,
        "open_blueprint_library": 15,
        "open_knowledge": 16,
        "associate_elements": 17,
        "dissociate_chapter_element": 18,
        "remove_document_element": 18,
        "collect_to_element_library": 19,
        "add_chapter_element": 20,
        "submit_points": 30,
    },
)

FREEFORM_POLICY = ActionOrderPolicy(
    pipeline_rank={
        "help_draft": 5,
        "generate_outline": 10,
        "confirm_outline": 20,
        "generate_points": 30,
        "skip_points": 35,
        "start_writing": 40,
    },
    requires=(
        ("generate_outline", "confirm_outline"),
        ("confirm_outline", "generate_points"),
        ("generate_points", "start_writing"),
    ),
    inject_if_missing=(
        ("confirm_outline", "generate_points"),
        ("generate_outline", "confirm_outline"),
    ),
)

# 对话总控 ReAct / 计划短路：选型 → 选章 → 开写；无蓝本目录 → 要点 → 开写
CHAT_CONTROL_PLAN_POLICY = ActionOrderPolicy(
    pipeline_rank={
        "remember_writing_goal": 5,
        "set_session_meta": 8,
        "submit_freeform_meta": 10,
        "ask_intent": 11,
        "ask_entry_path": 11,
        "focus_document": 12,
        "match_blueprints": 13,
        "list_blueprints": 13,
        "skip_blueprint": 14,
        "begin_blueprint_wizard": 15,
        "resource_check": 15,
        "blueprint_wizard": 16,
        "re_study_chapters": 18,
        "select_blueprint": 20,
        "resume_from_blueprint": 21,
        "submit_outline": 25,
        "trim_outline_top_chapters": 26,
        "generate_freeform_points": 28,
        "skip_freeform_points": 29,
        "select_write_chapters": 30,
        "write_analyzed_only": 31,
        "configure_chapter": 35,
        "add_chapter_element": 36,
        "collect_to_element_library": 36,
        "associate_elements": 36,
        "open_element_library": 36,
        "open_blueprint_library": 36,
        "open_knowledge": 36,
        "materials_pack_list": 36,
        "materials_pack_view": 36,
        "materials_pack_use": 36,
        "materials_pack_create": 36,
        "materials_pack_unbind": 36,
        "materials_pack_add_slot": 37,
        "materials_pack_remove_slot": 37,
        "materials_pack_update_slot": 37,
        "materials_pack_rename": 37,
        "materials_pack_confirm_library": 37,
        "materials_pack_skip": 37,
        "retry_search_chapter": 38,
        "start_writing": 40,
        "confirm_chapter": 45,
        "confirm_all_chapters": 46,
        "retry_chapter": 47,
        "pause_writing": 50,
        "get_writing_status": 55,
        "prepare_export_word": 60,
        "save_freeform_as_blueprint": 65,
        "sync_document_to_blueprint_library": 66,
    },
    requires=(
        ("match_blueprints", "select_blueprint"),
        ("select_blueprint", "select_write_chapters"),
        ("select_write_chapters", "start_writing"),
        ("write_analyzed_only", "start_writing"),
        ("submit_outline", "generate_freeform_points"),
        ("generate_freeform_points", "start_writing"),
    ),
    inject_if_missing=(
        # 勿注入 select_blueprint：无 blueprint_id 时会挂澄清却同轮继续 start_writing
        ("generate_freeform_points", "start_writing"),
    ),
)

# 计划执行器不调子 Agent（仍由 ReAct 单步委托）
CHAT_CONTROL_PLAN_BLOCKLIST = frozenset(
    {
        "blueprint_subagent",
        "blueprint_wizard_subagent",
        "render_subagent",
        "style_subagent",
    }
)


def parse_planned_tool_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    """从计划分类器 JSON 解析 [{tool, args}, …]（未排序）。"""
    steps: list[dict[str, Any]] = []
    raw = data.get("tools")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool") or "").strip()
            if not name or name == "none":
                continue
            args = item.get("args") or item.get("payload") or {}
            if not isinstance(args, dict):
                args = {}
            steps.append({"tool": name, "args": dict(args)})
    single = str(data.get("tool") or "").strip()
    if single and single != "none" and not steps:
        args = data.get("args") or data.get("payload") or {}
        if not isinstance(args, dict):
            args = {}
        steps.append({"tool": single, "args": dict(args)})
    return steps


def order_planned_tool_steps(steps: list[dict[str, Any]], policy: ActionOrderPolicy) -> list[dict[str, Any]]:
    ids = [str(s.get("tool") or "") for s in steps if s.get("tool")]
    ordered = order_action_ids(ids, policy)
    return reorder_by_ids(steps, ordered, lambda s: str(s.get("tool") or ""))


def parse_id_list_from_classifier(
    data: dict[str, Any],
    *,
    plural_keys: tuple[str, ...] = ("commands", "intents", "tools"),
    single_keys: tuple[str, ...] = ("command", "intent", "tool", "action"),
) -> list[str]:
    """从分类器 JSON 取出动作 id 列表（LLM 顺序保留，供后续 reorder）。"""
    for key in plural_keys:
        raw = data.get(key)
        if isinstance(raw, list) and raw:
            out: list[str] = []
            for item in raw:
                if isinstance(item, dict):
                    for sk in single_keys:
                        val = str(item.get(sk) or "").strip()
                        if val:
                            out.append(val)
                            break
                else:
                    val = str(item or "").strip()
                    if val:
                        out.append(val)
            if out:
                return _dedupe_preserve(out)
    for key in single_keys:
        val = str(data.get(key) or "").strip()
        if val and val != "none":
            return [val]
    return []


def _dedupe_preserve(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _inject_missing_prerequisites(ids: list[str], policy: ActionOrderPolicy) -> list[str]:
    present = set(ids)
    out = list(ids)
    for before, after in policy.inject_if_missing:
        if after not in present or before in present:
            continue
        idx = out.index(after) if after in out else len(out)
        out.insert(idx, before)
        present.add(before)
    return out


def _rank_key(action_id: str, policy: ActionOrderPolicy) -> tuple[int, int]:
    rank = policy.pipeline_rank.get(action_id, 500)
    return (rank, 0)


def order_action_ids(ids: list[str], policy: ActionOrderPolicy) -> list[str]:
    """按领域流水线 rank + 依赖约束重排（不删 LLM 未识别的 id）。"""
    if not ids:
        return []
    expanded = _inject_missing_prerequisites(_dedupe_preserve(ids), policy)
    ranked = sorted(expanded, key=lambda x: (_rank_key(x, policy)[0], expanded.index(x)))

    if not policy.requires:
        return ranked

    # 依赖修正：若后置出现在前置之前，把前置挪到后置前
    index = {aid: i for i, aid in enumerate(ranked)}
    for before, after in policy.requires:
        if before not in index or after not in index:
            continue
        if index[before] <= index[after]:
            continue
        ranked.remove(before)
        ranked.insert(ranked.index(after), before)
        index = {aid: i for i, aid in enumerate(ranked)}
    return ranked


def reorder_by_ids(items: list[T], ordered_ids: list[str], id_of: Callable[[T], str]) -> list[T]:
    by_id: dict[str, T] = {}
    for item in items:
        key = str(id_of(item) or "").strip()
        if key and key not in by_id:
            by_id[key] = item
    out: list[T] = []
    for aid in ordered_ids:
        if aid in by_id:
            out.append(by_id[aid])
    for item in items:
        key = str(id_of(item) or "").strip()
        if key and key not in ordered_ids:
            out.append(item)
    return out


def as_chain_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not actions:
        return None
    if len(actions) == 1:
        return actions[0]
    return {"type": "chain", "actions": actions}


def execute_action_chain(
    actions: list[Any],
    executor: Callable[[Any], tuple[str, list[Any], list[dict[str, Any]]]],
) -> tuple[str, list[Any], list[dict[str, Any]]]:
    """串行执行；后一步在同一 session 状态上继续。"""
    replies: list[str] = []
    cards: list[Any] = []
    events: list[dict[str, Any]] = []
    for step in actions:
        reply, step_cards, step_events = executor(step)
        if (reply or "").strip():
            replies.append(str(reply).strip())
        if step_cards:
            cards = step_cards
        if step_events:
            events.extend(step_events)
    text = "\n".join(replies).strip()
    return text or "好的。", cards, events
