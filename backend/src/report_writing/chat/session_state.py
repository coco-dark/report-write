"""会话侧状态：写作路径、计划指针、蓝本向导是否在进行中（不解析用户口语）。"""

from __future__ import annotations

from typing import Any

from report_writing.domain.models import ChatPhase, ChatSession

WRITING_PATH_FREEFORM = "freeform"
WRITING_PATH_BLUEPRINT = "blueprint"

_BLOCKED_PHASES = frozenset(
    {
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.FREEFORM_POINTS.value,
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
    }
)


def get_writing_path(session: ChatSession) -> str:
    return str((session.freeform or {}).get("writing_path") or "").strip()


def lock_writing_path(session: ChatSession, path: str) -> None:
    path = (path or "").strip()
    if path not in {WRITING_PATH_FREEFORM, WRITING_PATH_BLUEPRINT}:
        return
    ff = dict(session.freeform or {})
    ff["writing_path"] = path
    if path == WRITING_PATH_FREEFORM:
        from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode

        wiz = dict(ff.get("blueprint_wizard") or {})
        # 依据样本上传/提取进行中：锁定无蓝本路径时仍保留向导，否则下一步「提取目录」卡会丢
        if is_freeform_sample_wizard_mode(wiz):
            session.freeform = ff
            touch_session_plan(session, goal="skip_blueprint")
            return
        # 锁定无蓝本时一并拆掉创建向导，避免确认大纲旁仍出现「上传样板 / 学习分析」
        ff.pop("blueprint_wizard", None)
        session.blueprint_id = ""
    session.freeform = ff
    if path == WRITING_PATH_FREEFORM:
        touch_session_plan(session, goal="skip_blueprint")
        try:
            from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

            workflow_set(session, WORKFLOW_BLUEPRINT, None)
        except Exception:
            pass
    elif path == WRITING_PATH_BLUEPRINT:
        touch_session_plan(session, goal="pick_blueprint")


def user_chose_skip_blueprint(session: ChatSession) -> bool:
    try:
        from report_writing.chat.session_documents import focus_onboarding

        ep = str(focus_onboarding(session).get("entry_path") or "").strip()
        if ep in {"template", "match"}:
            return False
    except Exception:
        pass
    if get_writing_path(session) == WRITING_PATH_FREEFORM:
        return True
    plan = dict((session.freeform or {}).get("plan") or {})
    if plan.get("goal") in {"skip_blueprint", "freeform"}:
        return True
    if (session.phase or "") in {
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.FREEFORM_POINTS.value,
    }:
        return True
    return False


def dialogue_recently_skipped_blueprint(
    session: ChatSession,
    *,
    lookback: int = 10,
    invoke=None,
) -> bool:
    if user_chose_skip_blueprint(session):
        return True
    if invoke is None:
        return False
    from report_writing.chat.entry_intent import is_skip_blueprint_intent

    for msg in reversed(list(session.messages or [])[-lookback:]):
        if str(getattr(msg, "role", "") or "") != "user":
            continue
        text = str(getattr(msg, "text", "") or "").strip()
        if text and is_skip_blueprint_intent(text, invoke=invoke, session=session):
            return True
    return False


def touch_session_plan(session: ChatSession, **fields: Any) -> None:
    plan = dict((session.freeform or {}).get("plan") or {})
    for key, value in fields.items():
        if value is not None:
            plan[key] = value
    session.freeform = {**(session.freeform or {}), "plan": plan}


def phase_allows_blueprint_routing(phase: str) -> bool:
    return (phase or ChatPhase.INTENT.value) not in _BLOCKED_PHASES


def active_blueprint_wizard(session: ChatSession) -> dict[str, Any] | None:
    # 无蓝本路径不得继续判为「创建蓝本向导进行中」，否则会劫持确认大纲 / 学习要点
    if user_chose_skip_blueprint(session):
        return None
    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    step = str(wizard.get("step") or "").strip()
    if step == "done":
        wizard["step"] = "study"
        step = "study"
    if not step:
        if str(wizard.get("mode") or "").strip() == "selected" and str(
            wizard.get("blueprint_id") or session.blueprint_id or ""
        ).strip():
            return wizard
        return None
    return wizard
