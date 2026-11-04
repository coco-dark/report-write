"""蓝本相关能力统一为 action（按钮与口语预路由共用）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatPhase, ChatSession

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]

# 与前端 sendAction / UI_SHORTCUT 对齐
BLUEPRINT_ACTION_NAMES = frozenset(
    {
        "list_blueprints",
        "match_blueprints",
        "select_blueprint",
        "skip_blueprint",
        "begin_blueprint_wizard",
        "blueprint_wizard",
        "resume",
        "save_freeform_as_blueprint",
    }
)


def finish_blueprint_match(
    home: Path,
    session: ChatSession,
    message: str,
    agent_reply: str,
    matches: list,
    *,
    invoke: InvokeFn | None = None,
    emit: EmitFn | None = None,
) -> tuple[str, list[Any]]:
    from report_writing.chat.cards import _blueprint_list_card, _skip_blueprint_card
    from report_writing.chat.helpers import _emit_process
    from report_writing.chat.intent_router import allows_agent_blueprint_select, pick_auto_blueprint_id, touch_session_plan
    from report_writing.chat_orchestrator import _action_select_blueprint

    auto_id = ""
    if allows_agent_blueprint_select(message=message, user_action="", invoke=invoke, session=session):
        auto_id = pick_auto_blueprint_id(message, matches, invoke=invoke)
    if auto_id:
        _emit_process(emit, "已按你的描述选定蓝本，正在准备章节…")
        reply, cards = _action_select_blueprint(home, session, {"blueprint_id": auto_id})
        touch_session_plan(
            session,
            goal="write_with_blueprint",
            blueprint_id=auto_id,
            auto_selected=True,
        )
        title = next((getattr(m, "title", "") for m in matches if getattr(m, "id", "") == auto_id), auto_id)
        hint = f"好，我已经帮你订上「{title or auto_id}」了。{reply.strip()}".strip()
        from report_writing.chat.session_documents import append_doc_name_clarify_if_needed

        hint, cards = append_doc_name_clarify_if_needed(
            session,
            message=message,
            reply=hint,
            cards=cards,
            invoke=invoke,
        )
        return hint, cards

    if matches:
        session.phase = ChatPhase.BLUEPRINT_SELECT.value
        touch_session_plan(
            session, goal="pick_blueprint", candidates=[getattr(m, "id", "") for m in matches]
        )
        # 列表卡 + 多轮澄清：点选用或口语「用第二份」都能续上
        from report_writing.chat.action_slots import build_action_slot_clarify

        clarify_reply, clarify_cards = build_action_slot_clarify(
            session,
            "select_blueprint",
            {"message": message},
            ["blueprint_id"],
            message=message,
            blueprints=matches,
        )
        cards = list(clarify_cards or []) + [
            _blueprint_list_card(matches),
            _skip_blueprint_card(),
        ]
        hint = (
            (agent_reply or f"我帮你挑了 {len(matches)} 份可能合适的。").rstrip()
            + " "
            + (clarify_reply or "看中哪份点选，或说「用第几份」；不要蓝本就点跳过。")
        )
        from report_writing.chat.session_documents import append_doc_name_clarify_if_needed, is_writing_document_named

        hint, cards = append_doc_name_clarify_if_needed(
            session,
            message=message,
            reply=hint.strip(),
            cards=cards,
            invoke=invoke,
        )
        if not is_writing_document_named(session):
            hint = hint.strip()
        return hint, cards

    from report_writing.chat.entry_intent import extract_wizard_title_hint, is_create_blueprint_intent
    from report_writing.chat.entry_sample import guess_report_title_from_message, user_brings_own_sample_document
    from report_writing.chat.resource_actions import _action_blueprint_wizard, _begin_blueprint_wizard

    if user_brings_own_sample_document(
        message, invoke=invoke, session=session
    ) or is_create_blueprint_intent(message, invoke=invoke, session=session):
        title = extract_wizard_title_hint(
            message, invoke=invoke, session=session
        ) or guess_report_title_from_message(message, invoke=invoke)
        _emit_process(emit, "你这边有样板，咱们走上传样本建蓝本…")
        if title:
            reply, cards = _action_blueprint_wizard(
                home,
                session,
                {"step": "meta", "title": title, "blueprint_id": ""},
            )
            hint = (
                f"库里没有完全对口的蓝本，我按你说的「{title}」开了蓝本向导。"
                "请在卡片里上传 Word/PDF 样板，再提取目录、分析要点。"
            )
            return f"{hint}\n{reply}".strip(), cards
        reply, cards = _begin_blueprint_wizard(session, title_hint="", home=home)
        return (
            "没找到库里的对口蓝本。你既然有样板，请在向导里上传样本，我会按样板提取目录再写。"
            f"\n{reply}".strip(),
            cards,
        )

    from report_writing.chat.user_process import emit_user_process
    from report_writing.chat.write_path_after_match import reply_ask_write_path

    emit_user_process(emit, "没在库里找到特别对口的蓝本，想先确认你怎么开始…")
    return reply_ask_write_path(session, message, invoke=invoke)


def run_blueprint_action(
    home: Path,
    session: ChatSession,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    invoke: InvokeFn | None = None,
    emit: EmitFn | None = None,
) -> tuple[str, list[Any]]:
    key = (action or "").strip()
    body = dict(payload or {})
    if key not in BLUEPRINT_ACTION_NAMES:
        raise ValueError(f"未知蓝本 action：{key}")

    if key == "list_blueprints":
        from report_writing.chat_orchestrator import _action_list_blueprints

        return _action_list_blueprints(home, session, body)

    if key == "match_blueprints":
        from report_writing.chat.intent_router import user_chose_skip_blueprint
        from report_writing.chat_orchestrator import _action_skip_blueprint
        from report_writing.services.robots import match_blueprints_with_agent

        msg = str(body.get("message") or "").strip()
        if user_chose_skip_blueprint(session):
            return _action_skip_blueprint(session, home=home, invoke=invoke, emit=emit)
        agent_reply, matches = match_blueprints_with_agent(
            home,
            message=msg,
            robot_id=session.robot_id,
            invoke=invoke or (lambda _s, _u: ""),
        )
        return finish_blueprint_match(
            home,
            session,
            msg,
            agent_reply,
            matches,
            invoke=invoke,
            emit=emit,
        )

    if key == "select_blueprint":
        from report_writing.chat_orchestrator import _action_select_blueprint

        return _action_select_blueprint(home, session, body)

    if key == "skip_blueprint":
        from report_writing.chat_orchestrator import _action_skip_blueprint

        return _action_skip_blueprint(
            session,
            home=home,
            invoke=invoke,
            emit=emit,
            title_hint=str(body.get("title_hint") or body.get("title") or "").strip(),
            extra_message=str(body.get("message") or body.get("user_message") or "").strip(),
            path_setup_only=bool(body.get("path_setup_only") or body.get("defer_outline")),
        )

    if key == "begin_blueprint_wizard":
        from report_writing.chat.resource_actions import _begin_blueprint_wizard

        title_hint = str(
            body.get("title_hint")
            or body.get("title")
            or body.get("blueprint_title")
            or ""
        ).strip()
        user_message = str(body.get("user_message") or body.get("message") or "").strip()
        confirmed = bool(
            body.get("confirmed_create")
            or body.get("from_entry_path")
            or body.get("explicit_create")
        )
        if not confirmed:
            from report_writing.chat.intent_clarify import build_entry_path_clarify

            return build_entry_path_clarify(
                session,
                message=user_message,
                title_hint=title_hint,
                invoke=invoke,
                home=home,
                emit=emit,
            )
        return _begin_blueprint_wizard(
            session,
            title_hint=title_hint,
            home=home,
            force_new=bool(body.get("force_new", True)),
            user_message=user_message,
            invoke=invoke,
        )

    if key == "blueprint_wizard":
        from report_writing.chat.resource_actions import _action_blueprint_wizard

        return _action_blueprint_wizard(home, session, body)

    if key == "resume":
        from report_writing.chat_orchestrator import _action_resume

        return _action_resume(home, session, body)

    if key == "save_freeform_as_blueprint":
        from report_writing.chat_orchestrator import _action_save_freeform_as_blueprint

        return _action_save_freeform_as_blueprint(home, session, body, invoke=invoke, emit=emit)

    raise ValueError(f"未实现的蓝本 action：{key}")
