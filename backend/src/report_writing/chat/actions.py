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

def _action_list_blueprints(home: Path, session: ChatSession, payload: dict) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _blueprint_list_card, _skip_blueprint_card
    q = str(payload.get("q") or "").strip()
    matches = search_blueprints(home, q=q, robot_id=session.robot_id) if q else []
    if not matches:
        matches = search_blueprints(home, q="", robot_id=session.robot_id)[:12]
    session.phase = ChatPhase.BLUEPRINT_SELECT.value
    if not matches:
        from report_writing.chat.resource_readiness import blueprint_stage_cards
        from report_writing.services.robots import get_robot

        robot = get_robot(home, session.robot_id) if session.robot_id else None
        extra = blueprint_stage_cards(home, robot) or [_skip_blueprint_card()]
        return (
            "这边还没有能用的蓝本。要在对话里建一份，还是先跳过蓝本直接写？",
            extra,
        )
    return (
        f"这边有 {len(matches)} 份蓝本，用哪份你来定——点「选用」确认，或跳过：",
        [_blueprint_list_card(matches), _skip_blueprint_card()],
    )


def _clear_blueprint_wizard_state(session: ChatSession) -> None:
    """结束对话内创建向导，避免仍判为「配蓝本中」。"""
    ff = dict(session.freeform or {})
    ff.pop("blueprint_wizard", None)
    session.freeform = ff
    from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

    workflow_set(session, WORKFLOW_BLUEPRINT, None)


def _action_select_blueprint(home: Path, session: ChatSession, payload: dict) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _blueprint_wizard_card, _offer_chapter_picker_cards
    from report_writing.chat.gates import is_blueprint_valid, partition_blueprint_chapters
    from report_writing.chat.helpers import _chapter_label, _join_labels, _load_task
    from report_writing.chat.wizard_state import (
        WIZARD_MODE_SELECTED,
        _wizard_card_title,
        seed_selected_blueprint_wizard,
    )

    blueprint_id = str(payload.get("blueprint_id") or "").strip()
    if not blueprint_id:
        raise ValueError("请指定 blueprint_id")
    # robot scope check
    robot = get_robot(home, session.robot_id) if session.robot_id else None
    if robot and robot.blueprint_ids and blueprint_id not in robot.blueprint_ids:
        raise ValueError("该蓝本不在当前机器人绑定范围内")
    task = _load_task(home, blueprint_id)
    from report_writing.chat.intent_router import lock_writing_path, WRITING_PATH_BLUEPRINT

    session.blueprint_id = blueprint_id
    _clear_blueprint_wizard_state(session)
    session.freeform = {**(session.freeform or {}), "write_chapter_keys": []}
    # 换蓝本后不得沿用上一份写作 clone，否则文档窗/配置会串到旧任务
    prev_tid = str(getattr(session, "task_id", "") or "").strip()
    if prev_tid and prev_tid != blueprint_id:
        session.task_id = ""
    lock_writing_path(session, WRITING_PATH_BLUEPRINT)
    from report_writing.chat.session_documents import (
        is_writing_document_named,
        mark_focus_entry_path,
        mark_focus_name_confirmed,
        peek_pending_entry,
        set_focus_document_key,
        upsert_session_document,
    )

    mark_focus_entry_path(session, "match")

    # 文档窗展示「文档名」：会话已起的名字优先，不用蓝本库条目标题顶替
    bp_title = (task.title if task else None) or blueprint_id
    ff = dict(session.freeform or {})
    pending = peek_pending_entry(session)
    user_title = str(
        ff.get("title") or pending.get("title") or ""
    ).strip()
    title = user_title or bp_title
    # 一份文档一份写作副本：库蓝本可共享，登记槽按文档隔离
    from report_writing.chat.session_documents import (
        clean_document_display_title,
        session_documents_map,
        _titles_conflict,
    )
    import secrets

    docs = session_documents_map(session)
    bp_key = f"bp:{blueprint_id}"
    existing = dict(docs.get(bp_key) or {})
    prev_wid = str(existing.get("writing_task_id") or "").strip()
    prev_title = clean_document_display_title(str(existing.get("title") or ""))
    cur_title = clean_document_display_title(title)
    reg_key = ""
    reuse_wid = ""
    if prev_wid and prev_title and cur_title and not _titles_conflict(prev_title, cur_title):
        # 回到同一文档：复用其写作副本
        reuse_wid = prev_wid
    elif prev_wid and cur_title and (not prev_title or _titles_conflict(prev_title, cur_title)):
        # 另一份文档选用同一库蓝本：新开登记槽 + 新副本
        reg_key = f"doc:{secrets.token_hex(4)}"
        reuse_wid = ""
    else:
        reuse_wid = prev_wid or ""
    # 选用即建/绑文档副本：对话内变更只打 w*；显式「同步到蓝本库」才回写源 bp*
    from report_writing.chat.writing import ensure_session_document_copy

    working = ensure_session_document_copy(
        home,
        session,
        blueprint_id,
        document_title=title,
        reuse_writing_task_id=reuse_wid or None,
        registry_key=reg_key,
        require_outline=False,
    )
    set_focus_document_key(session, reg_key or bp_key)
    if is_writing_document_named(session):
        mark_focus_name_confirmed(session)
    # 禁止用蓝本库标题或题材推断静默定名；开写前须用户确认 doc_name 澄清
    if not is_blueprint_valid(task) and not is_blueprint_valid(working):
        session.phase = ChatPhase.CREATE_HANDOFF.value
        from report_writing.chat.cards import _doc_window_hint_card

        return (
            f"「{title}」这本蓝本目录还没配齐，对话里没法直接学/写。"
            "请在右侧文档窗口把目录配好（改动只落在本对话文档副本，不会改蓝本库）。",
            [
                _doc_window_hint_card(
                    session,
                    blueprint_id=blueprint_id,
                    task_id=working.id,
                    title="打开文档窗口配置目录",
                    action="analyze",
                )
            ],
        )

    # 选用已有蓝本：确认/学习/开写都打文档副本；库蓝本仅作源，改动靠同步回写
    wizard = seed_selected_blueprint_wizard(home, session, blueprint_id)
    wizard["working_task_id"] = working.id
    wizard["source_blueprint_id"] = blueprint_id
    session.freeform = {**(session.freeform or {}), "blueprint_wizard": dict(wizard)}
    step = str(wizard.get("step") or "study").strip() or "study"
    ops_card = _blueprint_wizard_card(
        blueprint_id=blueprint_id,
        step=step,
        title=_wizard_card_title(step, mode=WIZARD_MODE_SELECTED),
        extra=wizard,
    )
    view = working if is_blueprint_valid(working) else task
    parts = partition_blueprint_chapters(view)
    analyzed = parts["analyzed"]
    unanalyzed = parts["unanalyzed"]

    from report_writing.chat.materials_pack_gate import soft_prompt_materials_pack_if_needed

    user_msg = str(pending.get("message") or payload.get("message") or "").strip()

    def _with_pack_tip(text: str, base_cards: list) -> tuple[str, list]:
        tip, pack_cards = soft_prompt_materials_pack_if_needed(session, home, working)
        if not tip:
            return text, base_cards
        return f"{text}\n\n{tip}", list(base_cards) + list(pack_cards)

    def _finish_select(text: str, base_cards: list) -> tuple[str, list]:
        t, c = _with_pack_tip(text, base_cards)
        from report_writing.chat.session_documents import append_doc_name_clarify_if_needed

        return append_doc_name_clarify_if_needed(
            session, message=user_msg, reply=t, cards=c
        )

    if unanalyzed and not analyzed:
        session.phase = ChatPhase.CREATE_HANDOFF.value
        un_labels = [_chapter_label(n) for n in unanalyzed[:12]]
        label_text = _join_labels(un_labels) + ("…" if len(unanalyzed) > 8 else "")
        return _finish_select(
            f"好，就用「{title}」。目录齐了，但还有 {len(unanalyzed)} 章没学（{label_text}）。"
            "你可以说「开始分析」或「分析第1到3章」，也可以点下方卡片里的开始分析；"
            "确认目录和分析是分开的，目录未确认时我会先提醒你确认。"
            "改动先落在本文档副本，需要时再说「同步到蓝本库」。",
            [ops_card],
        )
    if unanalyzed and analyzed:
        session.phase = ChatPhase.CREATE_HANDOFF.value
        un_labels = [_chapter_label(n) for n in unanalyzed[:8]]
        label_text = _join_labels(un_labels) + ("…" if len(unanalyzed) > 6 else "")
        return _finish_select(
            f"好，就用「{title}」。已学 {len(analyzed)} 章，还有 {len(unanalyzed)} 章未学（{label_text}）。"
            "可以说「开始分析」补未分析章，或「帮我写已分析的章节」先开写；"
            "也可以勾选已分析章再开写。改动先落在本文档副本，需要时再说「同步到蓝本库」。",
            [ops_card] + _offer_chapter_picker_cards(session, view),
        )

    session.phase = ChatPhase.READY_TO_WRITE.value
    return _finish_select(
        f"好，就用「{title}」——一共 {len(analyzed)} 章已分析。"
        "先勾选要写的章节，或直接说「帮我写第1章 / 写已分析的章节」。"
        "改动先落在本文档副本，需要时再说「同步到蓝本库」。",
        [ops_card] + _offer_chapter_picker_cards(session, view),
    )


def _action_select_write_chapters(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    emit=None,
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    """指定/勾选要写的已分析章节，按勾选顺序依次开写（不再选书写方式）。"""
    from report_writing.chat.cards import _offer_chapter_picker_cards
    from report_writing.chat.gates import _parse_chapter_keys, partition_blueprint_chapters
    from report_writing.chat.helpers import _load_task
    blueprint_id = str(payload.get("blueprint_id") or session.blueprint_id or "").strip()
    if not blueprint_id:
        raise ValueError("请先选择蓝本")
    session.blueprint_id = blueprint_id
    from report_writing.chat.session_documents import _writing_copy_id_for_blueprint

    copy_id = _writing_copy_id_for_blueprint(session, blueprint_id)
    task = _load_task(home, copy_id) if copy_id else None
    if task is None:
        task = _load_task(home, blueprint_id)
    if task is None:
        raise ValueError(f"蓝本不存在：{blueprint_id}")
    parts = partition_blueprint_chapters(task)
    analyzed = parts["analyzed"]
    analyzed_keys = {n.key for n in analyzed}
    if not analyzed:
        session.phase = ChatPhase.CREATE_HANDOFF.value
        from report_writing.chat.wizard_state import (
            WIZARD_MODE_SELECTED,
            _wizard_card_title,
            is_selected_wizard_mode,
            seed_selected_blueprint_wizard,
        )
        from report_writing.chat.cards import _blueprint_wizard_card

        wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
        if not is_selected_wizard_mode(wizard):
            wizard = seed_selected_blueprint_wizard(home, session, blueprint_id)
        step = str(wizard.get("step") or "study").strip() or "study"
        return (
            "这本蓝本暂时没有已分析的章节，还开不了写。"
            "可以说「开始分析」或「分析第1到3章」，也可以点下方卡片开始分析。",
            [
                _blueprint_wizard_card(
                    blueprint_id=blueprint_id,
                    step=step,
                    title=_wizard_card_title(step, mode=WIZARD_MODE_SELECTED),
                    extra=wizard,
                )
            ],
        )
    requested = _parse_chapter_keys(payload)
    if not requested:
        session.phase = ChatPhase.CREATE_HANDOFF.value if parts["unanalyzed"] else ChatPhase.READY_TO_WRITE.value
        return (
            "请先在右侧文档窗口勾选至少一章要写的内容。",
            _offer_chapter_picker_cards(session, task),
        )
    keys = [key for key in requested if key in analyzed_keys]
    # 保持大纲顺序
    order = {n.key: i for i, n in enumerate(analyzed)}
    keys = sorted(keys, key=lambda k: order.get(k, 10_000))
    if not keys:
        return (
            "勾选的章节里没有已分析的，请在右侧文档窗口从已分析列表里再选一次。",
            _offer_chapter_picker_cards(session, task),
        )
    _apply_project_folders_from_payload(session, payload)
    session.freeform = {**(session.freeform or {}), "write_chapter_keys": keys}
    session.phase = ChatPhase.READY_TO_WRITE.value
    # 勾选后直接按章依次开写，不再选书写方式、不等待逐章确认
    from report_writing.chat.helpers import WRITING_PACE_BATCH

    reply, cards, _events = _action_start_writing(
        home,
        session,
        emit=emit,
        writing_pace=WRITING_PACE_BATCH,
        chapter_keys=keys,
        payload=payload,
        invoke=invoke,
    )
    return reply, list(cards or [])


def _action_write_analyzed_only(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    emit: EmitFn | None = None,
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    """兼容旧按钮：无 chapter_keys 时引导到文档窗选章；有则按勾选开写。"""
    from report_writing.chat.cards import _doc_window_hint_card, _offer_chapter_picker_cards
    from report_writing.chat.gates import _parse_chapter_keys, partition_blueprint_chapters
    from report_writing.chat.helpers import _load_task
    keys = _parse_chapter_keys(payload)
    if keys:
        return _action_select_write_chapters(
            home, session, {**payload, "chapter_keys": keys}, emit=emit, invoke=invoke
        )
    blueprint_id = str(payload.get("blueprint_id") or session.blueprint_id or "").strip()
    if not blueprint_id:
        raise ValueError("请先选择蓝本")
    session.blueprint_id = blueprint_id
    # 已分析章以文档副本为准，禁止读库蓝本章节状态
    from report_writing.chat.resource_actions import _wizard_working_task_id
    from report_writing.services.task_ids import is_writing_task_id

    work_id = _wizard_working_task_id(session, blueprint_id, payload) or ""
    if not work_id or not is_writing_task_id(work_id):
        raise ValueError("请先打开文档副本；写作以副本章节为准，不能直接用库蓝本")
    task = _load_task(home, work_id)
    if task is None:
        raise ValueError(f"文档副本不存在：{work_id}")
    parts = partition_blueprint_chapters(task)
    if not parts["analyzed"]:
        session.phase = ChatPhase.CREATE_HANDOFF.value
        return (
            "这份文档副本暂时没有已分析的章节，还开不了写。请在右侧文档窗口先分析几章。",
            [
                _doc_window_hint_card(
                    session,
                    blueprint_id=blueprint_id,
                    task_id=work_id,
                    title="打开文档窗口分析",
                    action="analyze",
                )
            ],
        )
    session.phase = ChatPhase.CREATE_HANDOFF.value if parts["unanalyzed"] else ChatPhase.READY_TO_WRITE.value
    return (
        f"已分析好的有 {len(parts['analyzed'])} 章，请在右侧文档窗口勾选要写的章节。",
        _offer_chapter_picker_cards(session, task),
    )


def _action_skip_blueprint(
    session: ChatSession,
    *,
    home: Path | None = None,
    invoke=None,
    emit=None,
    title_hint: str = "",
    extra_message: str = "",
    path_setup_only: bool = False,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.intent_router import (
        infer_freeform_meta_from_messages,
        lock_writing_path,
        touch_session_plan,
        WRITING_PATH_FREEFORM,
    )
    from report_writing.chat.write_path_after_match import clear_pending_no_match

    clear_pending_no_match(session)
    # 跳过蓝本时必须拆掉创建向导，否则无蓝本确认大纲后仍会注入「上传样板 / 分析要点」卡
    _clear_blueprint_wizard_state(session)
    session.blueprint_id = ""
    # 对齐 force_new：丢掉旧写作焦点，避免后续 outline regenerate 覆盖其它文档任务
    from report_writing.chat.session_documents import (
        clean_document_display_title,
        focus_document_key,
        focus_document_meta,
        focus_has_display_title,
        is_writing_document_named,
        mark_focus_entry_path,
        peek_pending_entry,
        pop_pending_entry,
        quick_title_from_write_message,
        sanitize_candidate_document_title,
        session_documents_map,
        set_focus_document_key,
    )
    from report_writing.chat.session_goal import get_session_goal
    from report_writing.services.task_ids import is_writing_task_id

    old_focus = focus_document_key(session)
    old_goal = get_session_goal(session)
    if old_focus and any(old_goal.get(k) for k in ("doc_title", "doc_category", "summary", "notes")):
        docs = session_documents_map(session)
        if old_focus in docs:
            entry = dict(docs[old_focus])
            entry["goal_snapshot"] = {
                k: old_goal.get(k)
                for k in ("doc_title", "doc_category", "summary", "notes", "project")
                if old_goal.get(k)
            }
            docs[old_focus] = entry
            ff_snap = dict(session.freeform or {})
            ff_snap["session_documents"] = docs
            session.freeform = ff_snap
    # 继续当前已定名无蓝本壳：保留 focus/task，避免文档窗 snap 到第一项并另起 w*
    cur_meta = focus_document_meta(session)
    cur_tid = str(session.task_id or cur_meta.get("task_id") or "").strip()
    keep_current_shell = bool(
        old_focus.startswith("task:")
        and cur_tid
        and is_writing_task_id(cur_tid)
        and (
            is_writing_document_named(session)
            or focus_has_display_title(session)
        )
    )
    if not keep_current_shell:
        session.task_id = ""
        set_focus_document_key(session, "")
    touch_session_plan(session, goal="skip_blueprint")
    lock_writing_path(session, WRITING_PATH_FREEFORM)
    mark_focus_entry_path(session, "freeform")

    from report_writing.chat.helpers import is_vague_freeform_meta

    pending = peek_pending_entry(session)
    if pending or title_hint or extra_message:
        pending = pop_pending_entry(session) or pending
    tip = sanitize_candidate_document_title(
        (title_hint or "").strip() or pending.get("title") or "",
        message=extra_message or pending.get("message") or "",
    ) or quick_title_from_write_message(extra_message or pending.get("message") or "")
    extra = (extra_message or pending.get("message") or "").strip()
    if extra:
        try:
            from report_writing.chat.freeform_outline import (
                remember_outline_constraints_from_message,
            )

            remember_outline_constraints_from_message(session, extra)
        except Exception:
            pass

    inferred = infer_freeform_meta_from_messages(session, extra, invoke=invoke)
    if inferred:
        category, title = inferred
        # 跳过蓝本时若推断出的仍是路径旁白，当成未定题目，交后续多轮问清
        if is_vague_freeform_meta(title, field=True) or is_vague_freeform_meta(
            category, field=True
        ):
            inferred = None
    if not inferred and tip:
        # 口语题名交给后续多轮/大脑补全；有分隔符则拆类型与标题，否则整段当标题
        if "｜" in tip or "|" in tip:
            parts = re.split(r"[｜|]", tip, maxsplit=1)
            inferred = (
                parts[0].strip() or tip,
                (parts[1].strip() if len(parts) > 1 else tip) or tip,
            )
        else:
            inferred = ("文档", tip)
    if inferred:
        category, title = inferred
        merge_goal = {
            "doc_category": category,
            "doc_title": title,
            "summary": f"{category}｜{title}",
        }
        from report_writing.chat.session_goal import merge_session_goal, sync_goal_into_freeform

        merge_session_goal(session, merge_goal)
        sync_goal_into_freeform(session)
        if path_setup_only:
            from report_writing.chat.session_documents import focus_needs_doc_name

            # 只锁无蓝本路径，禁止用题材把新文档静默定名（否则会跳过起名卡）
            if not focus_needs_doc_name(session):
                session.freeform = {
                    **(session.freeform or {}),
                    "category": category,
                    "title": title,
                }
            return ("", [])
        if home is not None:
            return _collect_freeform_meta(
                session,
                f"{category}｜{title}",
                home=home,
                invoke=invoke,
                emit=emit,
            )
        session.phase = ChatPhase.FREEFORM_OUTLINE.value
        session.freeform = {
            **(session.freeform or {}),
            "category": category,
            "title": title,
            "meta_confirmed": True,
        }
        return (
            f"行，不用蓝本。我按你前面说的来写：【{category}】{title}。"
            "接下来我会在卡片里起草章节目录，你改满意再确认。",
            [],
        )

    if path_setup_only:
        return ("", [])

    session.phase = ChatPhase.FREEFORM_ASK.value
    # 新开无蓝本文档：清掉旧题目，避免会话目标把上一份文档名又填回来
    ff_ask = dict(session.freeform or {})
    ff_ask.update(
        {
            "meta_confirmed": False,
            "category": "",
            "title": "",
            "outline_text": "",
            "outline_confirmed": False,
            "outline_pending_edit": False,
        }
    )
    goal = dict(ff_ask.get("session_goal") or {})
    for k in ("doc_title", "doc_category", "summary"):
        goal.pop(k, None)
    if goal:
        ff_ask["session_goal"] = goal
    else:
        ff_ask.pop("session_goal", None)
    session.freeform = ff_ask
    return (
        "行，咱就不绑蓝本了。你想写什么、文档打算叫什么，跟我说就行——"
        "题目定清了，我再帮你在文档窗口搭目录。",
        [],
    )


def _action_resume(home: Path, session: ChatSession, payload: dict) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _offer_chapter_picker_cards, _offer_start_writing
    from report_writing.chat.gates import _analysis_gate_if_needed, is_blueprint_valid, partition_blueprint_chapters
    from report_writing.chat.helpers import _load_task
    blueprint_id = str(payload.get("blueprint_id") or session.blueprint_id or "").strip()
    if payload.get("task_id"):
        session.task_id = str(payload["task_id"]).strip()
    if not blueprint_id:
        session.phase = ChatPhase.INTENT.value
        return ("回来啦。想写什么直接跟我说；要用蓝本就选个机器人，或者说「开始写」。", [])
    session.blueprint_id = blueprint_id
    task = _load_task(home, blueprint_id)
    if not is_blueprint_valid(task):
        session.phase = ChatPhase.CREATE_HANDOFF.value
        from report_writing.chat.cards import _doc_window_hint_card

        return (
            "蓝本这边还差点火候，请在右侧文档窗口把目录和分析补上。",
            [
                _doc_window_hint_card(
                    session,
                    blueprint_id=blueprint_id,
                    task_id=blueprint_id,
                    title="打开文档窗口继续",
                    action="analyze",
                )
            ],
        )
    # 从蓝本页返回：若用户已确认仅写已分析章，且仍有未分析章，直接进入就绪
    prefer_keys = list((session.freeform or {}).get("write_chapter_keys") or [])
    parts = partition_blueprint_chapters(task)
    if prefer_keys and parts["unanalyzed"]:
        keep = [k for k in prefer_keys if any(n.key == k for n in parts["analyzed"])]
        if keep:
            session.freeform = {**(session.freeform or {}), "write_chapter_keys": keep}
            session.phase = ChatPhase.READY_TO_WRITE.value
            from report_writing.chat.helpers import WRITING_PACE_BATCH

            # 不再问书写节奏：有勾选就按 batch 依次开写
            reply, cards, _events = _action_start_writing(
                home,
                session,
                writing_pace=WRITING_PACE_BATCH,
                chapter_keys=keep,
            )
            if reply or cards:
                return reply, list(cards or [])
            tip, tip_cards = _offer_start_writing(session, home=home)
            return tip, tip_cards
    gate = _analysis_gate_if_needed(session, task)
    if gate is not None:
        return gate
    session.freeform = {**(session.freeform or {}), "write_chapter_keys": []}
    session.phase = ChatPhase.READY_TO_WRITE.value
    return (
        f"欢迎回来，「{task.title or blueprint_id}」各章都分析好了。"
        "先勾选要写的章节吧。",
        _offer_chapter_picker_cards(session, task),
    )


def _freeform_start_writing_gate(
    home: Path, session: ChatSession
) -> tuple[str, list[ChatCard]] | None:
    """无蓝本：未确认大纲或未进入就绪阶段时禁止开写。"""
    from report_writing.chat.freeform_points import freeform_points_help_card
    from report_writing.chat.helpers import _load_task

    from report_writing.chat.session_documents import focus_has_display_title
    from report_writing.chat.tool_capability import (
        capability_reply_hint,
        missing_for_tool,
    )

    miss = missing_for_tool(session, "start_writing", home=home)
    if miss:
        return capability_reply_hint(miss), []
    ff = dict(session.freeform or {})
    phase = str(session.phase or "")
    if not ff.get("meta_confirmed") and not focus_has_display_title(session):
        session.phase = ChatPhase.FREEFORM_ASK.value
        return (
            "先说一下要写什么，我会生成可改的大纲，确认后再开写。",
            [],
        )
    if ff.get("outline_pending_edit") or not ff.get("outline_confirmed"):
        session.phase = ChatPhase.FREEFORM_OUTLINE.value
        task_id = (session.task_id or "").strip()
        text = str(ff.get("outline_text") or "").strip()
        if task_id:
            task = _load_task(home, task_id)
            if task and text:
                return (
                    "大纲还没确认。请在卡片里改好目录，点「确认大纲并继续」后再开写。",
                    [_outline_preview_card(task_id, task, text, editable=True, session=session)],
                )
        return (
            "可以说「帮我生成目录大纲」，或粘贴章节目录；生成后请在卡片里修改并确认。",
            [],
        )
    if phase == ChatPhase.FREEFORM_POINTS.value:
        task = _load_task(home, (session.task_id or "").strip()) if session.task_id else None
        cards = [freeform_points_help_card(session, task)] if task else []
        return (
            "大纲已确认。可点「帮我生成写作要点」自动起草各章写法，或「先跳过」直接开写。",
            cards,
        )
    if phase not in {
        ChatPhase.READY_TO_WRITE.value,
        ChatPhase.WRITING.value,
        ChatPhase.CONFIRM_EXPORT.value,
    }:
        # 不再追问书写节奏：升到就绪后交给 start_writing 按 batch 依次开写
        session.phase = ChatPhase.READY_TO_WRITE.value
        return None
    return None


def _apply_project_folders_from_payload(session: ChatSession, payload: dict | None) -> None:
    """开写 payload 可带 project_folder_ids，写入会话供 clone 使用。"""
    body = dict(payload or {})
    if "project_folder_ids" not in body and "project_folder_label" not in body:
        return
    folders = [str(x).strip() for x in (body.get("project_folder_ids") or []) if str(x).strip()]
    label = str(body.get("project_folder_label") or "").strip()
    ff = dict(session.freeform or {})
    ff["project_folder_ids"] = folders
    if label or not folders:
        ff["project_folder_label"] = label
    session.freeform = ff


def _action_start_writing(
    home: Path,
    session: ChatSession,
    *,
    emit: EmitFn | None = None,
    writing_pace: str = "",
    chapter_keys: list[str] | None = None,
    payload: dict | None = None,
    invoke=None,
) -> tuple[str, list[ChatCard], list[dict]]:
    # 前端节奏卡片可能再次带上 chapter_keys，优先写入会话，避免丢勾选
    from report_writing.chat.cards import _offer_chapter_picker_cards, _progress_and_confirm_cards
    from report_writing.chat.gates import _analysis_gate_if_needed, is_blueprint_valid, partition_blueprint_chapters
    from report_writing.chat.helpers import WRITING_PACE_STEP, _emit_process, _load_task
    from report_writing.chat.writing import (
        _finish_writing_round,
        _normalize_writing_pace,
        _pending_write_keys,
        _prepare_writing_task,
        _run_writing_keys,
        _session_writing_pace,
        _writing_chapter_keys,
        apply_write_chapter_filter,
        clone_blueprint_to_writing_task,
        maybe_offer_writing_pace_clarify,
        remember_writing_pace_choice,
        writing_batch_scope,
    )
    from report_writing.services.task_ids import is_writing_task_id
    _apply_project_folders_from_payload(session, payload)
    incoming_keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    if incoming_keys:
        session.freeform = {**(session.freeform or {}), "write_chapter_keys": incoming_keys}
    prefer_keys = list((session.freeform or {}).get("write_chapter_keys") or [])
    if session.blueprint_id and not prefer_keys:
        bp = _load_task(home, session.blueprint_id)
        if bp is not None and is_blueprint_valid(bp) and partition_blueprint_chapters(bp)["analyzed"]:
            session.phase = ChatPhase.READY_TO_WRITE.value
            return (
                "还没勾选要写的章节。请在右侧文档窗口勾选后点「写作」。",
                _offer_chapter_picker_cards(session, bp),
                [],
            )

    from report_writing.chat.helpers import WRITING_PACE_BATCH, WRITING_PACE_STEP

    explicit_pace = _normalize_writing_pace(writing_pace)
    # 逐步续写：已有任务且仍有待写章时不重复问节奏
    existing_id = str(session.task_id or "").strip()
    skip_pace_clarify = False
    if len(prefer_keys) == 1:
        skip_pace_clarify = True
    if existing_id and (_normalize_writing_pace(writing_pace) or _session_writing_pace(session)) == WRITING_PACE_STEP:
        existing_task = _load_task(home, existing_id)
        if existing_task is not None and _pending_write_keys(existing_task):
            skip_pace_clarify = True
    if not skip_pace_clarify:
        pace_clarify = maybe_offer_writing_pace_clarify(
            session,
            prefer_keys,
            writing_pace=writing_pace,
            task_id_hint=existing_id or str(session.blueprint_id or "").strip(),
        )
        if pace_clarify is not None:
            reply, cards = pace_clarify
            return reply, cards, []

    pace = explicit_pace or _session_writing_pace(session) or WRITING_PACE_BATCH

    if not session.blueprint_id:
        gate = _freeform_start_writing_gate(home, session)
        if gate is not None:
            return gate[0], gate[1], []

    session.freeform = {**(session.freeform or {}), "writing_pace": pace}
    scope_tid = str(session.task_id or session.blueprint_id or "").strip()
    if explicit_pace or pace:
        remember_writing_pace_choice(
            session,
            scope=writing_batch_scope(scope_tid, prefer_keys),
            pace=pace,
        )
    events: list[dict] = []

    # 逐步模式且任务已存在：继续写下一章（且任务章集合须匹配勾选）
    existing_id = str(session.task_id or "").strip()
    writing: WritingTask | None = None
    if existing_id and pace == WRITING_PACE_STEP:
        writing = _load_task(home, existing_id)
        if writing is not None and _pending_write_keys(writing):
            task_keys = set(_writing_chapter_keys(writing))
            if prefer_keys and not set(prefer_keys).issubset(task_keys):
                # 勾选范围与副本 scope 不一致（含旧版曾裁剪目录的副本），按勾选重建 scope
                writing = None
                session.task_id = ""
            else:
                _emit_process(emit, "接着上一章继续写…", task_id=existing_id)

    if writing is None:
        if session.blueprint_id:
            # 优先用选用时已建的文档副本；分析门闩也看副本
            from report_writing.chat.session_documents import (
                _writing_copy_id_for_blueprint,
                focus_document_meta,
            )

            copy_id = (
                str(focus_document_meta(session).get("writing_task_id") or "").strip()
                or _writing_copy_id_for_blueprint(session, session.blueprint_id)
                or (existing_id if is_writing_task_id(existing_id) else "")
            )
            working = _load_task(home, copy_id) if copy_id else None
            task = working or _load_task(home, session.blueprint_id)
            if task is not None:
                gate = _analysis_gate_if_needed(session, task)
                prefer = list((session.freeform or {}).get("write_chapter_keys") or [])
                if gate is not None and not prefer:
                    reply, cards = gate
                    return reply, cards, []
            prefer = list((session.freeform or {}).get("write_chapter_keys") or [])
            if not prefer:
                bp = task or _load_task(home, session.blueprint_id)
                if bp is not None:
                    return (
                        "还没勾选要写的章节，先勾选再开写。",
                        _offer_chapter_picker_cards(session, bp),
                        [],
                    )
                raise ValueError("请先勾选要写的章节")
            _emit_process(
                emit,
                f"正在按你勾选的 {len(prefer)} 章准备开写…",
            )
            ff = dict(session.freeform or {})
            project_folders = [
                str(x).strip()
                for x in (ff.get("project_folder_ids") or [])
                if str(x).strip()
            ]
            if working is not None and is_blueprint_valid(working):
                writing = apply_write_chapter_filter(home, working, prefer)
                if project_folders:
                    writing.project_folder_ids = project_folders
                    writing.project_folder_label = str(ff.get("project_folder_label") or "").strip()
                    from report_writing.services.store import TaskStore

                    TaskStore(home / "tasks" / writing.id).save(writing)
            else:
                writing = clone_blueprint_to_writing_task(
                    home,
                    session.blueprint_id,
                    chapter_keys=prefer,
                    project_folder_ids=project_folders,
                    project_folder_label=str(ff.get("project_folder_label") or "").strip(),
                )
        elif session.task_id:
            _emit_process(emit, "正在准备开写…")
            writing = _load_task(home, session.task_id)
            if writing is None:
                raise ValueError("写作任务不存在")
            writing = _prepare_writing_task(home, writing)
        else:
            raise ValueError("请先选择蓝本或完成无蓝本大纲")

    # 已有写作任务时也可更新项目资料根（会话里已设）
    ff_proj = dict(session.freeform or {})
    proj_folders = [
        str(x).strip() for x in (ff_proj.get("project_folder_ids") or []) if str(x).strip()
    ]
    if proj_folders and list(getattr(writing, "project_folder_ids", None) or []) != proj_folders:
        writing.project_folder_ids = proj_folders
        writing.project_folder_label = str(ff_proj.get("project_folder_label") or "").strip()
        from report_writing.services.store import TaskStore

        TaskStore(home / "tasks" / writing.id).save(writing)
        try:
            from report_writing.services.writing_task_catalog import save_writing_task

            save_writing_task(writing)
        except Exception:
            pass

    # 资料包：按写作文档绑定；payload 可带 pack_id；有 file_slot 且未 skip/未绑 → 引导
    from report_writing.chat.materials_pack_gate import (
        _materials_pack_skipped_for_task,
        clear_pending_materials_pack,
        mark_materials_pack_skipped,
        materials_pack_gate_satisfied,
        offer_materials_pack_cards,
        peek_pending_materials_pack,
    )
    from report_writing.services.store import TaskStore as _TaskStorePack

    body_pack = dict(payload or {})
    incoming_pack = str(
        body_pack.get("materials_pack_id") or body_pack.get("pack_id") or ""
    ).strip()

    # 选用蓝本时若已点「开写时不用」，带到本写作文档
    bp_id = str(session.blueprint_id or getattr(writing, "source_blueprint_id", "") or "").strip()
    if bp_id and _materials_pack_skipped_for_task(session, bp_id):
        mark_materials_pack_skipped(session, writing.id, skipped=True)

    # 开写前预建/预选的资料包
    if not incoming_pack:
        incoming_pack = peek_pending_materials_pack(session, blueprint_id=bp_id)

    if body_pack.get("materials_pack_skip") or body_pack.get("skip_materials_pack"):
        writing.materials_pack_id = ""
        mark_materials_pack_skipped(session, writing.id, skipped=True)
        clear_pending_materials_pack(session, blueprint_id=bp_id)
        _TaskStorePack(home / "tasks" / writing.id).save(writing)
        try:
            from report_writing.services.writing_task_catalog import save_writing_task

            save_writing_task(writing)
        except Exception:
            pass
    elif incoming_pack:
        writing.materials_pack_id = incoming_pack
        mark_materials_pack_skipped(session, writing.id, skipped=False)
        clear_pending_materials_pack(session, blueprint_id=bp_id, pack_id=incoming_pack)
        _TaskStorePack(home / "tasks" / writing.id).save(writing)
        try:
            from report_writing.services.writing_task_catalog import save_writing_task

            save_writing_task(writing)
        except Exception:
            pass
    elif not materials_pack_gate_satisfied(session, writing):
        session.task_id = writing.id
        reply, cards = offer_materials_pack_cards(session, home, writing)
        return reply, cards, []

    body = dict(payload or {})
    if body.get("already_persisted") or body.get("from_doc_window"):
        fresh = _load_task(home, writing.id)
        if fresh is not None:
            writing = fresh
    force_skip_sup = bool(
        body.get("skip_supplement_check") or body.get("_skip_supplement_check")
    )
    run_keys = list(prefer_keys) if prefer_keys else _writing_chapter_keys(writing)
    from report_writing.chat.writing_supplement_check import (
        mark_resolved_gaps_in_registry,
        mark_supplement_skipped,
        run_supplement_check,
        supplement_batch_scope,
        supplement_check_card,
        supplement_gate_needed,
    )

    mark_resolved_gaps_in_registry(session, writing, home)
    if supplement_gate_needed(
        session, writing, run_keys, force_skip=force_skip_sup, home=home
    ):
        reply_chk, open_items, _reg = run_supplement_check(
            home, session, writing, run_keys, invoke=invoke
        )
        if open_items:
            card = supplement_check_card(
                writing,
                run_keys,
                writing_pace=pace,
                items=open_items,
                home=home,
            )
            if card is not None:
                return reply_chk, [card], []
    elif force_skip_sup:
        mark_supplement_skipped(session, supplement_batch_scope(writing.id, run_keys))

    session.task_id = writing.id
    session.phase = ChatPhase.WRITING.value
    from report_writing.chat.session_documents import (
        clean_document_display_title,
        upsert_session_document,
    )

    write_title = clean_document_display_title(str(getattr(writing, "title", "") or ""))
    ff_title = clean_document_display_title(str((session.freeform or {}).get("title") or ""))
    upsert_session_document(
        session,
        kind="blueprint" if session.blueprint_id else "writing",
        task_id=str(session.blueprint_id or writing.id).strip(),
        blueprint_id=str(session.blueprint_id or "").strip(),
        writing_task_id=writing.id,
        title=write_title or ff_title,
    )
    all_keys = _writing_chapter_keys(writing)
    pending = _pending_write_keys(writing)
    if pace == WRITING_PACE_STEP:
        keys_to_run = pending[:1]
        pace_tip = "逐步确认：本轮只写一章"
    else:
        keys_to_run = pending or all_keys
        pace_tip = "全部书写：本轮写完待写章节"
    if prefer_keys:
        want = {str(k).strip() for k in prefer_keys if str(k).strip()}
        if want:
            keys_to_run = [k for k in keys_to_run if k in want]
            if not keys_to_run:
                task_key_set = set(_writing_chapter_keys(writing))
                keys_to_run = [k for k in prefer_keys if k in task_key_set]
            if len(prefer_keys) == 1 and keys_to_run:
                pace_tip = "本轮只写勾选的 1 章"
            elif keys_to_run and want:
                pace_tip = f"本轮写勾选的 {len(keys_to_run)} 章"
    if not keys_to_run:
        from report_writing.chat.resource_readiness import export_phase_cards, robot_export_ready
        from report_writing.services.robots import get_robot

        robot = get_robot(home, session.robot_id) if session.robot_id else None
        cards = _progress_and_confirm_cards(writing, pace=pace)
        session.phase = ChatPhase.CONFIRM_EXPORT.value
        if writing.chapters and all(
            c.status in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for c in writing.chapters
        ):
            cards.extend(export_phase_cards(home, writing.id, robot))
            return "各章都齐了，可以在输入框上方「下载 Word」导出。", cards, []
        return "没有待写章节了，展开进度卡片改改、确认就行。", cards, []

    from report_writing.chat.materials_pack_gate import remind_slot_upload_before_write

    slot_reminder = remind_slot_upload_before_write(
        home,
        session,
        writing,
        keys_to_run,
        invoke=invoke,
        emit=emit,
    )

    from report_writing.services.writing_cancel import clear_writing_cancel

    clear_writing_cancel(session.id)
    session.freeform = {**(session.freeform or {}), "writing_paused": False}

    _emit_process(
        emit,
        f"准备好了（{pace_tip}），开始按章检索、起草、检查…",
        task_id=writing.id,
    )

    writing, run_events = _run_writing_keys(home, session, writing, keys_to_run, emit=emit)
    events.extend(run_events)
    reply, cards, events = _finish_writing_round(home, session, writing, events, pace=pace)
    if slot_reminder:
        reply = f"{slot_reminder}\n\n{reply}".strip()
    return reply, cards, events


def pause_writing_session(home: Path, session_id: str) -> tuple[ChatSession, list[ChatCard], int]:
    """请求暂停：打标取消 + 同步已确认章节到库 + 返回进度卡片。"""
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.writing import _session_writing_pace
    from report_writing.services.chat_session import get_session, save_session
    from report_writing.domain.models import ChatPhase
    from report_writing.services.store import TaskStore
    from report_writing.services.task_chapters import sync_confirmed_chapters
    from report_writing.services.writing_cancel import request_writing_cancel

    sid = (session_id or "").strip()
    if not sid:
        raise ValueError("需要 session_id")
    session = get_session(home, sid)
    if session is None:
        raise ValueError("会话不存在")
    request_writing_cancel(sid)
    saved = 0
    task_id = (session.task_id or "").strip()
    cards: list[ChatCard] = []
    if task_id:
        task_dir = home / "tasks" / task_id
        try:
            task = TaskStore(task_dir).load()
            saved = sync_confirmed_chapters(task)
            cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session))
        except Exception:
            pass
    session.phase = ChatPhase.CONFIRM_EXPORT.value
    session.freeform = {**(session.freeform or {}), "writing_paused": True}
    save_session(home, session, durable=True, action="pause_writing")
    return session, cards, saved


def _action_confirm_chapter(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    emit: EmitFn | None = None,
) -> tuple[str, list[ChatCard], list[dict]]:
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.session_documents import bind_session_focus_to_task, resolve_task_id
    from report_writing.chat.writing import _maybe_continue_step_writing, _session_writing_pace

    task_id = resolve_task_id(session, payload, require=True)
    chapter_key = str(payload.get("chapter_key") or "").strip()
    decision_raw = str(payload.get("decision") or "approve").strip().lower()
    from report_writing.chat.chapter_client_sync import client_chapter_draft_text, clear_client_chapter_edit

    text = str(payload.get("text") or "").strip()
    if not text:
        text = client_chapter_draft_text(session, task_id, chapter_key)
    comment = str(payload.get("comment") or "")
    pause_after = bool(payload.get("pause_after"))
    if not chapter_key:
        raise ValueError("需要 chapter_key")
    try:
        decision = HumanDecision(decision_raw)
    except ValueError as error:
        raise ValueError("decision 无效") from error

    from report_writing.services.pipeline import confirm_chapter

    task = _load_task(home, task_id)
    if task is None:
        raise ValueError("任务不存在")
    template = Path(task.template_path) if task.template_path else home / "tasks" / task_id / "outline-template.json"
    dataset = Path(task.dataset_dir) if task.dataset_dir else home / "tasks" / task_id
    updated = confirm_chapter(
        home / "tasks" / task_id,
        template,
        dataset,
        chapter_key,
        decision,
        text=text,
        comment=comment,
    )
    source_bp = str(getattr(updated, "source_blueprint_id", "") or "").strip()
    bind_session_focus_to_task(
        session,
        task_id,
        blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
        writing_task_id=task_id if source_bp else None,
        title=str(getattr(updated, "title", "") or ""),
    )
    clear_client_chapter_edit(session, task_id, chapter_key)
    title = next((c.title for c in (updated.chapters or []) if c.key == chapter_key), chapter_key)
    prefix = f"「{title}」确认好了。"
    # 退回修改：不自动写下章
    if decision == HumanDecision.RETURN:
        session.phase = ChatPhase.CONFIRM_EXPORT.value
        cards = _progress_and_confirm_cards(updated, pace=_session_writing_pace(session))
        return f"{prefix}需要的话可点「保存并重试本章」。", cards, []

    if pause_after and decision == HumanDecision.APPROVE:
        from report_writing.services.writing_cancel import request_writing_cancel

        request_writing_cancel(session.id)
        session.phase = ChatPhase.CONFIRM_EXPORT.value
        session.freeform = {**(session.freeform or {}), "writing_paused": True}
        cards = _progress_and_confirm_cards(updated, pace=_session_writing_pace(session))
        if emit is not None:
            from report_writing.chat.helpers import _emit_process

            _emit_process(emit, "本章已确认，写作已暂停。")
        return (
            f"{prefix}按你的要求先停在这里，不会再自动写下章；已确认的内容已保存。",
            cards,
            [],
        )

    return _maybe_continue_step_writing(home, session, updated, emit=emit, prefix=prefix)


def _action_confirm_all_chapters(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    emit: EmitFn | None = None,
) -> tuple[str, list[ChatCard], list[dict]]:
    """统一确认：按提交的章节正文批量确认；无正文且 skip_empty 时跳过。"""
    from report_writing.chat.cards import _chapter_draft_text, _progress_and_confirm_cards
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.session_documents import bind_session_focus_to_task, resolve_task_id
    from report_writing.chat.writing import _maybe_continue_step_writing, _session_writing_pace

    task_id = resolve_task_id(session, payload, require=True)
    items = payload.get("chapters") or []
    if not isinstance(items, list):
        raise ValueError("chapters 须为数组")
    skip_empty = bool(payload.get("skip_empty", True))

    from report_writing.services.pipeline import confirm_chapter

    task = _load_task(home, task_id)
    if task is None:
        raise ValueError("任务不存在")
    template = Path(task.template_path) if task.template_path else home / "tasks" / task_id / "outline-template.json"
    dataset = Path(task.dataset_dir) if task.dataset_dir else home / "tasks" / task_id
    path = home / "tasks" / task_id

    by_key = {str(item.get("chapter_key") or "").strip(): item for item in items if isinstance(item, dict)}
    confirmed = 0
    skipped = 0
    pending_keys = [
        c.key
        for c in (task.chapters or [])
        if c.status in {ChapterStatus.NEEDS_HUMAN, ChapterStatus.AWAITING_CONFIRM}
    ]
    if not pending_keys:
        cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session))
        return "这会儿没有待确认的章节啦。", cards, []

    for key in pending_keys:
        item = by_key.get(key) or {}
        text = str(item.get("text") or "").strip()
        if not text:
            chapter = task.chapter(key)
            text = _chapter_draft_text(chapter).strip()
        if text:
            task = confirm_chapter(
                path,
                template,
                dataset,
                key,
                HumanDecision.APPROVE,
                text=text,
                comment=str(item.get("comment") or "统一确认"),
            )
            confirmed += 1
        elif skip_empty:
            task = confirm_chapter(
                path,
                template,
                dataset,
                key,
                HumanDecision.SKIP,
                comment=str(item.get("comment") or "统一确认：无正文，已跳过"),
            )
            skipped += 1

    source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
    bind_session_focus_to_task(
        session,
        task_id,
        blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
        writing_task_id=task_id if source_bp else None,
        title=str(getattr(task, "title", "") or ""),
    )
    session.phase = ChatPhase.CONFIRM_EXPORT.value
    prefix = f"统一确认好了：确认 {confirmed} 章，跳过 {skipped} 章。"
    return _maybe_continue_step_writing(home, session, task, emit=emit, prefix=prefix)


def _action_check_writing_supplements(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    invoke=None,
) -> tuple[str, list[ChatCard], list[dict]]:
    from report_writing.chat.session_documents import resolve_task_id
    from report_writing.chat.writing_supplement_check import (
        mark_resolved_gaps_in_registry,
        run_supplement_check,
        supplement_check_card,
    )

    task_id = resolve_task_id(session, payload, require=True)
    task = TaskStore(home / "tasks" / task_id).load()
    keys = [str(k).strip() for k in (payload.get("chapter_keys") or payload.get("write_chapter_keys") or []) if str(k).strip()]
    if not keys:
        keys = [str(k).strip() for k in ((session.freeform or {}).get("write_chapter_keys") or []) if str(k).strip()]
    if not keys and task.outline:
        keys = [str(n.key) for n in task.outline.chapters if not bool(getattr(n, "structure_only", False))]
    mark_resolved_gaps_in_registry(session, task, home)
    reply, _open, reg = run_supplement_check(home, session, task, keys, invoke=invoke)
    from report_writing.chat.writing_supplement_check import effective_open_gaps

    open_items = effective_open_gaps(task, reg, home)
    pace = str(payload.get("writing_pace") or (session.freeform or {}).get("writing_pace") or "").strip()
    card = (
        supplement_check_card(task, keys, writing_pace=pace, items=open_items, home=home)
        if open_items
        else None
    )
    cards = [card] if card is not None else []
    if not open_items:
        return "当前没有必须补的配置项，可以直接开写。", cards, []
    return reply, cards, []


def _action_skip_writing_supplement_check(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    emit: EmitFn | None = None,
    invoke=None,
) -> tuple[str, list[ChatCard], list[dict]]:
    body = dict(payload or {})
    body["_skip_supplement_check"] = True
    return _action_start_writing(
        home,
        session,
        emit=emit,
        writing_pace=str(body.get("writing_pace") or "").strip(),
        chapter_keys=body.get("chapter_keys") if isinstance(body.get("chapter_keys"), list) else None,
        payload=body,
        invoke=invoke,
    )


def _action_dismiss_writing_supplement(
    home: Path,
    session: ChatSession,
    payload: dict,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.session_documents import resolve_task_id
    from report_writing.chat.writing_supplement_check import (
        dismiss_gap,
        effective_open_gaps,
        load_registry,
        supplement_check_card,
    )

    task_id = resolve_task_id(session, payload, require=True)
    gap_id = str(payload.get("gap_id") or "").strip()
    if not gap_id:
        raise ValueError("需要 gap_id")
    task = TaskStore(home / "tasks" / task_id).load()
    task = dismiss_gap(home, session, task, gap_id)
    reg = load_registry(session)
    open_items = effective_open_gaps(task, reg, home)
    keys = list(reg.get("chapter_keys") or [])
    pace = str((session.freeform or {}).get("writing_pace") or "").strip()
    card = supplement_check_card(task, keys, writing_pace=pace, items=open_items, home=home)
    cards = [card] if card is not None else []
    return "已从待补充列表移除；对应章节不再关联该要素。", cards


def _action_configure_chapter(home: Path, session: ChatSession, payload: dict) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.session_documents import bind_session_focus_to_task, resolve_task_id
    from report_writing.chat.writing import _apply_chapter_config, _session_writing_pace

    task_id = resolve_task_id(session, payload, require=True)
    chapter_key = str(payload.get("chapter_key") or "").strip()
    if not chapter_key:
        raise ValueError("需要 chapter_key")
    path = home / "tasks" / task_id
    store = TaskStore(path)
    task = store.load()
    before_ids = {str(e.id or "").strip() for e in (task.element_nodes or []) if str(e.id or "").strip()}
    _apply_chapter_config(task, chapter_key, payload)
    store.save(task)
    from report_writing.chat.writing_supplement_check import mark_gaps_filled_for_element, mark_resolved_gaps_in_registry

    for eid in list(payload.get("element_ids") or []):
        mark_gaps_filled_for_element(session, task, str(eid))
    if payload.get("elements"):
        for row in payload.get("elements") or []:
            if isinstance(row, dict) and row.get("id"):
                mark_gaps_filled_for_element(session, task, str(row.get("id")))
    mark_resolved_gaps_in_registry(session, task, home)
    store.save(task)
    from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

    invalidate_doc_outline_cache(task_id)
    source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
    if source_bp:
        invalidate_doc_outline_cache(source_bp)
    bind_session_focus_to_task(
        session,
        task_id,
        blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
        writing_task_id=task_id if source_bp else None,
        title=str(getattr(task, "title", "") or ""),
    )
    from report_writing.chat.intent_router import active_blueprint_wizard

    if not active_blueprint_wizard(session):
        session.phase = ChatPhase.CONFIRM_EXPORT.value
    # 保存配置不是开分析：清掉会话里残留的一次性分析 trigger，避免前端 done 又自动开跑
    wizard = dict((session.freeform or {}).get("blueprint_wizard") or {})
    if wizard:
        wizard.pop("trigger_begin_study", None)
        wizard.pop("trigger_chapter_study", None)
        wizard.pop("chapter_study_keys", None)
        session.freeform = {**(session.freeform or {}), "blueprint_wizard": wizard}
        from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

        workflow_set(session, WORKFLOW_BLUEPRINT, dict(wizard))
    title = next((c.title for c in (task.outline.chapters if task.outline else []) if c.key == chapter_key), chapter_key)
    node = next((c for c in (task.outline.chapters if task.outline else []) if c.key == chapter_key), None)
    from report_writing.chat.element_library_pending import queue_new_elements_after_configure

    queued = queue_new_elements_after_configure(
        session,
        task,
        before_ids,
        chapter_key=chapter_key,
        chapter_title=title,
    )
    if node is not None and bool(getattr(node, "structure_only", False)):
        reply = f"「{title}」已标为目录节点：不用解析、也不写正文，正文由子节展开。"
    else:
        reply = f"「{title}」的配置我记下了。想重跑本章就点重试，或确认本章。"
        if queued:
            reply += " 本次有新要素，可在右侧文档窗口点「待收录要素」收入个人要素库。"
    # 待收录入口改到文档窗口角标，不再往对话里塞 element_library_pending 卡
    cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session))
    return reply, cards


def _action_add_chapter_element(home: Path, session: ChatSession, payload: dict) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.session_documents import bind_session_focus_to_task, resolve_task_id
    from report_writing.chat.writing import _apply_add_chapter_element, _session_writing_pace

    task_id = resolve_task_id(session, payload, require=True)
    chapter_key = str(payload.get("chapter_key") or "").strip()
    if not chapter_key:
        raise ValueError("需要 chapter_key")
    path = home / "tasks" / task_id
    store = TaskStore(path)
    task = store.load()
    title = next(
        (c.title for c in (task.outline.chapters if task.outline else []) if c.key == chapter_key),
        chapter_key,
    )
    from report_writing.chat.element_configure_pending import (
        merge_cards_with_configure,
        payload_to_draft_enriched,
        queue_configure_pending,
    )

    draft = payload_to_draft_enriched(
        payload,
        task_elements=list(task.element_nodes or []),
        chapter_title=title,
    )
    agent_hint = str(payload.get("hint") or payload.get("user_note") or "").strip()
    slot_label = str(
        payload.get("slot")
        or payload.get("slot_label")
        or payload.get("name")
        or ""
    ).strip()
    queue_configure_pending(
        session,
        task_id=task_id,
        chapter_key=chapter_key,
        chapter_title=title,
        draft=draft,
        agent_hint=agent_hint,
        point_index=payload.get("point_index"),
        slot_label=slot_label,
    )
    source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
    bind_session_focus_to_task(
        session,
        task_id,
        blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
        writing_task_id=task_id if source_bp else None,
        title=str(getattr(task, "title", "") or ""),
    )
    from report_writing.chat.intent_router import active_blueprint_wizard

    if not active_blueprint_wizard(session):
        session.phase = ChatPhase.CONFIRM_EXPORT.value
    kind = (draft.get("kind") or "kv").strip()
    kind_label = {
        "kv": "键值",
        "fixed": "固定",
        "file": "文件",
        "file_slot": "引用槽位",
        "rag_search": "文件",
        "llm_generate": "LLM",
        "node_ref": "引用结果",
        "image": "图片",
    }.get(kind, kind)
    reply = (
        f"已根据对话为「{title}」生成{kind_label}要素草稿（名称、分类、键值/检索等已预填）。"
        "请在下方卡片打开确认，无误后点「保存并关联本章」即可。"
    )
    cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session))
    return reply, merge_cards_with_configure(session, cards)


def _looks_like_chapter_slug(text: str, key: str = "") -> bool:
    t = str(text or "").strip()
    k = str(key or "").strip()
    if not t:
        return True
    if k and t.lower() == k.lower():
        return True
    if any("\u4e00" <= ch <= "\u9fff" for ch in t):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", t))


def _retry_chapter_display_title(task, chapter_key: str, payload: dict | None = None) -> str:
    """用户可见章名：优先大纲中文标题，勿回落英文 slug（下划线会被 Markdown 吃成斜体）。"""
    body = dict(payload or {})
    seq = str(body.get("sequence") or "").strip()
    title = str(body.get("title") or body.get("chapter_title") or "").strip()
    key = str(chapter_key or "").strip()
    if title and _looks_like_chapter_slug(title, key):
        title = ""
    outline = getattr(task, "outline", None)
    node = next(
        (n for n in list(getattr(outline, "chapters", None) or []) if str(getattr(n, "key", "") or "") == key),
        None,
    )
    if node is not None:
        seq = seq or str(getattr(node, "sequence", "") or "").strip()
        node_title = str(getattr(node, "title", "") or "").strip()
        if node_title and not _looks_like_chapter_slug(node_title, key):
            title = node_title
    if not title:
        state = next((c for c in (task.chapters or []) if c.key == key), None)
        state_title = str(getattr(state, "title", "") or "").strip() if state else ""
        if state_title and not _looks_like_chapter_slug(state_title, key):
            title = state_title
    label = f"{seq} {title}".strip() if seq and title else (title or seq)
    return label or "本章"


def _ensure_outline_confirmed_for_retry(path: Path, session: ChatSession, task) -> object:
    """用户已点保存并开写/重写、且大纲已有章节时，落库对齐确认，避免 UI 已确认却仍被闸门拦住。"""
    if task is None:
        return task
    if bool(getattr(task, "outline_confirmed", False)):
        return task
    outline = getattr(task, "outline", None)
    if outline is None or not list(getattr(outline, "chapters", None) or []):
        return task
    confirm_outline(path)
    return TaskStore(path).load()


def _action_retry_chapter(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    emit: EmitFn | None = None,
    invoke=None,
) -> tuple[str, list[ChatCard], list[dict]]:
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.helpers import _STEP_LABEL, _emit, _emit_process
    from report_writing.chat.resource_readiness import export_phase_cards, robot_export_ready
    from report_writing.chat.session_documents import bind_session_focus_to_task, resolve_task_id
    from report_writing.chat.writing import _apply_chapter_config, _session_writing_pace
    from report_writing.services.robots import get_robot

    task_id = resolve_task_id(session, payload, require=True)
    chapter_key = str(payload.get("chapter_key") or "").strip()
    if not chapter_key:
        raise ValueError("需要 chapter_key")

    path = home / "tasks" / task_id
    store = TaskStore(path)
    task = store.load()
    already_persisted = bool(
        payload.get("already_persisted") or payload.get("from_doc_window")
    )
    configure_fields = (
        "points",
        "points_text",
        "point_items",
        "keywords",
        "keywords_text",
        "describes",
        "aux_prompt",
        "reference_text",
        "fixed_output",
        "structure_only",
        "search_terms_separate",
        "element_ids",
        "elements",
    )
    if not already_persisted and any(key in payload for key in configure_fields):
        _apply_chapter_config(task, chapter_key, payload)
        store.save(task)
        from report_writing.chat.writing_supplement_check import reset_supplement_gate_for_scope

        reset_supplement_gate_for_scope(session, task.id, [chapter_key])
    elif already_persisted:
        task = store.load()

    task = _ensure_outline_confirmed_for_retry(path, session, task)
    title = _retry_chapter_display_title(task, chapter_key, payload)
    pace = _session_writing_pace(session)
    force_skip_sup = bool(
        payload.get("skip_supplement_check") or payload.get("_skip_supplement_check")
    )
    run_keys = [chapter_key]
    from report_writing.chat.writing_supplement_check import (
        mark_resolved_gaps_in_registry,
        mark_supplement_skipped,
        run_supplement_check,
        supplement_batch_scope,
        supplement_check_card,
        supplement_gate_needed,
    )

    mark_resolved_gaps_in_registry(session, task, home)
    if supplement_gate_needed(
        session, task, run_keys, force_skip=force_skip_sup, home=home
    ):
        reply_chk, open_items, _reg = run_supplement_check(
            home, session, task, run_keys, invoke=invoke
        )
        if open_items:
            card = supplement_check_card(
                task,
                run_keys,
                writing_pace=pace,
                items=open_items,
                home=home,
            )
            if card is not None:
                return reply_chk, [card], []
    elif force_skip_sup:
        mark_supplement_skipped(session, supplement_batch_scope(task.id, run_keys))

    _emit_process(emit, f"正在重试章节「{title}」…", task_id=task_id, chapter=chapter_key)
    events: list[dict] = []

    def on_event(event: dict) -> None:
        events.append(event)
        _emit(emit, event)
        if event.get("type") != "step":
            return
        step = str(event.get("step") or "")
        status = str(event.get("status") or "")
        chap_title = str(event.get("title") or "").strip()
        if not chap_title or chap_title == chapter_key or "_" in chap_title:
            chap_title = title
        label = _STEP_LABEL.get(step, step or "步骤")
        status_cn = {"running": "进行中", "done": "完成", "skipped": "跳过", "error": "失败"}.get(status, status)
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        note = str((result or {}).get("note") or "").strip()
        tip = f"{label} · {status_cn}" + (f" · {chap_title}" if chap_title else "")
        if note:
            tip = f"{tip} {note}"
        _emit_process(emit, tip, step=step, status=status, chapter=event.get("chapter") or "")
        if step == "search" and status in {"done", "skipped"}:
            from report_writing.chat.helpers import emit_search_element_lines

            emit_search_element_lines(emit, result or {})
        if step == "write" and status == "running":
            from report_writing.chat.helpers import emit_write_points_lines

            emit_write_points_lines(emit, result or {})

    from report_writing.chat.session_goal import apply_session_goal_to_task
    from report_writing.services.pipeline import run_chapters

    if apply_session_goal_to_task(session, task):
        store.save(task)

    try:
        updated = run_chapters(
            path,
            chapter_keys=[chapter_key],
            include_children=False,
            robot_id=session.robot_id or "",
            on_event=on_event,
        )
        _emit_process(emit, f"「{title}」重试完成")
    except Exception as error:
        _log.warning("retry_chapter from chat: %s", error)
        err = {"type": "error", "detail": str(error)}
        events.append(err)
        _emit(emit, err)
        _emit_process(emit, f"重试失败：{error}")
        updated = store.load()
        source_bp = str(getattr(updated, "source_blueprint_id", "") or "").strip()
        bind_session_focus_to_task(
            session,
            task_id,
            blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
            writing_task_id=task_id if source_bp else None,
            title=str(getattr(updated, "title", "") or ""),
        )
        detail = str(error).strip() or "未知错误"
        if "确认目录" in detail:
            return (
                f"「{title}」还没法开写：目录还没在任务里确认落盘。"
                "请在文档窗再点一次「确认大纲并继续」，然后再重试。",
                _progress_and_confirm_cards(updated, pace=_session_writing_pace(session)),
                events,
            )
        return (
            f"「{title}」这次没跑成：{detail}",
            _progress_and_confirm_cards(updated, pace=_session_writing_pace(session)),
            events,
        )

    source_bp = str(getattr(updated, "source_blueprint_id", "") or "").strip()
    bind_session_focus_to_task(
        session,
        task_id,
        blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
        writing_task_id=task_id if source_bp else None,
        title=str(getattr(updated, "title", "") or ""),
    )
    session.phase = ChatPhase.CONFIRM_EXPORT.value
    robot = get_robot(home, session.robot_id) if session.robot_id else None
    cards = _progress_and_confirm_cards(updated, pace=_session_writing_pace(session))
    if updated.chapters and all(
        c.status in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for c in updated.chapters
    ):
        cards.extend(export_phase_cards(home, task_id, robot))
        return f"「{title}」我重新跑完了，各章也都确认好了，可以导出了。", cards, events
    applied = any(
        key in payload
        for key in (
            "points_text",
            "keywords_text",
            "describes",
            "aux_prompt",
            "reference_text",
            "user_turn",
        )
    )
    if applied and payload.get("aux_prompt"):
        reply = f"「{title}」已按你补充的要求重写了，展开章节看看效果。"
    elif applied:
        reply = f"「{title}」已按新配置重跑，你展开章节看看效果。"
    else:
        reply = f"「{title}」已重跑，你展开章节看看效果。"
    return reply, cards, events


def _action_retry_search_chapter(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    emit: EmitFn | None = None,
) -> tuple[str, list[ChatCard], list[dict]]:
    from report_writing.chat.helpers import _emit
    from report_writing.chat.session_documents import bind_session_focus_to_task, resolve_task_id
    from report_writing.chat.writing import _apply_chapter_config

    task_id = resolve_task_id(session, payload, require=True)
    chapter_key = str(payload.get("chapter_key") or "").strip()
    if not chapter_key:
        raise ValueError("需要 chapter_key")

    path = home / "tasks" / task_id
    store = TaskStore(path)
    task = store.load()
    if any(
        key in payload
        for key in (
            "points",
            "points_text",
            "keywords",
            "keywords_text",
            "describes",
            "aux_prompt",
            "reference_text",
            "fixed_output",
            "structure_only",
            "search_terms_separate",
            "element_ids",
            "elements",
        )
    ):
        _apply_chapter_config(task, chapter_key, payload)
        store.save(task)

    title = next((c.title for c in (task.chapters or []) if c.key == chapter_key), chapter_key)
    events: list[dict] = []

    def _branch_event(event: dict) -> dict:
        tagged = dict(event)
        tagged["branch"] = "search"
        return tagged

    def _emit_branch(event: dict) -> None:
        events.append(event)
        _emit(emit, _branch_event(event))

    _emit_branch({"type": "process", "message": f"仅检索「{title}」…"})

    def on_event(event: dict) -> None:
        if event.get("type") != "step":
            _emit_branch(event)
            return
        step = str(event.get("step") or "")
        if step != "search":
            return
        _emit_branch(event)

    from report_writing.services.pipeline import run_chapters

    try:
        updated = run_chapters(
            path,
            chapter_keys=[chapter_key],
            include_children=False,
            robot_id=session.robot_id or "",
            on_event=on_event,
            search_only=True,
        )
    except Exception as error:
        _log.warning("retry_search_chapter from chat: %s", error)
        err = {"type": "error", "detail": str(error), "branch": "search"}
        events.append(err)
        _emit(emit, err)
        source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
        bind_session_focus_to_task(
            session,
            task_id,
            blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
            writing_task_id=task_id if source_bp else None,
            title=str(getattr(task, "title", "") or ""),
        )
        return f"「{title}」检索失败：{error}", [], events

    chapter_state = updated.chapter(chapter_key)
    adopted_cards = [item for item in (chapter_state.evidence or []) if getattr(item, "adopted", False)]
    branch_result = {
        "type": "search_branch_result",
        "task_id": task_id,
        "chapter_key": chapter_key,
        "title": title,
        "adopted": len(adopted_cards),
        "note": (chapter_state.human_note or "").strip(),
        "cards": [card.to_dict() for card in (chapter_state.evidence or [])],
    }
    events.append(branch_result)
    _emit(emit, _branch_event(branch_result))

    source_bp = str(getattr(updated, "source_blueprint_id", "") or "").strip()
    bind_session_focus_to_task(
        session,
        task_id,
        blueprint_id=source_bp or str(payload.get("blueprint_id") or "").strip(),
        writing_task_id=task_id if source_bp else None,
        title=str(getattr(updated, "title", "") or ""),
    )
    session.phase = ChatPhase.CONFIRM_EXPORT.value
    short_reply = f"「{title}」检索完成：采纳 {len(adopted_cards)} 条证据（旁支试检，不影响写作主线）。"
    if updated.chapters and all(
        c.status in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for c in updated.chapters
    ):
        return f"{short_reply} 各章也都确认好了。", [], events
    return short_reply, [], events


def _action_confirm_freeform_meta(
    session: ChatSession,
    payload: dict,
    message: str,
    *,
    home: Path | None = None,
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    return _collect_freeform_meta(
        session,
        message or str(payload.get("text") or ""),
        home=home,
        invoke=invoke,
    )


def _action_reopen_outline_edit(home: Path, session: ChatSession) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.freeform_outline import chapters_to_outline_text
    from report_writing.chat.helpers import _load_task

    task_id = (session.task_id or "").strip()
    if not task_id:
        raise ValueError("还没有大纲任务")
    task = _load_task(home, task_id)
    if task is None or not task.outline:
        raise ValueError("大纲不存在")
    text = str((session.freeform or {}).get("outline_text") or "").strip()
    if not text:
        text = chapters_to_outline_text(
            [
                {"sequence": n.sequence, "title": n.title}
                for n in (task.outline.chapters or [])
            ]
        )
    meta = dict(session.freeform or {})
    meta["outline_pending_edit"] = True
    meta["outline_text"] = text
    session.freeform = meta
    session.phase = ChatPhase.FREEFORM_OUTLINE.value
    return (
        "可以改目录了：在下方文本框里增删改，满意后点「确认大纲并继续」。",
        [_outline_preview_card(task_id, task, text, editable=True, session=session)],
    )


def _action_submit_outline(
    home: Path,
    session: ChatSession,
    payload: dict,
    message: str,
    *,
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard]]:
    if payload.get("reopen_edit"):
        return _action_reopen_outline_edit(home, session)
    regen = bool(payload.get("regenerate"))
    if regen:
        from report_writing.chat.session_documents import (
            focus_needs_doc_name,
            focus_needs_entry_path,
        )

        if focus_needs_doc_name(session) or focus_needs_entry_path(session):
            from report_writing.chat.session_documents import (
                apply_working_title,
                mark_focus_entry_path,
                mark_focus_freeform_mode,
                quick_title_from_write_message,
            )

            mark_focus_entry_path(session, "freeform")
            mark_focus_freeform_mode(session, "auto_outline")
            working = str(
                payload.get("title") or (session.freeform or {}).get("title") or ""
            ).strip() or quick_title_from_write_message(message)
            if working:
                apply_working_title(session, working, home=home, message=message)
    meta = dict(session.freeform or {})
    text = str(payload.get("outline_text") or message or "").strip()
    if bool(payload.get("confirm")) and not text:
        text = str(meta.get("outline_text") or "").strip()
    from report_writing.chat.freeform_outline import looks_like_outline_directory
    from report_writing.chat.session_documents import resolve_task_id

    # 文档窗 regenerate 会带 task_id：对齐焦点，避免另开一份同名文档
    payload_tid = resolve_task_id(session, payload) if isinstance(payload, dict) else ""
    if payload_tid:
        session.task_id = payload_tid

    revision = str(
        payload.get("revision_instructions")
        or payload.get("message")
        or ""
    ).strip()
    # regenerate 且 outline_text 为空：用户原话/修订要求在 message 或 revision 字段
    regen = bool(payload.get("regenerate"))
    collect_msg = text
    if regen and not looks_like_outline_directory(text):
        collect_msg = revision or message or text
    from report_writing.chat.gates import _parse_chapter_keys

    scoped_keys = _parse_chapter_keys(payload if isinstance(payload, dict) else {})
    return _collect_freeform_outline(
        home,
        session,
        collect_msg,
        invoke=invoke,
        confirm=bool(payload.get("confirm")),
        regenerate=regen,
        revision_instructions=revision or (message if regen else ""),
        emit=emit,
        chapter_keys=scoped_keys,
    )


def _action_submit_points(
    home: Path,
    session: ChatSession,
    payload: dict,
    message: str,
    *,
    invoke=None,
) -> tuple[str, list[ChatCard]]:
    return _collect_freeform_points(
        home,
        session,
        message or json.dumps(payload, ensure_ascii=False),
        invoke=invoke,
    )


def _recent_wants_generate_outline(session: ChatSession, *, invoke=None) -> bool:
    from report_writing.chat.freeform_outline import wants_generate_outline

    phase = str(session.phase or "")
    for msg in reversed(list(session.messages or [])[-8:]):
        if str(getattr(msg, "role", "") or "") != "user":
            continue
        t = str(getattr(msg, "text", "") or "").strip()
        if t and wants_generate_outline(t, invoke=invoke, phase=phase, session=session):
            return True
    return False


def _collect_freeform_meta(
    session: ChatSession,
    message: str,
    *,
    home: Path | None = None,
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.freeform_help import wants_freeform_help_draft
    from report_writing.chat.freeform_outline import wants_generate_outline
    from report_writing.chat.helpers import is_vague_freeform_meta
    from report_writing.chat.intent_router import infer_freeform_meta_from_messages
    from report_writing.chat.session_documents import (
        explicit_doc_title_from_message,
        quick_title_from_write_message,
        sanitize_candidate_document_title,
        title_contains_action_clause,
    )
    from report_writing.chat.session_goal import get_session_goal

    text = (message or "").strip()
    extracted_title = (
        sanitize_candidate_document_title("", message=text)
        or explicit_doc_title_from_message(text)
        or quick_title_from_write_message(text)
    )
    try:
        from report_writing.chat.freeform_outline import (
            remember_outline_constraints_from_message,
        )

        remember_outline_constraints_from_message(session, text)
    except Exception:
        pass
    help_draft = wants_freeform_help_draft(text, invoke=invoke, phase=str(session.phase or ""))
    wants_gen = (
        wants_generate_outline(text, invoke=invoke, phase=str(session.phase or ""), session=session)
        or _recent_wants_generate_outline(session, invoke=invoke)
        or help_draft
    )
    if help_draft and "｜" not in text and "|" not in text:
        ff = dict(session.freeform or {})
        goal = get_session_goal(session)
        cat = str(goal.get("doc_category") or ff.get("category") or "").strip()
        tit = str(goal.get("doc_title") or ff.get("title") or "").strip()
        if cat and tit:
            text = f"{cat}｜{tit}"
        elif not wants_gen:
            session.phase = ChatPhase.FREEFORM_ASK.value
            return (
                "可以，我先帮你起草目录大纲，你改完确认后再开写。"
                "先跟我说清楚写什么、文档打算叫什么就行。",
                [],
            )
    if is_vague_freeform_meta(text) or wants_generate_outline(text, invoke=invoke, phase=str(session.phase or ""), session=session):
        inferred = infer_freeform_meta_from_messages(session, text, invoke=invoke)
        if inferred and title_contains_action_clause(str(inferred[1] or "")):
            inferred = None
        if not inferred:
            goal = get_session_goal(session)
            cat = str(goal.get("doc_category") or "").strip()
            tit = str(goal.get("doc_title") or extracted_title or "").strip()
            if extracted_title:
                inferred = (cat or "文档", extracted_title)
            elif cat and tit and not title_contains_action_clause(tit):
                inferred = (cat, tit)
        if inferred:
            category, title = inferred
            text = f"{category}｜{title}"
        elif wants_gen:
            session.phase = ChatPhase.FREEFORM_ASK.value
            return (
                "可以帮你生成目录。还不太清楚文档叫什么、写哪一类——"
                "你补充一下题目或类型，我立刻起草可编辑的大纲。",
                [],
            )
        else:
            session.phase = ChatPhase.FREEFORM_ASK.value
            return (
                "好，那就不用蓝本。你想写什么、文档打算叫什么，用你习惯的说法告诉我就行。",
                [],
            )
    category, title = text, text
    if "｜" in text or "|" in text:
        parts = re.split(r"[｜|]", text, maxsplit=1)
        category = parts[0].strip() or "通用"
        title = (parts[1].strip() if len(parts) > 1 else text) or text
    elif " / " in text:
        parts = text.split(" / ", 1)
        category, title = parts[0].strip(), parts[1].strip()
    else:
        from report_writing.chat.intent_router import infer_freeform_meta_from_messages

        inferred = infer_freeform_meta_from_messages(session, text, invoke=invoke)
        if inferred and title_contains_action_clause(str(inferred[1] or "")):
            inferred = None
        if inferred:
            category, title = inferred
        elif extracted_title:
            category, title = "文档", extracted_title
    if extracted_title and title_contains_action_clause(title):
        title = extracted_title
        if title_contains_action_clause(category):
            category = "文档"
    if is_vague_freeform_meta(title, field=True) or is_vague_freeform_meta(category, field=True):
        session.phase = ChatPhase.FREEFORM_ASK.value
        return (
            "题目还不够具体。再说清楚一点写什么、文档叫什么名字就行。",
            [],
        )
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        ensure_named_freeform_document,
        explicit_doc_title_from_message,
        focus_needs_doc_name,
        is_writing_document_named,
    )
    from report_writing.chat.session_snapshot import looks_like_freeform_meta_line

    # 口语推断出的题目：先挂定名澄清，勿直接「收到，是【…】」跳过引导
    raw_in = (message or "").strip()
    explicit_title = explicit_doc_title_from_message(raw_in)
    from report_writing.chat.dialogue import message_repeats_assistant_reply
    from report_writing.chat.session_documents import (
        message_looks_like_new_write_topic,
        quick_title_from_write_message,
        title_is_write_subject_not_doc_name,
    )

    # 助手原文被误记成用户话时，不能拿来定名，也不能把上一份文档的标题拼进来。
    if message_repeats_assistant_reply(session, raw_in):
        from report_writing.chat.intent_clarify import build_document_name_clarify

        return build_document_name_clarify(
            session,
            message="",
            title_hint="",
            invoke=invoke,
            home=home,
            emit=emit,
            follow_entry=False,
        )
    subject_only = bool(raw_in) and not explicit_title and (
        title_is_write_subject_not_doc_name(raw_in, title)
        or (
            message_looks_like_new_write_topic(raw_in)
            and not is_writing_document_named(session)
        )
    )
    if (
        is_writing_document_named(session)
        and message_looks_like_new_write_topic(raw_in)
        and not explicit_title
    ):
        from report_writing.chat.intent_clarify import build_document_scope_clarify

        return build_document_scope_clarify(
            session,
            message=raw_in,
            topic_hint=quick_title_from_write_message(raw_in) or title,
        )
    if subject_only:
        from report_writing.chat.session_documents import apply_working_title

        working = quick_title_from_write_message(raw_in) or title
        if working:
            apply_working_title(session, working, home=home, message=raw_in)
            return (
                f"先按「{working}」来写，正式名字之后还能改。接下来可以匹配蓝本、用模版，或让我生成目录。",
                [],
            )
        session.phase = ChatPhase.FREEFORM_ASK.value
        from report_writing.chat.intent_clarify import build_document_name_clarify

        return build_document_name_clarify(
            session,
            message=raw_in,
            title_hint="",
            invoke=invoke,
            home=home,
            emit=emit,
            follow_entry=False,
        )
    if explicit_title and focus_needs_doc_name(session):
        confirm_writing_document_name(
            session,
            explicit_title,
            home=home,
            message=raw_in or explicit_title,
        )
    structured = looks_like_freeform_meta_line(raw_in) or bool(explicit_title)
    if (
        not explicit_title
        and not structured
        and focus_needs_doc_name(session)
        and title
    ):
        from report_writing.chat.intent_clarify import build_document_name_clarify
        from report_writing.chat.session_documents import stash_pending_entry

        session.freeform = {
            **(session.freeform or {}),
            "category": category,
            "title": title,
        }
        stash_pending_entry(session, message=raw_in or f"{category}｜{title}", title=title)
        session.phase = ChatPhase.FREEFORM_ASK.value
        return build_document_name_clarify(
            session,
            message=raw_in or f"{category}｜{title}",
            title_hint=title,
            invoke=invoke,
        )
    session.freeform = {
        **(session.freeform or {}),
        "category": category,
        "title": title,
        "meta_confirmed": True,
    }
    from report_writing.chat.session_goal import merge_session_goal, sync_goal_into_freeform

    merge_session_goal(
        session,
        {
            "doc_category": category,
            "doc_title": title,
            "summary": f"{category}｜{title}",
        },
    )
    sync_goal_into_freeform(session)
    # 开写前先定名并登记，文档窗立刻展示；同会话新题目会新开一份
    ensure_named_freeform_document(session, home, title=title, category=category)
    # 若任务已有目录：只更新名称，勿回「帮我生成目录」
    if home is not None:
        from report_writing.chat.helpers import _load_task

        tid_exist = (session.task_id or "").strip()
        exist = _load_task(home, tid_exist) if tid_exist else None
        n_ch = len(list(exist.outline.chapters or [])) if exist and exist.outline else 0
        if n_ch >= 1:
            prev_phase = str(session.phase or "") or ChatPhase.FREEFORM_OUTLINE.value
            # 已有目录时不降级到「从零起草」；保留更后的写作阶段
            if prev_phase in {
                ChatPhase.FREEFORM_POINTS.value,
                ChatPhase.READY_TO_WRITE.value,
                ChatPhase.WRITING.value,
                ChatPhase.CONFIRM_EXPORT.value,
            }:
                session.phase = prev_phase
            else:
                session.phase = ChatPhase.FREEFORM_OUTLINE.value
            ff_now = dict(session.freeform or {})
            cards: list[ChatCard] = []
            if not ff_now.get("outline_confirmed") and exist is not None:
                from report_writing.chat.freeform_outline import chapters_to_outline_text

                outline_text = chapters_to_outline_text(
                    [
                        {"sequence": n.sequence, "title": n.title}
                        for n in exist.outline.chapters
                    ]
                )
                cards = [
                    _outline_preview_card(
                        tid_exist,
                        exist,
                        outline_text,
                        editable=True,
                        session=session,
                        doc_title=title,
                    )
                ]
            return (
                f"好，文档名已更新为【{category}】{title}。目录仍在（共 {n_ch} 章），"
                "可在文档窗口调整；满意就点「确认大纲并继续」。",
                cards,
            )
    session.phase = ChatPhase.FREEFORM_OUTLINE.value
    # 槽位已声明自动大纲：定名后直接起草，勿停在「请说帮我生成目录」
    try:
        from report_writing.chat.session_documents import focus_onboarding

        ob = focus_onboarding(session)
        prefer = str(ob.get("preferred_entry_path") or ob.get("entry_path") or "").strip()
        residual = "generate_outline" in list(
            ((session.freeform or {}).get("onboarding_gaps_snapshot") or {}).get("residual_intents")
            or []
        )
        mode_auto = (
            prefer == "freeform"
            and (
                residual
                or str(
                    ((session.freeform or {}).get("onboarding_gaps_snapshot") or {}).get(
                        "declared_freeform_mode"
                    )
                    or ""
                ).strip()
                == "auto_outline"
            )
        )
        if mode_auto and home is not None and invoke is not None:
            return _collect_freeform_outline(
                home,
                session,
                "帮我生成目录大纲",
                invoke=invoke,
                regenerate=True,
                emit=emit,
            )
    except Exception:
        pass
    # 定名后不自动生成目录：由总控/用户再调度 submit_outline
    return (
        f"收到，是【{category}】{title}。可以说「帮我生成目录」，或直接粘贴章节目录；"
        "定好后在文档窗口调整，再确认大纲。",
        [],
    )


def _freeform_outline_doc_title(session: ChatSession | None, task) -> str:
    """无蓝本大纲文档显示名：优先标题，其次类型（如「工作报告」）。"""
    from report_writing.chat.session_documents import (
        sanitize_candidate_document_title,
        title_contains_action_clause,
    )

    meta = dict((session.freeform if session else None) or {})
    tit = str(meta.get("title") or "").strip()
    cat = str(meta.get("category") or "").strip()
    task_tit = str(getattr(task, "title", "") or "").strip() if task is not None else ""
    task_cat = str(getattr(task, "category", "") or "").strip() if task is not None else ""
    for candidate in (tit, task_tit, cat, task_cat):
        cleaned = sanitize_candidate_document_title(candidate)
        if cleaned and not title_contains_action_clause(cleaned):
            return cleaned
        if candidate and not title_contains_action_clause(candidate):
            return candidate
    return ""


def _outline_preview_card(
    task_id: str,
    task,
    outline_text: str,
    *,
    editable: bool,
    session: ChatSession | None = None,
    doc_title: str = "",
) -> ChatCard:
    from report_writing.chat.freeform_outline import chapters_to_outline_text

    preview = [
        n.to_dict() if hasattr(n, "to_dict") else {"key": n.key, "sequence": n.sequence, "title": n.title}
        for n in (task.outline.chapters if task and task.outline else [])[:80]
    ]
    body = (outline_text or "").strip() or chapters_to_outline_text(
        [{"sequence": c.get("sequence"), "title": c.get("title")} for c in preview]
    )
    name = (doc_title or _freeform_outline_doc_title(session, task)).strip()
    if name:
        card_title = f"{name}（可调整）" if editable else name
    else:
        card_title = "章节目录（可调整）" if editable else "章节目录"
    return ChatCard(
        type="outline_preview",
        title=card_title,
        payload={
            "chapters": preview,
            "task_id": task_id,
            "outline_text": body,
            "editable": editable,
            "pending_confirm": editable,
            "doc_title": name,
            "doc_category": str(
                ((session.freeform if session else None) or {}).get("category")
                or getattr(task, "category", "")
                or ""
            ).strip(),
        },
    )


def _apply_outline_to_task(home: Path, task_path: Path, text: str, *, confirmed: bool = False):
    from report_writing.chat.helpers import _load_task

    outline_from_pasted_text(task_path, text, use_llm=False)
    # 预览目录默认不确认，避免文档窗过早出现「目录已确认 / 待学习」
    if confirmed:
        confirm_outline(task_path)
    return _load_task(home, task_path.name)


def _freeform_outline_may_reuse_task(
    session: ChatSession,
    task_id: str,
    *,
    title: str = "",
    category: str = "",
) -> bool:
    """当前无蓝本大纲是否可复用 session.task_id（防异文档 regenerate 覆盖）。

    即使 focus 仍指向旧 task（session.task_id 回落），标题/类型不一致也必须拒绝复用。
    """
    tid = (task_id or "").strip()
    if not tid:
        return False
    from report_writing.chat.session_documents import session_documents_map

    want_title = (title or "").strip()
    want_cat = (category or "").strip()
    docs = session_documents_map(session)
    entry = docs.get(f"task:{tid}") or {}
    entry_title = str(entry.get("title") or "").strip()
    entry_kind = str(entry.get("kind") or "").strip()
    if entry_title and want_title and entry_title != want_title:
        if want_title not in entry_title and entry_title not in want_title:
            return False
    if entry_kind and entry_kind not in {"outline", "writing", "task"}:
        # 蓝本登记项不可被无蓝本大纲 regenerate 覆盖
        return False
    from report_writing.chat.session_documents import focus_document_key

    focus = focus_document_key(session)
    focus_hit = focus == f"task:{tid}"
    # 定名壳 writing / 大纲 outline：焦点指向本 task 且标题未冲突 → 复用（勿每轮 mint 新 w*）
    if entry_kind in {"outline", "writing", "task"} or (entry and not entry_kind):
        if entry_title or want_title:
            if focus_hit or entry_kind == "outline" or not entry_kind:
                return True
    # 无登记：焦点明确指向该 task 且有目标标题 → 复用定名壳
    if focus_hit and want_title:
        return True
    return False


def _collect_freeform_outline(
    home: Path,
    session: ChatSession,
    message: str,
    *,
    invoke=None,
    confirm: bool = False,
    regenerate: bool = False,
    revision_instructions: str = "",
    emit=None,
    chapter_keys: list[str] | None = None,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.freeform_outline import (
        chapters_to_outline_text,
        is_bare_generate_outline_phrase,
        llm_generate_outline_text,
        looks_like_outline_directory,
        merge_outline_constraints,
        normalize_pasted_outline,
        outline_top_chapter_count,
        remember_outline_constraints_from_message,
        starter_outline_for_category,
        trim_outline_directory_text,
        wants_generate_outline,
        wants_outline_confirm,
    )
    from report_writing.chat.helpers import _load_task, is_vague_freeform_meta

    text = (message or "").strip()
    meta = dict(session.freeform or {})
    pending = bool(meta.get("outline_pending_edit"))
    rev = (revision_instructions or "").strip()
    # 重新生成：把用户原话写入约束，供本轮与后续再生成沿用
    if regenerate:
        tip = rev or (text if text and not looks_like_outline_directory(text) else "")
        if tip and tip not in {
            "generate",
            "generate_outline",
            "help_draft",
            "starter_fallback",
        }:
            meta["outline_constraints"] = merge_outline_constraints(
                str(meta.get("outline_constraints") or ""), tip
            )
            session.freeform = meta
    elif rev:
        meta["outline_constraints"] = merge_outline_constraints(
            str(meta.get("outline_constraints") or ""), rev
        )
        session.freeform = meta
    remember_outline_constraints_from_message(session, text)
    meta = dict(session.freeform or {})
    # 话术确认优先于调用方漏传 confirm / 误传 regenerate
    if (not regenerate) and wants_outline_confirm(
        text, invoke=None, phase=str(session.phase or "")
    ):
        confirm = True
        regenerate = False
    if looks_like_outline_directory(text):
        cat, tit, body = normalize_pasted_outline(text)
        if cat:
            meta["category"] = cat
        if tit:
            meta["title"] = tit
        if cat or tit:
            meta["meta_confirmed"] = True
        if body:
            text = body
        session.freeform = meta
    if wants_outline_confirm(text, invoke=invoke, phase=str(session.phase or "")) and not looks_like_outline_directory(
        text
    ):
        stored = str(meta.get("outline_text") or "").strip()
        if stored:
            text = stored
        elif not pending:
            session.phase = ChatPhase.FREEFORM_OUTLINE.value
            return (
                "还没有目录内容。请粘贴章节目录，或说「帮我生成目录大纲」；生成后可在卡片里改再确认。",
                [],
            )
    if not meta.get("meta_confirmed"):
        session.phase = ChatPhase.FREEFORM_ASK.value
        return (
            "还没定文档叫什么。先告诉我写什么、题目是什么，再说生成目录或粘贴目录。",
            [],
        )
    title_meta = str(meta.get("title") or "").strip()
    category = str(meta.get("category") or "").strip()
    if is_vague_freeform_meta(title_meta, field=True):
        session.phase = ChatPhase.FREEFORM_ASK.value
        return ("文档标题还不清楚，再说一下叫什么名字吧。", [])

    from report_writing.chat.freeform_help import wants_freeform_help_draft

    task_id = (session.task_id or "").strip()
    phase = str(session.phase or "")
    should_generate = (
        regenerate
        or wants_generate_outline(text, invoke=invoke, phase=phase, session=session)
        or wants_freeform_help_draft(text, invoke=invoke, phase=phase)
        or (not text and wants_generate_outline(str(meta.get("last_outline_request") or ""), invoke=invoke, phase=phase, session=session))
    )

    if should_generate and not confirm:
        if invoke is None:
            session.phase = ChatPhase.FREEFORM_OUTLINE.value
            return (
                "要自动生成目录需要接上写作模型；你也可以直接把目录粘贴过来（每行一章）。",
                [],
            )
        from report_writing.chat.helpers import _emit_process

        _emit_process(emit, "正在根据你的题目生成章节目录…")
        current_outline = str(meta.get("outline_text") or "").strip()
        if not current_outline:
            peek_tid = (session.task_id or "").strip()
            peek = _load_task(home, peek_tid) if peek_tid else None
            if peek and peek.outline and peek.outline.chapters:
                current_outline = chapters_to_outline_text(
                    [
                        {"sequence": n.sequence, "title": n.title}
                        for n in peek.outline.chapters
                    ]
                )
        rev_for_llm = rev or str(meta.get("outline_constraints") or "").strip()
        if is_bare_generate_outline_phrase(rev_for_llm):
            rev_for_llm = str(meta.get("outline_constraints") or "").strip()
        if not rev_for_llm:
            notes = str((meta.get("session_goal") or {}).get("notes") or "")
            if outline_top_chapter_count(notes):
                rev_for_llm = notes
        if not rev_for_llm and text and not looks_like_outline_directory(text):
            if not is_bare_generate_outline_phrase(text):
                rev_for_llm = text
        generated = llm_generate_outline_text(
            session,
            category=category,
            title=title_meta,
            invoke=invoke,
            revision_instructions=rev_for_llm,
            current_outline_text=current_outline,
            regenerate=bool(regenerate or current_outline),
        )
        if not generated or len(generated.splitlines()) < 2:
            starter = starter_outline_for_category(category, title_meta)
            if starter:
                text = starter
                meta["outline_source"] = "starter_fallback"
                meta["last_outline_request"] = "starter_fallback"
            else:
                session.phase = ChatPhase.FREEFORM_OUTLINE.value
                return ("目录生成失败，请重试或手动粘贴目录。", [])
        else:
            text = generated
            meta["outline_source"] = "llm"
            meta["last_outline_request"] = rev_for_llm or message or "generate"
        keep_top = outline_top_chapter_count(
            rev_for_llm
        ) or outline_top_chapter_count(str(meta.get("outline_constraints") or ""))
        if keep_top:
            text = trim_outline_directory_text(text, keep_top)
        session.freeform = meta

    if not text:
        task_id_peek = (session.task_id or "").strip()
        if confirm and task_id_peek:
            from report_writing.chat.freeform_outline import chapters_to_outline_text

            peek = _load_task(home, task_id_peek)
            if peek and peek.outline and peek.outline.chapters:
                text = chapters_to_outline_text(
                    [{"sequence": n.sequence, "title": n.title} for n in peek.outline.chapters]
                )
        if not text:
            session.phase = ChatPhase.FREEFORM_OUTLINE.value
            return (
                "章节目录会在下方卡片里展示。可说「帮我生成目录」重新起草，或在拓扑里调整后点确认。",
                [],
            )

    if not confirm and not pending and wants_generate_outline(text, invoke=invoke, phase=phase, session=session) and invoke:
        should_generate = True

    if not should_generate and not confirm:
        if is_vague_freeform_meta(text) and not wants_generate_outline(text, invoke=invoke, phase=phase, session=session):
            # 「继续」等含糊话：已有目录 → 确认大纲，勿回「帮我生成目录」
            peek_tid = (session.task_id or "").strip()
            peek = _load_task(home, peek_tid) if peek_tid else None
            if peek and peek.outline and peek.outline.chapters:
                return _collect_freeform_outline(
                    home,
                    session,
                    "",
                    invoke=invoke,
                    confirm=True,
                    emit=emit,
                )
            session.phase = ChatPhase.FREEFORM_OUTLINE.value
            return (
                "可以说「帮我生成目录」，或粘贴章节目录，例如：\n1 总则\n1.1 编制依据",
                [],
            )
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2 and not re.search(r"\d+[\.、．]\s*\S", text):
            session.phase = ChatPhase.FREEFORM_OUTLINE.value
            return (
                "目录至少两行，或带「1.1 标题」编号；也可以说「帮我生成目录大纲」。",
                [],
            )

    title = title_meta or "对话写作任务"
    # 定名壳 / 当前焦点文档：首次生成与 regenerate 都优先复用，禁止同标题连开多份 w*
    if task_id:
        if not _freeform_outline_may_reuse_task(session, task_id, title=title, category=category):
            task_id = ""
        else:
            task_path = home / "tasks" / task_id
            if not task_path.is_dir():
                task_id = ""

    if not task_id:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        task_id = f"w{stamp}{secrets.token_hex(2)}"
        task_path = home / "tasks" / task_id
        ensure_task(task_path)
        save_spec_meta(task_path, title=title, category=category, tags=["chat"], description="对话无蓝本写作")
    else:
        task_path = home / "tasks" / task_id

    from report_writing.chat.freeform_outline import chapters_to_outline_text

    task = None
    if confirm and task_id:
        existing = _load_task(home, task_id)
        if existing and existing.outline and len(existing.outline.chapters or []) >= 2:
            task = existing
            text = chapters_to_outline_text(
                [{"sequence": n.sequence, "title": n.title} for n in existing.outline.chapters]
            )
    explicit_confirm = confirm or (
        pending and wants_outline_confirm(message or text, invoke=invoke, phase=phase)
    )
    # 调用方显式 confirm=True 时优先确认，不被「目录正文像生成意图」的 should_generate 挡住
    do_confirm = bool(confirm) or (explicit_confirm and not should_generate)
    if task is None:
        task = _apply_outline_to_task(
            home, task_path, text, confirmed=bool(do_confirm)
        )
    session.task_id = task_id
    from report_writing.chat.session_documents import (
        prune_superseded_freeform_documents,
        set_focus_document_key,
        upsert_session_document,
    )

    upsert_session_document(
        session,
        kind="outline",
        task_id=task_id,
        title=title,
        clear_blueprint_id=True,
    )
    # 同文档只留最新一份：清掉同标题旧 task 登记
    prune_superseded_freeform_documents(session, keep_task_id=task_id, title=title)
    # 同会话新大纲：立刻切焦点，供前端文档窗刷新到本任务
    set_focus_document_key(session, f"task:{task_id}")
    # 纯无蓝本自动大纲：清掉残留依据样本向导，避免文档窗仍显示上传/提取步进与旧目录
    from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode

    wiz_left = dict((session.freeform or {}).get("blueprint_wizard") or {})
    wiz_work = str(
        wiz_left.get("working_task_id") or wiz_left.get("blueprint_id") or ""
    ).strip()
    if is_freeform_sample_wizard_mode(wiz_left) and wiz_work and wiz_work != task_id:
        ff_clear = dict(session.freeform or {})
        ff_clear.pop("blueprint_wizard", None)
        session.freeform = ff_clear
        try:
            from report_writing.chat.session_runtime import WORKFLOW_BLUEPRINT, workflow_set

            workflow_set(session, WORKFLOW_BLUEPRINT, None)
        except Exception:
            pass
    elif not is_freeform_sample_wizard_mode(wiz_left) and wiz_left:
        ff_clear = dict(session.freeform or {})
        ff_clear.pop("blueprint_wizard", None)
        session.freeform = ff_clear
    try:
        from report_writing.chat.session_documents import rebind_session_to_focus_document

        rebind_session_to_focus_document(session, home)
    except Exception:
        pass
    # 登记表已在 session.freeform 更新；meta 是函数开头快照，写回前必须并入，否则会把 prune 结果冲掉
    meta = {**(session.freeform or {}), **meta}
    meta["session_documents"] = dict((session.freeform or {}).get("session_documents") or {})
    meta["outline_text"] = text

    if do_confirm:
        preview_n = len(task.outline.chapters) if task and task.outline else 0
        if preview_n < 2:
            meta["outline_pending_edit"] = True
            session.freeform = meta
            session.phase = ChatPhase.FREEFORM_OUTLINE.value
            return (
                "目录至少要有 2 章。请在下方修改后再点「确认大纲并继续」。",
                [_outline_preview_card(task_id, task, text, editable=True, session=session)],
            )
        confirm_outline(task_path)
        meta["outline_pending_edit"] = False
        meta["outline_text"] = text
        meta["outline_confirmed"] = True
        session.freeform = meta
        session.phase = ChatPhase.FREEFORM_POINTS.value
        # 无蓝本不走「依据样本学习」：确认大纲后自动生成各章写作信息
        # 确认后必须带一份不可编辑预览卡，否则前端仍按旧「可调整」卡显示「待确认目录」
        confirmed_preview = _outline_preview_card(
            task_id, task, text, editable=False, session=session
        )
        if invoke is not None:
            from report_writing.chat.helpers import _emit_process

            scoped = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
            _emit_process(
                emit,
                (
                    f"大纲已确认，正在为勾选的 {len(scoped)} 章起草写作要点…"
                    if scoped
                    else "大纲已确认，正在自动生成各章写作要点…"
                ),
            )
            reply, cards = _collect_freeform_points(
                home,
                session,
                "",
                invoke=invoke,
                generate=True,
                emit=emit,
                payload={"chapter_keys": scoped} if scoped else None,
            )
            head = f"大纲已确认，共 {preview_n} 章。"
            return (f"{head}{reply}".strip(), [confirmed_preview, *(cards or [])])
        from report_writing.chat.freeform_points import cards_after_outline_confirmed

        return (
            f"大纲已确认，共 {preview_n} 章。接下来我会按目录自动起草各章写作要点。",
            cards_after_outline_confirmed(home, session, [confirmed_preview], task),
        )

    meta["outline_pending_edit"] = True
    session.freeform = meta
    session.phase = ChatPhase.FREEFORM_OUTLINE.value
    preview_n = len(task.outline.chapters) if task and task.outline else 0
    from report_writing.chat.freeform_blueprint import save_blueprint_bar_hint

    cards = [_outline_preview_card(task_id, task, text, editable=True, session=session)]
    save_hint = save_blueprint_bar_hint(home, session)
    name = _freeform_outline_doc_title(session, task) or title_meta or category
    return (
        f"好，「{name}」的目录我先帮你搭了 {preview_n} 章，在下面卡片里随便改；"
        f"觉得 OK 就点「确认大纲并继续」。{save_hint}",
        cards,
    )


def _collect_freeform_points(
    home: Path,
    session: ChatSession,
    message: str,
    *,
    invoke=None,
    generate: bool = False,
    skip: bool = False,
    emit=None,
    payload: dict | None = None,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.freeform_points import (
        finish_points_phase_cards,
        freeform_points_help_card,
        llm_draft_chapter_points,
    )
    from report_writing.chat.gates import _parse_chapter_keys
    from report_writing.chat.helpers import _load_task

    text = (message or "").strip()
    task_id = (session.task_id or "").strip()
    if not task_id:
        # 仅当调用方显式 generate=True（generate_freeform_points）时起草目录；
        # 不再在本函数里用 wants_generate_outline / skip_blueprint 另选总控 Tool。
        if generate:
            return _collect_freeform_outline(
                home,
                session,
                text or "帮我生成目录大纲",
                invoke=invoke,
                regenerate=True,
                emit=emit,
            )
        return (
            "还没有目录任务。请先生成或确认大纲，再配要点。",
            [],
        )
    task = _load_task(home, task_id)
    meta = dict(session.freeform or {})
    pl = dict(payload or {})
    target_keys = _parse_chapter_keys(pl)
    focus = str(pl.get("focus_chapter_key") or pl.get("chapter_key") or "").strip()
    if focus and focus not in target_keys:
        # 章级按钮只带 focus 时仍按单章生成
        if not target_keys:
            target_keys = [focus]

    do_skip = bool(skip)
    do_generate = bool(generate)

    if do_skip:
        meta["points_step"] = "skipped"
        session.freeform = meta
        return (
            "好的，先不配要点。点「开始写作」我就开始检索和撰稿；之后仍可在文档窗口改章节配置。",
            finish_points_phase_cards(home, session),
        )

    if do_generate:
        if invoke is None:
            return (
                "要自动生成写作要点需要接上写作模型；你也可以在对话里粘贴说明，或点「先跳过」直接开写。",
                [freeform_points_help_card(session, task)] if task else [],
            )
        if task is None or not task.outline or not list(getattr(task.outline, "chapters", None) or []):
            outline_msg = text.strip() if text.strip() else "帮我生成目录大纲"
            return _collect_freeform_outline(
                home,
                session,
                outline_msg,
                invoke=invoke,
                regenerate=True,
                emit=emit,
            )
        want = set(target_keys) if target_keys else None
        chapters = llm_draft_chapter_points(
            session, task, invoke=invoke, emit=emit, target_keys=want
        )
        from report_writing.chat.freeform_points import apply_generated_points

        apply_generated_points(home, task_id, chapters, emit=emit, target_keys=want)
        meta["points_step"] = "generated"
        meta["outline_confirmed"] = True
        session.freeform = meta
        if want:
            n = sum(
                1
                for c in chapters
                if str(c.key or "").strip() in want
                and (c.points or [])
                and not getattr(c, "structure_only", False)
            )
            reply = (
                f"已为选中的 {n} 个章节起草了要点与检索词。展开「章节配置」可修改；"
                "不满意可以说「重新生成要点」。"
            )
        else:
            n = sum(
                1
                for c in chapters
                if (c.points or []) and not getattr(c, "structure_only", False)
            )
            reply = (
                f"已按目录为 {n} 个可写章节起草了要点与检索词。展开「章节配置」可修改；"
                "不满意可以说「重新生成要点」。"
            )
        phase = str(session.phase or "")
        if phase in {ChatPhase.WRITING.value, ChatPhase.CONFIRM_EXPORT.value}:
            from report_writing.chat.cards import _progress_and_confirm_cards
            from report_writing.chat.writing import _session_writing_pace

            task = _load_task(home, task_id)
            if task:
                return (
                    reply + " 保存配置后点「保存并重试本章」或「开始写本章」继续。",
                    _progress_and_confirm_cards(task, pace=_session_writing_pace(session)),
                )
        cards = finish_points_phase_cards(home, session)
        if phase not in {ChatPhase.WRITING.value, ChatPhase.CONFIRM_EXPORT.value}:
            from report_writing.chat.cards import _progress_and_confirm_cards
            from report_writing.chat.writing import _session_writing_pace

            task = _load_task(home, task_id)
            if task:
                cards = _progress_and_confirm_cards(task, pace=_session_writing_pace(session)) + list(cards)
                reply = (
                    reply
                    + " 可在右侧文档窗口展开各章改「章节配置」；确认没问题就点「开始写作」。"
                )
        if "开始写作" not in reply:
            reply = reply + " 确认没问题就点「开始写作」。"
        return reply, cards

    if text:
        return (
            "收到。若是在补充写作要点，请说明针对哪一章或贴分章说明；"
            "若是加要素、记背景，我会按对话理解后帮你落到对应章节。",
            [freeform_points_help_card(session, task)] if task else [],
        )

    session.phase = ChatPhase.FREEFORM_POINTS.value
    return (
        "你可以点「帮我生成写作要点」，或直接粘贴各章写法说明；着急就「先跳过」直接开写。",
        [freeform_points_help_card(session, task)] if task else [],
    )


def _action_generate_freeform_points(
    home: Path,
    session: ChatSession,
    payload: dict,
    *,
    invoke=None,
    emit=None,
) -> tuple[str, list[ChatCard]]:
    pl = dict(payload or {})
    # tool 可能以 chapter_keys_json 传入
    raw_json = pl.get("chapter_keys_json")
    if raw_json and not pl.get("chapter_keys"):
        pl["chapter_keys"] = raw_json
    return _collect_freeform_points(
        home,
        session,
        "",
        invoke=invoke,
        generate=True,
        emit=emit,
        payload=pl,
    )


def _action_skip_freeform_points(
    home: Path, session: ChatSession, payload: dict
) -> tuple[str, list[ChatCard]]:
    return _collect_freeform_points(home, session, "", skip=True)


def _action_save_freeform_as_blueprint(
    home: Path, session: ChatSession, payload: dict
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.cards import _progress_and_confirm_cards
    from report_writing.chat.freeform_blueprint import (
        freeform_outline_is_confirmed,
        promote_writing_task_to_blueprint,
        session_post_writing_complete,
    )
    from report_writing.chat.freeform_points import finish_points_phase_cards
    from report_writing.chat.resource_actions import _bind_robot_id
    from report_writing.chat.resource_readiness import export_phase_cards, robot_export_ready
    from report_writing.chat.writing import _session_writing_pace
    from report_writing.services.robots import get_robot

    from report_writing.chat.session_documents import resolve_task_id, upsert_session_document

    task_id = resolve_task_id(session, payload, require=False) or str(session.task_id or "").strip()
    meta = dict(session.freeform or {})
    post_writing = session_post_writing_complete(home, session, task_id)

    if payload.get("dismiss"):
        session.freeform = {**meta, "blueprint_offer_dismissed": True}
        if post_writing:
            return ("好的，这次先不收录到蓝本库。", [])
        return (
            "好的，这次先不收录到蓝本库。点「开始写作」即可按章依次开写。",
            finish_points_phase_cards(home, session),
        )

    if not task_id:
        raise ValueError("缺少写作任务")
    if not freeform_outline_is_confirmed(session, home):
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, task_id)
        text = str(meta.get("outline_text") or "").strip()
        cards = []
        if task is not None:
            cards = [
                _outline_preview_card(
                    task_id, task, text, editable=True, session=session
                )
            ]
        return (
            "收录到蓝本库前请先确认大纲：在下方卡片点「确认大纲并继续」，或直接说「确认大纲」。",
            cards,
        )
    raw_tags = payload.get("tags")
    if not isinstance(raw_tags, list):
        raw_tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    bp_id, task = promote_writing_task_to_blueprint(
        home,
        task_id,
        title=str(payload.get("title") or meta.get("title") or "").strip(),
        category=str(payload.get("category") or meta.get("category") or "").strip(),
        tags=[str(t).strip() for t in raw_tags if str(t).strip()],
    )
    _bind_robot_id(home, session, "blueprint_ids", bp_id)
    display_title = str(
        meta.get("title") or getattr(task, "title", "") or bp_id
    ).strip()
    # 收录后：原 task: 登记并入 bp:，禁止文档窗出现两份同名 tab
    from report_writing.chat.session_documents import (
        session_documents_map,
        set_focus_document_key,
        rebind_session_to_focus_document,
    )

    ff_docs = dict(session.freeform or {})
    docs = session_documents_map(session)
    old_key = f"task:{task_id}"
    if old_key in docs:
        prev = dict(docs.pop(old_key) or {})
        if not display_title:
            display_title = str(prev.get("title") or "").strip()
        ff_docs["session_documents"] = docs
        session.freeform = ff_docs
    ff_saved = dict(session.freeform or {})
    # 收录后不再当「未入库样本」，否则文档窗继续藏「同步到蓝本库」
    wiz_saved = dict(ff_saved.get("blueprint_wizard") or {})
    from report_writing.chat.wizard_state import is_freeform_sample_wizard_mode

    if is_freeform_sample_wizard_mode(wiz_saved):
        ff_saved.pop("blueprint_wizard", None)
    ff_saved["freeform_saved_blueprint_id"] = bp_id
    ff_saved["collected_blueprint_id"] = bp_id
    session.freeform = ff_saved
    session.blueprint_id = bp_id
    upsert_session_document(
        session,
        kind="blueprint",
        task_id=bp_id,
        blueprint_id=bp_id,
        writing_task_id=task_id,
        title=display_title or bp_id,
    )
    set_focus_document_key(session, f"bp:{bp_id}")
    docs_after = session_documents_map(session)
    for reg_key in (f"bp:{bp_id}", old_key):
        if reg_key in docs_after:
            row = dict(docs_after[reg_key])
            row["collected_blueprint_id"] = bp_id
            docs_after[reg_key] = row
    ff_col = dict(session.freeform or {})
    ff_col["session_documents"] = docs_after
    session.freeform = ff_col
    rebind_session_to_focus_document(session, home=home)

    if post_writing:
        from report_writing.chat.helpers import _load_task

        cards: list[ChatCard] = []
        writing = _load_task(home, task_id)
        if writing is not None:
            cards.extend(_progress_and_confirm_cards(writing, pace=_session_writing_pace(session)))
            robot = get_robot(home, session.robot_id) if session.robot_id else None
            if robot_export_ready(home, robot):
                cards.extend(export_phase_cards(home, task_id, robot))
        return (
            f"已收录到蓝本库：「{task.title or bp_id}」（{bp_id}）。下次写同类文档时可直接选用。",
            cards,
        )

    from report_writing.chat.cards import _doc_window_hint_card

    cards = list(finish_points_phase_cards(home, session))
    cards.append(
        _doc_window_hint_card(
            session,
            blueprint_id=bp_id,
            task_id=bp_id,
            title="打开文档窗口补充分析（可选）",
            hint="可在右侧文档窗口补充分析要点。",
            action="analyze",
            auto_open=False,
        )
    )
    return (
        f"已收录到蓝本库：「{task.title or bp_id}」（{bp_id}）。下次写同类文档可直接选用；"
        "当前对话仍用本文档副本继续写。之后改了目录/要点，可以说「同步到蓝本库」。",
        cards,
    )


def _action_sync_document_to_blueprint_library(
    home: Path, session: ChatSession, payload: dict
) -> tuple[str, list[ChatCard]]:
    """把文档副本配置显式回写源库蓝本（选用线 / 无蓝本收录后均可）。"""
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.session_documents import resolve_task_id
    from report_writing.chat.writing import sync_document_copy_to_library
    from report_writing.services.task_ids import is_writing_task_id

    task_id = resolve_task_id(session, payload, require=False) or str(
        session.task_id or ""
    ).strip()
    if not task_id or not is_writing_task_id(task_id):
        raise ValueError("请先打开文档副本，再同步到蓝本库")
    writing = _load_task(home, task_id)
    if writing is None:
        raise ValueError("写作文档不存在")
    ff = dict(session.freeform or {})
    collected = str(
        ff.get("collected_blueprint_id")
        or ff.get("freeform_saved_blueprint_id")
        or getattr(writing, "source_blueprint_id", "")
        or session.blueprint_id
        or ""
    ).strip()
    if not collected and not str(getattr(writing, "source_blueprint_id", "") or "").strip():
        raise ValueError("当前文档没有源蓝本，无法同步；无蓝本写作请先「收录到蓝本库」")
    if collected and not str(getattr(writing, "source_blueprint_id", "") or "").strip():
        writing.source_blueprint_id = collected
        from report_writing.services.store import TaskStore

        TaskStore(home / "tasks" / task_id).save(writing)
    bp_id, bp_task = sync_document_copy_to_library(home, task_id)
    title = str(getattr(bp_task, "title", "") or bp_id).strip()
    return (
        f"已把当前文档的目录/要点/要素同步到蓝本库「{title}」（{bp_id}）。",
        [],
    )


