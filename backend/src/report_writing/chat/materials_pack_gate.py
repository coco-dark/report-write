"""开写前资料包引导：不用 / 使用已有 / 新建；缺槽文件时的非阻断提醒。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from report_writing.domain.models import ChatActionPayload, ChatCard, ChatPayload, ChatSession, WritingTask

EmitFn = Callable[[dict[str, Any]], None]
InvokeFn = Callable[[str, str], str]


def _session_packs(home: Path, session: ChatSession, **kwargs):
    from report_writing.services.robots import scoped_materials_packs

    rid = str(getattr(session, "robot_id", "") or "").strip()
    return scoped_materials_packs(home, rid or None, **kwargs)

# 对话 / UI 快捷动作（与前端 sendAction、DISPATCH_TOOLS 对齐）
MATERIALS_PACK_ACTIONS = frozenset(
    {
        "materials_pack_skip",
        "materials_pack_use",
        "materials_pack_create",
        "materials_pack_confirm_library",
        "materials_pack_list",
        "materials_pack_view",
        "materials_pack_unbind",
        "materials_pack_add_slot",
        "materials_pack_remove_slot",
        "materials_pack_update_slot",
        "materials_pack_rename",
    }
)


def task_needs_materials_pack(task: WritingTask | None) -> bool:
    if task is None:
        return False
    for el in task.element_nodes or []:
        if str(getattr(el, "kind", "") or "").strip() == "file_slot":
            slots = list(getattr(el, "file_slots", None) or [])
            if slots:
                return True
    return False


def _materials_pack_skipped_for_task(session: ChatSession, task_id: str) -> bool:
    """跳过标记按文档（task）隔离，避免多文档互相覆盖。"""
    tid = (task_id or "").strip()
    ff = dict(session.freeform or {})
    by_task = ff.get("materials_pack_skipped_tasks")
    if isinstance(by_task, dict) and tid and bool(by_task.get(tid)):
        return True
    # 兼容旧会话级 skip：仅当焦点就是该 task 时生效
    if bool(ff.get("materials_pack_skipped")) and tid and tid == str(
        getattr(session, "task_id", "") or ""
    ).strip():
        return True
    return False


def mark_materials_pack_skipped(session: ChatSession, task_id: str, *, skipped: bool = True) -> None:
    tid = (task_id or "").strip()
    ff = dict(session.freeform or {})
    by_task = dict(ff.get("materials_pack_skipped_tasks") or {})
    if tid:
        if skipped:
            by_task[tid] = True
        else:
            by_task.pop(tid, None)
    ff["materials_pack_skipped_tasks"] = by_task
    if skipped and tid and tid == str(getattr(session, "task_id", "") or "").strip():
        ff["materials_pack_skipped"] = True
    elif not skipped:
        ff["materials_pack_skipped"] = False
    session.freeform = ff


def _is_blueprint_only_task(task: WritingTask | None) -> bool:
    """蓝本库条目（尚未 clone 成写作文档）。"""
    if task is None:
        return True
    tid = str(getattr(task, "id", "") or "").strip()
    if tid.startswith("bp") and not str(getattr(task, "source_blueprint_id", "") or "").strip():
        return True
    return False


def stash_pending_materials_pack(
    session: ChatSession,
    pack_id: str,
    *,
    blueprint_id: str = "",
) -> None:
    """开写前预建/预选资料包：记在会话上，开写时绑到写作文档。"""
    pid = (pack_id or "").strip()
    if not pid:
        return
    ff = dict(session.freeform or {})
    ff["pending_materials_pack_id"] = pid
    bp = (blueprint_id or str(getattr(session, "blueprint_id", "") or "")).strip()
    by_bp = dict(ff.get("pending_materials_pack_by_blueprint") or {})
    if bp:
        by_bp[bp] = pid
    ff["pending_materials_pack_by_blueprint"] = by_bp
    # 预建即表示不打算「开写时不用」
    if bp:
        skipped = dict(ff.get("materials_pack_skipped_tasks") or {})
        skipped.pop(bp, None)
        ff["materials_pack_skipped_tasks"] = skipped
    session.freeform = ff


def peek_pending_materials_pack(
    session: ChatSession,
    *,
    blueprint_id: str = "",
) -> str:
    ff = dict(session.freeform or {})
    bp = (blueprint_id or str(getattr(session, "blueprint_id", "") or "")).strip()
    by_bp = ff.get("pending_materials_pack_by_blueprint")
    if bp and isinstance(by_bp, dict):
        hit = str(by_bp.get(bp) or "").strip()
        if hit:
            return hit
    return str(ff.get("pending_materials_pack_id") or "").strip()


def clear_pending_materials_pack(
    session: ChatSession,
    *,
    blueprint_id: str = "",
    pack_id: str = "",
) -> None:
    ff = dict(session.freeform or {})
    pid = (pack_id or "").strip()
    bp = (blueprint_id or str(getattr(session, "blueprint_id", "") or "")).strip()
    if str(ff.get("pending_materials_pack_id") or "").strip() == pid or not pid:
        ff.pop("pending_materials_pack_id", None)
    by_bp = dict(ff.get("pending_materials_pack_by_blueprint") or {})
    if bp and (not pid or str(by_bp.get(bp) or "").strip() == pid):
        by_bp.pop(bp, None)
    ff["pending_materials_pack_by_blueprint"] = by_bp
    session.freeform = ff


def materials_pack_gate_satisfied(session: ChatSession, task: WritingTask | None) -> bool:
    if not task_needs_materials_pack(task):
        return True
    if str(getattr(task, "materials_pack_id", "") or "").strip():
        return True
    tid = str(getattr(task, "id", "") or "").strip()
    if _materials_pack_skipped_for_task(session, tid):
        return True
    return False


def soft_prompt_materials_pack_if_needed(
    session: ChatSession,
    home: Path,
    task: WritingTask | None,
) -> tuple[str, list[ChatCard]]:
    """选用蓝本后：若要素含资料包槽位引用，对话提示（不把包绑到蓝本）。"""
    if task is None or not task_needs_materials_pack(task):
        return "", []
    from report_writing.services.materials_pack import collect_file_slot_schema

    schema = collect_file_slot_schema(list(task.element_nodes or []))
    slot_n = len(schema or [])
    bid = str(getattr(task, "id", "") or session.blueprint_id or "").strip()
    library = _session_packs(home, session, in_library=True)
    reply = (
        f"这份蓝本有要素引用了资料包槽位（约 {slot_n or '若干'} 个）。"
        "资料库是放资料包的地方。你可以选用已有资料包，或新建一个；"
        "开写时会自动绑到本文档。也可点「不用资料包」——不配则检索跳过资料包文件。"
    )
    return (
        reply,
        [
            ChatCard(
                type="materials_pack_gate",
                title="资料包提示",
                payload={
                    "task_id": "",
                    "blueprint_id": bid,
                    "hint_only": True,
                    "per_document": True,
                    "slot_schema": schema,
                    "library_packs": [
                        {"id": p.id, "name": p.name, "slot_count": len(p.slots_fill or [])}
                        for p in library
                    ],
                    "options": [
                        {
                            "id": "skip",
                            "label": "不用资料包",
                            "action": "materials_pack_skip",
                        },
                        {
                            "id": "use",
                            "label": "使用已有",
                            "action": "materials_pack_use",
                        },
                        {
                            "id": "create",
                            "label": "新建资料包",
                            "action": "materials_pack_create",
                        },
                    ],
                },
            )
        ],
    )


def _chapter_element_ids(task: WritingTask, key: str) -> list[str]:
    kid = (key or "").strip()
    if not kid:
        return []
    brief = getattr(task, "brief", None)
    if brief is not None:
        try:
            req = brief.chapter(kid)
            ids = [str(x).strip() for x in (getattr(req, "element_ids", None) or []) if str(x).strip()]
            if ids:
                return ids
        except KeyError:
            pass
    outline = getattr(task, "outline", None)
    if outline is not None:
        by_key = {str(item.key): item for item in (outline.chapters or [])}
        node = by_key.get(kid)
        if node is not None:
            return [str(x).strip() for x in (getattr(node, "element_ids", None) or []) if str(x).strip()]
    return []


def _chapter_title(task: WritingTask, key: str) -> str:
    kid = (key or "").strip()
    for ch in list(getattr(task, "chapters", None) or []):
        if str(getattr(ch, "key", "") or "") == kid:
            title = str(getattr(ch, "title", "") or "").strip()
            if title:
                return title
    outline = getattr(task, "outline", None)
    if outline is not None:
        for node in list(outline.chapters or []):
            if str(getattr(node, "key", "") or "") == kid:
                return str(getattr(node, "title", "") or "").strip() or kid
    brief = getattr(task, "brief", None)
    if brief is not None:
        try:
            return str(getattr(brief.chapter(kid), "title", "") or "").strip() or kid
        except KeyError:
            pass
    return kid


def collect_write_slot_gaps(
    home: Path,
    task: WritingTask | None,
    chapter_keys: list[str] | None,
) -> list[dict[str, Any]]:
    """本轮章节关联的 file_slot 要素中，资料包未绑/未上传的槽位缺口（非阻断）。"""
    if task is None:
        return []
    keys = [str(k).strip() for k in (chapter_keys or []) if str(k).strip()]
    if not keys:
        return []
    by_id = {
        str(el.id): el
        for el in list(getattr(task, "element_nodes", None) or [])
        if str(getattr(el, "id", "") or "").strip()
    }
    pack_id = str(getattr(task, "materials_pack_id", "") or "").strip()
    from report_writing.domain.elements import file_slot_missing_nos

    gaps: list[dict[str, Any]] = []
    for key in keys:
        for eid in _chapter_element_ids(task, key):
            el = by_id.get(eid)
            if el is None:
                continue
            if str(getattr(el, "kind", "") or "").strip() != "file_slot":
                continue
            slots = list(getattr(el, "file_slots", None) or [])
            if not slots:
                continue
            missing = file_slot_missing_nos(el, materials_pack_id=pack_id, home=home)
            if not missing:
                continue
            gaps.append(
                {
                    "chapter_key": key,
                    "chapter_title": _chapter_title(task, key),
                    "element_id": eid,
                    "element_name": str(getattr(el, "name", "") or "").strip() or eid,
                    "missing_nos": missing,
                }
            )
    return gaps


def draft_slot_upload_reminder(gaps: list[dict[str, Any]], *, pack_bound: bool) -> str:
    """开写前缺槽文件提醒草稿（事实齐全，交 LLM 润色）。"""
    if not gaps:
        return ""
    chapter_titles: list[str] = []
    seen_ch: set[str] = set()
    slot_bits: list[str] = []
    seen_slot: set[str] = set()
    for g in gaps:
        title = str(g.get("chapter_title") or g.get("chapter_key") or "").strip()
        if title and title not in seen_ch:
            seen_ch.add(title)
            chapter_titles.append(title)
        el_name = str(g.get("element_name") or "").strip() or "要素"
        nos = [int(n) for n in (g.get("missing_nos") or []) if int(n) > 0]
        nos_txt = "、".join(str(n) for n in nos) if nos else "?"
        bit = f"{el_name}（槽位 {nos_txt}）"
        if bit not in seen_slot:
            seen_slot.add(bit)
            slot_bits.append(bit)
    ch_txt = "、".join(chapter_titles[:6])
    if len(chapter_titles) > 6:
        ch_txt += f" 等 {len(chapter_titles)} 章"
    slot_txt = "；".join(slot_bits[:8])
    if len(slot_bits) > 8:
        slot_txt += f" 等 {len(slot_bits)} 项"
    if pack_bound:
        bind_note = "资料包已绑定，但上述槽位还没上传文件"
    else:
        bind_note = "当前未绑定资料包或已选择暂不使用"
    return (
        f"开写前提醒：本轮章节（{ch_txt}）关联了资料包文件槽位——{slot_txt}。"
        f"{bind_note}；开写后这些槽位不会做材料检索，对应依据可能拿不到。"
        "不强制现在上传——可以先写；有空再到右侧文档窗资料包区补传槽位文件，"
        "补齐后再写或重试检索会更完整。"
    )


def remind_slot_upload_before_write(
    home: Path,
    session: ChatSession,
    task: WritingTask,
    chapter_keys: list[str],
    *,
    invoke: InvokeFn | None = None,
    emit: EmitFn | None = None,
    model=None,
) -> str:
    """若本轮章节有未齐的资料包槽位引用，用 LLM 拟人化提醒；不阻断开写。返回提醒正文或空串。"""
    gaps = collect_write_slot_gaps(home, task, chapter_keys)
    if not gaps:
        return ""
    pack_bound = bool(str(getattr(task, "materials_pack_id", "") or "").strip())
    draft = draft_slot_upload_reminder(gaps, pack_bound=pack_bound)
    if not draft:
        return ""
    llm = invoke
    if llm is None:
        try:
            from report_writing.infra.llm import or_live_model
            from report_writing.infra.llm_invoke import invoke_prompt

            chosen = or_live_model(model)

            def _live(system: str, user: str) -> str:
                if chosen is None:
                    return ""
                return invoke_prompt(chosen, system, user)

            llm = _live if chosen is not None else None
        except Exception:
            llm = None
    from report_writing.chat.helpers import _emit_tokens, polish_chat_reply

    reminder = polish_chat_reply(
        invoke=llm,
        model=model,
        user_message="开始写作",
        draft=draft,
        phase=str(getattr(session, "phase", "") or ""),
        action="start_writing",
        situation=(
            "用户刚要点开写；章节要素引用了资料包文件槽位但文件未齐。"
            "提醒即可，不要强迫上传，不要阻止开写，不要让用户去点卡片。"
            "语气专业、像靠谱同事，简短。"
        ),
        session=session,
        cards=None,
        force=True,
    )
    text = (reminder or draft).strip()
    if text:
        _emit_tokens(emit, text + "\n\n")
    return text


def offer_materials_pack_cards(
    session: ChatSession,
    home: Path,
    task: WritingTask,
) -> tuple[str, list[ChatCard]]:
    from report_writing.services.materials_pack import collect_file_slot_schema

    library = _session_packs(home, session, in_library=True)
    schema = collect_file_slot_schema(list(task.element_nodes or []))
    pending = _session_packs(home, session, in_library=False)
    return (
        "当前写作文档的要素引用了资料包槽位。请为本文档配置资料包："
        "可不用、从资料库选用，或新建（默认待入库）。"
        "不配置则开写时不检索资料包文件；资料库是放资料包的地方。",
        [
            ChatCard(
                type="materials_pack_gate",
                title="为本文档配置资料包",
                payload={
                    "task_id": task.id,
                    "blueprint_id": str(
                        session.blueprint_id
                        or getattr(task, "source_blueprint_id", "")
                        or ""
                    ),
                    "per_document": True,
                    "slot_schema": schema,
                    "library_packs": [
                        {"id": p.id, "name": p.name, "slot_count": len(p.slots_fill or [])}
                        for p in library
                    ],
                    "pending_packs": [
                        {"id": p.id, "name": p.name, "slot_count": len(p.slots_fill or [])}
                        for p in pending
                    ],
                    "options": [
                        {"id": "skip", "label": "不用资料包", "action": "materials_pack_skip"},
                        {"id": "use", "label": "使用已有资料包", "action": "materials_pack_use"},
                        {"id": "create", "label": "新建资料包", "action": "materials_pack_create"},
                    ],
                },
            )
        ],
    )


def bind_pack_to_task(home: Path, task: WritingTask, pack_id: str) -> WritingTask:
    from report_writing.services.store import TaskStore
    from report_writing.services.writing_task_catalog import save_writing_task

    task.materials_pack_id = (pack_id or "").strip()
    path = home / "tasks" / task.id
    TaskStore(path).save(task)
    try:
        save_writing_task(task)
    except Exception:
        pass
    return task


def _pack_slot_rows(pack) -> list[dict[str, Any]]:
    fills = {
        int(getattr(f, "slot_no", 0) or 0): f
        for f in list(getattr(pack, "slots_fill", None) or [])
        if int(getattr(f, "slot_no", 0) or 0) > 0
    }
    rows: list[dict[str, Any]] = []
    for s in list(getattr(pack, "slot_schema", None) or []):
        try:
            no = int(getattr(s, "no", 0) or 0)
        except (TypeError, ValueError):
            continue
        if no < 1:
            continue
        fill = fills.get(no)
        rows.append(
            {
                "no": no,
                "name": str(getattr(s, "name", "") or "").strip(),
                "file_name": str(getattr(fill, "file_name", "") or "").strip() if fill else "",
                "parse_status": str(getattr(fill, "parse_status", "") or "").strip() if fill else "",
                "parse_error": str(getattr(fill, "parse_error", "") or "").strip() if fill else "",
            }
        )
    for no, fill in fills.items():
        if any(r["no"] == no for r in rows):
            continue
        rows.append(
            {
                "no": no,
                "name": "",
                "file_name": str(getattr(fill, "file_name", "") or "").strip(),
                "parse_status": str(getattr(fill, "parse_status", "") or "").strip(),
                "parse_error": str(getattr(fill, "parse_error", "") or "").strip(),
            }
        )
    rows.sort(key=lambda r: r["no"])
    return rows


def _bound_pack_card(task: WritingTask | None, pack, *, created: bool = False) -> ChatCard:
    schema = [
        {"no": r["no"], "name": r["name"], "query": ""}
        for r in _pack_slot_rows(pack)
    ]
    return ChatCard(
        type="materials_pack_bound",
        title=f"{'新建资料包' if created else '资料包'} · {pack.name}",
        payload={
            "task_id": str(getattr(task, "id", "") or ""),
            "pack_id": pack.id,
            "pack": pack.model_dump(),
            "slot_schema": schema,
            "slots": _pack_slot_rows(pack),
            "created": created,
        },
    )


def _resolve_bound_pack(home: Path, session: ChatSession, body: dict[str, Any], task: WritingTask | None):
    from report_writing.services.materials_pack import get_pack

    pack_id = str(body.get("pack_id") or body.get("materials_pack_id") or "").strip()
    if not pack_id and task is not None:
        pack_id = str(getattr(task, "materials_pack_id", "") or "").strip()
    if not pack_id:
        return None, ""
    return get_pack(home, pack_id), pack_id


def _clarify_pick_pack(
    home: Path,
    session: ChatSession,
    tool: str,
    body: dict[str, Any],
    *,
    message: str = "",
) -> tuple[str, list[ChatCard]]:
    """缺 pack_id 时挂多轮澄清，列出可点资料包。"""
    from report_writing.chat.action_slots import build_action_slot_clarify

    library = _session_packs(home, session, in_library=True)
    pending = _session_packs(home, session, in_library=False)
    packs = [
        {"id": p.id, "name": p.name}
        for p in (list(library) + list(pending))
        if str(getattr(p, "id", "") or "").strip()
    ]
    if not packs:
        return ("还没有资料包。可以说「新建资料包」或在文档窗点新建。", [])
    return build_action_slot_clarify(
        session,
        tool,
        dict(body or {}),
        ["pack_id"],
        message=message,
        blueprints=packs,
    )


def apply_materials_pack_choice(
    home: Path,
    session: ChatSession,
    action: str,
    payload: ChatPayload = None,
    *,
    emit: EmitFn | None = None,
) -> tuple[str, list[ChatCard]]:
    from report_writing.chat.helpers import _load_task
    from report_writing.services.materials_pack import (
        apply_slot_schema,
        collect_file_slot_schema,
        create_pack,
        get_pack,
        rename_pack,
        set_pack_in_library,
    )
    from report_writing.services.store import TaskStore
    from report_writing.services.writing_task_catalog import save_writing_task

    body = ChatActionPayload.parse(payload).as_dict()
    key = (action or "").strip()
    tid = str(body.get("task_id") or session.task_id or "").strip()
    task = _load_task(home, tid) if tid else None

    if key == "materials_pack_skip":
        if task is not None and not (
            str(task.id or "").startswith("bp")
            and not str(getattr(task, "source_blueprint_id", "") or "").strip()
        ):
            task.materials_pack_id = ""
            TaskStore(home / "tasks" / task.id).save(task)
            try:
                save_writing_task(task)
            except Exception:
                pass
            mark_materials_pack_skipped(session, task.id, skipped=True)
        else:
            # 选用蓝本后的提示卡：按蓝本记下「开写时不用」，落到写作文档
            skip_key = str(
                body.get("blueprint_id")
                or session.blueprint_id
                or (task.id if task is not None else "")
                or session.task_id
                or ""
            ).strip()
            mark_materials_pack_skipped(session, skip_key, skipped=True)
        return (
            "已选择不用资料包。可以说「开始写作」继续；未绑定资料包时不会检索资料包文件。",
            [],
        )

    if key == "materials_pack_list":
        library = _session_packs(home, session, in_library=True)
        pending = _session_packs(home, session, in_library=False)
        if not library and not pending:
            return ("还没有资料包。可以说「新建资料包」或在文档窗点新建。", [])
        bound = str(getattr(task, "materials_pack_id", "") or "").strip() if task else ""
        return (
            f"共 {len(library) + len(pending)} 个资料包"
            f"（已入库 {len(library)}，待入库 {len(pending)}）。"
            "可点卡片换绑，或说「查看资料包 / 添加槽位」。",
            [
                ChatCard(
                    type="materials_pack_list",
                    title="全部资料包",
                    payload={
                        "task_id": tid,
                        "bound_pack_id": bound,
                        "library_packs": [
                            {
                                "id": p.id,
                                "name": p.name,
                                "slot_count": len(p.slot_schema or p.slots_fill or []),
                                "in_library": True,
                            }
                            for p in library
                        ],
                        "pending_packs": [
                            {
                                "id": p.id,
                                "name": p.name,
                                "slot_count": len(p.slot_schema or p.slots_fill or []),
                                "in_library": False,
                            }
                            for p in pending
                        ],
                    },
                )
            ],
        )

    if key == "materials_pack_view":
        pack, pack_id = _resolve_bound_pack(home, session, body, task)
        if pack is None:
            if not pack_id:
                return apply_materials_pack_choice(
                    home, session, "materials_pack_list", body, emit=emit
                )
            raise ValueError("资料包不存在")
        rows = _pack_slot_rows(pack)
        tip = "、".join(
            f"{r['no']}.{r['name'] or '未命名'}（"
            f"{'已解析' if r['parse_status'] == 'parsed' else ('失败' if r['parse_status'] == 'error' else ('待解析' if r['file_name'] else '未上传'))}）"
            for r in rows[:8]
        ) or "尚无槽位"
        return (
            f"资料包「{pack.name}」当前配置：{tip}。"
            "可说「添加槽位」「删除槽位 2」「改名槽位 1 为概况」或在文档窗上传文件。",
            [_bound_pack_card(task, pack)],
        )

    if key == "materials_pack_unbind":
        if task is None:
            raise ValueError("请先打开写作任务再解绑资料包")
        # 资料包只绑在写作文档上，不绑蓝本
        if str(task.id or "").startswith("bp") and not str(
            getattr(task, "source_blueprint_id", "") or ""
        ).strip():
            raise ValueError("资料包按写作文档绑定；请先开写生成文档后再解绑")
        task.materials_pack_id = ""
        mark_materials_pack_skipped(session, task.id, skipped=True)
        TaskStore(home / "tasks" / task.id).save(task)
        try:
            save_writing_task(task)
        except Exception:
            pass
        return ("已解绑资料包。需要时可以说「换绑资料包」或「新建资料包」。", [])

    if key == "materials_pack_use":
        pack_id = str(body.get("pack_id") or body.get("materials_pack_id") or "").strip()
        bp = str(
            body.get("blueprint_id")
            or session.blueprint_id
            or (getattr(task, "source_blueprint_id", "") if task is not None else "")
            or (task.id if task is not None and _is_blueprint_only_task(task) else "")
            or ""
        ).strip()
        if not pack_id:
            return _clarify_pick_pack(
                home,
                session,
                "materials_pack_use",
                {**body, "task_id": tid, "blueprint_id": bp},
                message=str(body.get("message") or ""),
            )
        pack = get_pack(home, pack_id)
        if pack is None:
            raise ValueError("资料包不存在")
        from report_writing.services.robots import assert_materials_pack_in_robot_scope

        assert_materials_pack_in_robot_scope(home, session.robot_id or "", pack_id)
        # 已有写作文档：直接绑定
        if task is not None and not _is_blueprint_only_task(task):
            task.materials_pack_id = pack_id
            mark_materials_pack_skipped(session, task.id, skipped=False)
            TaskStore(home / "tasks" / task.id).save(task)
            try:
                save_writing_task(task)
            except Exception:
                pass
            schema = collect_file_slot_schema(list(task.element_nodes or []))
            try:
                pack = apply_slot_schema(
                    home,
                    pack,
                    schema,
                    blueprint_id=bp,
                    prune=False,
                    preserve_pack_names=True,
                )
            except Exception:
                pass
            return (
                f"已换绑资料包「{pack.name}」。可在右侧文档窗按槽位上传/更换文件，或说「查看资料包」。",
                [_bound_pack_card(task, pack)],
            )
        # 开写前：预选，开写时再绑到写作文档（不绑蓝本）
        schema_src = task
        if schema_src is None and bp:
            schema_src = _load_task(home, bp)
        if schema_src is not None:
            schema = collect_file_slot_schema(list(schema_src.element_nodes or []))
            try:
                pack = apply_slot_schema(
                    home,
                    pack,
                    schema,
                    blueprint_id=bp,
                    prune=False,
                    preserve_pack_names=True,
                )
            except Exception:
                pass
        stash_pending_materials_pack(session, pack_id, blueprint_id=bp)
        return (
            f"已选好资料包「{pack.name}」。开写时会自动绑到本文档；"
            "也可继续分析目录，或说「开始写作」。",
            [_bound_pack_card(None, pack)],
        )

    if key == "materials_pack_create":
        name = str(body.get("name") or body.get("pack_name") or "").strip() or "未命名资料包"
        bp = str(
            body.get("blueprint_id")
            or session.blueprint_id
            or (getattr(task, "source_blueprint_id", "") if task is not None else "")
            or (task.id if task is not None and _is_blueprint_only_task(task) else "")
            or ""
        ).strip()
        schema_src = task
        if (schema_src is None or _is_blueprint_only_task(schema_src)) and bp:
            loaded = _load_task(home, bp)
            if loaded is not None:
                schema_src = loaded
        schema = (
            collect_file_slot_schema(list(schema_src.element_nodes or []))
            if schema_src is not None
            else []
        )
        pack = create_pack(
            home,
            name=name,
            in_library=False,
            slot_schema=schema,
            blueprint_id=bp,
        )
        # 已有写作文档：直接绑定
        if task is not None and not _is_blueprint_only_task(task):
            task.materials_pack_id = pack.id
            mark_materials_pack_skipped(session, task.id, skipped=False)
            TaskStore(home / "tasks" / task.id).save(task)
            try:
                save_writing_task(task)
            except Exception:
                pass
            return (
                f"已新建资料包「{pack.name}」（待入库）。请在文档窗按槽位上传项目文件；"
                "写完或随时可在资料库确认入库，供其它文档选用。",
                [_bound_pack_card(task, pack, created=True)],
            )
        # 开写前也可新建：记到会话，开写时自动绑本文档（不必先有「写作任务」）
        stash_pending_materials_pack(session, pack.id, blueprint_id=bp)
        return (
            f"已新建资料包「{pack.name}」（待入库）。开写时会自动绑到本文档；"
            "也可先在资料库管理，或开写后再到文档窗按槽位上传文件。",
            [_bound_pack_card(None, pack, created=True)],
        )

    if key == "materials_pack_confirm_library":
        ids = [str(x).strip() for x in (body.get("pack_ids") or []) if str(x).strip()]
        if not ids and body.get("pack_id"):
            ids = [str(body.get("pack_id")).strip()]
        updated = []
        for pid in ids:
            try:
                updated.append(set_pack_in_library(home, pid, True))
            except KeyError:
                continue
        if not updated:
            return ("没有可入库的资料包。", [])
        names = "、".join(p.name for p in updated)
        return (f"已将 {len(updated)} 个资料包入库：{names}。之后可在「使用已有资料包」中选用。", [])

    if key == "materials_pack_rename":
        pack, _pid = _resolve_bound_pack(home, session, body, task)
        if pack is None:
            return _clarify_pick_pack(
                home,
                session,
                "materials_pack_rename",
                {**body, "task_id": tid},
                message=str(body.get("message") or ""),
            )
        new_name = str(body.get("name") or body.get("pack_name") or "").strip()
        if not new_name:
            from report_writing.chat.clarify_flow import begin_clarify

            return begin_clarify(
                session,
                source="materials_name",
                question=f"资料包现在叫「{pack.name}」，想改成啥？直接回新名字就行。",
                options=[
                    {
                        "id": "keep_pack_name",
                        "label": f"先不改，还叫「{pack.name}」",
                        "slots": {
                            "tool": "materials_pack_rename",
                            "args": {
                                **body,
                                "pack_id": pack.id,
                                "name": pack.name,
                            },
                        },
                    }
                ],
                candidate={
                    "kind": "control_tool",
                    "name": "materials_pack_rename",
                    "payload": {**body, "pack_id": pack.id, "task_id": tid},
                },
                phase_hint="materials_name",
            )
        pack = rename_pack(home, pack.id, new_name)
        return (f"资料包已改名为「{pack.name}」。", [_bound_pack_card(task, pack)])

    if key in {"materials_pack_add_slot", "materials_pack_remove_slot", "materials_pack_update_slot"}:
        pack, _pid = _resolve_bound_pack(home, session, body, task)
        if pack is None:
            return _clarify_pick_pack(
                home,
                session,
                key,
                {**body, "task_id": tid},
                message=str(body.get("message") or ""),
            )
        schema = [
            {
                "no": int(getattr(s, "no", 0) or 0),
                "name": str(getattr(s, "name", "") or "").strip(),
                "query": "",
            }
            for s in list(pack.slot_schema or [])
            if int(getattr(s, "no", 0) or 0) > 0
        ]
        bp = ""
        if task is not None:
            bp = str(getattr(task, "source_blueprint_id", "") or session.blueprint_id or "").strip()

        if key == "materials_pack_add_slot":
            name = str(body.get("name") or body.get("slot_name") or "").strip()
            try:
                want_no = int(body.get("slot_no") or body.get("no") or 0)
            except (TypeError, ValueError):
                want_no = 0
            used = {int(s["no"]) for s in schema}
            no = want_no if want_no > 0 and want_no not in used else (max(used) + 1 if used else 1)
            schema.append({"no": no, "name": name or f"槽位 {no}", "query": ""})
            pack = apply_slot_schema(home, pack, schema, blueprint_id=bp, prune=False)
            return (
                f"已在「{pack.name}」添加槽位 {no}"
                + (f"（{name}）" if name else "")
                + "。可在文档窗上传文件，或继续说「添加槽位」。",
                [_bound_pack_card(task, pack)],
            )

        try:
            slot_no = int(body.get("slot_no") or body.get("no") or 0)
        except (TypeError, ValueError):
            slot_no = 0
        if slot_no < 1:
            from report_writing.chat.action_slots import build_action_slot_clarify

            return build_action_slot_clarify(
                session,
                key,
                {**body, "task_id": tid, "pack_id": pack.id},
                ["slot_no"],
                message=str(body.get("message") or ""),
                blueprints=schema,
            )

        if key == "materials_pack_remove_slot":
            if not any(int(s["no"]) == slot_no for s in schema):
                raise ValueError(f"资料包没有槽位 {slot_no}")
            schema = [s for s in schema if int(s["no"]) != slot_no]
            pack = apply_slot_schema(home, pack, schema, blueprint_id=bp, prune=True)
            return (
                f"已从「{pack.name}」删除槽位 {slot_no}。",
                [_bound_pack_card(task, pack)],
            )

        # materials_pack_update_slot
        new_name = str(body.get("name") or body.get("slot_name") or "").strip()
        if not new_name:
            from report_writing.chat.clarify_flow import begin_clarify

            return begin_clarify(
                session,
                source="materials_name",
                question=f"槽位 {slot_no} 想叫啥名字？直接回我就行。",
                options=[],
                candidate={
                    "kind": "control_tool",
                    "name": "materials_pack_update_slot",
                    "payload": {
                        **body,
                        "pack_id": pack.id,
                        "slot_no": slot_no,
                        "task_id": tid,
                    },
                },
                phase_hint="materials_name",
            )
        hit = False
        for s in schema:
            if int(s["no"]) == slot_no:
                s["name"] = new_name
                hit = True
                break
        if not hit:
            schema.append({"no": slot_no, "name": new_name, "query": ""})
        pack = apply_slot_schema(home, pack, schema, blueprint_id=bp, prune=False)
        return (
            f"已将「{pack.name}」槽位 {slot_no} 改名为「{new_name}」。",
            [_bound_pack_card(task, pack)],
        )

    raise ValueError(f"未知资料包动作：{key}")
