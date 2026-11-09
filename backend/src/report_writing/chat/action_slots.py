"""Action 缺参多轮收集：挂起 intent_clarify（source=action_slots），补齐后续跑原 Tool。"""

from __future__ import annotations

from typing import Any

from report_writing.domain.models import ChatActionPayload, ChatCard, ChatPayload, ChatSession

ACTION_SLOT_SOURCE = "action_slots"

# tool -> 必填槽位（有 task 上下文时）
_REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "configure_chapter": ("chapter_key",),
    "get_chapter_info": ("chapter_key",),
    "generate_chapter_describes": ("chapter_key",),
    "add_chapter_element": ("chapter_key",),
    "associate_elements": ("chapter_key",),
    "dissociate_chapter_element": ("chapter_key", "element_id"),
    "remove_document_element": ("element_id",),
    "re_study_chapters": ("chapter_sequences",),
    "select_write_chapters": ("chapter_sequences",),
    "select_blueprint": ("blueprint_id",),
    "materials_pack_use": ("pack_id",),
    "materials_pack_rename": ("pack_id",),
    "materials_pack_add_slot": ("pack_id",),
    "materials_pack_remove_slot": ("pack_id", "slot_no"),
    "materials_pack_update_slot": ("pack_id", "slot_no"),
}

_TOOL_QUESTIONS: dict[str, str] = {
    "configure_chapter": "你想改哪一章的配置？点一下，或者说章节号也行。",
    "get_chapter_info": "要看哪一章的配置？点一下，或者说章节号也行。",
    "generate_chapter_describes": "给哪一章生成章节说明？点一下，或者说章节号也行。",
    "add_chapter_element": "加到哪一章？点一下，或者说章节号就行。",
    "associate_elements": "关联到哪一章？点一下，或者说章节号就行。",
    "dissociate_chapter_element": "要取消哪一章、哪个要素的关联？",
    "remove_document_element": "要删文档里的哪个要素？",
    "re_study_chapters": "想分析哪一章？点一下，或说「分析第2章」。",
    "select_write_chapters": "先写哪一章？点一下，或说「帮我写2.3」。",
    "select_blueprint": "用哪一份蓝本？点一下确认，也可以说「再找一批」。",
    "materials_pack_use": "用哪一份资料包？点一下就行。",
    "materials_pack_rename": "给哪一份资料包改名？点一下确认。",
    "collect_to_element_library": "这几个都有点像，你要收入哪一个？",
}


def _writable_chapters(task: Any, *, limit: int = 6) -> list[tuple[str, str]]:
    """返回 [(chapter_key, label), …]，跳过纯目录节点。"""
    outline = getattr(task, "outline", None) if task is not None else None
    rows: list[tuple[str, str]] = []
    for node in list(getattr(outline, "chapters", None) or []):
        if bool(getattr(node, "structure_only", False)):
            continue
        key = str(getattr(node, "key", "") or "").strip()
        if not key:
            continue
        seq = str(getattr(node, "sequence", "") or "").strip()
        title = str(getattr(node, "title", "") or "").strip()
        label = f"{seq} {title}".strip() or key
        rows.append((key, label))
        if len(rows) >= limit:
            break
    return rows


def _chapter_elements(task: Any, chapter_key: str, *, limit: int = 6) -> list[tuple[str, str]]:
    """本章关联要素，文件类优先：[(element_id, name), …]。"""
    key = str(chapter_key or "").strip()
    if not task or not key:
        return []
    node = None
    for item in list(getattr(getattr(task, "outline", None), "chapters", None) or []):
        if str(getattr(item, "key", "") or "") == key:
            node = item
            break
    if node is None:
        return []
    from report_writing.domain.point_items_sync import (
        element_ids_for_chapter_node,
        elements_by_id_from_task,
    )

    by_id = elements_by_id_from_task(task)
    ids = element_ids_for_chapter_node(node, elements_by_id=by_id)
    file_first: list[tuple[str, str]] = []
    other: list[tuple[str, str]] = []
    for eid in ids:
        el = by_id.get(eid)
        if el is None:
            continue
        name = str(getattr(el, "name", "") or eid).strip()
        kind = str(getattr(el, "kind", "") or "").strip()
        (file_first if kind in {"file", "file_slot", "rag_search"} else other).append((eid, name))
    out = file_first + other
    return out[:limit]


def missing_action_slots(
    tool: str,
    payload: ChatPayload,
    *,
    session: ChatSession | None = None,
    task: Any = None,
) -> list[str]:
    """返回仍缺的必填槽位名。导航类返回空。"""
    name = str(tool or "").strip()
    body = ChatActionPayload.parse(payload).as_dict()
    required = _REQUIRED_SLOTS.get(name)
    if not required:
        return []
    missing: list[str] = []
    for slot in required:
        if slot == "chapter_key":
            raw = str(
                body.get("chapter_key") or body.get("chapter_key_hint") or ""
            ).strip()
            if not raw:
                missing.append("chapter_key")
        elif slot == "chapter_sequences":
            seqs = body.get("chapter_sequences") or body.get("sequences") or []
            keys = body.get("chapter_keys") or []
            if not (isinstance(seqs, list) and any(str(s).strip() for s in seqs)) and not (
                isinstance(keys, list) and any(str(k).strip() for k in keys)
            ):
                missing.append("chapter_sequences")
        elif slot == "blueprint_id":
            if not str(body.get("blueprint_id") or "").strip():
                missing.append("blueprint_id")
        elif slot == "pack_id":
            if not str(
                body.get("pack_id") or body.get("materials_pack_id") or ""
            ).strip():
                missing.append("pack_id")
        elif slot == "slot_no":
            try:
                no = int(body.get("slot_no") or body.get("no") or 0)
            except (TypeError, ValueError):
                no = 0
            if no < 1:
                missing.append("slot_no")
        elif slot == "element_id":
            if not str(body.get("element_id") or "").strip():
                missing.append("element_id")
        else:
            if body.get(slot) in (None, "", [], {}):
                missing.append(slot)
    _ = session  # 预留会话态（如焦点章）后续扩展
    return missing


def build_action_slot_clarify(
    session: ChatSession,
    tool: str,
    payload: ChatPayload,
    missing: list[str],
    *,
    task: Any = None,
    message: str = "",
    blueprints: list[Any] | None = None,
) -> tuple[str, list[ChatCard]]:
    """挂起 action_slots 澄清；options 携带要合并进 args 的槽位。"""
    from report_writing.chat.clarify_flow import begin_clarify

    name = str(tool or "").strip()
    body = ChatActionPayload.parse(payload).as_dict()
    task_id = str(body.get("task_id") or getattr(session, "task_id", "") or "").strip()
    if task_id:
        body.setdefault("task_id", task_id)
    miss = [m for m in (missing or []) if m]
    if not miss:
        miss = missing_action_slots(name, body, session=session, task=task)

    options: list[dict[str, Any]] = []
    primary = miss[0] if miss else "chapter_key"

    if primary == "chapter_key":
        prefer = str(body.get("_prefer_chapter_key") or "").strip()
        rows = _writable_chapters(task)
        if prefer:
            rows = sorted(rows, key=lambda r: 0 if r[0] == prefer else 1)
        for key, label in rows:
            tip = f"{label}（待确认）" if key == prefer else label
            args = {**body, "chapter_key": key, "chapter_key_hint": key}
            args.pop("_prefer_chapter_key", None)
            options.append(
                {
                    "id": f"ch_{key}",
                    "label": tip,
                    "slots": {
                        "tool": name,
                        "chapter_key": key,
                        "args": args,
                    },
                }
            )
    elif primary == "element_id":
        ck = str(body.get("chapter_key") or "").strip()
        # 有 blueprints 时优先用外部候选（收录多匹配）；否则用本章要素
        rows: list[Any] = list(blueprints or [])
        if not rows and ck:
            rows = list(_chapter_elements(task, ck))
        for item in rows[:8]:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                eid, ename = str(item[0]).strip(), str(item[1]).strip()
            elif isinstance(item, dict):
                eid = str(item.get("id") or item.get("element_id") or "").strip()
                ename = str(item.get("name") or eid).strip()
            else:
                eid = str(getattr(item, "id", "") or "").strip()
                ename = str(getattr(item, "name", "") or eid).strip()
            if not eid:
                continue
            args = {**body, "element_id": eid, "element_name": ename}
            if ck:
                args["chapter_key"] = ck
            options.append(
                {
                    "id": f"el_{eid}",
                    "label": ename or eid,
                    "slots": {
                        "tool": name,
                        "element_id": eid,
                        "args": args,
                    },
                }
            )
    elif primary == "chapter_sequences":
        for key, label in _writable_chapters(task):
            seq = label.split()[0] if label.split() else key
            args = {
                **body,
                "chapter_key": key,
                "chapter_keys": [key],
                "chapter_sequences": [seq],
                "sequences": [seq],
            }
            options.append(
                {
                    "id": f"seq_{key}",
                    "label": label,
                    "slots": {
                        "tool": name,
                        "chapter_key": key,
                        "chapter_sequences": [seq],
                        "args": args,
                    },
                }
            )
    elif primary == "blueprint_id":
        for item in list(blueprints or [])[:8]:
            bid = str(getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else "") or "").strip()
            title = str(
                getattr(item, "title", None)
                or (item.get("title") if isinstance(item, dict) else "")
                or bid
            ).strip()
            if not bid:
                continue
            options.append(
                {
                    "id": f"bp_{bid}",
                    "label": title or bid,
                    "slots": {
                        "tool": name,
                        "blueprint_id": bid,
                        "args": {**body, "blueprint_id": bid},
                    },
                }
            )
        options.append(
            {
                "id": "match_again",
                "label": "再找一批蓝本",
                "slots": {
                    "tool": "match_blueprints",
                    "args": {"message": str(message or body.get("message") or "").strip()},
                },
            }
        )
    elif primary == "pack_id":
        for item in list(blueprints or [])[:8]:
            # 复用 blueprints 参数传入资料包列表（dict 或对象）
            pid = str(
                getattr(item, "id", None)
                or (item.get("id") if isinstance(item, dict) else "")
                or ""
            ).strip()
            pname = str(
                getattr(item, "name", None)
                or (item.get("name") if isinstance(item, dict) else "")
                or pid
            ).strip()
            if not pid:
                continue
            options.append(
                {
                    "id": f"pack_{pid}",
                    "label": pname or pid,
                    "slots": {
                        "tool": name,
                        "pack_id": pid,
                        "args": {**body, "pack_id": pid, "materials_pack_id": pid},
                    },
                }
            )
    elif primary == "slot_no":
        for item in list(blueprints or [])[:12]:
            # blueprints 复用为槽位行：{no, name}
            raw_no = (
                getattr(item, "no", None)
                if not isinstance(item, dict)
                else item.get("no") or item.get("slot_no")
            )
            try:
                no = int(raw_no or 0)
            except (TypeError, ValueError):
                no = 0
            if no < 1:
                continue
            sname = str(
                getattr(item, "name", None)
                or (item.get("name") if isinstance(item, dict) else "")
                or f"槽位 {no}"
            ).strip()
            options.append(
                {
                    "id": f"slot_{no}",
                    "label": f"{no} {sname}",
                    "slots": {
                        "tool": name,
                        "slot_no": no,
                        "args": {**body, "slot_no": no, "no": no},
                    },
                }
            )

    if not options:
        # 无可点选项：仍挂起，引导用户口头补充
        options = [
            {
                "id": "say_again",
                "label": "我再说一次",
                "slots": {"tool": "", "args": {}},
            }
        ]

    question = _TOOL_QUESTIONS.get(name) or "还差一点信息，请选一项或直接补充。"
    if primary == "element_id" and str(body.get("chapter_key") or "").strip():
        question = "这一章挂了好几个要素，你想动哪一个？"
    if primary == "chapter_sequences":
        if name == "select_write_chapters":
            question = "我还没听准要写哪一章。点一下，或说「帮我写2.3」也行。"
        else:
            question = "我还没听准要分析哪一章。点一下，或说「分析第2章」也行。"
    if primary == "pack_id":
        question = _TOOL_QUESTIONS.get(name) or "用哪一份资料包？"
    if primary == "slot_no":
        question = "动哪个槽位？点一下，或直接说「槽位 2」。"
    if primary == "element_id" and name == "collect_to_element_library":
        question = "这几个名字有点像，你要收入哪一个？"
    if primary == "element_id" and name == "remove_document_element":
        question = "文档里有多个同名要素，你要删哪一个？"
    if primary == "element_id" and name == "dissociate_chapter_element":
        question = "同名要素有好几个，你要取消哪一个的关联？"

    candidate = {
        "kind": "control_tool",
        "name": name,
        "payload": {
            **body,
            "message": str(message or body.get("message") or "").strip(),
            "task_id": task_id,
            "_missing_slots": miss,
        },
    }
    return begin_clarify(
        session,
        source=ACTION_SLOT_SOURCE,
        question=question,
        options=options,
        candidate=candidate,
        phase_hint=ACTION_SLOT_SOURCE,
    )


def try_resolve_action_slot_message(
    session: ChatSession,
    message: str,
    *,
    task: Any = None,
) -> dict[str, Any] | None:
    """pending=action_slots 时，用原话匹配章节序号/标题或选项，合成 spec。"""
    import re

    from report_writing.chat.clarify_flow import _spec_from_option, clear_pending, get_pending

    pending = get_pending(session)
    if not pending or str(pending.get("source") or "") != ACTION_SLOT_SOURCE:
        return None
    text = (message or "").strip()
    if not text:
        return None
    options = list(pending.get("options") or [])
    candidate = dict(pending.get("candidate") or {})
    tool = str(candidate.get("name") or "").strip()
    base_payload = dict(candidate.get("payload") or {})

    if task is not None:
        from report_writing.chat.chapter_resolve import resolve_chapter_key_from_message

        key = resolve_chapter_key_from_message(task, text)
        if key:
            label = key
            for node in list(getattr(getattr(task, "outline", None), "chapters", None) or []):
                if str(getattr(node, "key", "") or "") == key:
                    seq = str(getattr(node, "sequence", "") or "").strip()
                    title = str(getattr(node, "title", "") or "").strip()
                    label = f"{seq} {title}".strip() or key
                    break
            args = {**base_payload, "chapter_key": key, "chapter_key_hint": key}
            if tool in {"re_study_chapters", "select_write_chapters", "write_analyzed_only"}:
                seq = label.split()[0] if label.split() else key
                # 优先用大纲节点真实序号，避免 label 退化成 key
                node_seq = ""
                for node in list(getattr(getattr(task, "outline", None), "chapters", None) or []):
                    if str(getattr(node, "key", "") or "") == key:
                        node_seq = str(getattr(node, "sequence", "") or "").strip()
                        break
                seq = node_seq or seq
                args["chapter_sequences"] = [seq]
                args["sequences"] = [seq]
                args["chapter_keys"] = [key]
            clear_pending(session)
            return {
                "candidate": candidate,
                "slots": {"tool": tool, "chapter_key": key, "args": args},
                "option_id": f"ch_{key}",
                "defaulted": False,
                "note": "",
                "source": ACTION_SLOT_SOURCE,
            }

    # 选项：整句 / 包含 / 序号前缀（「2.2」「第2.2章」）/ 第 N 份（蓝本）
    seq_hit = ""
    m = re.search(r"(?:第\s*)?(\d+(?:\.\d+)*)\s*章?", text)
    if m:
        seq_hit = m.group(1)
    ordinal = 0
    om = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*(?:份|个|本|项)", text)
    if om:
        raw = om.group(1)
        cn = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        if raw.isdigit():
            ordinal = int(raw)
        else:
            ordinal = int(cn.get(raw, 0) or 0)
    # 可点选项（排除「再找一批」等辅助项）
    pickable = [
        o
        for o in options
        if isinstance(o, dict)
        and str(o.get("id") or "") not in {"match_again", "say_again"}
        and (
            str((o.get("slots") or {}).get("blueprint_id") or "")
            or str((o.get("slots") or {}).get("pack_id") or "")
            or str((o.get("slots") or {}).get("chapter_key") or "")
            or str((o.get("slots") or {}).get("element_id") or "")
            or (o.get("slots") or {}).get("slot_no") not in (None, "", 0)
            or str(o.get("id") or "").startswith(
                ("bp_", "pack_", "ch_", "seq_", "study_", "el_", "slot_")
            )
        )
    ]
    if ordinal > 0 and ordinal <= len(pickable):
        clear_pending(session)
        return _spec_from_option(pending, pickable[ordinal - 1])

    # 「槽位 2」「第2槽」→ slot_no
    slot_oral = 0
    sm = re.search(r"(?:槽位|槽)\s*(\d+)|第\s*(\d+)\s*槽", text)
    if sm:
        try:
            slot_oral = int(sm.group(1) or sm.group(2) or 0)
        except (TypeError, ValueError):
            slot_oral = 0

    for opt in options:
        if not isinstance(opt, dict):
            continue
        label = str(opt.get("label") or "").strip()
        oid = str(opt.get("id") or "").strip()
        slots = dict(opt.get("slots") or {})
        try:
            opt_slot = int(slots.get("slot_no") or 0)
        except (TypeError, ValueError):
            opt_slot = 0
        if slot_oral > 0 and opt_slot == slot_oral:
            clear_pending(session)
            return _spec_from_option(pending, opt)
        if label and (text == label or label in text or text in label):
            clear_pending(session)
            return _spec_from_option(pending, opt)
        if seq_hit and label:
            head = label.split()[0] if label.split() else ""
            head_num = head.rstrip(".")
            if head == seq_hit or head_num == seq_hit or label.startswith(seq_hit + " "):
                clear_pending(session)
                return _spec_from_option(pending, opt)
            if opt_slot > 0 and str(opt_slot) == seq_hit:
                clear_pending(session)
                return _spec_from_option(pending, opt)
        if seq_hit and str(slots.get("chapter_key") or "") and oid.endswith(seq_hit.replace(".", "")):
            clear_pending(session)
            return _spec_from_option(pending, opt)
        if str(slots.get("element_id") or "") and (
            text == str(slots.get("element_id"))
            or text == str(slots.get("element_name") or "")
        ):
            clear_pending(session)
            return _spec_from_option(pending, opt)
    return None
