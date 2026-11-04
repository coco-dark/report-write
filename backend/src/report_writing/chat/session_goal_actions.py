"""会话目标 action：与 UI sendAction / 口语预路由同名。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatPhase, ChatSession

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

SESSION_GOAL_ACTION_NAMES = frozenset(
    {
        "set_session_meta",
        "remember_writing_goal",
    }
)

def run_session_goal_action(
    home: Path,
    session: ChatSession,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    invoke: InvokeFn | None = None,
    emit: EmitFn | None = None,
) -> tuple[str, list[Any]]:
    key = (action or "").strip()
    if key not in SESSION_GOAL_ACTION_NAMES:
        raise ValueError(f"unknown session_goal action: {key}")
    body = dict(payload or {})

    if key == "remember_writing_goal":
        return _remember_writing_goal(home, session, body, invoke=invoke)

    return _set_session_meta(home, session, body, invoke=invoke, emit=emit)

def _intent_invoke(invoke: InvokeFn | None) -> InvokeFn | None:
    if invoke is None:
        return None
    from report_writing.chat.intent_invoke import intent_invoke_fn

    return intent_invoke_fn(invoke)

def _remember_writing_goal(
    home: Path,
    session: ChatSession,
    payload: dict[str, Any],
    *,
    invoke: InvokeFn | None,
) -> tuple[str, list[Any]]:
    from report_writing.chat.session_goal import apply_session_goal_to_task, remember_from_user_message
    from report_writing.services.store import TaskStore

    notes = str(payload.get("notes") or payload.get("message") or "").strip()
    llm = _intent_invoke(invoke)
    reply = remember_from_user_message(session, notes, invoke=llm)
    tid = (session.task_id or "").strip()
    if tid and home:
        from report_writing.chat.helpers import _load_task

        task = _load_task(home, tid)
        if task is not None and apply_session_goal_to_task(session, task, force=True):
            TaskStore(home / "tasks" / tid).save(task)
            from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

            invalidate_doc_outline_cache(tid)
    return reply, []

def _freeform_outline_chapter_count(home: Path | None, session: ChatSession) -> int:
    tid = (session.task_id or "").strip()
    if not tid or home is None:
        return 0
    from report_writing.chat.helpers import _load_task

    task = _load_task(home, tid)
    if task is None or not task.outline:
        return 0
    return len(list(task.outline.chapters or []))

def _seed_meta_patch_from_payload(
    payload: dict[str, Any],
    message: str,
    *,
    invoke: InvokeFn | None,
    session: ChatSession | None = None,
) -> dict[str, str]:
    """UI/工具显式 title、category 优先；LLM/管道解析与纯标题回退只补空缺。不用改名正则。"""
    from report_writing.chat.helpers import is_vague_freeform_meta
    from report_writing.chat.session_goal import parse_goal_patch_from_message

    category = str(payload.get("category") or "").strip()
    title = str(payload.get("title") or "").strip()
    patch: dict[str, str] = {}
    if category and not is_vague_freeform_meta(category, field=True):
        patch["doc_category"] = category
    if title and not is_vague_freeform_meta(title, field=True):
        patch["doc_title"] = title

    parsed = parse_goal_patch_from_message(message, invoke=invoke, session=session)
    if parsed.get("doc_category") and not patch.get("doc_category"):
        candidate_cat = str(parsed["doc_category"]).strip()
        if candidate_cat and not is_vague_freeform_meta(candidate_cat, field=True):
            patch["doc_category"] = candidate_cat
    if parsed.get("doc_title") and not patch.get("doc_title"):
        candidate = str(parsed["doc_title"]).strip()
        if candidate and not is_vague_freeform_meta(candidate, field=True):
            patch["doc_title"] = candidate
    for key in ("summary", "project", "notes"):
        if parsed.get(key) and not patch.get(key):
            patch[key] = str(parsed[key]).strip()

    # 只发干净题目、无「类型｜标题」——整句当标题（含改名指令词的句子不当标题）
    if not patch.get("doc_title"):
        text = (message or "").strip()
        if text and "｜" not in text and "|" not in text:
            if not is_vague_freeform_meta(text, field=True):
                patch["doc_title"] = text[:160]
    if patch.get("doc_category") or patch.get("doc_title"):
        cat = patch.get("doc_category") or ""
        tit = patch.get("doc_title") or ""
        patch.setdefault("summary", f"{cat}｜{tit}".strip("｜"))
    return patch

def _set_session_meta(
    home: Path,
    session: ChatSession,
    payload: dict[str, Any],
    *,
    invoke: InvokeFn | None,
    emit: EmitFn | None,
) -> tuple[str, list[Any]]:
    from report_writing.chat.helpers import _emit_process
    from report_writing.chat.session_goal import merge_session_goal, sync_goal_into_freeform

    category = str(payload.get("category") or "").strip()
    title = str(payload.get("title") or "").strip()
    message = str(payload.get("message") or "").strip()
    from report_writing.chat.dialogue import message_repeats_assistant_reply
    from report_writing.chat.session_documents import is_writing_document_named

    # 新开文档刷新后，模型可能把助手原文当成用户原话再调本工具。
    if message_repeats_assistant_reply(session, message):
        if is_writing_document_named(session):
            return "刚才那句是我的回复，不会拿来当文档名。要改名直接说「名字叫…」。", []
        from report_writing.chat.intent_clarify import build_document_name_clarify

        return build_document_name_clarify(
            session,
            message="",
            title_hint="",
            home=home,
            emit=emit,
            follow_entry=False,
        )
    # 有显式字段时拼出可解析行；不要覆盖用户原话（便于纯标题回退）
    if not message and (category or title):
        message = f"{category}｜{title}".strip("｜")
    # 调度层偶发空 args：仍不应回固定「跟我说一下…」，由调用方补 message；双保险
    if not message:
        return "跟我说一下文档叫什么、写哪一类就行（口语或「类型｜标题」都可以）。", []

    llm = _intent_invoke(invoke)

    from report_writing.chat.session_documents import (
        focus_has_active_writing_context,
        focus_needs_doc_name,
        is_writing_document_named,
        quick_title_from_write_message,
        silent_rename_blocked_topic,
        try_confirm_document_name_from_message,
        write_subject_on_active_doc_should_clarify,
    )

    raw_message = str(payload.get("message") or "").strip()
    gate_msg = raw_message or message
    topic_hint = (title or "").strip() or quick_title_from_write_message(gate_msg)

    from report_writing.chat.session_documents import (
        ensure_write_onboarding_started,
        message_confirms_write_document_intent,
    )

    # 活跃焦点 / 已定名 + 写题材句：先判作用域，禁止先落工作名再「题材已对齐」
    need_scope_clarify = False
    scope_topic = topic_hint
    if is_writing_document_named(session) and not focus_needs_doc_name(session):
        blocked = silent_rename_blocked_topic(session, gate_msg, title=topic_hint)
        if blocked:
            need_scope_clarify = True
            scope_topic = blocked
    elif focus_has_active_writing_context(session) and gate_msg:
        blocked = silent_rename_blocked_topic(session, gate_msg, title=topic_hint)
        if blocked or write_subject_on_active_doc_should_clarify(
            session, gate_msg, title_hint=topic_hint, invoke=llm
        ):
            need_scope_clarify = True
            scope_topic = blocked or topic_hint
    if need_scope_clarify:
        from report_writing.chat.intent_clarify import build_document_scope_clarify

        return build_document_scope_clarify(
            session,
            message=gate_msg,
            topic_hint=scope_topic or topic_hint,
        )

    if home and message_confirms_write_document_intent(
        gate_msg, session=session, invoke=llm
    ):
        ensure_write_onboarding_started(
            session, home, gate_msg, invoke=llm, emit=emit
        )

    if focus_needs_doc_name(session):
        from report_writing.chat.helpers import is_vague_freeform_meta
        from report_writing.chat.session_documents import (
            confirm_writing_document_name,
            explicit_doc_title_from_message,
            message_wants_own_template,
            title_is_write_subject_not_doc_name,
            title_rejects_as_document_name,
        )

        # 入口若用 category｜title 拼出伪 message，不算用户原话
        raw_message = str(payload.get("message") or "").strip()
        declared = try_confirm_document_name_from_message(
            session, raw_message or message, title_hint=title, home=home
        )
        # 「写一份安全报告 / 用我的模版」：题材 title 禁止静默定名
        # 无真实原话时也不要用结构化 title 盲定名（否则空 message + title=安全报告会直接落盘）
        if (
            not declared
            and title
            and raw_message
            and not is_vague_freeform_meta(title, field=True)
            and not title_is_write_subject_not_doc_name(raw_message, title)
            and not title_rejects_as_document_name(title, message=raw_message)
            and not write_subject_on_active_doc_should_clarify(
                session, raw_message, title_hint=title, invoke=llm
            )
        ):
            confirm_writing_document_name(
                session, title, home=home, message=raw_message or title
            )
            declared = title
        # 改名句未带结构化 title：确定性抽出新名后直接定名，勿挂「起个名字」
        if not declared:
            extracted = explicit_doc_title_from_message(raw_message or message)
            if extracted:
                confirm_writing_document_name(
                    session, extracted, home=home, message=(raw_message or message) or extracted
                )
                declared = extracted
        if not declared:
            from report_writing.chat.session_snapshot import looks_like_freeform_meta_line

            if looks_like_freeform_meta_line(raw_message or message):
                structured_title = title or _seed_meta_patch_from_payload(
                    payload, raw_message or message, invoke=llm, session=session
                ).get("doc_title")
                if structured_title and not is_vague_freeform_meta(
                    structured_title, field=True
                ):
                    confirm_writing_document_name(
                        session,
                        structured_title,
                        home=home,
                        message=raw_message or message,
                    )
                    declared = structured_title
        if declared:
            from report_writing.chat.session_documents import session_blueprint_already_selected

            if session_blueprint_already_selected(session):
                return (
                    f"好，文档就叫「{declared}」。蓝本已选好，在文档窗口继续选章或开始分析就行。",
                    [],
                )
            # 无蓝本：定名后继续走目录，不要只回「好，文档就叫」
        else:
            from report_writing.chat.session_documents import apply_working_title

            subject = ""
            if title and title_is_write_subject_not_doc_name(raw_message or message, title):
                subject = title
            if not subject:
                subject = quick_title_from_write_message(gate_msg)
            if subject:
                apply_working_title(session, subject, home=home, message=gate_msg)
                return (
                    f"先按「{subject}」来写，正式名字之后还能改。",
                    [],
                )
            from report_writing.chat.intent_clarify import build_document_name_clarify

            tip = title or str((session.freeform or {}).get("title") or "").strip()
            gate_msg = raw_message or message
            if not raw_message:
                tip = ""
            elif tip and message_wants_own_template(gate_msg) and not explicit_doc_title_from_message(
                gate_msg
            ):
                tip = ""
            return build_document_name_clarify(
                session,
                message=raw_message or message,
                title_hint=tip,
                invoke=llm,
                home=home,
                emit=emit,
                follow_entry=False,
            )

    patch = _seed_meta_patch_from_payload(
        payload, message, invoke=llm, session=session
    )
    if patch.get("doc_category") or patch.get("doc_title"):
        if not is_writing_document_named(session):
            merge_session_goal(session, patch)
            sync_goal_into_freeform(session)

    phase = str(session.phase or "")
    skip_path = False
    from report_writing.chat.session_state import user_chose_skip_blueprint

    if user_chose_skip_blueprint(session):
        skip_path = True

    ff = dict(session.freeform or {})
    chapter_n = _freeform_outline_chapter_count(home, session)
    has_outline_progress = (
        chapter_n >= 1
        or bool(ff.get("outline_pending_edit"))
        or bool(ff.get("outline_confirmed"))
    )
    # 已有目录 / 已确认：改名只改名，勿走定题文案「帮我生成目录」，勿降级 phase
    if skip_path and has_outline_progress:
        from report_writing.chat.session_documents import apply_focused_document_title
        from report_writing.chat.session_goal import get_session_goal, session_goal_brief

        if not (patch.get("doc_category") or patch.get("doc_title")):
            from report_writing.chat.intent_clarify import build_session_meta_clarify
            from report_writing.chat.session_goal import get_session_goal

            goal = get_session_goal(session)
            return build_session_meta_clarify(
                session,
                current_title=str(goal.get("doc_title") or ff.get("title") or "").strip(),
                current_category=str(
                    goal.get("doc_category") or ff.get("category") or ""
                ).strip(),
                message=message,
            )
        goal = get_session_goal(session)
        doc_title = str(goal.get("doc_title") or title or "").strip()
        doc_cat = str(goal.get("doc_category") or category or "").strip()
        from report_writing.chat.session_documents import (
            explicit_doc_title_from_message,
            title_is_write_subject_not_doc_name,
        )

        # 「新写一份述职报告」只是题材。已有目录时也不许把它写成文档名。
        if (
            doc_title
            and message
            and not explicit_doc_title_from_message(message)
            and title_is_write_subject_not_doc_name(message, doc_title)
        ):
            from report_writing.chat.intent_clarify import build_document_scope_clarify

            return build_document_scope_clarify(
                session,
                message=message,
                topic_hint=doc_title,
            )
        if doc_title:
            apply_focused_document_title(session, doc_title, home=home, category=doc_cat)
        # 必须叠在改名之后的 freeform 上。ff 是改名前的快照，整份写回会把
        # 登记表标题冲回旧名，文档窗下拉就一直显示改名前的名字。
        ff_now = dict(session.freeform or {})
        session.freeform = {
            **ff_now,
            **({"category": doc_cat} if doc_cat else {}),
            **({"title": doc_title} if doc_title else {}),
            "meta_confirmed": True,
            "writing_doc_named": True,
        }
        brief = session_goal_brief(session)
        cards: list[Any] = []
        tid = (session.task_id or "").strip()
        if tid and chapter_n >= 1 and not ff.get("outline_confirmed"):
            from report_writing.chat.actions import _outline_preview_card
            from report_writing.chat.freeform_outline import chapters_to_outline_text
            from report_writing.chat.helpers import _load_task

            task = _load_task(home, tid)
            if task and task.outline and task.outline.chapters:
                outline_text = chapters_to_outline_text(
                    [
                        {"sequence": n.sequence, "title": n.title}
                        for n in task.outline.chapters
                    ]
                )
                cards = [
                    _outline_preview_card(
                        tid,
                        task,
                        outline_text,
                        editable=True,
                        session=session,
                    )
                ]
        name_bit = f"「{brief}」" if brief else "新名称"
        if chapter_n >= 1 and not ff.get("outline_confirmed"):
            return (
                f"好，文档名已更新为{name_bit}。目录仍在文档窗口（共 {chapter_n} 章），"
                "满意就点「确认大纲并继续」。",
                cards,
            )
        return (
            f"好，文档名已更新为{name_bit}。可在文档窗口继续改配置或开写。",
            cards,
        )

    if phase in {ChatPhase.FREEFORM_ASK.value, ChatPhase.FREEFORM_OUTLINE.value, ""} or (
        skip_path and not has_outline_progress
    ):
        _emit_process(emit, "正在整理文档类型和标题…")
        from report_writing.chat.actions import _collect_freeform_meta

        return _collect_freeform_meta(session, message, home=home, invoke=invoke, emit=emit)

    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        is_writing_document_named,
    )
    from report_writing.chat.session_goal import get_session_goal, session_goal_brief

    goal = get_session_goal(session)
    doc_title = str(goal.get("doc_title") or title or "").strip()
    doc_cat = str(goal.get("doc_category") or category or "").strip()
    if doc_title:
        from report_writing.chat.session_documents import (
            _titles_conflict,
            clean_document_display_title,
            confirm_writing_document_name,
            ensure_named_freeform_document,
            focus_document_key,
            focus_needs_doc_name,
            session_documents_map,
            should_mint_new_document_slot,
        )
        from report_writing.chat.intent_clarify import build_document_name_clarify
        from report_writing.chat.session_documents import try_confirm_document_name_from_message

        if focus_needs_doc_name(session):
            declared = try_confirm_document_name_from_message(
                session, message, title_hint=doc_title, home=home
            )
            if declared:
                return f"好，文档就叫「{declared}」。", []
            return build_document_name_clarify(
                session,
                message=message or doc_title,
                title_hint=doc_title,
                invoke=invoke,
                home=home,
                emit=emit,
                follow_entry=False,
            )

        if not is_writing_document_named(session):
            confirm_writing_document_name(
                session,
                doc_title,
                home=home,
                category=doc_cat,
                message=message or doc_title,
            )
        else:
            docs = session_documents_map(session)
            focus_key = focus_document_key(session)
            entry = docs.get(focus_key) or {}
            entry_title = clean_document_display_title(str(entry.get("title") or ""))
            if entry_title and _titles_conflict(entry_title, doc_title):
                if not should_mint_new_document_slot(
                    session, entry_title, doc_title, message=message or doc_title
                ):
                    confirm_writing_document_name(
                        session,
                        doc_title,
                        home=home,
                        category=doc_cat,
                        message=message or doc_title,
                    )
                else:
                    ensure_named_freeform_document(
                        session, home, title=doc_title, category=doc_cat
                    )
            else:
                confirm_writing_document_name(
                    session,
                    doc_title,
                    home=home,
                    category=doc_cat,
                    message=message or doc_title,
                )

    from report_writing.chat.session_documents import (
        focus_needs_entry_path,
        session_blueprint_already_selected,
    )

    brief = session_goal_brief(session)
    tip = doc_title or (brief.split("：")[-1].strip() if brief else "")
    if focus_needs_entry_path(session) and not session_blueprint_already_selected(session):
        from report_writing.chat.intent_clarify import build_entry_path_clarify

        reply2, cards2 = build_entry_path_clarify(
            session,
            message=message or tip,
            title_hint=tip,
            invoke=invoke,
            home=home,
            emit=emit,
            after_doc_name_confirm=True,
            apply_plan=False,
        )
        head = (
            f"好，题目记下了：{brief}。文档窗口会显示这个名字。"
            if brief
            else "好，已记录文档类型与标题。"
        )
        body = (reply2 or "").strip()
        if body and body not in head:
            return f"{head}\n{body}".strip(), list(cards2 or [])
        return head, list(cards2 or [])
    if brief:
        return f"好，题目记下了：{brief}。文档窗口会显示这个名字。", []
    return "好，已记录文档类型与标题。", []
