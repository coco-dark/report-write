"""蓝本向导：规则同步 vs 用户/对话自驱 — 避免步骤回退与重复触发形成循环。"""

from __future__ import annotations

from typing import Any

from report_writing.chat.wizard_state import wizard_step_index

_STEP_ALIASES: dict[str, str] = {
    "upload_done": "outline",
    "outline_done": "outline_confirm",
    "study_done": "study",
    "done": "study",
}

# 已前进的向导不得因 derive 空窗回到 meta；回到 upload 须 pending_wizard_command=re_upload_sample
# outline_confirm：任务未确认时允许从 study 退回，避免 UI「已确认」与任务真值长期脱节
_ALLOW_REGRESS_TO = frozenset({"outline_confirm"})

_TRANSITION_PAYLOAD_STEPS = frozenset(
    {"upload_done", "outline_done", "outline_confirm", "study_done"}
)


def normalized_wizard_step(step: str) -> str:
    s = (step or "meta").strip()
    return _STEP_ALIASES.get(s, s)


def clamp_wizard_step_regression(prev_step: str, proposed_step: str) -> str:
    """derive/sync 不得把会话里已前进的向导步倒回（未确认目录除外可退回确认步）。"""
    prev = normalized_wizard_step(prev_step)
    proposed = normalized_wizard_step(proposed_step)
    pi = wizard_step_index(prev)
    ni = wizard_step_index(proposed)
    if pi < 0 or ni < 0:
        return proposed_step
    if ni < pi and proposed not in _ALLOW_REGRESS_TO:
        return prev_step
    return proposed_step


def consume_wizard_triggers(wizard: dict[str, Any], *, transition_step: str = "") -> None:
    """UI/对话过渡步完成后清掉一次性自驱标记，避免 sync 反复拉起提取/重学。"""
    step = (transition_step or "").strip()
    if step in {"outline_done", "outline_confirm", "study_done", "upload_done"}:
        wizard.pop("trigger_outline_extract", None)
    if step in {"study_done", "outline_confirm"}:
        wizard.pop("trigger_chapter_study", None)
        wizard.pop("chapter_study_keys", None)
    if step == "study_done":
        wizard.pop("trigger_begin_study", None)


def clear_stale_chapter_study_triggers(home, wizard: dict[str, Any], blueprint_id: str) -> None:
    """只丢掉大纲里已不存在的章键；已有要点的章仍保留。

    用户「再分析第×章」会带 trigger，即便该章已有要点也必须留给前端开跑。
    刷新后自动重学由 stripEphemeral / 会话恢复清 trigger，不在这里按要点清掉。
    """
    keys = list(wizard.get("trigger_chapter_study") or wizard.get("chapter_study_keys") or [])
    if not keys:
        return
    from report_writing.chat.helpers import _load_task

    task = _load_task(home, blueprint_id)
    if task is None or not task.outline:
        return
    by_key = {str(n.key or ""): n for n in (task.outline.chapters or [])}
    valid = [k for k in keys if k in by_key]
    if not valid:
        wizard.pop("trigger_chapter_study", None)
        wizard.pop("chapter_study_keys", None)
        return
    wizard["trigger_chapter_study"] = valid
    if "chapter_study_keys" in wizard:
        wizard["chapter_study_keys"] = valid


def clear_stale_begin_study_trigger(wizard: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    """已有学习进度或已学完时，丢掉一次性「开始学习全部」，避免重进对话整本重跑。

    不清理 trigger_chapter_study：指定章重学（含已分析章）要靠它驱动前端开跑。
    """
    mode = str((extra or {}).get("study_mode") or wizard.get("study_mode") or "").strip()
    analyzed = int((extra or {}).get("study_analyzed") or wizard.get("study_analyzed") or 0)
    if mode in {"continue", "complete", "confirm_pending"} or analyzed > 0:
        wizard.pop("trigger_begin_study", None)


def idempotent_transition_reply(
    transition_step: str,
    prev_wizard: dict[str, Any],
    *,
    already_msg: str,
    has_substantive_change: bool = True,
) -> tuple[str, list] | None:
    """重复点击同一过渡按钮时只提示，不再叠卡片/气泡。"""
    step = (transition_step or "").strip()
    if step not in _TRANSITION_PAYLOAD_STEPS:
        return None
    target = normalized_wizard_step(step)
    prev = normalized_wizard_step(str(prev_wizard.get("step") or ""))
    pi = wizard_step_index(prev)
    ti = wizard_step_index(target)
    if pi < 0 or ti < 0:
        return None
    if pi >= ti and not has_substantive_change:
        return (already_msg, [])
    return None


def should_skip_redundant_wizard_card(
    session_wizard: dict[str, Any],
    card_payload: dict[str, Any],
) -> bool:
    """新卡片不落后于会话真值且无一过性 trigger 时，不必再插一张向导卡。"""
    if not card_payload:
        return False
    chapters = card_payload.get("chapters")
    if isinstance(chapters, list) and chapters:
        return False
    if (
        card_payload.get("trigger_outline_extract")
        or card_payload.get("trigger_chapter_study")
        or card_payload.get("trigger_begin_study")
    ):
        return False
    cur = normalized_wizard_step(str(session_wizard.get("step") or ""))
    new = normalized_wizard_step(str(card_payload.get("step") or ""))
    ci = wizard_step_index(cur)
    ni = wizard_step_index(new)
    # 仅丢弃「步数落后于会话」的旧卡；同一步（如 upload）的新卡必须保留，否则会话里只剩历史 meta 卡形成循环
    if ci >= 0 and ni >= 0 and ni < ci:
        return True
    return False
