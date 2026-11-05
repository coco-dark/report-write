"""可选遗留：整轮 JSON 计划（slots/tools/missing）。

默认 `features.intent_plan=false` 时不参与口语路径；理解+调 Tool 由 create_agent 完成。
仍供 UI·clarify·单测与 `REPORT_WRITING_INTENT_PLAN=1` 时使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatCard, ChatSession

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

HIGH_CONFIDENCE = 0.72
MIN_PLAN_CONFIDENCE = 0.55


def _append_unique_reply(replies: list[str], text: str) -> None:
    r = str(text or "").strip()
    if r and (not replies or replies[-1] != r):
        replies.append(r)


ALLOWED_MISSING = frozenset(
    {
        "doc_name",
        "writing_path",
        "freeform_mode",
        "chapter_key",
        "blueprint_id",
        "title",
        "entry_path",
        "entry_branch",
        "write_scope",
        "study_scope",
        "element_id",
        "pack_id",
    }
)
_MISSING_ALIAS = {
    "title": "doc_name",
    "entry_path": "writing_path",
    "entry_branch": "writing_path",
    "name": "doc_name",
    "chapter": "chapter_key",
    "chapter_key_hint": "chapter_key",
    "scope": "write_scope",
}
_WRITING_PATH_ALIAS = {
    "match": "match",
    "match_blueprints": "match",
    "with_blueprint": "match",
    "template": "template",
    "create_blueprint": "template",
    "begin_blueprint_wizard": "template",
    "freeform": "freeform",
    "auto_outline": "freeform",
    "skip_blueprint": "freeform",
    "without_blueprint": "without_blueprint",
}

# 低置信 / 不确定时禁止静默执行
HARD_TOOLS = frozenset(
    {
        "start_writing",
        "retry_chapter",
        "select_write_chapters",
        "skip_blueprint",
        "begin_blueprint_wizard",
        "select_blueprint",
        "remove_document_element",
        "collect_to_element_library",
        "save_freeform_as_blueprint",
        "sync_document_to_blueprint_library",
        "confirm_chapter",
        "confirm_all_chapters",
        "materials_pack_unbind",
        "materials_pack_remove_slot",
    }
)
_CLARIFY_ONLY_TOOLS = frozenset({"ask_entry_path", "ask_intent"})


@dataclass
class TurnPlan:
    confidence: float = 0.0
    uncertain: bool = False
    slots: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def should_execute(self) -> bool:
        return (not self.uncertain) and self.confidence >= HIGH_CONFIDENCE and bool(self.tools)

    @property
    def should_clarify_only(self) -> bool:
        return bool(self.uncertain) or self.confidence < HIGH_CONFIDENCE


def parse_turn_plan(
    data: dict[str, Any] | None,
    *,
    allowed_tools: set[str] | frozenset[str] | None = None,
) -> TurnPlan:
    """从分类器 JSON 抽出 slots / tools / missing / uncertain（不执行）。"""
    from report_writing.chat.intent_action_chain import (
        CHAT_CONTROL_PLAN_BLOCKLIST,
        CHAT_CONTROL_PLAN_POLICY,
        order_planned_tool_steps,
        parse_planned_tool_steps,
    )

    body = dict(data or {})
    try:
        confidence = float(body.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    uncertain = bool(body.get("uncertain"))
    slots = _normalize_slots(body.get("slots") if isinstance(body.get("slots"), dict) else {})
    missing = _normalize_missing(body.get("missing"))
    allowed = set(allowed_tools or ()) or {
        k
        for k in CHAT_CONTROL_PLAN_POLICY.pipeline_rank
        if k not in CHAT_CONTROL_PLAN_BLOCKLIST
    }
    steps = parse_planned_tool_steps(body)
    steps = [s for s in steps if str(s.get("tool") or "") in allowed]
    steps = [s for s in steps if str(s.get("tool") or "") not in CHAT_CONTROL_PLAN_BLOCKLIST]
    steps = order_planned_tool_steps(steps, CHAT_CONTROL_PLAN_POLICY)
    return TurnPlan(
        confidence=confidence,
        uncertain=uncertain,
        slots=slots,
        tools=steps,
        missing=missing,
        raw=body,
    )


def _normalize_slots(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    name = raw.get("doc_name") if raw.get("doc_name") not in (None, "") else raw.get("title")
    if name not in (None, ""):
        out["doc_name"] = str(name).strip()[:160]
    path = raw.get("writing_path") if raw.get("writing_path") not in (None, "") else raw.get("entry_path")
    if path not in (None, ""):
        key = _WRITING_PATH_ALIAS.get(str(path).strip(), "")
        if key:
            out["writing_path"] = key
    mode = raw.get("freeform_mode")
    if mode not in (None, ""):
        out["freeform_mode"] = str(mode).strip()
    chapter = raw.get("chapter_key") or raw.get("chapter_key_hint")
    if chapter not in (None, ""):
        out["chapter_key"] = str(chapter).strip()
    bid = raw.get("blueprint_id")
    if bid not in (None, ""):
        out["blueprint_id"] = str(bid).strip()
    return out


def _normalize_missing(raw: Any) -> list[str]:
    items: list[str] = []
    if isinstance(raw, str) and raw.strip():
        items = [raw.strip()]
    elif isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = _MISSING_ALIAS.get(item, item)
        canon = _MISSING_ALIAS.get(key, key)
        if canon not in ALLOWED_MISSING:
            continue
        if canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
    return out


def apply_declared_slots(session: ChatSession, plan: TurnPlan, *, home: Path | None = None, message: str = "") -> None:
    """把 JSON slots 落到会话（定名 / 偏好线路），不 dispatch Tool。"""
    from report_writing.chat.session_documents import (
        confirm_writing_document_name,
        focus_needs_doc_name,
        stash_or_commit_entry_path,
        title_is_write_subject_not_doc_name,
    )

    name = str(plan.slots.get("doc_name") or "").strip()
    msg = (message or "").strip()
    from report_writing.chat.session_documents import write_subject_on_active_doc_should_clarify

    # 「写一份安全报告」：题材落工作名，不正式定名
    if name and msg and title_is_write_subject_not_doc_name(msg, name):
        from report_writing.chat.session_documents import apply_working_title

        apply_working_title(session, name, home=home, message=msg)
    # 「安全报告 用我的模版」：slots.doc_name=安全报告 也不得静默定名；无原话时也不盲确认
    elif (
        name
        and msg
        and not plan.uncertain
        and not write_subject_on_active_doc_should_clarify(session, msg, title_hint=name)
    ):
        from report_writing.chat.session_documents import (
            _titles_conflict,
            begin_document_onboarding,
            clean_document_display_title,
            explicit_doc_title_from_message,
            focus_document_key,
            is_writing_document_named,
            session_documents_map,
        )

        # 已有定名文档 + 显式新题名冲突 → force_new（名字叫… 不再短路成改旧文档）
        explicit = explicit_doc_title_from_message(msg) or name
        if is_writing_document_named(session) and explicit:
            docs = session_documents_map(session)
            focus = focus_document_key(session)
            entry = dict(docs.get(focus) or {})
            entry_title = clean_document_display_title(str(entry.get("title") or ""))
            if entry_title and _titles_conflict(entry_title, explicit):
                begin_document_onboarding(
                    session,
                    home,
                    message=msg,
                    title_hint=explicit,
                    force_new=True,
                )
        if explicit_doc_title_from_message(msg) or focus_needs_doc_name(session):
            confirm_writing_document_name(session, name, home=home, message=msg)
    path = str(plan.slots.get("writing_path") or "").strip()
    mode = str(plan.slots.get("freeform_mode") or "").strip()
    if path in {"match", "template", "freeform"}:
        stash_or_commit_entry_path(session, path)
    elif path == "without_blueprint":
        from report_writing.chat.session_documents import mark_focus_entry_branch

        if not focus_needs_doc_name(session):
            mark_focus_entry_branch(session, "without_blueprint")
        if mode == "auto_outline":
            stash_or_commit_entry_path(session, "freeform")
        elif mode == "template":
            stash_or_commit_entry_path(session, "template")
    elif mode == "auto_outline":
        stash_or_commit_entry_path(session, "freeform")
    elif mode == "template":
        stash_or_commit_entry_path(session, "template")


def remaining_missing(session: ChatSession, plan: TurnPlan) -> list[str]:
    """以 JSON missing 为准，去掉本轮已经满足、或并非当前 Tool 前置的槽。"""
    from report_writing.chat.session_documents import (
        focus_needs_doc_name,
        focus_needs_freeform_mode,
    )
    from report_writing.chat.tool_capability import filter_plan_missing

    filtered: list[str] = []
    for key in filter_plan_missing(session, list(plan.missing or []), tools=plan.tools):
        if key == "doc_name" and not focus_needs_doc_name(session):
            continue
        if key == "freeform_mode" and not focus_needs_freeform_mode(session):
            continue
        if key == "chapter_key" and plan.slots.get("chapter_key"):
            continue
        if key == "blueprint_id" and (
            plan.slots.get("blueprint_id") or str(session.blueprint_id or "").strip()
        ):
            continue
        if key in {"write_scope", "study_scope", "element_id", "pack_id"} and plan.slots.get(key):
            continue
        if key not in filtered:
            filtered.append(key)
    return filtered


def _coalesce_plan_steps_for_onboarding(
    steps: list[dict[str, Any]], session: ChatSession
) -> list[dict[str, Any]]:
    """match 已在本轮时勿同轮再 ask_entry_path（避免选蓝本+入口+定名卡叠满屏）。"""
    del session
    names = {str(s.get("tool") or "").strip() for s in (steps or [])}
    if not names.intersection({"match_blueprints", "list_blueprints", "select_blueprint"}):
        return list(steps or [])
    drop = frozenset({"ask_entry_path", "ask_intent"})
    return [s for s in steps if str(s.get("tool") or "").strip() not in drop]


def hang_missing_slots(
    session: ChatSession,
    missing: list[str],
    *,
    message: str = "",
    title_hint: str = "",
    invoke: InvokeFn | None = None,
    home: Path | None = None,
    emit: EmitFn | None = None,
    uncertain: bool = False,
    existing_cards: list[Any] | None = None,
) -> tuple[str, list[ChatCard]]:
    """按 missing 挂澄清卡；起名与选路分轮（先名后路），避免 pending 错配。"""
    from report_writing.chat.cards import turn_has_blueprint_pick_cards
    from report_writing.chat.intent_clarify import (
        _apply_auto_freeform_and_dispatch,
        _auto_freeform_outline_ready,
        begin_clarify,
        build_ask_intent_clarify,
        build_document_name_clarify,
        build_entry_branch_clarify,
        build_entry_freeform_mode_clarify,
    )

    replies: list[str] = []
    cards: list[ChatCard] = []
    if turn_has_blueprint_pick_cards(existing_cards, session=session):
        missing = [
            m
            for m in (missing or [])
            if m not in {"doc_name", "writing_path", "freeform_mode", "entry_path", "entry_branch"}
        ]
    want_name = "doc_name" in missing
    want_path = "writing_path" in missing
    want_mode = "freeform_mode" in missing

    if want_name:
        from report_writing.chat.session_documents import (
            ensure_write_onboarding_started,
            focus_needs_doc_name,
            is_writing_document_named,
            message_confirms_write_document_intent,
        )

        if (
            home
            and message
            and message_confirms_write_document_intent(message, session=session, invoke=invoke)
        ):
            ensure_write_onboarding_started(
                session, home, message, invoke=invoke, emit=emit
            )
        # 已定名 / 已有工作名：跳过起名卡，避免写意图被钉成定名仪式
        from report_writing.chat.session_documents import focus_has_display_title

        if (
            is_writing_document_named(session)
            or not focus_needs_doc_name(session)
            or focus_has_display_title(session)
        ):
            want_name = False
        else:
            # 只挂起名：同轮再挂选路会覆盖/错配 pending，点「无蓝本」会误报过期
            reply, name_cards = build_document_name_clarify(
                session,
                message=message,
                title_hint=title_hint,
                invoke=invoke,
                home=home,
                emit=emit,
                follow_entry=False,
            )
            if reply:
                replies.append(reply)
            cards.extend(list(name_cards or []))
            return "\n".join(replies).strip(), cards

    if want_path:
        reply, path_cards = build_entry_branch_clarify(
            session, message=message, title_hint=title_hint
        )
        return reply, list(path_cards or [])

    if want_mode:
        from report_writing.chat.cards import turn_has_outline_preview_cards

        if turn_has_outline_preview_cards(existing_cards):
            return build_entry_freeform_mode_clarify(
                session,
                message=message,
                title_hint=title_hint,
                home=home,
                invoke=invoke,
                emit=emit,
            )
        if _auto_freeform_outline_ready(session, message, invoke=invoke):
            auto = _apply_auto_freeform_and_dispatch(
                home,
                session,
                message=message,
                title_hint=title_hint,
                invoke=invoke,
                emit=emit,
            )
            if auto is not None:
                return auto
        return build_entry_freeform_mode_clarify(
            session,
            message=message,
            title_hint=title_hint,
            home=home,
            invoke=invoke,
            emit=emit,
        )

    if "chapter_key" in missing:
        return begin_clarify(
            session,
            source="action_slots",
            question="要操作哪一章？说章节号，或点选目录里的那一章。",
            options=[],
            candidate={
                "kind": "action_slots",
                "name": "chapter_key",
                "payload": {"message": (message or "")[:500]},
            },
            phase_hint="writing",
        )

    if "blueprint_id" in missing:
        return begin_clarify(
            session,
            source="control",
            question="要用哪一份蓝本？可以说名字，或先让我列出可选蓝本。",
            options=[
                {
                    "id": "list_blueprints",
                    "label": "列出可选蓝本",
                    "slots": {"tool": "list_blueprints"},
                }
            ],
            candidate={
                "kind": "entry_intent",
                "name": "list_blueprints",
                "payload": {"message": (message or "")[:500]},
            },
        )

    if "write_scope" in missing or "study_scope" in missing:
        kind = "study_scope" if "study_scope" in missing else "write_scope"
        return begin_clarify(
            session,
            source="control",
            question="范围怎么定？只要本章，还是含下面的子章节？",
            options=[
                {
                    "id": "self",
                    "label": "只要本章",
                    "slots": {kind: "self"},
                },
                {
                    "id": "whole",
                    "label": "整章含下属",
                    "slots": {kind: "whole"},
                },
            ],
            candidate={
                "kind": "action_slots",
                "name": kind,
                "payload": {"message": (message or "")[:500]},
            },
            phase_hint="writing",
        )

    if "element_id" in missing:
        return begin_clarify(
            session,
            source="control",
            question="要操作哪个要素？可以说名字，或打开要素库点选。",
            options=[
                {
                    "id": "open_element_library",
                    "label": "打开要素库",
                    "slots": {"tool": "open_element_library"},
                }
            ],
            candidate={
                "kind": "entry_intent",
                "name": "open_element_library",
                "payload": {"message": (message or "")[:500]},
            },
        )

    if "pack_id" in missing:
        return begin_clarify(
            session,
            source="control",
            question="要用哪个资料包？可以说名字，或先列出资料包。",
            options=[
                {
                    "id": "materials_pack_list",
                    "label": "列出资料包",
                    "slots": {"tool": "materials_pack_list"},
                }
            ],
            candidate={
                "kind": "entry_intent",
                "name": "materials_pack_list",
                "payload": {"message": (message or "")[:500]},
            },
        )

    if uncertain:
        return build_ask_intent_clarify(
            session, message=message, invoke=invoke, home=home, emit=emit
        )
    return "", []


def apply_turn_plan(
    home: Path,
    session: ChatSession,
    message: str,
    *,
    invoke: InvokeFn | None = None,
    emit: EmitFn | None = None,
    plan: TurnPlan | None = None,
) -> tuple[str, list[Any], list[dict[str, Any]]] | None:
    """JSON 契约执行：高置信执行 tools；缺槽挂卡；不确定只澄清。"""
    text = (message or "").strip()
    from report_writing.chat.session_gaps import observe_session_gaps, refresh_session_gaps

    gaps_snapshot = observe_session_gaps(session, text, home=home, invoke=invoke)
    refresh_session_gaps(session, gaps_snapshot)
    gaps_event = gaps_snapshot.to_event()

    if plan is None:
        if invoke is None or not text:
            return None
        from report_writing.chat.intent_llm import classify_turn_plan_llm

        plan = classify_turn_plan_llm(
            text,
            session=session,
            invoke=invoke,
            session_gaps=gaps_snapshot,
        )
    if plan.confidence <= 0 and not plan.tools and not plan.missing and not plan.uncertain:
        return None

    # 文档范围门闩：独立再调一次 scope 分类（新 system/user，勿复用 turn-plan JSON 解析）。
    from report_writing.chat.session_documents import (
        begin_document_onboarding,
        confirm_writing_document_name,
        explicit_doc_title_from_message,
        focus_has_active_writing_context,
        focus_needs_entry_path,
        is_writing_document_named,
        resolve_document_scope,
        silent_rename_blocked_topic,
        stash_pending_entry,
        title_is_write_subject_not_doc_name,
    )

    scope_resolved = str((session.freeform or {}).get("document_scope_resolved") or "").strip()
    scope_action = ""
    scope_title = str(plan.slots.get("doc_name") or "").strip()
    from report_writing.chat.intent_router import active_blueprint_wizard

    wiz_now = dict(active_blueprint_wizard(session) or {})
    wizard_busy = str(wiz_now.get("step") or "").strip() in {
        "meta",
        "upload",
        "outline",
        "outline_confirm",
        "upload_done",
        "study",
    }
    plan_tool_names = {str(s.get("tool") or "").strip() for s in (plan.tools or [])}
    wizard_plan = bool(
        plan_tool_names
        & {
            "skip_blueprint",
            "blueprint_wizard",
            "begin_blueprint_wizard",
        }
    )
    from report_writing.chat.session_documents import (
        message_wants_new_document_strong,
        message_wants_template_new_document,
    )

    explicit_new_doc = message_wants_new_document_strong(
        text
    ) or message_wants_template_new_document(session, text)
    if (
        scope_resolved not in {"continue", "rewrite_current", "force_new"}
        and (is_writing_document_named(session) or focus_has_active_writing_context(session))
        and text
        and not explicit_doc_title_from_message(text)
        and not (wizard_busy and not explicit_new_doc)
        and not wizard_plan
    ):
        scope = resolve_document_scope(
            session, text, title_hint=scope_title, invoke=invoke
        )
        scope_action = str(scope.get("action") or "").strip()
        scope_title = str(scope.get("title") or scope_title or "").strip()
        scope_events = [
            {
                "type": "document_scope",
                "action": scope_action,
                "title": scope_title,
                "source": scope.get("source"),
                "confidence": scope.get("confidence"),
            }
        ]
        if scope.get("need_clarify") or scope_action == "ask":
            from report_writing.chat.intent_clarify import (
                build_document_scope_clarify,
                pending_document_scope_reply,
            )

            pending_hit = pending_document_scope_reply(session)
            if pending_hit is not None:
                reply, cards = pending_hit
            else:
                reply, cards = build_document_scope_clarify(
                    session, message=text, topic_hint=scope_title
                )
            return (
                reply,
                list(cards or []),
                scope_events
                + [
                    {
                        "type": "turn_plan",
                        "uncertain": True,
                        "tools": [],
                        "missing": ["document_scope"],
                    }
                ],
            )
        if scope_action == "force_new" or scope.get("force_new"):
            from report_writing.chat.session_documents import (
                apply_working_title,
                quick_title_from_write_message,
            )

            begin_document_onboarding(
                session, home, message=text, title_hint=scope_title, force_new=True
            )
            working = scope_title or quick_title_from_write_message(text)
            if working:
                apply_working_title(session, working, home=home, message=text)
            stash_pending_entry(session, message=text, title=working)
            ff = dict(session.freeform or {})
            ff["document_scope_resolved"] = "force_new"
            session.freeform = ff
            prefix = (
                f"好，新开一份「{working}」。" if working else "好，新开一份文档。"
            )
            from report_writing.chat.intent_clarify import (
                continue_after_new_document,
            )

            extra_reply = ""
            extra_cards: list[Any] = []
            extra_events: list[Any] = []
            if home is not None:
                extra_reply, extra_cards, extra_events = continue_after_new_document(
                    home,
                    session,
                    message=text,
                    title=working,
                    invoke=invoke,
                    emit=emit,
                )
            ff2 = dict(session.freeform or {})
            ff2.pop("document_scope_resolved", None)
            session.freeform = ff2
            body = f"{prefix}\n{extra_reply}".strip() if extra_reply else prefix
            return (
                body,
                list(extra_cards or []),
                scope_events
                + list(extra_events or [])
                + [
                    {
                        "type": "turn_plan",
                        "uncertain": False,
                        "tools": ["force_new"],
                        "missing": [],
                    }
                ],
            )
        if scope_action == "chapter_revise":
            # 禁止整份开写；缺章则追问 chapter_key
            steps_preview = [
                str(s.get("tool") or "").strip() for s in (plan.tools or [])
            ]
            hard = {"start_writing", "select_write_chapters"}
            if not any(t == "retry_chapter" for t in steps_preview) or any(
                t in hard for t in steps_preview
            ):
                hang_reply, hang_cards = hang_missing_slots(
                    session,
                    ["chapter_key"],
                    message=text,
                    invoke=invoke,
                    home=home,
                    emit=emit,
                    uncertain=False,
                )
                return (
                    hang_reply or "要重写哪一章？可以说章节号，或在右侧勾选后重试。",
                    list(hang_cards or []),
                    scope_events
                    + [
                        {
                            "type": "turn_plan",
                            "uncertain": False,
                            "tools": [],
                            "missing": ["chapter_key"],
                        }
                    ],
                )
        if scope_action == "rewrite_current":
            from report_writing.chat.intent_clarify import build_entry_path_clarify

            ff = dict(session.freeform or {})
            ff["rewrite_current_pending"] = True
            ff["document_scope_resolved"] = "rewrite_current"
            session.freeform = ff
            reply, cards = build_entry_path_clarify(
                session,
                message=text,
                title_hint=scope_title,
                invoke=invoke,
                home=home,
                emit=emit,
                after_doc_name_confirm=True,
                apply_plan=False,
            )
            tip = "好，按当前这份整份重写。请勾选要重写的章节，或告诉我写哪些章。"
            ff2 = dict(session.freeform or {})
            ff2.pop("document_scope_resolved", None)
            session.freeform = ff2
            return (
                f"{tip}\n{reply}".strip() if reply else tip,
                list(cards or []),
                scope_events
                + [
                    {
                        "type": "turn_plan",
                        "uncertain": False,
                        "tools": [],
                        "missing": ["chapter_key"],
                    }
                ],
            )
        # continue / switch_existing：若计划带改名且题材会被静默改名，仍拦
        has_meta = any(
            str(s.get("tool") or "") in {"set_session_meta", "submit_freeform_meta"}
            for s in plan.tools
        )
        if has_meta and scope_action in {"", "continue"}:
            blocked = silent_rename_blocked_topic(session, text, title=scope_title)
            if blocked or (
                scope_title and title_is_write_subject_not_doc_name(text, scope_title)
            ):
                from report_writing.chat.intent_clarify import (
                    build_document_scope_clarify,
                    pending_document_scope_reply,
                )

                pending_hit = pending_document_scope_reply(session)
                if pending_hit is not None:
                    reply, cards = pending_hit
                else:
                    reply, cards = build_document_scope_clarify(
                        session, message=text, topic_hint=blocked or scope_title
                    )
                return (
                    reply,
                    list(cards or []),
                    scope_events
                    + [
                        {
                            "type": "turn_plan",
                            "uncertain": True,
                            "tools": [],
                            "missing": ["document_scope"],
                        }
                    ],
                )

    apply_declared_slots(session, plan, home=home, message=text)
    events: list[dict[str, Any]] = [
        gaps_event,
        {
            "type": "turn_plan",
            "confidence": plan.confidence,
            "uncertain": plan.uncertain,
            "tools": [str(s.get("tool") or "") for s in plan.tools],
            "missing": list(plan.missing),
        },
    ]

    replies: list[str] = []
    cards: list[Any] = []

    if plan.should_execute:
        from report_writing.chat.intent_dispatch import execute_intent_plan
        from report_writing.chat.intent_multi_plan import expand_plan_auto_continue

        steps = [dict(s) for s in expand_plan_auto_continue(list(plan.tools), text, session)]
        name = str(plan.slots.get("doc_name") or "").strip()
        from report_writing.chat.session_documents import (
            message_looks_like_new_write_topic,
            message_wants_new_document_strong,
            title_is_write_subject_not_doc_name,
        )

        # 新开/再写一份：计划里若误带 focus_document，禁止切回旧文档
        if message_wants_new_document_strong(text) or message_looks_like_new_write_topic(text):
            steps = [s for s in steps if str(s.get("tool") or "").strip() != "focus_document"]

        from report_writing.chat.session_documents import (
            explicit_doc_title_from_message,
            session_blueprint_already_selected,
        )

        if session_blueprint_already_selected(session) and explicit_doc_title_from_message(text):
            steps = [
                s for s in steps if str(s.get("tool") or "").strip() != "match_blueprints"
            ]

        # 题材误入 slots.doc_name 时勿注入 set_session_meta(title=…)
        inject_title = bool(name) and not title_is_write_subject_not_doc_name(text, name)
        if name or text:
            for step in steps:
                if str(step.get("tool") or "") in {"set_session_meta", "submit_freeform_meta"}:
                    args = dict(step.get("args") or {})
                    if inject_title:
                        args.setdefault("title", name)
                    elif title_is_write_subject_not_doc_name(
                        text, str(args.get("title") or "").strip()
                    ):
                        args.pop("title", None)
                    if text:
                        args.setdefault("message", text[:500])
                    step["args"] = args
        for step in steps:
            if str(step.get("tool") or "") == "begin_blueprint_wizard":
                args = dict(step.get("args") or {})
                if args.get("explicit_create") or args.get("confirmed_create") or args.get("from_entry_path"):
                    args.setdefault("confirmed_create", True)
                    args.setdefault("from_entry_path", True)
                elif args.get("explicit_create") is not False:
                    args.setdefault("explicit_create", True)
                    args.setdefault("confirmed_create", True)
                    args.setdefault("from_entry_path", True)
                step["args"] = args
        miss_now = remaining_missing(session, plan)
        if miss_now:
            steps = [
                s
                for s in steps
                if str(s.get("tool") or "").strip() not in _CLARIFY_ONLY_TOOLS
            ]
        if any(str(s.get("tool") or "") in HARD_TOOLS for s in steps) and plan.should_clarify_only:
            steps = [s for s in steps if str(s.get("tool") or "") not in HARD_TOOLS]
        if steps:
            steps = _coalesce_plan_steps_for_onboarding(steps, session)
            hit = execute_intent_plan(
                home, session, steps, text, emit=emit, invoke=invoke
            )
            if hit is not None:
                reply, step_cards, step_events = hit
                if reply:
                    _append_unique_reply(replies, str(reply).strip())
                cards.extend(list(step_cards or []))
                events.extend(list(step_events or []))

    miss = remaining_missing(session, plan)
    from report_writing.chat.cards import turn_has_blueprint_pick_cards

    if turn_has_blueprint_pick_cards(cards, session=session):
        miss = [
            m
            for m in miss
            if m
            not in {"doc_name", "writing_path", "freeform_mode", "entry_path", "entry_branch"}
        ]
    if plan.should_clarify_only and not miss and plan.tools:
        from report_writing.chat.intent_multi_plan import _hang_low_confidence_clarify

        if plan.confidence >= MIN_PLAN_CONFIDENCE:
            reply, step_cards, step_events = _hang_low_confidence_clarify(
                session, text, plan.tools, plan.confidence
            )
            return reply, list(step_cards or []), list(events) + list(step_events or [])
        # 低置信且无完整计划：整段会话统一追问，勿静默交给猜测
        hang_reply, hang_cards = hang_missing_slots(
            session,
            [],
            message=text,
            invoke=invoke,
            home=home,
            emit=emit,
            uncertain=True,
            existing_cards=cards,
        )
        if hang_reply or hang_cards:
            return hang_reply, list(hang_cards or []), events
        return None

    if miss or (plan.uncertain and not replies and not cards):
        from report_writing.chat.session_documents import title_is_write_subject_not_doc_name

        tip = str(plan.slots.get("doc_name") or (session.freeform or {}).get("title") or "").strip()
        if tip and title_is_write_subject_not_doc_name(text, tip):
            tip = ""
        hang_reply, hang_cards = hang_missing_slots(
            session,
            miss,
            message=text,
            title_hint=tip,
            invoke=invoke,
            home=home,
            emit=emit,
            uncertain=plan.uncertain or (not miss and not plan.tools),
            existing_cards=cards,
        )
        if hang_reply:
            _append_unique_reply(replies, hang_reply)
        cards.extend(list(hang_cards or []))

    if not replies and not cards:
        # 有置信度但空计划：中途也优先追问，避免 ReAct 瞎猜破坏性动作
        if plan.uncertain or (
            plan.confidence >= MIN_PLAN_CONFIDENCE
            and plan.confidence < HIGH_CONFIDENCE
            and not plan.tools
        ):
            hang_reply, hang_cards = hang_missing_slots(
                session,
                [],
                message=text,
                invoke=invoke,
                home=home,
                emit=emit,
                uncertain=True,
            )
            if hang_reply or hang_cards:
                return hang_reply, list(hang_cards or []), events
        return None
    from report_writing.chat.cards import collapse_duplicate_turn_cards

    refresh_session_gaps(session, message=text, home=home, invoke=invoke)
    return (
        "\n".join(r for r in replies if r).strip() or "好的。",
        collapse_duplicate_turn_cards(cards, session=session),
        events,
    )
