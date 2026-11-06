"""对话会话运行时：统一存放可恢复的交互状态（向导、写作进度等）。

真值落在 ChatSession（MySQL writing_chat_session.document），结构为：

    freeform.runtime = {
        "version": 1,
        "workflows": {
            "blueprint_wizard": { ... },
            "writing": { phase, task_id, progress, pace, ... },
            "template_wizard": { resource, step, item_id },
            "export": { template_id, style_id, confirmed_only },
            "freeform": { category, title, outline_text, plan, active },
        },
    }

历史 freeform 字段会在 reconcile / migrate 时与 runtime 双向对齐。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.domain.models import ChapterStatus, ChatPhase, ChatSession

RUNTIME_VERSION = 1

WORKFLOW_BLUEPRINT = "blueprint_wizard"
WORKFLOW_WRITING = "writing"
WORKFLOW_TEMPLATE = "template_wizard"
WORKFLOW_EXPORT = "export"
WORKFLOW_FREEFORM = "freeform"


def _runtime_root(session: ChatSession) -> dict[str, Any]:
    ff = dict(session.freeform or {})
    raw = ff.get("runtime")
    if not isinstance(raw, dict):
        raw = {"version": RUNTIME_VERSION, "workflows": {}}
    workflows = raw.get("workflows")
    if not isinstance(workflows, dict):
        workflows = {}
    return {"version": int(raw.get("version") or RUNTIME_VERSION), "workflows": workflows}


def _commit_runtime(session: ChatSession, runtime: dict[str, Any]) -> None:
    ff = dict(session.freeform or {})
    ff["runtime"] = runtime
    session.freeform = ff


def workflow_get(session: ChatSession, name: str) -> dict[str, Any]:
    rt = _runtime_root(session)
    data = rt["workflows"].get(name)
    return dict(data) if isinstance(data, dict) else {}


def workflow_set(session: ChatSession, name: str, data: dict[str, Any] | None) -> None:
    rt = _runtime_root(session)
    if data:
        rt["workflows"][name] = dict(data)
    else:
        rt["workflows"].pop(name, None)
    _commit_runtime(session, rt)


def workflow_patch(session: ChatSession, name: str, patch: dict[str, Any]) -> dict[str, Any]:
    merged = {**workflow_get(session, name), **(patch or {})}
    workflow_set(session, name, merged)
    return merged


def workflow_touch_template(session: ChatSession, payload: dict[str, Any]) -> None:
    data = {
        "resource": str(payload.get("resource") or "").strip(),
        "step": str(payload.get("step") or "choose").strip(),
        "item_id": str(payload.get("item_id") or "").strip(),
    }
    workflow_patch(session, WORKFLOW_TEMPLATE, data)
    ff = dict(session.freeform or {})
    ff["template_wizard"] = {**(ff.get("template_wizard") or {}), **data}
    session.freeform = ff


def latest_card_payload(session: ChatSession, card_type: str) -> dict[str, Any]:
    for msg in reversed(list(session.messages or [])):
        if (getattr(msg, "role", None) or "") != "assistant":
            continue
        for card in reversed(list(msg.cards or [])):
            if getattr(card, "type", "") != card_type:
                continue
            return dict(getattr(card, "payload", None) or {})
    return {}


def migrate_legacy_freeform(session: ChatSession) -> None:
    """把散落 freeform 键收拢到 runtime.workflows，并回写兼容字段。"""
    ff = dict(session.freeform or {})
    rt = _runtime_root(session)
    workflows = dict(rt["workflows"])

    legacy_bp = ff.get("blueprint_wizard")
    if isinstance(legacy_bp, dict):
        from report_writing.chat.wizard_loop_guard import clamp_wizard_step_regression, normalized_wizard_step
        from report_writing.chat.wizard_state import wizard_step_index

        runtime_bp = dict(workflows.get(WORKFLOW_BLUEPRINT) or {})
        try:
            from report_writing.chat.session_documents import wizard_belongs_to_focus

            if runtime_bp and not wizard_belongs_to_focus(session, runtime_bp):
                runtime_bp = {}
        except Exception:
            pass
        if bool(legacy_bp.get("fresh_meta")):
            merged = dict(legacy_bp)
        else:
            merged = {**runtime_bp, **legacy_bp}
        bid = str(merged.get("blueprint_id") or "").strip()
        if bool(legacy_bp.get("fresh_meta")):
            bid = str(legacy_bp.get("blueprint_id") or "").strip()
        if bid:
            merged["blueprint_id"] = bid
        if not bool(legacy_bp.get("fresh_meta")):
            rt_step = str(runtime_bp.get("step") or "").strip()
            lg_step = str(legacy_bp.get("step") or "").strip()
            step = clamp_wizard_step_regression(rt_step or lg_step, lg_step or rt_step)
            step = clamp_wizard_step_regression(step, rt_step or step)
            if bid and wizard_step_index(normalized_wizard_step(step)) <= wizard_step_index("meta"):
                step = "upload"
            if step:
                merged["step"] = step
        workflows[WORKFLOW_BLUEPRINT] = merged
        ff["blueprint_wizard"] = merged

    legacy_tpl = ff.get("template_wizard")
    if isinstance(legacy_tpl, dict):
        workflows[WORKFLOW_TEMPLATE] = {**workflows.get(WORKFLOW_TEMPLATE, {}), **legacy_tpl}

    writing = dict(workflows.get(WORKFLOW_WRITING) or {})
    for key, wf_key in (
        ("writing_pace", "pace"),
        ("write_chapter_keys", "chapter_keys"),
        ("writing_paused", "paused"),
    ):
        if key in ff and wf_key not in writing:
            writing[wf_key] = ff[key]
    if writing:
        workflows[WORKFLOW_WRITING] = {**writing, **workflows.get(WORKFLOW_WRITING, {})}

    export_prefs = ff.get("export_prefs")
    if isinstance(export_prefs, dict):
        workflows[WORKFLOW_EXPORT] = {**workflows.get(WORKFLOW_EXPORT, {}), **export_prefs}

    freeform_wf = {
        "category": ff.get("category"),
        "title": ff.get("title"),
        "outline_text": ff.get("outline_text"),
        "plan": ff.get("plan"),
    }
    if any(v for v in freeform_wf.values() if v not in (None, "", {})):
        workflows[WORKFLOW_FREEFORM] = {**workflows.get(WORKFLOW_FREEFORM, {}), **freeform_wf}

    for dead in (
        "intent_audit",
        "intent_chain_log",
        "_last_intent",
        "_suggested_intent",
        "_orch_classified_msg",
    ):
        ff.pop(dead, None)

    rt["workflows"] = workflows
    rt["version"] = RUNTIME_VERSION
    ff["runtime"] = rt
    session.freeform = ff


def derive_freeform_workflow(session: ChatSession) -> dict[str, Any]:
    ff = dict(session.freeform or {})
    phase = str(session.phase or ChatPhase.INTENT.value)
    active_phases = {
        ChatPhase.FREEFORM_ASK.value,
        ChatPhase.FREEFORM_OUTLINE.value,
        ChatPhase.FREEFORM_POINTS.value,
    }
    stored = workflow_get(session, WORKFLOW_FREEFORM)
    outline_confirmed = bool(
        stored.get("outline_confirmed")
        if "outline_confirmed" in stored
        else ff.get("outline_confirmed")
    )
    # 进入要点/待开写/写作阶段即视为目录已确认（兼容旧会话未写 flag）
    if (
        phase
        in {
            ChatPhase.FREEFORM_POINTS.value,
            ChatPhase.READY_TO_WRITE.value,
            ChatPhase.WRITING.value,
            ChatPhase.CONFIRM_EXPORT.value,
        }
        or str(phase).endswith("_points")
        or str(ff.get("points_step") or "").strip() in {"generated", "skipped", "ask"}
    ):
        outline_confirmed = True
    return {
        "active": phase in active_phases
        or phase
        in {
            ChatPhase.READY_TO_WRITE.value,
            ChatPhase.WRITING.value,
        },
        "phase": phase,
        "category": str(stored.get("category") or ff.get("category") or "").strip(),
        "title": str(stored.get("title") or ff.get("title") or "").strip(),
        "outline_text": str(stored.get("outline_text") or ff.get("outline_text") or "").strip(),
        "outline_confirmed": outline_confirmed,
        "points_step": str(stored.get("points_step") or ff.get("points_step") or "").strip(),
        "outline_pending_edit": bool(
            stored.get("outline_pending_edit")
            if "outline_pending_edit" in stored
            else ff.get("outline_pending_edit")
        ),
        "plan": dict(stored.get("plan") or ff.get("plan") or {})
        if isinstance(stored.get("plan") or ff.get("plan"), dict)
        else {},
    }


def _sync_freeform_legacy(session: ChatSession, freeform: dict[str, Any]) -> None:
    ff = dict(session.freeform or {})
    for key in ("category", "title", "outline_text", "plan"):
        if key in freeform and freeform[key] not in (None, ""):
            ff[key] = freeform[key]
    for key in ("outline_confirmed", "outline_pending_edit"):
        if key in freeform:
            ff[key] = bool(freeform.get(key))
    session.freeform = ff


def derive_export_workflow(session: ChatSession) -> dict[str, Any]:
    ff = dict(session.freeform or {})
    prefs = ff.get("export_prefs") if isinstance(ff.get("export_prefs"), dict) else {}
    stored = workflow_get(session, WORKFLOW_EXPORT)
    merged = {**prefs, **stored}
    return {
        "template_id": str(merged.get("template_id") or "").strip(),
        "style_id": str(merged.get("style_id") or "ws_default").strip() or "ws_default",
        "confirmed_only": bool(merged.get("confirmed_only")),
    }


def _sync_export_legacy(session: ChatSession, export_wf: dict[str, Any]) -> None:
    ff = dict(session.freeform or {})
    ff["export_prefs"] = {
        "template_id": export_wf.get("template_id") or "",
        "style_id": export_wf.get("style_id") or "ws_default",
        "confirmed_only": bool(export_wf.get("confirmed_only")),
    }
    session.freeform = ff


def derive_template_workflow(session: ChatSession) -> dict[str, Any]:
    stored = workflow_get(session, WORKFLOW_TEMPLATE)
    if stored.get("resource") and stored.get("step"):
        return stored
    payload = latest_card_payload(session, "template_wizard")
    if not payload:
        return stored
    return {
        "resource": str(payload.get("resource") or "").strip(),
        "step": str(payload.get("step") or "choose").strip(),
        "item_id": str(payload.get("item_id") or "").strip(),
    }


def refresh_template_wizard_card_in_session(session: ChatSession) -> bool:
    wf = derive_template_workflow(session)
    resource = str(wf.get("resource") or "").strip()
    step = str(wf.get("step") or "").strip()
    if not resource or not step:
        return False
    messages = list(session.messages or [])
    target_mi = -1
    target_ci = -1
    for mi in range(len(messages) - 1, -1, -1):
        msg = messages[mi]
        if (getattr(msg, "role", None) or "") != "assistant":
            continue
        cards = list(msg.cards or [])
        for ci in range(len(cards) - 1, -1, -1):
            card = cards[ci]
            if getattr(card, "type", "") != "template_wizard":
                continue
            payload = dict(getattr(card, "payload", None) or {})
            if str(payload.get("resource") or "").strip() != resource:
                continue
            target_mi, target_ci = mi, ci
            break
        if target_mi >= 0:
            break
    if target_mi < 0:
        return False
    msg = messages[target_mi]
    cards = list(msg.cards or [])
    card = cards[target_ci]
    payload = dict(getattr(card, "payload", None) or {})
    payload.update(wf)
    payload["step"] = step
    if wf.get("item_id"):
        payload["item_id"] = wf["item_id"]
    cards[target_ci] = card.model_copy(update={"payload": payload})
    msg.cards = cards
    messages[target_mi] = msg
    session.messages = messages
    workflow_set(session, WORKFLOW_TEMPLATE, wf)
    return True


def derive_writing_workflow(home: Path, session: ChatSession) -> dict[str, Any]:
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.writing import _session_writing_pace

    phase = str(session.phase or ChatPhase.INTENT.value)
    active_phases = {
        ChatPhase.READY_TO_WRITE.value,
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
    }
    ff = dict(session.freeform or {})
    tid = str(session.task_id or "").strip()
    progress = {
        "total": 0,
        "confirmed": 0,
        "pending_write": 0,
        "needs_human": 0,
        "running": False,
    }
    task_title = ""
    if tid:
        task = _load_task(home, tid)
        if task is not None:
            task_title = (task.title or "").strip()
            chapters = list(task.chapters or [])
            progress["total"] = len(chapters)
            for chapter in chapters:
                status = getattr(chapter, "status", None)
                if status == ChapterStatus.CONFIRMED:
                    progress["confirmed"] += 1
                elif status == ChapterStatus.NEEDS_HUMAN:
                    progress["needs_human"] += 1
                elif status == ChapterStatus.PENDING:
                    progress["pending_write"] += 1
            progress["running"] = (
                phase == ChatPhase.WRITING.value and not bool(ff.get("writing_paused"))
            )
    return {
        "phase": phase,
        "task_id": tid,
        "task_title": task_title,
        "blueprint_id": str(session.blueprint_id or "").strip(),
        "pace": _session_writing_pace(session),
        "chapter_keys": [str(k).strip() for k in (ff.get("write_chapter_keys") or []) if str(k).strip()],
        "paused": bool(ff.get("writing_paused")),
        "active": phase in active_phases,
        "progress": progress,
    }


def _sync_writing_legacy_freeform(session: ChatSession, writing: dict[str, Any]) -> None:
    ff = dict(session.freeform or {})
    pace = writing.get("pace")
    if pace:
        ff["writing_pace"] = pace
    keys = writing.get("chapter_keys")
    if keys is not None:
        ff["write_chapter_keys"] = list(keys)
    if writing.get("paused"):
        ff["writing_paused"] = True
    elif "paused" in writing and not writing.get("paused"):
        ff.pop("writing_paused", None)
    session.freeform = ff


def reconcile_chat_session(home: Path, session: ChatSession) -> bool:
    """根据任务真值刷新各 workflow，并更新最新向导卡片。有变更返回 True。"""
    from report_writing.chat.wizard_state import (
        is_freeform_sample_wizard_mode,
        refresh_latest_wizard_card_in_session,
        sync_blueprint_wizard_session,
    )

    migrate_legacy_freeform(session)
    from report_writing.chat.dialogue import drop_assistant_echo_user_messages

    dirty = drop_assistant_echo_user_messages(session)

    from report_writing.chat.session_state import user_chose_skip_blueprint
    from report_writing.chat.write_path_after_match import is_awaiting_path_after_no_match

    wiz_ff = dict((session.freeform or {}).get("blueprint_wizard") or {})
    from report_writing.chat.chat_card_policy import infer_sample_wizard_active

    sample_wizard_active = infer_sample_wizard_active(session, wiz_ff)
    from report_writing.chat.session_documents import (
        drop_foreign_blueprint_wizard,
        focus_blocks_stale_blueprint_sync,
    )

    # 未定名新 w* / 模版入口：禁止用残留 session.blueprint_id 把上一份 selected 向导灌回来
    if focus_blocks_stale_blueprint_sync(session):
        if drop_foreign_blueprint_wizard(session):
            dirty = True
        tid_now = str(getattr(session, "task_id", "") or "").strip()
        bid_now = str(getattr(session, "blueprint_id", "") or "").strip()
        if bid_now and bid_now != tid_now:
            session.blueprint_id = ""
            dirty = True
        keep_wiz = dict((session.freeform or {}).get("blueprint_wizard") or {})
        if not keep_wiz and workflow_get(session, WORKFLOW_BLUEPRINT):
            workflow_set(session, WORKFLOW_BLUEPRINT, None)
            dirty = True
        # 本焦点没有自己的向导 id 时，跳过下方 leftover bid sync
    # 无蓝本 / 等待改选路径：不得继续同步或注入蓝本创建向导
    # 依据样本（freeform_sample）例外：writing_path 已是 freeform，但仍需保留上传/提取向导
    elif (user_chose_skip_blueprint(session) or is_awaiting_path_after_no_match(session)) and not sample_wizard_active:
        ff = dict(session.freeform or {})
        if ff.get("blueprint_wizard"):
            ff.pop("blueprint_wizard", None)
            session.freeform = ff
            dirty = True
        if workflow_get(session, WORKFLOW_BLUEPRINT):
            workflow_set(session, WORKFLOW_BLUEPRINT, None)
            dirty = True
        if session.blueprint_id:
            session.blueprint_id = ""
            dirty = True
    else:
        bid = (
            str(workflow_get(session, WORKFLOW_BLUEPRINT).get("blueprint_id") or "").strip()
            or str(session.blueprint_id or "").strip()
        )
        if bid:
            before = dict((session.freeform or {}).get("blueprint_wizard") or {})
            sync_blueprint_wizard_session(home, session, blueprint_id=bid)
            after = dict((session.freeform or {}).get("blueprint_wizard") or {})
            workflow_set(session, WORKFLOW_BLUEPRINT, after)
            if after != before:
                dirty = True
            if refresh_latest_wizard_card_in_session(home, session):
                dirty = True

    for name, derive, sync_legacy in (
        (WORKFLOW_WRITING, lambda: derive_writing_workflow(home, session), _sync_writing_legacy_freeform),
        (WORKFLOW_FREEFORM, lambda: derive_freeform_workflow(session), _sync_freeform_legacy),
        (WORKFLOW_EXPORT, lambda: derive_export_workflow(session), _sync_export_legacy),
        (WORKFLOW_TEMPLATE, lambda: derive_template_workflow(session), None),
    ):
        derived = derive()
        before = workflow_get(session, name)
        if derived != before:
            workflow_set(session, name, derived)
            if sync_legacy:
                sync_legacy(session, derived)
            dirty = True

    if refresh_template_wizard_card_in_session(session):
        dirty = True

    migrate_legacy_freeform(session)
    return dirty


def runtime_public_view(session: ChatSession, home: Path | None = None) -> dict[str, Any]:
    """返回可给前端的运行时快照（GET 会话 / SSE done）。"""
    migrate_legacy_freeform(session)
    rt = _runtime_root(session)
    workflows = {
        WORKFLOW_BLUEPRINT: workflow_get(session, WORKFLOW_BLUEPRINT),
        WORKFLOW_WRITING: workflow_get(session, WORKFLOW_WRITING),
        WORKFLOW_TEMPLATE: workflow_get(session, WORKFLOW_TEMPLATE),
        WORKFLOW_EXPORT: workflow_get(session, WORKFLOW_EXPORT),
        WORKFLOW_FREEFORM: workflow_get(session, WORKFLOW_FREEFORM),
    }
    bp = workflows[WORKFLOW_BLUEPRINT]
    # GET 会话 / softSync 对外快照：剥掉一次性自驱标记，避免刷新自动重跑已分析章。
    # 不改磁盘会话；真正清库由前端 ui-state / 分析开跑落库完成。
    # 例外：re_extract_outline / outline_extract_active 进行中要保留，否则 softSync
    # 会立刻回显任务旧目录、前端不再打提取流。
    if isinstance(bp, dict) and (
        bp.get("trigger_begin_study")
        or bp.get("trigger_chapter_study")
        or bp.get("chapter_study_keys")
        or bp.get("study_stream_active")
        or bp.get("trigger_outline_extract")
        or bp.get("outline_extract_active")
    ):
        bp = dict(bp)
        pending = str(bp.get("pending_wizard_command") or "").strip()
        keep_re_extract = pending == "re_extract_outline" or bool(
            bp.get("outline_extract_active")
        )
        for key in (
            "trigger_begin_study",
            "trigger_chapter_study",
            "chapter_study_keys",
            "study_stream_active",
            *(
                ()
                if keep_re_extract
                else ("trigger_outline_extract", "outline_extract_active")
            ),
        ):
            bp.pop(key, None)
        if pending in {"go_study", "re_study_chapters"}:
            bp.pop("pending_wizard_command", None)
        elif pending == "re_extract_outline" and not keep_re_extract:
            bp.pop("pending_wizard_command", None)
        workflows[WORKFLOW_BLUEPRINT] = bp
    from report_writing.chat.session_goal import get_session_goal, session_goal_brief

    goal = get_session_goal(session)
    from report_writing.chat.writing_brief import get_writing_brief, writing_brief_prompt_block

    brief = get_writing_brief(session)
    ff = dict(session.freeform or {})
    agent_chain = ff.get("agent_chain_log") if isinstance(ff.get("agent_chain_log"), list) else []
    writing_path = str(ff.get("writing_path") or "")
    chain_tail = agent_chain[-12:]
    from report_writing.chat.freeform_blueprint import composer_save_blueprint_state

    composer_save = composer_save_blueprint_state(home, session)
    from report_writing.chat.next_step import infer_suggested_next_step

    suggested_next_step = infer_suggested_next_step(session, home=home)
    from report_writing.chat.element_library_pending import list_pending
    from report_writing.chat.materials_pack_gate import peek_pending_materials_pack
    from report_writing.chat.session_documents import (
        focus_document_key,
        sanitize_freeform_document_leak,
        session_documents_map,
    )

    # 无蓝本未收录：修掉焦点文档误挂的上一份库蓝本（文档串台 / 误显示「同步」）
    sanitize_freeform_document_leak(session)
    from report_writing.chat.session_documents import collected_blueprint_id_for_focus

    collected_scoped = collected_blueprint_id_for_focus(session)

    pending_pack_by_bp = ff.get("pending_materials_pack_by_blueprint")
    if not isinstance(pending_pack_by_bp, dict):
        pending_pack_by_bp = {}
    # 无蓝本未收录：全局 pending 包属于上一份选用蓝本时不要回落到当前文档
    pending_pack_id = peek_pending_materials_pack(session)
    from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode

    sample_path = is_freeform_sample_wizard_mode(ff.get("blueprint_wizard"))
    bare_freeform = writing_path == "freeform" or sample_path
    if bare_freeform and not collected_scoped:
        # 只丢掉挂在「其它库蓝本」上的预选包，避免串上一份。
        # 当前文档自己新建/预选的资料包必须留下，否则槽位文件配置了也查找不到。
        own = str(ff.get("pending_materials_pack_id") or "").strip()
        current_bp = str(getattr(session, "blueprint_id", "") or "").strip()
        foreign = False
        if own and isinstance(pending_pack_by_bp, dict):
            owners = [
                str(k).strip()
                for k, v in pending_pack_by_bp.items()
                if str(v).strip() == own and str(k).strip()
            ]
            if owners and all(owner != current_bp for owner in owners):
                foreign = True
        pending_pack_id = "" if foreign else (own or pending_pack_id)
    return {
        "version": rt["version"],
        "workflows": workflows,
        "suggested_next_step": suggested_next_step,
        "element_library_pending": list_pending(session),
        "session_documents": session_documents_map(session),
        "focus_document_key": focus_document_key(session),
        # 开写前预建/预选的资料包（文档窗应能展示，不必等写作 clone）
        "pending_materials_pack_id": pending_pack_id,
        "pending_materials_pack_by_blueprint": {
            str(k): str(v)
            for k, v in pending_pack_by_bp.items()
            if str(k).strip() and str(v).strip()
        },
        "writing_doc_title": str(ff.get("title") or goal.get("doc_title") or "").strip(),
        "writing_doc_named": bool(ff.get("writing_doc_named")),
        "freeform_saved_blueprint_id": collected_scoped,
        "collected_blueprint_id": collected_scoped,
        # 可同步回库的源蓝本：仅「本任务已收录」或「选用蓝本路径的会话蓝本」。
        # 无蓝本 / 依据样本未收录时不要回落 session.blueprint_id（否则会串到上一份选用蓝本，误显示「同步」）。
        "sync_blueprint_id": (
            collected_scoped
            or (
                ""
                if bare_freeform
                else str(getattr(session, "blueprint_id", "") or "").strip()
            )
        ),
        "composer_save_blueprint": composer_save,
        "blueprint_wizard": bp,
        "session_goal": goal.to_dict(),
        "session_goal_brief": session_goal_brief(session),
        "writing_brief": brief,
        "writing_brief_prompt_preview": writing_brief_prompt_block(session)[:500],
        "writing_path": writing_path,
        "chain_debug": {
            "writing_path": writing_path,
            "agent_chain_tail": chain_tail,
        },
        # 兼容旧前端字段（不再写入意图分类）
        "intent": {
            "writing_path": writing_path,
            "agent_chain_tail": chain_tail,
        },
    }
