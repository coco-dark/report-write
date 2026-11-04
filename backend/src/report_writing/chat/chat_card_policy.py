"""对话卡片可见性策略：onboarding / 向导 / 回合去重的单一入口。

| 类型 | 对话区 | 文档小窗 |
| blueprint_wizard meta/upload/outline/outline_confirm | 是（最新一张可操作） | 可选 |
| blueprint_wizard study | 否（历史快照只读） | 是 |
| intent_clarify onboarding | 是 | — |
| outline_preview / progress | 是 | 视场景 |
"""

from __future__ import annotations

from typing import Any

from report_writing.domain.models import ChatSession

# 与 wizard_state._WIZARD_STEP_ORDER 前段一致；对话内可操作向导步
WIZARD_STEPS_CHAT = frozenset({"meta", "upload", "outline", "outline_confirm"})

_SAMPLE_MODES = frozenset({"create", "freeform_sample"})


def _active_template_wizard_for_cards(
    session: ChatSession, wizard: dict[str, Any]
) -> bool:
    """本会话是否处于依据模版/样本的对话向导（含尚未建 id 的 meta 步）。"""
    from report_writing.services.task_ids import is_writing_task_id

    wiz = dict(wizard or {})
    step = str(wiz.get("step") or "").strip()
    if step not in WIZARD_STEPS_CHAT:
        return False
    mode = str(wiz.get("mode") or "").strip()
    if mode in _SAMPLE_MODES:
        return True
    plan = dict((session.freeform or {}).get("plan") or {})
    if str(plan.get("goal") or "") == "template_wizard":
        return True
    bid = str(wiz.get("blueprint_id") or session.task_id or "").strip()
    if bid and is_writing_task_id(bid):
        return step in {"upload", "outline", "outline_confirm"} or (
            step == "meta" and bool(wiz.get("fresh_meta"))
        )
    return step == "meta" and (
        mode == "create" or bool(wiz.get("fresh_meta")) or plan.get("goal") == "template_wizard"
    )


def merge_wizard_snapshots(*snaps: dict[str, Any] | None) -> dict[str, Any]:
    """合并 reconcile 前后的 wizard 快照（非空字段优先）。"""
    out: dict[str, Any] = {}
    for snap in snaps:
        if not snap or not isinstance(snap, dict):
            continue
        for key, val in snap.items():
            if val is None or val == "":
                continue
            if key == "chapters" and isinstance(val, list) and not val:
                continue
            out[key] = val
    return out


def finalize_turn_cards(
    session: ChatSession,
    cards: list,
    *,
    session_wizard: dict[str, Any] | None = None,
) -> list:
    """同回合：澄清去重 + 向导卡回贴/合成。"""
    from report_writing.chat.cards import collapse_duplicate_turn_cards
    from report_writing.chat.wizard_state import finalize_wizard_cards_for_response

    wiz = dict(
        session_wizard
        or (session.freeform or {}).get("blueprint_wizard")
        or {}
    )
    collapsed = collapse_duplicate_turn_cards(list(cards or []), session=session)
    finalized = finalize_wizard_cards_for_response(session, collapsed, wiz)
    return ensure_wizard_chat_card_invariant(session, finalized, wiz)


def ensure_wizard_chat_card_invariant(
    session: ChatSession,
    cards: list,
    session_wizard: dict[str, Any] | None = None,
) -> list:
    """样本/创建向导处于 upload 等对话步时，本轮必须带一张 blueprint_wizard 卡。"""
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.wizard_state import (
        WIZARD_MODE_CREATE,
        WIZARD_MODE_FREEFORM_SAMPLE,
        _wizard_card_title,
    )
    from report_writing.services.task_ids import is_writing_task_id

    wiz = dict(
        session_wizard
        or (session.freeform or {}).get("blueprint_wizard")
        or {}
    )
    step = str(wiz.get("step") or "").strip()
    if step not in WIZARD_STEPS_CHAT:
        return list(cards or [])
    if not _active_template_wizard_for_cards(session, wiz):
        return list(cards or [])

    bid = str(wiz.get("blueprint_id") or "").strip()
    # 新开名称步：空壳 w* 只挂 working_task_id，不能当 blueprint_id（否则卡会被打成上传）
    if not bid and not (bool(wiz.get("fresh_meta")) and step == "meta"):
        bid = str(session.task_id or "").strip()
    mode = str(wiz.get("mode") or "").strip()

    has_wiz = any(
        str(getattr(c, "type", "") or "") == "blueprint_wizard" for c in (cards or [])
    )
    if has_wiz:
        return list(cards or [])

    extra = dict(wiz)
    if not extra.get("mode") and bid:
        from report_writing.services.task_ids import is_writing_task_id

        if is_writing_task_id(bid):
            extra["mode"] = WIZARD_MODE_FREEFORM_SAMPLE
    if not extra.get("mode"):
        extra["mode"] = WIZARD_MODE_CREATE
    synth = _blueprint_wizard_card(
        blueprint_id=bid,
        step=step,
        title=_wizard_card_title(
            step,
            mode=str(extra.get("mode") or ""),
        ),
        extra=extra,
    )
    return list(cards or []) + [synth]


def infer_sample_wizard_active(session: ChatSession, wizard: dict[str, Any] | None = None) -> bool:
    """mode 被 ui-state 清空时，仍识别 w* 样本向导。"""
    from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode
    from report_writing.services.task_ids import is_writing_task_id

    wiz = dict(wizard or (session.freeform or {}).get("blueprint_wizard") or {})
    if is_freeform_sample_wizard_mode(wiz):
        return True
    if _active_template_wizard_for_cards(session, wiz):
        return True
    bid = str(wiz.get("blueprint_id") or wiz.get("working_task_id") or "").strip()
    step = str(wiz.get("step") or "").strip()
    if bid and is_writing_task_id(bid) and step in WIZARD_STEPS_CHAT | {"upload_done"}:
        return True
    wf = dict((session.freeform or {}).get("plan") or {})
    if str(wf.get("goal") or "") == "template_wizard":
        return True
    return False
