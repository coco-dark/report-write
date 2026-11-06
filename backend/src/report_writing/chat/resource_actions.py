"""对话内创建蓝本 / 渲染模版 / Word 样式等资源。"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from report_writing.domain.models import ChatActionPayload, ChatCard, ChatPayload, ChatPhase, ChatSession
from report_writing.services.robots import get_robot, update_robot


def _bind_robot_id(home: Path, session: ChatSession, field: str, item_id: str) -> None:
    if not session.robot_id or not item_id:
        return
    robot = get_robot(home, session.robot_id)
    if robot is None:
        return
    ids = list(getattr(robot, field, None) or [])
    if item_id not in ids:
        ids.append(item_id)
    data = robot.to_dict()
    data[field] = ids
    update_robot(home, robot.id, data)


def _wizard_working_task_id(
    session: ChatSession, blueprint_id: str = "", payload: ChatPayload = None
) -> str:
    """向导落盘目标：优先文档副本 w*（无蓝本·样本 / 选用即 clone）。学习/写作禁止回落到库蓝本章节。"""
    from report_writing.services.task_ids import is_writing_task_id

    body = ChatActionPayload.parse(payload).as_dict()
    for key in ("working_task_id", "writing_task_id", "writingTaskId"):
        cand = str(body.get(key) or "").strip()
        if cand and is_writing_task_id(cand):
            return cand
    task_cand = str(body.get("task_id") or "").strip()
    if task_cand and is_writing_task_id(task_cand):
        return task_cand

    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    wid = str(wizard.get("working_task_id") or "").strip()
    if wid and is_writing_task_id(wid):
        return wid
    from report_writing.chat.wizard_state import (
        is_freeform_sample_wizard_mode,
        is_selected_wizard_mode,
    )

    tid = str(getattr(session, "task_id", "") or "").strip()
    if tid and is_writing_task_id(tid):
        if is_freeform_sample_wizard_mode(wizard) or is_selected_wizard_mode(wizard):
            return tid
    bid = (blueprint_id or str(wizard.get("blueprint_id") or "") or "").strip()
    if bid:
        from report_writing.chat.session_documents import _writing_copy_id_for_blueprint

        copy_id = _writing_copy_id_for_blueprint(session, bid)
        if copy_id and is_writing_task_id(copy_id):
            return copy_id
    # 选用线：没有文档副本时不要回落 bp*（避免学习/写作改库蓝本章节）
    if bid and is_selected_wizard_mode(wizard):
        return ""
    return bid


def _create_freeform_sample_document(
    home: Path,
    session: ChatSession,
    *,
    title: str,
    category: str = "",
) -> tuple[str, dict]:
    """无蓝本·依据样本：只建文档任务 w*，不 create_blueprint / 不入库。

    上传/提取向导进行中不要 lock writing_path=freeform：前端会按「无蓝本」吞掉向导卡。
    收录前以 mode=freeform_sample 标识；真正开写无蓝本大纲时再锁路径。
    """
    from report_writing.chat.session_state import (
        WRITING_PATH_FREEFORM,
        get_writing_path,
    )
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        flush_lane,
        focus_document_key,
        mark_focus_entry_path,
        open_document_thread,
        stamp_document_thread_messages,
        upsert_session_document,
        set_focus_document_key,
    )
    from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set
    from report_writing.chat.wizard_state import WIZARD_MODE_FREEFORM_SAMPLE
    from report_writing.controller import ensure_task
    from report_writing.services.store import TaskStore
    from report_writing.services.task_ids import is_writing_task_id

    tip = (title or "").strip() or "未命名文档"

    from report_writing.chat.session_documents import (
        empty_writing_shell_reusable,
        reusable_onboarding_shell,
    )

    wiz_pref = dict((session.freeform or {}).get("blueprint_wizard") or {})
    reuse_tid = reusable_onboarding_shell(session, home, title_hint=tip)
    if not reuse_tid:
        from report_writing.chat.helpers import _load_task
        from report_writing.chat.session_documents import _titles_conflict

        for cand in (
            str(wiz_pref.get("working_task_id") or "").strip(),
            str(getattr(session, "task_id", "") or "").strip(),
            (focus_document_key(session) or "").replace("task:", "", 1),
        ):
            c = (cand or "").strip()
            if not c or not empty_writing_shell_reusable(home, c):
                continue
            old = _load_task(home, c)
            old_title = str(getattr(old, "title", "") or "").strip() if old else ""
            if (
                tip
                and old_title
                and _titles_conflict(old_title, tip)
                and not empty_writing_shell_reusable(home, c)
            ):
                continue
            reuse_tid = c
            break

    if reuse_tid:
        task_id = reuse_tid
        path = home / "tasks" / task_id
        store = TaskStore(path)
        task = store.load()
        task.title = tip
        if (category or "").strip():
            task.category = (category or "").strip()
        task.description = "无蓝本·依据样本写作（收录前不入库）"
        store.save(task)
    else:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        task_id = f"w{stamp}{secrets.token_hex(2)}"
        path = home / "tasks" / task_id
        ensure_task(path, biz_type="002")
        store = TaskStore(path)
        task = store.load()
        task.title = tip
        task.category = (category or "").strip()
        task.description = "无蓝本·依据样本写作（收录前不入库）"
        store.save(task)
    session.task_id = task_id
    session.blueprint_id = ""
    # 退出无蓝本锁，否则上传卡被前端 isFreeformWritingSession 吞掉
    ff = {**(session.freeform or {})}
    if get_writing_path(session) == WRITING_PATH_FREEFORM:
        ff.pop("writing_path", None)
    plan = dict(ff.get("plan") or {})
    if plan.get("goal") in {"skip_blueprint", "freeform"}:
        plan.pop("goal", None)
        if plan:
            ff["plan"] = plan
        else:
            ff.pop("plan", None)
    session.freeform = ff
    from report_writing.chat.session_documents import prune_superseded_freeform_documents

    upsert_session_document(
        session,
        kind="outline",
        task_id=task_id,
        blueprint_id="",
        writing_task_id=task_id,
        title=tip,
        clear_blueprint_id=True,
    )
    prune_superseded_freeform_documents(
        session, keep_task_id=task_id, title=tip
    )
    wizard = {
        "mode": WIZARD_MODE_FREEFORM_SAMPLE,
        "working_task_id": task_id,
        # 卡片/API 兼容：用 working id 作为 blueprint_id 槽，避免打到库 bp*
        "blueprint_id": task_id,
        "step": "upload",
        "title": tip,
    }
    session.freeform = {
        **(session.freeform or {}),
        "blueprint_wizard": wizard,
        "meta_confirmed": True,
        "title": tip,
        "writing_doc_named": True,
    }
    confirm_writing_document_name(
        session, tip, home=home, category=(category or "").strip(), message=tip
    )
    reg_key = f"task:{task_id}"
    set_focus_document_key(session, reg_key)
    open_document_thread(session, reg_key)
    stamp_document_thread_messages(session, reg_key)
    session.phase = ChatPhase.CREATE_HANDOFF.value
    workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
    from report_writing.chat.session_state import touch_session_plan

    touch_session_plan(session, goal="template_wizard", step="wizard_upload")
    mark_focus_entry_path(session, "template")
    ff_path = dict(session.freeform or {})
    if str(ff_path.get("writing_path") or "") == WRITING_PATH_FREEFORM:
        ff_path.pop("writing_path", None)
        session.freeform = ff_path
    flush_lane(session)
    return task_id, wizard


def _study_after_outline_confirmed_reply(
    home: Path,
    session: ChatSession,
    blueprint_id: str,
    *,
    already: bool = False,
) -> tuple[str, list[ChatCard]]:
    """目录已确认：把向导推到 study，并提示开始分析（不自动开跑）。"""
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.wizard_state import (
        WIZARD_MODE_SELECTED,
        _wizard_card_title,
        is_selected_wizard_mode,
    )

    bid = (blueprint_id or "").strip()
    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    wizard["step"] = "study"
    wizard["outline_confirmed"] = True
    wizard.pop("trigger_begin_study", None)
    wizard.pop("trigger_chapter_study", None)
    wizard.pop("chapter_study_keys", None)
    wizard.pop("trigger_outline_extract", None)
    wizard.pop("outline_extract_active", None)
    from report_writing.services.task_ids import is_writing_task_id

    ff_extra: dict = {"blueprint_wizard": wizard}
    if bid and is_writing_task_id(bid):
        # 无蓝本·样本：确认目录后可收录
        ff_extra["outline_confirmed"] = True
        session.task_id = bid
        session.blueprint_id = ""
    elif bid:
        session.blueprint_id = bid
    session.freeform = {**(session.freeform or {}), **ff_extra}
    from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

    workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
    task = _load_task(home, bid) if bid else None
    chapter_n = len(list(task.outline.chapters or [])) if task and task.outline else 0
    count_bit = f"一共大约 {chapter_n} 章，" if chapter_n else ""
    if already:
        tip = (
            f"目录这边已经确认过啦。{count_bit}"
            "接下来可以点「开始分析全部」，"
            "或者跟我说「分析第1到5章」「只分析概述」——我按你的范围来。"
        )
    else:
        tip = (
            f"好，目录我记下了。{count_bit}"
            "下一步是分析写作要点——你可以点下方「开始分析全部」一口气分析完，"
            "也可以在对话里指定范围，比如「分析第1到3章」「只分析第2章」。确认和分析是分开的，你定好范围我再动手。"
        )
    mode = WIZARD_MODE_SELECTED if is_selected_wizard_mode(wizard) else ""
    return (
        tip,
        [
            _blueprint_wizard_card(
                blueprint_id=bid,
                step="study",
                title=_wizard_card_title("study", mode=mode),
                extra=wizard,
            )
        ],
    )


def _prompt_confirm_outline_before_study(
    home: Path,
    session: ChatSession,
    blueprint_id: str,
    *,
    want: str = "分析",
) -> tuple[str, list[ChatCard]] | None:
    """目录未确认时，用对话提示先确认，不直接开学习/重学。"""
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.wizard_state import (
        WIZARD_MODE_SELECTED,
        _wizard_card_title,
        is_selected_wizard_mode,
    )

    card_bid = (blueprint_id or "").strip()
    if not card_bid:
        return None
    bid = _wizard_working_task_id(session, card_bid) or card_bid
    task = _load_task(home, bid)
    if task is None:
        return None
    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    selected = is_selected_wizard_mode(wizard)
    chapters = list((task.outline.chapters if task.outline else None) or [])
    if not chapters:
        if selected:
            return (
                "这份蓝本还没有目录，对话里没法分析要点。请先去蓝本页配好目录，或换一份已有目录的蓝本。",
                [],
            )
        return (
            "目录还没提取出来，谈不上分析要点。请先在下方卡片点「提取目录」，提出章节后再确认目录。",
            [
                _blueprint_wizard_card(
                    blueprint_id=card_bid,
                    step="outline",
                    title="依据模版编写 · 提取目录",
                    extra=wizard,
                )
            ],
        )
    if bool(getattr(task, "outline_confirmed", False)):
        return None
    # UI/会话已标确认但任务位丢失：补齐确认，避免「右侧已确认、对话仍拦学习」
    if bool(wizard.get("outline_confirmed")):
        bp_dir = home / "tasks" / bid
        if bp_dir.is_dir():
            try:
                from report_writing.controller import confirm_outline
                from report_writing.services.task_ids import is_blueprint_task_id

                confirm_outline(bp_dir)
                fixed = _load_task(home, bid) or task
                if is_blueprint_task_id(bid):
                    from report_writing.services.blueprint_catalog import save_blueprint_task

                    save_blueprint_task(fixed)
                return None
            except Exception:
                pass
    wizard["step"] = "outline_confirm"
    wizard.pop("trigger_begin_study", None)
    wizard.pop("trigger_chapter_study", None)
    wizard.pop("chapter_study_keys", None)
    from report_writing.chat.wizard_state import _chapter_preview

    preview = _chapter_preview(task)
    wizard["chapters"] = preview
    wizard["chapter_count"] = len(chapters)
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    from report_writing.services.task_ids import is_writing_task_id as _is_w

    if selected:
        session.blueprint_id = card_bid
        if _is_w(bid):
            session.task_id = bid
            wizard["working_task_id"] = bid
    elif _is_w(bid):
        session.task_id = bid
        session.blueprint_id = ""
    else:
        session.blueprint_id = card_bid
    from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

    workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
    mode = WIZARD_MODE_SELECTED if selected else str(wizard.get("mode") or "")
    wizard_card = _blueprint_wizard_card(
        blueprint_id=card_bid,
        step="outline_confirm",
        title=_wizard_card_title("outline_confirm", mode=mode),
        extra={"chapters": preview, **wizard},
    )
    from report_writing.chat.clarify_flow import begin_clarify

    question = (
        f"目录还没确认（共 {len(chapters)} 章），还不能{want}。"
        "先确认目录，还是稍后再说？"
    )
    clarify_reply, clarify_cards = begin_clarify(
        session,
        source="wizard",
        question=question,
        options=[
            {
                "id": "confirm_outline",
                "label": "确认全部目录",
                "slots": {},
            },
            {
                "id": "later",
                "label": "先看看再说",
                "slots": {"wizard_defer": True},
            },
        ],
        candidate={
            "kind": "wizard_command",
            "name": "confirm_outline",
            "payload": {
                "blueprint_id": card_bid,
                "study_message": f"确认目录后再{want}",
            },
        },
        phase_hint="wizard",
    )
    return (
        clarify_reply
        or (
            f"目录还没确认（共 {len(chapters)} 章），还不能{want}哦。"
            "满意的话点「确认全部目录」，确认和分析是分开的。"
        ),
        list(clarify_cards or []) + [wizard_card],
    )


def _continue_active_wizard_reply(
    home: Path | None,
    session: ChatSession,
    *,
    title_hint: str = "",
) -> tuple[str, list[ChatCard]] | None:
    """已有进行中的向导时不再重置为 meta，避免重复「填写名称」卡片。"""
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.session_state import active_blueprint_wizard
    from report_writing.chat.wizard_state import (
        _wizard_card_title,
        refresh_latest_wizard_card_in_session,
        sync_blueprint_wizard_session,
    )

    wizard = dict(active_blueprint_wizard(session) or {})
    from report_writing.chat.session_documents import wizard_belongs_to_focus

    if wizard and not wizard_belongs_to_focus(session, wizard):
        return None
    bid = str(session.blueprint_id or "").strip()
    if not wizard and bid and home is not None:
        sync_blueprint_wizard_session(home, session, blueprint_id=bid)
        wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    if not wizard:
        return None
    bid = str(wizard.get("blueprint_id") or bid or "").strip()
    hint = (title_hint or "").strip() or str(wizard.get("title") or "").strip()
    if bid and home is not None:
        sync_blueprint_wizard_session(home, session, blueprint_id=bid)
        wizard = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        refresh_latest_wizard_card_in_session(home, session)
    elif hint and not str(wizard.get("title") or "").strip():
        wizard["title"] = hint
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    step = str(wizard.get("step") or "meta").strip()
    bid = str(wizard.get("blueprint_id") or "").strip()
    if step == "meta" and not bid and home is not None:
        meta_title = hint or str(wizard.get("title") or "").strip()
        if meta_title:
            return _action_blueprint_wizard(
                home,
                session,
                {"step": "meta", "title": meta_title, "blueprint_id": ""},
            )
    title = _wizard_card_title(step)
    return (
        "蓝本向导已在进行，请在最下方卡片继续当前步骤（无需重复创建）。",
        [
            _blueprint_wizard_card(
                blueprint_id=bid,
                step=step,
                title=title,
                extra=wizard,
            )
        ],
    )


def _begin_blueprint_wizard(
    session: ChatSession,
    *,
    title_hint: str = "",
    home: Path | None = None,
    force_new: bool = False,
    user_message: str = "",
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _blueprint_wizard_card
    from report_writing.chat.session_state import touch_session_plan
    from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

    hint = (title_hint or "").strip()
    msg = (user_message or "").strip() or hint
    from report_writing.chat.session_documents import (
        apply_matched_document_to_session,
        match_session_document,
        resolve_document_scope,
    )

    scope = resolve_document_scope(session, msg, title_hint=hint, invoke=invoke)
    if scope.get("title") and not hint:
        hint = str(scope.get("title") or "").strip()
    strong_new = bool(scope.get("force_new"))
    ff_plan = dict((session.freeform or {}).get("plan") or {})
    wiz_ff = dict((session.freeform or {}).get("blueprint_wizard") or {})
    skip_match = bool(
        (wiz_ff.get("fresh_meta") and str(wiz_ff.get("step") or "meta").strip() == "meta")
        or ff_plan.get("goal") == "template_wizard"
        or "document_thread_since_index" in dict(session.freeform or {})
        or force_new
        or strong_new
    )
    # 会话里已有同名文档 → 切焦点（新开文档意图 / 模版向导进行中除外）
    matched = None if skip_match else match_session_document(session, msg, title_hint=hint)
    if matched and home is not None and not strong_new and not force_new:
        bid = str(matched.get("blueprint_id") or matched.get("task_id") or "").strip()
        title = str(matched.get("title") or bid).strip()
        if bid:
            ids = apply_matched_document_to_session(session, matched)
            from report_writing.chat.actions import _action_select_blueprint

            reply, cards = _action_select_blueprint(
                home, session, {"blueprint_id": bid, "title": title}
            )
            prefix = f"已切换到「{ids.get('title') or title}」。"
            if reply and prefix not in reply:
                reply = f"{prefix}\n{reply}"
            elif not reply:
                reply = f"{prefix}可以说分析/配置/写某一章，或从文档小窗继续。"
            return reply, list(cards or [])

    # 新开文档：LLM/兜底判定或调用方显式 force_new
    if not force_new and strong_new:
        force_new = True

    if force_new:
        from report_writing.chat.session_documents import (
            begin_document_onboarding,
            bind_onboarding_shell_to_session,
            reusable_onboarding_shell,
        )

        shell_tid = reusable_onboarding_shell(session, home, title_hint=hint) if home else ""
        if not shell_tid:
            begin_document_onboarding(
                session, home, message=msg, title_hint=hint, force_new=True
            )
            shell_tid = (
                reusable_onboarding_shell(session, home, title_hint=hint) if home else ""
            ) or str(getattr(session, "task_id", "") or "").strip()
        # 丢掉当前向导/写作焦点，但绝不清 session_documents（多文档登记保留）
        session.blueprint_id = ""
        ff = {**(session.freeform or {})}
        ff.pop("blueprint_wizard", None)
        if shell_tid:
            bind_onboarding_shell_to_session(session, shell_tid, title_hint=hint)
            ff = {**(session.freeform or {})}
        else:
            session.task_id = ""
            ff.pop("focus_document_key", None)
        # 新文档题目以本轮 hint 为准，勿沿用上一份的 freeform 标题
        if hint:
            ff["title"] = hint
        else:
            ff.pop("title", None)
        session.freeform = ff
        workflow_set(session, WORKFLOW_BLUEPRINT, {})

    existing_bid = str(session.blueprint_id or "").strip()
    from report_writing.chat.session_documents import drop_foreign_blueprint_wizard

    if not force_new:
        drop_foreign_blueprint_wizard(session)
        existing_bid = str(session.blueprint_id or "").strip()
        from report_writing.chat.session_documents import message_wants_own_template

        wiz_left = dict((session.freeform or {}).get("blueprint_wizard") or {})
        if message_wants_own_template(msg) and (
            str(wiz_left.get("mode") or "") == "selected"
            or str(wiz_left.get("step") or "") == "study"
        ):
            ff_drop = dict(session.freeform or {})
            ff_drop.pop("blueprint_wizard", None)
            session.freeform = ff_drop
            session.blueprint_id = ""
            existing_bid = ""
            workflow_set(session, WORKFLOW_BLUEPRINT, {})
    if not force_new and (existing_bid or (session.freeform or {}).get("blueprint_wizard")):
        cont = _continue_active_wizard_reply(home, session, title_hint=hint)
        if cont is not None:
            return cont
        if existing_bid:
            from report_writing.chat.wizard_state import _wizard_card_title

            step = "upload"
            wizard = {"blueprint_id": existing_bid, "step": step}
            if hint:
                wizard["title"] = hint
            session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
            return (
                "已就绪，请在最下方向导卡继续当前步骤，无需重复填名称。",
                [
                    _blueprint_wizard_card(
                        blueprint_id=existing_bid,
                        step=step,
                        title=_wizard_card_title(step),
                        extra=wizard,
                    )
                ],
            )

    session.phase = ChatPhase.CREATE_HANDOFF.value
    from report_writing.chat.wizard_state import WIZARD_MODE_CREATE
    from report_writing.chat.session_state import (
        WRITING_PATH_FREEFORM,
        get_writing_path,
    )

    # 用户改口「我有样本 / 创建蓝本」：必须退出无蓝本锁，否则前端会按 freeform 吞掉向导卡
    ff_unlock = {**(session.freeform or {})}
    if get_writing_path(session) == WRITING_PATH_FREEFORM:
        ff_unlock.pop("writing_path", None)
    plan = dict(ff_unlock.get("plan") or {})
    if plan.get("goal") in {"skip_blueprint", "freeform"}:
        plan.pop("goal", None)
        if plan:
            ff_unlock["plan"] = plan
        else:
            ff_unlock.pop("plan", None)
    session.freeform = ff_unlock

    wizard = {"step": "meta", "mode": WIZARD_MODE_CREATE, "fresh_meta": True}
    if hint:
        wizard["title"] = hint
    shell_tid = str(getattr(session, "task_id", "") or "").strip()
    if shell_tid:
        wizard["working_task_id"] = shell_tid
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
    touch_session_plan(session, goal="template_wizard", step="wizard_meta")
    from report_writing.chat.session_documents import flush_lane, mark_focus_entry_path

    mark_focus_entry_path(session, "template")
    flush_lane(session)
    extra: dict[str, Any] = {"mode": WIZARD_MODE_CREATE, "fresh_meta": True}
    if hint:
        extra["title"] = hint
    if shell_tid:
        extra["working_task_id"] = shell_tid
    return (
        "好，咱们依据模版编写：先确认名称，再上传 Word/PDF 模版；我会提取目录和分析要点。"
        "在收录进蓝本库之前，都在当前文档窗口里编辑。",
        [
            _blueprint_wizard_card(
                blueprint_id="",
                step="meta",
                title="依据模版编写 · 名称",
                extra=extra,
            )
        ],
    )


def _action_resource_choice(
    home: Path,
    session: ChatSession,
    payload: ChatPayload,
    *,
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.actions import _action_skip_blueprint
    from report_writing.chat.cards import _blueprint_wizard_card, _template_wizard_card

    payload = ChatActionPayload.parse(payload)
    resource = str(payload.get("resource") or "").strip()
    accept = bool(payload.get("accept"))
    if not accept:
        if resource == "blueprint":
            return _action_skip_blueprint(session, home=home, invoke=invoke, emit=emit)
        return ("好的，这一项先跳过。需要时随时跟我说。", [])

    if resource == "blueprint":
        return _begin_blueprint_wizard(session, title_hint="")
    if resource == "render_template":
        return (
            "在对话里配置渲染模版：可选用已有或新建 HTML 模版，保存后会绑定到当前机器人。",
            [
                _template_wizard_card(
                    "render_template",
                    step="choose",
                    title="渲染模版向导",
                    message="导出 Word 时按模版占位填充章节。点选已有模版，或新建。",
                )
            ],
        )
    if resource == "word_style":
        return (
            "在对话里配置 Word 样式：可选用已有或新建，保存后会绑定到当前机器人。",
            [
                _template_wizard_card(
                    "word_style",
                    step="choose",
                    title="Word 样式向导",
                    message="控制导出 Word 的字号、字体等。点选已有样式，或新建。",
                )
            ],
        )
    return ("收到。", [])


def execute_wizard_command(
    home: Path,
    session: ChatSession,
    command: str,
    *,
    study_message: str = "",
    chapter_sequences: list[str] | None = None,
    study_scope: str = "",
    study_method: str = "",
    study_text: str = "",
    chapter_keys: list[str] | None = None,
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    """子 Agent 工具统一入口：wizard_command → 卡片与回复。"""
    from report_writing.chat.session_state import active_blueprint_wizard
    from report_writing.chat.wizard_state import (
        CREATE_ONLY_WIZARD_COMMANDS,
        is_selected_wizard_mode,
        seed_selected_blueprint_wizard,
    )

    wizard = dict(active_blueprint_wizard(session) or {})
    if not wizard:
        wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    blueprint_id = str(wizard.get("blueprint_id") or session.blueprint_id or "").strip()
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("未指定向导操作")
    if not blueprint_id and cmd not in {"re_upload_sample"}:
        raise ValueError("还没有打开依据模版向导，请先填写文档名称")
    # 选用路径若会话向导被清空，按任务进度重新种 selected ops
    if (
        blueprint_id
        and not is_selected_wizard_mode(wizard)
        and str((session.freeform or {}).get("writing_path") or "").strip() == "blueprint"
        and not str(wizard.get("step") or "").strip()
    ):
        wizard = seed_selected_blueprint_wizard(home, session, blueprint_id)
    if is_selected_wizard_mode(wizard) and cmd in CREATE_ONLY_WIZARD_COMMANDS:
        raise ValueError(
            "当前是选用已有蓝本，不用走创建向导（上传样板/提取目录）。"
            "可以说「确认目录」「开始分析」或「帮我写已分析的章节」。"
        )
    body: dict[str, Any] = {
        **wizard,
        "wizard_command": cmd,
        "blueprint_id": blueprint_id,
    }
    if cmd == "re_study_chapters":
        body["study_message"] = study_message
        body["chapter_sequences"] = list(chapter_sequences or [])
        if str(study_scope or "").strip():
            body["study_scope"] = str(study_scope).strip()
        if str(study_method or "").strip():
            body["study_method"] = str(study_method).strip()
        if str(study_text or "").strip():
            body["study_text"] = str(study_text)
        keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
        if keys:
            body["chapter_keys"] = keys
        if invoke is not None:
            body["_clarify_invoke"] = invoke
    return _action_blueprint_wizard(home, session, body)


def _resolve_meta_existing_blueprint(
    home: Path,
    session: ChatSession,
    wizard: dict[str, Any],
    payload: ChatPayload,
) -> str:
    """meta 步是否沿用已有 task id；新文档/标题冲突时返回空以触发新建。"""
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.session_documents import _titles_conflict, focus_document_meta

    payload = ChatActionPayload.parse(payload)
    requested = str(payload.get("title") or wizard.get("title") or "").strip()
    wizard_bid = str(wizard.get("blueprint_id") or "").strip()
    session_bid = str(session.blueprint_id or "").strip()
    fresh = bool(wizard.get("fresh_meta")) and not wizard_bid
    if fresh:
        existing = ""
    else:
        existing = wizard_bid or session_bid
    if not existing:
        return ""
    if not requested:
        return existing
    task = _load_task(home, existing)
    old_title = str(getattr(task, "title", "") or "").strip() if task else ""
    focus_title = str(focus_document_meta(session).get("title") or "").strip()
    if _titles_conflict(old_title, requested) or (
        focus_title and _titles_conflict(focus_title, requested)
    ):
        session.blueprint_id = ""
        wiz = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        wiz.pop("blueprint_id", None)
        wiz.pop("working_task_id", None)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wiz}
        return ""
    return existing


def _action_blueprint_wizard(
    home: Path,
    session: ChatSession,
    payload: ChatPayload,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _blueprint_wizard_card, _handoff_card
    from report_writing.chat.gates import is_blueprint_valid
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.actions import _action_select_blueprint

    from report_writing.chat.wizard_state import (
        CREATE_ONLY_WIZARD_COMMANDS,
        CREATE_ONLY_WIZARD_STEPS,
        _wizard_card_title,
        is_selected_wizard_mode,
        refresh_latest_wizard_card_in_session,
        sync_blueprint_wizard_session,
    )

    from report_writing.chat.wizard_state import WIZARD_MODE_FREEFORM_SAMPLE
    from report_writing.services.task_ids import is_writing_task_id

    payload = ChatActionPayload.parse(payload)
    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    step = str(payload.get("step") or wizard.get("step") or "meta").strip()
    payload_bid = str(payload.get("blueprint_id") or "").strip()
    # UI / Tool 可能用 command 别名；回退命令必须优先于空 step 兜底
    wizard_command_early = str(
        payload.get("wizard_command") or payload.get("command") or ""
    ).strip()
    if is_selected_wizard_mode(wizard):
        if wizard_command_early in CREATE_ONLY_WIZARD_COMMANDS:
            raise ValueError(
                "当前是选用已有蓝本，不用走创建向导（上传样板/提取目录）。"
                "可以说「确认目录」「开始分析」或「帮我写已分析的章节」。"
            )
        if step in CREATE_ONLY_WIZARD_STEPS:
            step = "outline_confirm"
            payload = {**payload, "step": step}
    # 「填名称」且未带 id：若无会话蓝本则新建；已有蓝本则沿用，避免重复 bp 前缀
    if step == "meta" and not payload_bid:
        requested_title = str(payload.get("title") or wizard.get("title") or "").strip()
        existing = _resolve_meta_existing_blueprint(home, session, wizard, payload)
        if existing:
            blueprint_id = existing
            synced = sync_blueprint_wizard_session(home, session, blueprint_id=existing)
            if synced:
                wizard = dict(synced)
        else:
            wiz_next: dict[str, Any] = {
                "step": "meta",
                "title": requested_title,
            }
            if str(wizard.get("mode") or "").strip():
                wiz_next["mode"] = str(wizard.get("mode") or "").strip()
            if wizard.get("fresh_meta"):
                wiz_next["fresh_meta"] = True
            wizard = wiz_next
            blueprint_id = ""
    else:
        blueprint_id = payload_bid or str(wizard.get("blueprint_id") or "").strip()
        if not blueprint_id:
            blueprint_id = str(session.blueprint_id or "").strip()
    # ui-state 若误清向导：w* 样本任务须恢复 freeform_sample，否则上传后无「提取目录」卡
    if blueprint_id and is_writing_task_id(blueprint_id) and not is_selected_wizard_mode(wizard):
        if not str(wizard.get("mode") or "").strip():
            wizard["mode"] = WIZARD_MODE_FREEFORM_SAMPLE
        wizard.setdefault("working_task_id", blueprint_id)
        wizard.setdefault("blueprint_id", blueprint_id)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    # 主动回退命令：先落 pending，再 sync，避免 derive 把 step 钉回 outline/study
    rollback_cmds = {"re_upload_sample", "re_extract_outline", "go_outline_confirm"}
    if wizard_command_early in rollback_cmds:
        if wizard_command_early == "re_upload_sample":
            wizard["step"] = "upload"
            wizard["pending_wizard_command"] = "re_upload_sample"
            wizard.pop("trigger_outline_extract", None)
        elif wizard_command_early == "re_extract_outline":
            wizard["step"] = "outline"
            wizard["pending_wizard_command"] = "re_extract_outline"
            wizard["trigger_outline_extract"] = True
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    if blueprint_id and step != "meta" and wizard_command_early not in rollback_cmds:
        synced = sync_blueprint_wizard_session(home, session, blueprint_id=blueprint_id)
        if synced:
            wizard = dict(synced)
            if is_writing_task_id(blueprint_id) and not str(wizard.get("mode") or "").strip():
                wizard["mode"] = WIZARD_MODE_FREEFORM_SAMPLE
                session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
    elif blueprint_id and wizard_command_early in rollback_cmds:
        synced = sync_blueprint_wizard_session(home, session, blueprint_id=blueprint_id)
        if synced:
            wizard = dict(synced)
    wizard_command = str(
        payload.get("wizard_command") or payload.get("command") or ""
    ).strip()
    # 仅带 step=upload 且当前已过上传：视为主动回退（避免落到「继续按卡片提示」）
    if not wizard_command and step == "upload" and not is_selected_wizard_mode(wizard):
        from report_writing.chat.wizard_loop_guard import normalized_wizard_step
        from report_writing.chat.wizard_state import wizard_step_index

        cur = normalized_wizard_step(str(wizard.get("step") or ""))
        if wizard_step_index(cur) > wizard_step_index("upload"):
            wizard_command = "re_upload_sample"

    if wizard_command == "re_extract_outline":
        if not blueprint_id:
            raise ValueError("还没有蓝本，请先完成名称与上传样板")
        wizard["step"] = "outline"
        wizard["trigger_outline_extract"] = True
        wizard["pending_wizard_command"] = "re_extract_outline"
        wizard["outline_confirmed"] = False
        # 清掉预览目录，避免前端/softSync 把任务旧目录当成「已提取完成」
        wizard["chapters"] = []
        wizard["chapter_count"] = 0
        wizard.pop("trigger_begin_study", None)
        wizard.pop("trigger_chapter_study", None)
        wizard.pop("chapter_study_keys", None)
        wizard.pop("outline_extract_active", None)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        session.phase = ChatPhase.CREATE_HANDOFF.value
        try:
            from report_writing.services.store import TaskStore

            store = TaskStore(home / "tasks" / blueprint_id)
            if store.path.is_dir():
                task = store.load()
                task.outline_confirmed = False
                store.save(task)
        except Exception:
            pass
        refresh_latest_wizard_card_in_session(home, session)
        return (
            "好，咱们从样板重新提取目录。已在下方卡片开始提取，过程会显示在卡片里。",
            [
                _blueprint_wizard_card(
                    blueprint_id=blueprint_id,
                    step="outline",
                    title="依据模版编写 · 提取目录",
                    extra={**wizard},
                )
            ],
        )

    if wizard_command == "re_upload_sample":
        wizard["step"] = "upload"
        wizard["pending_wizard_command"] = "re_upload_sample"
        wizard["outline_confirmed"] = False
        wizard.pop("trigger_outline_extract", None)
        wizard.pop("trigger_begin_study", None)
        wizard.pop("trigger_chapter_study", None)
        wizard.pop("chapter_study_keys", None)
        wizard.pop("outline_extract_active", None)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        session.phase = ChatPhase.CREATE_HANDOFF.value
        bid = blueprint_id or ""
        if bid:
            try:
                from report_writing.services.store import TaskStore

                store = TaskStore(home / "tasks" / bid)
                if store.path.is_dir():
                    task = store.load()
                    task.outline_confirmed = False
                    store.save(task)
            except Exception:
                pass
            refresh_latest_wizard_card_in_session(home, session)
            wizard = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        return (
            "行，先重新上传样板。上传完成后再说「提取目录」或点卡片里的按钮。",
            [
                _blueprint_wizard_card(
                    blueprint_id=bid,
                    step="upload",
                    title="依据模版编写 · 上传样板",
                    extra={**wizard},
                )
            ],
        )

    if wizard_command == "go_outline_confirm":
        if not blueprint_id:
            raise ValueError("还没有蓝本")
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, blueprint_id)
        from report_writing.chat.wizard_state import _chapter_preview

        chapters = _chapter_preview(task)
        chapter_n = len(list(task.outline.chapters or [])) if task and task.outline else 0
        if not chapter_n:
            wizard["step"] = "outline"
            session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
            return (
                "目录还是空的，请先说「提取目录」或点卡片里的「提取目录」。",
                [
                    _blueprint_wizard_card(
                        blueprint_id=blueprint_id,
                        step="outline",
                        title="依据模版编写 · 提取目录",
                        extra=wizard,
                    )
                ],
            )
        # 目录已确认：勿解开再拽回确认步，交给分析
        if task and bool(getattr(task, "outline_confirmed", False)):
            return execute_wizard_command(
                home,
                session,
                "go_study",
                invoke=payload.get("_clarify_invoke"),
            )
        wizard["step"] = "outline_confirm"
        wizard["outline_confirmed"] = False
        wizard["chapters"] = chapters
        wizard["chapter_count"] = chapter_n
        wizard.pop("trigger_begin_study", None)
        wizard.pop("trigger_chapter_study", None)
        wizard.pop("chapter_study_keys", None)
        wizard.pop("trigger_outline_extract", None)
        # 短暂挂起，便于 refresh 把最新卡 step 倒回确认目录
        wizard["pending_wizard_command"] = "go_outline_confirm"
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        refresh_latest_wizard_card_in_session(home, session)
        wizard = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        wizard.pop("pending_wizard_command", None)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        return (
            f"好嘞，目录一共 {chapter_n} 章，你先在右侧文档窗口里核对一下树和拓扑图。"
            "整份没问题就点「确认目录」，也可以跟我说「确认全部」或「确认第1到3章」——"
            "这步只定目录，确认完我们再商量怎么学。",
            [
                _blueprint_wizard_card(
                    blueprint_id=blueprint_id,
                    step="outline_confirm",
                    title="依据模版编写 · 确认目录",
                    extra={**wizard, "chapters": chapters},
                )
            ],
        )

    if wizard_command == "go_study":
        if not blueprint_id:
            raise ValueError("还没有蓝本")
        blocked = _prompt_confirm_outline_before_study(
            home, session, blueprint_id, want="开始分析各章要点"
        )
        if blocked:
            return blocked
        from report_writing.chat.helpers import _load_task
        from report_writing.chat.wizard_state import partition_wizard_study

        wizard["step"] = "study"
        wizard.pop("trigger_outline_extract", None)
        work_id = _wizard_working_task_id(session, blueprint_id, payload) or ""
        if not work_id:
            raise ValueError("请先打开文档副本再分析；学习必须落在文档副本，不能改库蓝本章节")
        if work_id != blueprint_id:
            wizard["working_task_id"] = work_id
        task = _load_task(home, work_id)
        if task is None:
            raise ValueError(f"文档副本不存在：{work_id}")
        parts = partition_wizard_study(task)
        remain_keys = [
            str(getattr(n, "key", "") or "").strip()
            for n in (parts.get("unanalyzed") or [])
            if str(getattr(n, "key", "") or "").strip()
        ]
        analyzed_n = len(parts.get("analyzed") or [])
        # 已有学过的章：只触发未学章，避免重进会话/再说「开始学习」把已学内容整模板重跑
        if analyzed_n > 0 and remain_keys:
            wizard.pop("trigger_begin_study", None)
            wizard["trigger_chapter_study"] = remain_keys
            tip = (
                f"好，接着学还没完成的 {len(remain_keys)} 章（已学 {analyzed_n} 章会保留）。"
                "章节进度请在右侧文档窗口查看；也可以先说「帮我写已分析的章节」开写。"
            )
        elif analyzed_n > 0 and not remain_keys:
            wizard.pop("trigger_begin_study", None)
            wizard.pop("trigger_chapter_study", None)
            tip = (
                "各章要点其实都齐了。若要整本重分析可以说「重新分析」；"
                "要开写可以说「帮我写已分析的章节」或点卡片里的开写。"
            )
        else:
            # 用户明确说「开始学习 / 全部学习」才自动开跑；指定章节走 re_study_chapters
            wizard["trigger_begin_study"] = True
            wizard.pop("trigger_chapter_study", None)
            tip = (
                "好，那我按当前目录开始学全部章节的写作要点，进度请在右侧文档窗口查看。"
                "下次要是只想分析一部分，直接说「分析第1到3章」或「只分析概述」就行。"
            )
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        session.phase = ChatPhase.CREATE_HANDOFF.value
        from report_writing.chat.wizard_state import WIZARD_MODE_SELECTED, _wizard_card_title, is_selected_wizard_mode

        mode = WIZARD_MODE_SELECTED if is_selected_wizard_mode(wizard) else ""
        return (
            tip,
            [
                _blueprint_wizard_card(
                    blueprint_id=blueprint_id,
                    step="study",
                    title=_wizard_card_title("study", mode=mode),
                    extra=wizard,
                )
            ],
        )

    if wizard_command == "re_study_chapters":
        if not blueprint_id:
            raise ValueError("还没有蓝本")
        clarify_invoke = payload.pop("_clarify_invoke", None)
        from report_writing.chat.helpers import _load_task
        from report_writing.chat.study_chapter_resolve import resolve_study_chapter_keys

        blocked = _prompt_confirm_outline_before_study(
            home, session, blueprint_id, want="分析指定章节"
        )
        if blocked:
            return blocked
        work_id = _wizard_working_task_id(session, blueprint_id, payload) or ""
        if not work_id:
            raise ValueError("请先打开文档副本再分析；学习必须落在文档副本，不能改库蓝本章节")
        if work_id != blueprint_id:
            wizard["working_task_id"] = work_id
        task = _load_task(home, work_id)
        if task is None:
            raise ValueError(f"文档副本不存在：{work_id}")
        keys = [str(k).strip() for k in (payload.get("chapter_keys") or []) if str(k).strip()]
        msg = str(payload.get("study_message") or "").strip()
        seqs = list(payload.get("chapter_sequences") or [])
        resolved_scope = str(payload.get("study_scope") or "").strip()
        if not keys:
            from report_writing.chat.clarify_flow import (
                build_study_scope_clarify,
                study_scope_needs_clarify,
            )
            from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message

            tops = [str(s).strip() for s in seqs if str(s).strip()] or parse_study_sequences_from_message(
                msg
            )
            from report_writing.chat.study_chapter_resolve import STUDY_SCOPES

            if (
                resolved_scope not in STUDY_SCOPES
                and clarify_invoke is not None
                and study_scope_needs_clarify(task, msg, tops, resolved_scope=resolved_scope)
            ):
                from report_writing.chat.classifier_context import pack_classifier_context
                from report_writing.chat.clarify_llm import classify_study_scope_llm

                ctx = pack_classifier_context(session)
                judged = classify_study_scope_llm(
                    msg,
                    dialogue_tail=list(ctx.get("dialogue_tail") or []),
                    focus_document_brief=str(ctx.get("focus_document_brief") or ""),
                    invoke=clarify_invoke,
                )
                if judged in STUDY_SCOPES:
                    resolved_scope = judged
            if resolved_scope in STUDY_SCOPES:
                from report_writing.chat.clarify_flow import merge_study_message_with_scope

                msg = merge_study_message_with_scope(msg, resolved_scope)
            elif tops and study_scope_needs_clarify(
                task, msg, tops, resolved_scope=resolved_scope
            ):
                return build_study_scope_clarify(
                    session, sequences=tops, study_message=msg or f"分析第{tops[0]}章"
                )
            keys = resolve_study_chapter_keys(
                task,
                msg,
                llm_sequences=seqs,
                scope=resolved_scope or "whole",
            )
        if not keys:
            from report_writing.chat.action_slots import build_action_slot_clarify

            return build_action_slot_clarify(
                session,
                "re_study_chapters",
                {
                    "task_id": blueprint_id,
                    "chapter_sequences": list(seqs or []),
                    "study_message": msg,
                    "study_scope": resolved_scope,
                },
                ["chapter_sequences"],
                task=task,
                message=msg,
            )
        from report_writing.chat.study_chapter_resolve import infer_study_method

        method = infer_study_method(
            str(payload.get("study_method") or payload.get("study_source") or ""),
            msg,
        )
        has_sample = bool(getattr(task, "sample_ready", False))
        chapter_label = ""
        by_key = {
            str(getattr(n, "key", "") or "").strip(): n
            for n in list(getattr(getattr(task, "outline", None), "chapters", None) or [])
        }
        if len(keys) == 1:
            node = by_key.get(keys[0])
            if node is not None:
                chapter_label = (
                    f"{str(getattr(node, 'sequence', '') or '').strip()} "
                    f"{str(getattr(node, 'title', '') or '').strip()}"
                ).strip() or keys[0]
        elif keys:
            chapter_label = f"已选 {len(keys)} 章"
        if method not in {"source", "sample", "auto"}:
            from report_writing.chat.clarify_flow import build_study_method_clarify

            return build_study_method_clarify(
                session,
                chapter_keys=keys,
                chapter_sequences=list(seqs or []),
                study_message=msg,
                study_scope=resolved_scope,
                chapter_label=chapter_label,
                has_sample=has_sample,
            )
        if method == "sample" and not has_sample:
            method = "auto"
        if method == "auto":
            from report_writing.chat.actions import _action_generate_freeform_points

            ff = dict(session.freeform or {})
            ff.pop("study_source_pending", None)
            session.freeform = ff
            if work_id:
                session.task_id = work_id
            return _action_generate_freeform_points(
                home,
                session,
                {
                    "task_id": work_id,
                    "chapter_keys": keys,
                    "focus_chapter_key": keys[0] if keys else "",
                },
            )
        if method == "source":
            study_text = str(payload.get("study_text") or payload.get("text") or "").strip()
            if not study_text:
                pending = {
                    "chapter_keys": keys,
                    "chapter_sequences": list(seqs or []),
                    "study_scope": resolved_scope,
                    "study_message": msg,
                    "chapter_label": chapter_label,
                    "task_id": work_id,
                    "blueprint_id": blueprint_id,
                    "working_task_id": work_id,
                }
                session.freeform = {
                    **(session.freeform or {}),
                    "study_source_pending": pending,
                }
                return (
                    f"好，按原文分析「{chapter_label or '本章'}」。"
                    "请把这一章的原文粘贴到下方文本框，再点「开始分析」。",
                    [
                        ChatCard(
                            type="study_source_paste",
                            title="根据原文内容分析",
                            payload=pending,
                        )
                    ],
                )
        wizard["step"] = "study"
        wizard["study_method"] = method
        wizard["trigger_chapter_study"] = keys
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        session.blueprint_id = blueprint_id
        ff_clear = dict(session.freeform or {})
        ff_clear.pop("study_source_pending", None)
        session.freeform = ff_clear
        from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message

        tops = [str(s).strip() for s in seqs if str(s).strip()] or parse_study_sequences_from_message(
            msg
        )
        top_hint = "、".join(f"第{t}章" for t in tops[:3]) if tops else ""
        if resolved_scope == "children" and tops:
            hint = f"{top_hint}下面的全部子章节（共 {len(keys)} 章）"
        elif resolved_scope == "self" and tops:
            hint = f"仅{top_hint}本章（不含下属）"
        elif tops:
            hint = (
                f"{top_hint}整章（含下属，共 {len(keys)} 章）"
                if len(keys) > 1
                else f"{top_hint}（共 {len(keys)} 章）"
            )
        else:
            hint = f"共 {len(keys)} 章"
        method_hint = "按原文" if method == "source" else "依据样本"
        return (
            f"收到，我先{method_hint}来学：{hint}。"
            "章节目录和进度请在右侧文档窗口查看，学完了再跟我说下一批也行。",
            [
                _blueprint_wizard_card(
                    blueprint_id=blueprint_id,
                    step="study",
                    title="依据模版编写 · 分析要点",
                    extra={**wizard, "chapter_study_keys": keys, "study_method": method},
                )
            ],
        )

    if step == "meta":
        title = str(payload.get("title") or wizard.get("title") or "").strip() or "依据模版编写"
        payload_for_resolve = {**payload, "title": title}
        existing_bid = _resolve_meta_existing_blueprint(
            home, session, wizard, payload_for_resolve
        )
        fresh_meta = bool(wizard.get("fresh_meta")) and not str(
            wizard.get("blueprint_id") or ""
        ).strip()
        if not existing_bid and not fresh_meta:
            existing_bid = str(wizard.get("blueprint_id") or payload_bid or "").strip()
        if fresh_meta:
            session.blueprint_id = ""
            tid_now = str(getattr(session, "task_id", "") or "").strip()
            if tid_now and home is not None:
                try:
                    from report_writing.chat.helpers import _load_task
                    from report_writing.chat.session_documents import (
                        _titles_conflict,
                        clean_document_display_title,
                    )

                    cur_task = _load_task(home, tid_now)
                    old_title = clean_document_display_title(
                        str(getattr(cur_task, "title", "") or "") if cur_task else ""
                    )
                    if old_title and _titles_conflict(old_title, title):
                        session.task_id = ""
                except Exception:
                    pass
        if existing_bid and not fresh_meta:
            sync_blueprint_wizard_session(home, session, blueprint_id=existing_bid)
            wizard = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
            cur = str(wizard.get("step") or "upload").strip()
            refresh_latest_wizard_card_in_session(home, session)
            shown = str(wizard.get("title") or title).strip()
            return (
                f"「{shown}」已就绪，请在最下方向导卡继续（当前：{_wizard_card_title(cur)}），无需重复填名称。",
                [
                    _blueprint_wizard_card(
                        blueprint_id=existing_bid,
                        step=cur,
                        title=_wizard_card_title(cur),
                        extra=wizard,
                    )
                ],
            )
        resume_bid = str(payload.get("blueprint_id") or "").strip()
        if resume_bid:
            existing_bid = resume_bid
            if str(wizard.get("step") or "").strip() not in {"", "meta"}:
                sync_blueprint_wizard_session(home, session, blueprint_id=existing_bid)
                wizard = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
                cur = str(wizard.get("step") or "upload").strip()
                return (
                    f"「{wizard.get('title') or title}」已在配置中，请在最下方向导卡继续当前步骤，无需重复创建。",
                    [
                        _blueprint_wizard_card(
                            blueprint_id=existing_bid,
                            step=cur,
                            title=_wizard_card_title(cur),
                            extra=wizard,
                        )
                    ],
                )
        # 无蓝本·依据样本：只建文档任务，收录前不入库
        task_id, wizard = _create_freeform_sample_document(
            home,
            session,
            title=title,
            category=str(payload.get("category") or "").strip(),
        )
        blueprint_id = task_id
        return (
            f"「{title}」已按「依据样本」开写文档（尚未入库）。"
            "请上传样板 Word 或 PDF，提取目录并分析要点；写满意后再「收录到蓝本库」。",
            [
                _blueprint_wizard_card(
                    blueprint_id=task_id,
                    step="upload",
                    title="依据样本 · 上传样板",
                    extra={"title": title, **wizard},
                )
            ],
        )

    if not blueprint_id:
        raise ValueError("请先填写蓝本名称完成创建")

    if step == "upload_done":
        from report_writing.chat.wizard_loop_guard import consume_wizard_triggers, idempotent_transition_reply
        from report_writing.chat.wizard_state import (
            WIZARD_MODE_CREATE,
            WIZARD_MODE_FREEFORM_SAMPLE,
        )
        from report_writing.services.task_ids import is_writing_task_id

        prev_w = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        # mode 常被 ui-state 空 {} 清掉：必须补回，否则 freeform 闸门会剥掉「提取目录」卡
        if not str(wizard.get("mode") or "").strip():
            if is_writing_task_id(blueprint_id):
                wizard["mode"] = WIZARD_MODE_FREEFORM_SAMPLE
            else:
                wizard["mode"] = str(prev_w.get("mode") or "").strip() or WIZARD_MODE_CREATE
        hit = idempotent_transition_reply(
            "upload_done",
            prev_w,
            already_msg="样板已就绪，请在最下方向导卡点「提取目录」或对话里说「提取目录」。",
        )
        consume_wizard_triggers(wizard, transition_step="upload_done")
        wizard["step"] = "outline"
        wizard["blueprint_id"] = blueprint_id
        if is_writing_task_id(blueprint_id):
            wizard["mode"] = str(wizard.get("mode") or "").strip() or WIZARD_MODE_FREEFORM_SAMPLE
            wizard["working_task_id"] = str(wizard.get("working_task_id") or blueprint_id)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        task = _load_task(home, blueprint_id)
        sample_tip = ""
        if task and getattr(task, "sample_ready", False):
            name = (getattr(task, "sample_name", "") or "").strip() or "样板"
            chars = int(getattr(task, "sample_chars", 0) or 0)
            sample_tip = f"已收到「{name}」" + (f"（约 {chars} 字已解析）" if chars else "") + "。"
        outline_card = _blueprint_wizard_card(
            blueprint_id=blueprint_id,
            step="outline",
            title="依据模版编写 · 提取目录",
            extra=wizard,
        )
        # 即使幂等提示，也必须带回提取目录卡（前端 bump 到 outline 后常误走空卡路径）
        if hit:
            msg = hit[0]
            return msg, [outline_card]
        return (
            (sample_tip or "样板已上传。")
            + "下一步请点「提取目录」，我会从样板里抽出章节目录（进度在卡片里显示）。",
            [outline_card],
        )

    if step == "outline":
        from report_writing.chat.wizard_loop_guard import normalized_wizard_step
        from report_writing.chat.wizard_state import wizard_step_index

        prev_step = normalized_wizard_step(str(wizard.get("step") or ""))
        pending_cmd = str(
            payload.get("pending_wizard_command")
            or wizard.get("pending_wizard_command")
            or ""
        ).strip()
        wizard_cmd = str(payload.get("wizard_command") or wizard_command or "").strip()
        explicit_re_extract = (
            wizard_cmd == "re_extract_outline" or pending_cmd == "re_extract_outline"
        )
        task_now = _load_task(home, blueprint_id)
        already_confirmed = bool(
            (task_now and getattr(task_now, "outline_confirmed", False))
            or wizard.get("outline_confirmed")
        )
        past_confirm = wizard_step_index(prev_step) >= wizard_step_index("study")
        # 目录已确认后禁止静默 step=outline 回滚重提（否则会整本 regenerate，章数漂移成循环）
        if (already_confirmed or past_confirm) and not explicit_re_extract:
            return _study_after_outline_confirmed_reply(
                home, session, blueprint_id, already=True
            )
        # 上传完成后卡片已是「提取目录」；再点提取只更新状态，勿再插一张一模一样的卡
        already_outline = wizard_step_index(prev_step) >= wizard_step_index("outline")
        wizard["step"] = "outline"
        if explicit_re_extract:
            wizard["outline_confirmed"] = False
            wizard["pending_wizard_command"] = "re_extract_outline"
            wizard["chapters"] = []
            wizard["chapter_count"] = 0
            try:
                from report_writing.services.store import TaskStore

                store = TaskStore(home / "tasks" / blueprint_id)
                if store.path.is_dir():
                    task_clr = store.load()
                    task_clr.outline_confirmed = False
                    store.save(task_clr)
            except Exception:
                pass
        if payload.get("outline_extract_active") is False:
            wizard.pop("outline_extract_active", None)
        else:
            wizard["outline_extract_active"] = True
        wizard.pop("trigger_outline_extract", None)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

        workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
        reply = "好，开始从样板提取目录。进度会显示在下方卡片里，完成后请确认目录。"
        if already_outline:
            return reply, []
        return (
            reply,
            [
                _blueprint_wizard_card(
                    blueprint_id=blueprint_id,
                    step="outline",
                    title="依据模版编写 · 提取目录",
                    extra=wizard,
                )
            ],
        )

    if step == "outline_done":
        from report_writing.chat.wizard_loop_guard import (
            consume_wizard_triggers,
            idempotent_transition_reply,
            normalized_wizard_step,
        )
        from report_writing.chat.wizard_state import wizard_step_index

        def _outline_done_chapters() -> tuple[list[dict], int]:
            """章节须与任务全量对齐；前端预览若被截断则回源任务补全。"""
            from report_writing.chat.wizard_state import slim_chapter_dicts, _chapter_preview

            raw = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
            preview, preview_n = slim_chapter_dicts(raw)
            try:
                count_hint = int(payload.get("chapter_count") or 0)
            except (TypeError, ValueError):
                count_hint = 0
            if count_hint or preview_n:
                return preview, count_hint or preview_n
            task = _load_task(home, blueprint_id)
            if not task or not task.outline:
                return [], 0
            nodes = list(task.outline.chapters or [])
            return _chapter_preview(task), len(nodes)

        # 目录已确认或向导已到分析：迟到的 outline_done 不得再把用户拽回确认
        task_now = _load_task(home, blueprint_id)
        prev_w = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        prev_norm = normalized_wizard_step(str(prev_w.get("step") or ""))
        already_confirmed = bool(task_now and getattr(task_now, "outline_confirmed", False))
        past_confirm = wizard_step_index(prev_norm) >= wizard_step_index("study")
        if already_confirmed or past_confirm or bool(prev_w.get("outline_confirmed")):
            return _study_after_outline_confirmed_reply(
                home, session, blueprint_id, already=True
            )

        hit = idempotent_transition_reply(
            "outline_done",
            prev_w,
            already_msg=(
                "目录已经在卡片里啦。你核对后点「确认目录」，或跟我说「确认全部 / 确认第×到×章」；"
                "不用再点「完成目录提取」了。"
            ),
        )
        chapters, chapter_total = _outline_done_chapters()
        consume_wizard_triggers(wizard, transition_step="outline_done")
        wizard.pop("outline_extract_active", None)
        wizard["step"] = "outline_confirm"
        if chapters:
            wizard["chapters"] = chapters
        if chapter_total:
            wizard["chapter_count"] = chapter_total
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

        workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
        card = _blueprint_wizard_card(
            blueprint_id=blueprint_id,
            step="outline_confirm",
            title="依据模版编写 · 确认目录",
            extra={"chapters": chapters, **wizard},
        )
        if hit:
            return hit[0], [card]
        shown = chapter_total or len(chapters)
        preview = ""
        if chapters:
            head = chapters[:5]
            preview = "前几章：" + "；".join(
                f"{c.get('sequence') or ''} {c.get('title') or ''}".strip() for c in head
            )
            if shown > 5:
                preview += f" …等共 {shown} 章"
        return (
            f"目录已经从样板里提出来了，一共 {shown} 章。"
            + (f"\n{preview}" if preview else "")
            + "\n你先在右侧文档窗口里看看树和拓扑图，有不顺眼的改一改。"
            "核对好了可以点「确认目录」整份确认，也可以跟我说「确认第1到3章」——这步只定目录，不会自动开分析。",
            [card],
        )

    if step == "outline_confirm":
        from report_writing.chat.wizard_loop_guard import consume_wizard_triggers, idempotent_transition_reply

        prev_w = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        work_id = _wizard_working_task_id(session, blueprint_id) or blueprint_id
        if work_id != blueprint_id:
            wizard["working_task_id"] = work_id
        bp_dir = home / "tasks" / work_id
        task = _load_task(home, work_id)
        # 须先落库确认：向导步可能已到 study（重学/误推进），幂等短路不能跳过未确认任务
        confirmed_now = False
        if (
            task
            and task.outline
            and list(task.outline.chapters or [])
            and not task.outline_confirmed
        ):
            if not bp_dir.is_dir():
                return (
                    "找不到文档任务目录，没法确认。你重新打开向导，或再提取一次目录试试。",
                    [
                        _blueprint_wizard_card(
                            blueprint_id=blueprint_id,
                            step="outline_confirm",
                            title="依据模版编写 · 确认目录",
                            extra=wizard,
                        )
                    ],
                )
            from report_writing.controller import confirm_outline
            from report_writing.services.task_ids import is_blueprint_task_id

            try:
                task = confirm_outline(bp_dir)
                # 仅库蓝本写入 catalog；副本 / 无蓝本·样本（w*）只落本地任务
                if is_blueprint_task_id(work_id):
                    from report_writing.services.blueprint_catalog import save_blueprint_task

                    save_blueprint_task(task)
                confirmed_now = True
            except Exception as exc:
                return (
                    f"确认目录没成功：{exc}。你再核对一下目录后重试就行。",
                    [
                        _blueprint_wizard_card(
                            blueprint_id=blueprint_id,
                            step="outline_confirm",
                            title="依据模版编写 · 确认目录",
                            extra=wizard,
                        )
                    ],
                )
        task_confirmed = bool(task and getattr(task, "outline_confirmed", False))
        hit = idempotent_transition_reply(
            "outline_confirm",
            prev_w,
            already_msg=(
                "目录这边已经确认过啦。接下来可以点「开始分析全部」，"
                "或者跟我说「分析第1到5章」「只分析概述」——我按你的范围来。"
            ),
            has_substantive_change=confirmed_now,
        )
        # 幂等短路时也必须把会话推到 study，避免下一步「分析」又落到确认目录
        if hit and (task_confirmed or confirmed_now or bool(prev_w.get("outline_confirmed"))):
            return _study_after_outline_confirmed_reply(
                home, session, blueprint_id, already=True
            )
        if hit:
            return hit
        consume_wizard_triggers(wizard, transition_step="outline_confirm")
        return _study_after_outline_confirmed_reply(
            home, session, blueprint_id, already=False
        )

    if step == "study":
        # 确认目录后前端会推进到 step=study（trigger_begin_study=false），只进步骤、不开跑。
        # 真正开全章分析必须显式 wizard_command=go_study，或 trigger_begin_study=true。
        cmd = str(payload.get("wizard_command") or "").strip()
        want_run = cmd == "go_study" or payload.get("trigger_begin_study") is True
        if want_run:
            return execute_wizard_command(home, session, "go_study")
        return _study_after_outline_confirmed_reply(
            home,
            session,
            blueprint_id,
            already=bool(payload.get("outline_confirmed")),
        )

    if step == "study_done":
        sync_blueprint_wizard_session(home, session, blueprint_id=blueprint_id)
        wizard = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
        study_complete = str(wizard.get("study_mode") or "").strip() == "complete"
        unanalyzed = int(wizard.get("study_unanalyzed") or 0)
        pending = bool(wizard.get("study_pending_confirm"))
        if not study_complete or unanalyzed > 0 or pending:
            refresh_latest_wizard_card_in_session(home, session)
            analyzed = wizard.get("study_analyzed")
            total = wizard.get("study_total")
            progress = ""
            if analyzed is not None and total:
                progress = f"（已分析 {analyzed}/{total} 章）"
            return (
                f"分析还没全部完成{progress}，请在上方的蓝本向导卡片里继续操作（已同步最新进度），无需重复点「开始分析」。",
                [],
            )
        wizard["step"] = "study"
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        refresh_latest_wizard_card_in_session(home, session)
        task = _load_task(home, blueprint_id)
        if task is not None and is_blueprint_valid(task):
            session.phase = ChatPhase.READY_TO_WRITE.value
            return _action_select_blueprint(home, session, {"blueprint_id": blueprint_id})
        return (
            "分析要点流程已跑完。若还有章节未分析，可继续在卡片里完善，或去蓝本页细调。",
            [_handoff_card(session, blueprint_id=blueprint_id, title="在蓝本页继续完善")],
        )

    sync_blueprint_wizard_session(home, session, blueprint_id=blueprint_id)
    wizard = dict((session.freeform or {}).get("blueprint_wizard") or wizard)
    step = str(wizard.get("step") or step)
    refresh_latest_wizard_card_in_session(home, session)
    return (
        "继续按卡片提示操作即可。",
        [_blueprint_wizard_card(blueprint_id=blueprint_id, step=step, extra=wizard)],
    )


def open_template_wizard_choose(
    session: ChatSession,
    resource: str,
) -> tuple[str, list[ChatCard]]:
    """打开渲染模版 / Word 样式对话向导（选择步）。"""
    from report_writing.chat.cards import _template_wizard_card
    from report_writing.chat.session_runtime import workflow_touch_template

    workflow_touch_template(session, {"resource": resource, "step": "choose", "item_id": ""})

    label = "渲染模版" if resource == "render_template" else "Word 样式"
    return (
        f"在对话里配置{label}：可选用已有或新建，保存后会绑定到当前机器人。",
        [
            _template_wizard_card(
                resource,
                step="choose",
                title=f"{label}向导",
                message="导出 Word 前可在此选定 HTML 渲染模版或 Word 样式。",
            )
        ],
    )


def _action_template_wizard(
    session: ChatSession,
    payload: ChatPayload,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _template_wizard_card
    from report_writing.chat.session_runtime import workflow_touch_template

    payload = ChatActionPayload.parse(payload)
    resource = str(payload.get("resource") or "").strip()
    step = str(payload.get("step") or "choose").strip()
    item_id = str(payload.get("item_id") or "").strip()
    workflow_touch_template(session, payload)
    if step == "edit":
        title = "编辑渲染模版" if resource == "render_template" else "编辑 Word 样式"
        return (
            "按表单填写后点保存，会自动绑定到当前机器人。",
            [
                _template_wizard_card(
                    resource,
                    step="edit",
                    item_id=item_id,
                    title=title,
                    message=payload.get("message") or "",
                )
            ],
        )
    if step == "done":
        label = "渲染模版" if resource == "render_template" else "Word 样式"
        cards = [
            _template_wizard_card(
                resource,
                step="done",
                item_id=item_id,
                title=f"{label}已配置",
            )
        ]
        export_hint = (
            "可在输入框上方点「下载 Word」导出。"
            if (session.task_id or "").strip()
            else "开写并确认章节后，可在输入框上方「下载 Word」导出。"
        )
        return (
            f"{label}已就绪（{item_id or '已绑定'}）。{export_hint}",
            cards,
        )
    return ("请继续在卡片中操作。", [_template_wizard_card(resource, step=step, item_id=item_id, title="")])
