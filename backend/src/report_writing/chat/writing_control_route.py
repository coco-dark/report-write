"""写作控制执行层（口语主路径为 ReAct Tool）。

`apply_writing_control_classification` 供 Tool 与单测复用。
口语选哪个写作 Tool 归总控，不再先 LLM 分类再改道。
绑定 `expected_tool` 时若传入的分类结果是另一个 Tool，只返回 `suggested_next_tool` 事件，不执行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatActionPayload, ChatPayload, ChatSession

def _browse_element_items(
    home: Path, session: ChatSession
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """返回 (个人库条目, 本任务未入库条目, 空提示)。已入库/同内容克隆不进本任务列表。"""
    from report_writing.chat.helpers import _load_task
    from report_writing.services.element_library import (
        element_content_fingerprint,
        normalize_element_name,
        relink_task_elements_by_content,
    )
    from report_writing.services.robots import scoped_element_library_items

    library_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    linked_library_ids: set[str] = set()
    library_fps: set[str] = set()
    library_names: set[str] = set()
    lib_items = []
    try:
        rid = str(getattr(session, "robot_id", "") or "").strip()
        lib_items = list(scoped_element_library_items(home, rid)[:40])
    except Exception:
        lib_items = []
    for item in lib_items:
        eid = str(getattr(item, "id", "") or "").strip()
        if not eid:
            continue
        linked_library_ids.add(eid)
        fp = element_content_fingerprint(item)
        if fp:
            library_fps.add(fp)
        nk = normalize_element_name(str(getattr(item, "name", "") or "")).casefold()
        if nk:
            library_names.add(nk)
        library_rows.append(
            {
                "id": eid,
                "name": str(getattr(item, "name", "") or eid).strip(),
                "kind": str(getattr(item, "kind", "") or "").strip() or "kv",
                "category": str(getattr(item, "category", "") or "").strip(),
                "source": "library",
            }
        )

    tid = _session_writing_task_id(session)
    if tid:
        try:
            relink_task_elements_by_content(home, tid)
        except Exception:
            pass
    task = _load_task(home, tid) if tid else None
    if task is not None:
        for el in list(getattr(task, "element_nodes", None) or [])[:40]:
            eid = str(getattr(el, "id", "") or "").strip()
            if not eid:
                continue
            lib_id = str(getattr(el, "library_item_id", "") or "").strip()
            if lib_id and lib_id in linked_library_ids:
                continue
            fp = element_content_fingerprint(el)
            if fp and fp in library_fps:
                continue
            nk = normalize_element_name(str(getattr(el, "name", "") or "")).casefold()
            if nk and nk in library_names:
                continue
            task_rows.append(
                {
                    "id": eid,
                    "name": str(getattr(el, "name", "") or eid).strip(),
                    "kind": str(getattr(el, "kind", "") or "").strip() or "kv",
                    "category": str(getattr(el, "category", "") or "").strip(),
                    "source": "task",
                }
            )
    if not library_rows and not task_rows:
        return [], [], "暂无要素。可点下方进入要素库新建，或给章节添加要素。"
    return library_rows, task_rows, ""

def _browse_materials_pack_items(home: Path, session: ChatSession | None = None) -> tuple[list[dict[str, Any]], str]:
    from report_writing.services.robots import robot_for_session, scoped_materials_packs

    rows: list[dict[str, Any]] = []
    try:
        rid = ""
        if session is not None:
            robot = robot_for_session(home, session)
            rid = robot.id if robot else str(getattr(session, "robot_id", "") or "").strip()
        for pack in scoped_materials_packs(home, rid or None, in_library=True)[:40]:
            pid = str(getattr(pack, "id", "") or "").strip()
            if not pid:
                continue
            rows.append(
                {
                    "id": pid,
                    "name": str(getattr(pack, "name", "") or pid).strip(),
                    "kind": "materials_pack",
                    "category": "",
                    "source": "materials",
                }
            )
    except Exception:
        pass
    if not rows:
        return [], "资料库还没有已入库的资料包。可点下方打开资料库新建或确认入库。"
    return rows[:48], ""

def _browse_blueprint_items(
    home: Path, session: ChatSession
) -> tuple[list[dict[str, Any]], str]:
    """蓝本库摘要列表（浏览用，不进入选用流程）。"""
    rows: list[dict[str, Any]] = []
    try:
        from report_writing.services.robots import search_blueprints

        rid = str(getattr(session, "robot_id", "") or "").strip()
        found = search_blueprints(home, q="", robot_id=rid or "", enabled_only=False) or []
        for bp in found[:48]:
            bid = str(getattr(bp, "id", "") or "").strip()
            if not bid:
                continue
            outline = getattr(bp, "outline", None)
            n_ch = len(list(getattr(outline, "chapters", None) or [])) if outline else 0
            rows.append(
                {
                    "id": bid,
                    "name": str(getattr(bp, "title", "") or bid).strip(),
                    "kind": "blueprint",
                    "category": str(getattr(bp, "category", "") or "").strip(),
                    "source": "blueprint_library",
                    "chapter_count": n_ch,
                    "enabled": bool(getattr(bp, "enabled", True)),
                }
            )
    except Exception:
        pass
    if not rows:
        return [], "蓝本库还没有蓝本。可点下方打开蓝本库新建或从写作任务收录。"
    return rows[:48], ""


def _action_open_nav(
    home: Path,
    session: ChatSession,
    target: str,
) -> tuple[str, list[Any]]:
    from report_writing.chat.cards import _nav_action_card

    tid = str(target or "").strip()
    if tid in {"blueprints", "blueprint"}:
        items, empty = _browse_blueprint_items(home, session)
        n = len(items)
        hint = (
            f"蓝本库里有 {n} 个蓝本（摘要）。点下方打开完整蓝本库管理；选用写作仍用「匹配/列出蓝本」。"
            if n
            else empty
        )
        card = _nav_action_card(
            target="blueprints",
            title="查看蓝本库",
            hint=hint,
            items=items,
            total=n,
            empty_hint=empty,
        )
        if n:
            return f"蓝本库里有 {n} 个蓝本；点下方打开蓝本库查看与管理。", [card]
        return "蓝本库还是空的。可以点下面打开蓝本库新建，或写完后「收录到蓝本库」。", [card]
    if tid == "elements":
        library_rows, task_rows, empty = _browse_element_items(home, session)
        items = [*library_rows, *task_rows]
        n_lib, n_task = len(library_rows), len(task_rows)
        n = n_lib + n_task
        if n:
            parts = []
            if n_lib:
                parts.append(f"个人库 {n_lib} 个")
            if n_task:
                parts.append(f"本任务未入库 {n_task} 个")
            hint = "；".join(parts) + "。下方分组列出；改内容请打开要素库。"
        else:
            hint = empty
        card = _nav_action_card(
            target="elements",
            title="查看要素",
            hint=hint,
            items=items,
            total=n,
            empty_hint=empty,
            sections=[
                {"key": "library", "title": "个人要素库", "items": library_rows},
                {"key": "task", "title": "本任务（未入库）", "items": task_rows},
            ],
        )
        if n:
            return (
                f"个人库 {n_lib} 个、本任务未入库 {n_task} 个，已分开列出；"
                f"已收录过的不会在「本任务」里再出现旧名。"
            ), [card]
        return "还没有要素。可以点下面打开要素库新建，或先给某章添加要素。", [card]
    if tid in {"materials", "knowledge"}:
        items, empty = _browse_materials_pack_items(home, session)
        n = len(items)
        hint = (
            f"资料库是放资料包的地方；已入库 {n} 个。可点下方打开资料库管理。"
            if n
            else empty
        )
        card = _nav_action_card(
            target="materials",
            title="查看资料库",
            hint=hint,
            items=items,
            total=n,
            empty_hint=empty,
        )
        if n:
            return f"资料库里有 {n} 个已入库资料包；点下方打开资料库管理。", [card]
        return "资料库还是空的。可以点下面打开资料库新建资料包并确认入库。", [card]
    return "", []

def _chapter_label(task: Any, chapter_key: str) -> str:
    key = str(chapter_key or "").strip()
    if not task or not key:
        return key
    outline = getattr(task, "outline", None)
    for node in list(getattr(outline, "chapters", None) or []):
        if str(getattr(node, "key", "") or "") == key:
            seq = str(getattr(node, "sequence", "") or "").strip()
            title = str(getattr(node, "title", "") or "").strip()
            return f"{seq} {title}".strip() or key
    return key

def _resolve_associate_chapter_key(
    home: Path,
    session: ChatSession,
    message: str,
    payload: ChatPayload,
    task: Any,
) -> str:
    from report_writing.chat.chapter_resolve import _chapter_key_on_task

    payload = ChatActionPayload.parse(payload)
    raw = str(payload.get("chapter_key") or "").strip()
    if task is not None and raw and _chapter_key_on_task(task, raw):
        return raw
    # 未点名章节时不要落到大纲第一章，缺槽挂 intent_clarify（与加要素一致）
    return _resolve_chapter_key(
        home, session, message, payload, allow_outline_default=False
    )

def _action_associate_elements(
    home: Path,
    session: ChatSession,
    message: str,
    payload: ChatPayload,
) -> tuple[str, list[Any]]:
    from report_writing.chat.action_slots import build_action_slot_clarify, missing_action_slots
    from report_writing.chat.cards import _associate_elements_card
    from report_writing.chat.helpers import _load_task

    payload = ChatActionPayload.parse(payload)
    task_id = _session_writing_task_id(session, payload)
    task = _load_task(home, task_id) if task_id else None
    chapter_key = _resolve_associate_chapter_key(home, session, message, payload, task)
    if not task_id:
        return "还没有写作任务，先选定蓝本或确认大纲后再关联要素。", []
    body = payload.with_updates(task_id=task_id).as_dict()
    if chapter_key:
        body["chapter_key"] = chapter_key
    if not chapter_key:
        miss = missing_action_slots("associate_elements", body, session=session, task=task)
        return build_action_slot_clarify(
            session,
            "associate_elements",
            body,
            miss or ["chapter_key"],
            task=task,
            message=message,
        )
    label = str(payload.get("chapter_label") or "").strip() or _chapter_label(task, chapter_key)
    card = _associate_elements_card(
        task_id=task_id,
        chapter_key=chapter_key,
        chapter_label=label,
        auto_open=True,
        extra=body,
    )
    return f"好，已打开「{label or chapter_key}」的关联面板，勾选后保存即可。", [card]

def _structural_chapter_hint(message: str) -> dict[str, Any]:
    """仅结构补全：从原话抽章序号，不作意图判决。"""
    text = (message or "").strip()
    if not text:
        return {}
    from report_writing.chat.study_chapter_resolve import parse_study_sequences_from_message

    seqs = parse_study_sequences_from_message(text)
    if seqs:
        return {"chapter_key_hint": seqs[0]}
    return {}

def _session_writing_task_id(
    session: ChatSession,
    payload: ChatPayload = None,
) -> str:
    """优先 payload 绑定的任务；无 payload 时才用会话焦点。"""
    from report_writing.chat.session_documents import resolve_task_id

    return resolve_task_id(session, payload or {})

EmitFn = Callable[[dict[str, Any]], None]


def _resolve_chapter_key(
    home: Path,
    session: ChatSession,
    message: str,
    payload: ChatPayload,
    *,
    allow_outline_default: bool = True,
) -> str:
    """原话序号优先于模型 hint / 上一焦点，避免「2.2」被错绑到「1.5.6」。"""
    payload = ChatActionPayload.parse(payload)
    from report_writing.chat.helpers import _load_task
    from report_writing.chat.chapter_resolve import (
        _chapter_key_on_task,
        message_has_chapter_sequence,
        pick_focus_chapter_key_extended,
        resolve_chapter_key_by_sequence,
        resolve_chapter_key_by_title,
        resolve_chapter_key_from_message,
    )

    task_id = _session_writing_task_id(session, payload)
    task = _load_task(home, task_id) if task_id else None
    if task is None:
        return ""

    # 1) 用户原话里的序号 / 标题（最可信）
    from_msg = resolve_chapter_key_from_message(task, message)
    if from_msg:
        return from_msg
    if message_has_chapter_sequence(message):
        # 点名了序号却对不上：再试 hint 是否正是该序号的另一种写法，仍失败则空
        hint_only = str(payload.get("chapter_key_hint") or "").strip()
        if hint_only:
            by_seq = resolve_chapter_key_by_sequence(task, hint_only)
            if by_seq:
                return by_seq
        return ""

    # 2) payload 已是合法 chapter_key
    raw_key = str(payload.get("chapter_key") or "").strip()
    if raw_key and _chapter_key_on_task(task, raw_key):
        return raw_key

    # 3) chapter_key_hint：序号或标题
    hint = str(payload.get("chapter_key_hint") or "").strip()
    if hint:
        by_seq = resolve_chapter_key_by_sequence(task, hint)
        if by_seq:
            return by_seq
        if _chapter_key_on_task(task, hint):
            return hint
        by_title = resolve_chapter_key_by_title(task, hint)
        if by_title:
            return by_title

    # 4) 无明确点名时才允许扩展回落（待确认 / 上一焦点等）
    return pick_focus_chapter_key_extended(
        session,
        task,
        message,
        allow_outline_default=allow_outline_default,
    )

def apply_writing_control_classification(
    home: Path,
    session: ChatSession,
    message: str,
    data: dict[str, Any],
    *,
    emit: EmitFn | None = None,
    expected_tool: str | None = None,
) -> tuple[str, list[Any], list[dict[str, Any]]] | None:
    requested = str(data.get("tool") or "").strip()
    bound = (expected_tool or requested).strip()
    if expected_tool:
        expected = expected_tool.strip()
        if requested and requested != expected:
            payload = dict(data.get("payload") or {})
            return (
                f"本轮绑定的是 {expected}，分类结果是 {requested}。"
                f"请改调 {requested}；本函数不执行其它总控 Tool。",
                [],
                [
                    {
                        "type": "suggested_next_tool",
                        "tool": requested,
                        "args": payload,
                    }
                ],
            )
        tool = bound
    else:
        tool = requested
    payload = dict(data.get("payload") or {})
    from report_writing.chat.session_documents import enrich_payload_with_message_document

    payload = enrich_payload_with_message_document(session, message, payload, switch_focus=True)
    if tool == "open_element_library":
        reply, cards = _action_open_nav(home, session, "elements")
        if not reply:
            return None
        return reply, cards, []
    if tool == "open_blueprint_library":
        reply, cards = _action_open_nav(home, session, "blueprints")
        if not reply:
            return None
        return reply, list(cards or []), []
    if tool == "open_knowledge":
        # 兼容旧意图：知识库已下线，改为打开资料库（放资料包）
        reply, cards = _action_open_nav(home, session, "materials")
        return reply, list(cards or []), []
    if tool == "associate_elements":
        reply, cards = _action_associate_elements(home, session, message, payload)
        return reply, list(cards or []), []
    if tool == "associate_element_knowledge":
        return (
            "「关联知识库」已下线。请编辑要素，将类型设为「文件」并上传解析；"
            "项目级资料请用「资料库」里的资料包与引用槽位。",
            [],
            [],
        )
    if tool == "collect_to_element_library":
        from report_writing.chat.collect_element_library import collect_task_elements_to_library
        from report_writing.chat.helpers import _emit_process
        from report_writing.chat.clarify_invoke import invoke_clarify_llm
        from report_writing.services.chat_session import save_session

        _emit_process(emit, "正在把本任务要素收入个人要素库…")
        reply, cards = collect_task_elements_to_library(
            home,
            session,
            message,
            payload,
            invoke=lambda s, u: invoke_clarify_llm(s, u, fallback=None),
        )
        try:
            save_session(home, session)
        except Exception:
            pass
        return reply, list(cards or []), []

    if tool == "dissociate_chapter_element":
        from report_writing.chat.element_document_actions import dissociate_chapter_element
        from report_writing.chat.helpers import _emit_process
        from report_writing.chat.clarify_invoke import invoke_clarify_llm
        from report_writing.services.chat_session import save_session

        _emit_process(emit, "正在取消章节与要素的关联…")
        reply, cards = dissociate_chapter_element(
            home,
            session,
            message,
            payload,
            invoke=lambda s, u: invoke_clarify_llm(s, u, fallback=None),
        )
        try:
            save_session(home, session)
        except Exception:
            pass
        return reply, list(cards or []), []

    if tool == "remove_document_element":
        from report_writing.chat.element_document_actions import remove_document_element
        from report_writing.chat.helpers import _emit_process
        from report_writing.chat.clarify_invoke import invoke_clarify_llm
        from report_writing.services.chat_session import save_session

        _emit_process(emit, "正在从本文档移除要素…")
        reply, cards = remove_document_element(
            home,
            session,
            message,
            payload,
            invoke=lambda s, u: invoke_clarify_llm(s, u, fallback=None),
        )
        try:
            save_session(home, session)
        except Exception:
            pass
        return reply, list(cards or []), []

    if tool == "add_chapter_element":
        from report_writing.chat.actions import _action_add_chapter_element
        from report_writing.chat.helpers import _emit_process

        # 收录 vs 加要素：分类器另选 collect 时由调用方 hop，本分支只执行加要素
        merged = dict(_structural_chapter_hint(message))
        for key, value in payload.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        payload = merged
        # 分类器 payload 过瘦时用专用抽取模型补全（仍非正则意图）
        if not str(payload.get("name") or "").strip() and not payload.get("kv_pairs"):
            from report_writing.chat.clarify_invoke import invoke_clarify_llm
            from report_writing.chat.clarify_llm import infer_element_payload_llm

            inferred = infer_element_payload_llm(
                message,
                chapter_title=str(payload.get("chapter_key_hint") or ""),
                invoke=lambda s, u: invoke_clarify_llm(s, u, fallback=None),
            )
            for key, value in (inferred or {}).items():
                if value not in (None, "", [], {}) and payload.get(key) in (None, "", [], {}):
                    payload[key] = value
        task_id = _session_writing_task_id(session, payload)
        # 加要素必须点名章节：禁止关键词猜测，也不回落到大纲第一章
        chapter_key = _resolve_chapter_key(
            home,
            session,
            message,
            payload,
            allow_outline_default=False,
        )
        if not task_id:
            return None
        body = {
            **payload,
            "task_id": task_id,
            "hint": message[:500],
            "user_note": message[:500],
        }
        if chapter_key:
            body["chapter_key"] = chapter_key
        if not chapter_key:
            from report_writing.chat.action_slots import (
                build_action_slot_clarify,
                missing_action_slots,
            )
            from report_writing.chat.helpers import _load_task

            task = _load_task(home, task_id)
            miss = missing_action_slots(
                "add_chapter_element", body, session=session, task=task
            )
            reply, cards = build_action_slot_clarify(
                session,
                "add_chapter_element",
                body,
                miss or ["chapter_key"],
                task=task,
                message=message,
            )
            return reply, cards, []
        _emit_process(emit, "正在根据对话理解并生成本章要素草稿…")
        reply, cards = _action_add_chapter_element(home, session, body)
        return reply, cards, []

    if tool == "remember_writing_goal":
        from report_writing.chat.session_goal_actions import run_session_goal_action

        notes = str(payload.get("notes") or message).strip()
        reply, cards = run_session_goal_action(
            home,
            session,
            "remember_writing_goal",
            {"notes": notes},
            invoke=invoke,
            emit=emit,
        )
        return reply, cards, []

    return None
