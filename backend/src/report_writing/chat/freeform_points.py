"""无蓝本：写作要点引导与按目录批量起草。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from report_writing.chat.dialogue import format_full_dialogue
from report_writing.controller import confirm_outline, save_outline
from report_writing.domain.models import ChatCard, ChatPhase, ChatSession, OutlineNode, WritingTask

InvokeFn = Callable[[str, str], str]

_POINTS_BATCH = 6

_POINTS_SYSTEM = (
    "# Role\n你是资深文档写作专家。文档类型由输入 doc_category 指定（函件、方案、说明书、纪要、合同、述职、报告等），"
    "不默认研究报告。\n"
    "# Task\n根据文档类型、标题与章节目录（无样板原文），为每章起草可跨项目复用的写作施工图："
    "组成部分、格式、怎么写、要点。\n"
    "# Rules\n"
    "- 先想本章通常由哪几块组成（开篇、依据、分述、表/清单、结论），再写 point_items，一条一块。\n"
    "- point_items.text 用「【组成部分】怎么写；版式：段落|条列|表格；须覆盖：信息类型」。"
    "缺数据处含「（待补充：资料类型）」并 needs_supplement=true、slot 填待补项名称。\n"
    "- describes 必须 1. 2. 分点，只写怎么写与格式（风格/组成块先后/描述方式/排版/邻章边界），不要内容提纲。\n"
    "- points 与 point_items 二选一；若只给 points，每条仍须对应一个组成部分。\n"
    "- material_slots：本章通常需要用户准备的资料类型（无则 []）。\n"
    "- structure_only 为 true：points/keywords/material_slots 可空，describes 说明仅作目录分组。\n"
    "- 必须按输入的 key 逐章返回；不要把多章写成同一套概括话术。\n"
    "# Output\n只返回 JSON 数组，不要 Markdown。每项字段：\n"
    '{"key":"章节key",'
    '"point_items":[{"id":"p0","text":"【组成部分】怎么写；版式：…；须覆盖：…","slot":"待补项名称或留空",'
    '"needs_supplement":false}],'
    '"points":["与 point_items[].text 同序，可省略若已给 point_items"],'
    '"describes":"1. 写作风格：…\\n2. 写作特点：…","keywords":["5～10个术语"],'
    '"material_slots":[{"name":"资料名","hint":"补什么"}],"structure_only":false}'
)


def count_chapters_missing_points(task: WritingTask) -> int:
    if not task.outline or not task.outline.chapters:
        return 0
    n = 0
    for node in task.outline.chapters:
        if bool(getattr(node, "structure_only", False)):
            continue
        if not (node.points or []) and not (node.describes or "").strip():
            n += 1
    return n


def freeform_points_help_card(session: ChatSession, task: WritingTask) -> ChatCard:
    tid = (session.task_id or task.id or "").strip()
    n = len(task.outline.chapters) if task.outline else 0
    return ChatCard(
        type="freeform_points_help",
        title="需要协助准备写作要点吗？",
        payload={
            "task_id": tid,
            "chapter_count": n,
            "hint": (
                "无蓝本写作时，每章最好先有几条「怎么写」的指引（要点、简述、检索词）。"
                "如果你不太熟悉，我可以按目录自动起草；熟悉的话可以自己粘贴说明，或先跳过直接开写。"
            ),
        },
    )


def _structure_only_keys(chapters: list[OutlineNode]) -> set[str]:
    keys = {str(n.key or "").strip() for n in chapters}
    parents = {str(n.parent or "").strip() for n in chapters if str(n.parent or "").strip()}
    return parents & keys


def _parse_chapter_points_json(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.I)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict) and "chapters" in data:
        data = data["chapters"]
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for row in data:
        if isinstance(row, dict) and str(row.get("key") or "").strip():
            out.append(row)
    return out


def _chapter_context_rows(
    chapters: list[OutlineNode],
    *,
    structure_parents: set[str],
    want: set[str] | None,
    max_chapters: int,
) -> list[dict[str, Any]]:
    by_key = {str(n.key or "").strip(): n for n in chapters if str(n.key or "").strip()}
    # 同级：按 parent 分组标题
    siblings: dict[str, list[str]] = {}
    for n in chapters:
        pk = str(n.parent or "").strip() or "__root__"
        title = str(n.title or "").strip()
        if title:
            siblings.setdefault(pk, []).append(title)

    rows: list[dict[str, Any]] = []
    for node in chapters[:max_chapters]:
        key = str(node.key or "").strip()
        if not key:
            continue
        if want is not None and key not in want:
            continue
        parent_key = str(node.parent or "").strip()
        parent = by_key.get(parent_key)
        sib_key = parent_key or "__root__"
        sib_titles = [t for t in siblings.get(sib_key, []) if t != str(node.title or "").strip()][:8]
        rows.append(
            {
                "key": key,
                "sequence": str(node.sequence or ""),
                "title": str(node.title or ""),
                "level": int(getattr(node, "level", 1) or 1),
                "parent_title": str(getattr(parent, "title", "") or "").strip() if parent else "",
                "sibling_titles": sib_titles,
                "structure_only": key in structure_parents,
            }
        )
    return rows


def _fallback_points(title: str, category: str, *, level: int = 1) -> list[str]:
    tip = (title or "本章").strip()
    cat = (category or "报告").strip()
    return [
        f"【开篇定位】用 1～2 句说明「{tip}」在整份{cat}中的作用与读者预期；版式：段落；须覆盖：本章约束范围（待补充：本章约束范围）",
        "【主体分述】按信息类型分条写对象/范围、依据或标准、现状或过程、结论或建议；版式：条列或短段；须覆盖：对应信息类型",
        "【表格或清单】若本章以表为主：先写表题与列类型，再按行填内容类别；版式：表格；须覆盖：列名与行对象，勿编造数值",
        "【依据与口径】点明判定所依据的标准、规范或文件类型，不抄实例条款；版式：段落或条列；须覆盖：依据类型",
        f"【边界】不重复上级已写内容，下级展开处只留索引（level={level}）；版式：段落；须覆盖：与邻章分工",
        "【收束】归纳本章可核查的交付物类型（清单、表、结论段）；版式：段落；须覆盖：仍缺的资料类型",
    ]


def _fallback_describes(title: str, category: str, parent_title: str = "") -> str:
    tip = (title or "本章").strip()
    cat = (category or "报告").strip()
    parent = f"隶属「{parent_title}」。" if parent_title else ""
    from report_writing.domain.compose_analysis import format_describes_as_points

    return format_describes_as_points(
        "\n".join(
            [
                f"写作风格：与{cat}体例一致，语气克制{('；' + parent.rstrip('。')) if parent else ''}",
                f"写作特点：「{tip}」先交代目的与适用范围，再按信息类型分节或列表展开",
                "描述方式：固定栏目写清栏位含义，需项目填报处标明资料类型",
                "排版格式：分节或列表，层级清楚，避免与上下级章重复概括",
                "写作注意事项：不写具体实例数据",
            ]
        )
    )


def _invoke_points_batch(invoke: InvokeFn, system: str, user: str, *, model=None) -> str:
    """要点专用调用：尽量 temperature=0，失败则回落普通 invoke。"""
    from report_writing.infra.llm import or_live_model
    from report_writing.infra.llm_invoke import invoke_prompt

    chosen = or_live_model(model)
    if chosen is not None:
        try:
            cold = chosen.bind(temperature=0.0) if hasattr(chosen, "bind") else chosen
            return str(invoke_prompt(cold, system, user) or "").strip()
        except Exception:
            try:
                return str(invoke_prompt(chosen, system, user) or "").strip()
            except Exception:
                pass
    return str(invoke(system, user) or "").strip()


def llm_draft_chapter_points(
    session: ChatSession,
    task: WritingTask,
    *,
    invoke: InvokeFn,
    max_chapters: int = 40,
    emit: Any = None,
    target_keys: set[str] | list[str] | None = None,
    model=None,
) -> list[OutlineNode]:
    from report_writing.chat.user_process import emit_user_process

    chapters = list(task.outline.chapters or []) if task.outline else []
    if not chapters:
        return []
    structure_parents = _structure_only_keys(chapters)
    meta = dict(session.freeform or {})
    category = str(meta.get("category") or task.category or "通用").strip()
    title = str(meta.get("title") or task.title or "未命名").strip()
    dialogue = format_full_dialogue(session, max_turns=8)

    want: set[str] | None = None
    if target_keys:
        want = {str(k).strip() for k in target_keys if str(k).strip()}
        if not want:
            want = None

    payload_chapters = _chapter_context_rows(
        chapters,
        structure_parents=structure_parents,
        want=want,
        max_chapters=max_chapters,
    )
    if not payload_chapters:
        return chapters

    writable_n = sum(1 for row in payload_chapters if not row.get("structure_only"))
    emit_user_process(
        emit,
        f"正在按目录为 {writable_n} 个可写章节起草写作要点（分批推理，可能需要十几秒）…",
    )

    drafts: dict[str, dict[str, Any]] = {}
    batches = [
        payload_chapters[i : i + _POINTS_BATCH]
        for i in range(0, len(payload_chapters), _POINTS_BATCH)
    ]
    for bi, batch in enumerate(batches, start=1):
        if len(batches) > 1:
            emit_user_process(emit, f"正在起草第 {bi}/{len(batches)} 批章节要点…")
        user = json.dumps(
            {
                "doc_category": category,
                "doc_title": title,
                "dialogue_tail": dialogue[-4:],
                "chapters": batch,
            },
            ensure_ascii=False,
        )
        raw = _invoke_points_batch(invoke, _POINTS_SYSTEM, user, model=model)
        for row in _parse_chapter_points_json(raw):
            key = str(row.get("key") or "").strip()
            if key:
                drafts[key] = row

    emit_user_process(emit, "要点草案已返回，正在按章写入配置…")

    updated: list[OutlineNode] = []
    for node in chapters:
        data = node.to_dict()
        key = str(data.get("key") or "").strip()
        # 未点名的章保持原样，避免空 draft 回填占位要点覆盖其它章
        if want is not None and key not in want:
            updated.append(node)
            continue
        draft = drafts.get(key) or {}
        structure_only = bool(draft.get("structure_only")) or key in structure_parents
        data["structure_only"] = structure_only
        parent_title = ""
        parent_key = str(getattr(node, "parent", "") or "").strip()
        if parent_key:
            for n in chapters:
                if str(n.key or "").strip() == parent_key:
                    parent_title = str(n.title or "").strip()
                    break
        if structure_only:
            data["points"] = []
            data["point_items"] = []
            data["keywords"] = []
            data["element_ids"] = []
            data["describes"] = (
                str(draft.get("describes") or "").strip()
                or f"目录节点「{data.get('title') or key}」，正文由子节展开。"
            )
        else:
            pts = [str(p).strip() for p in (draft.get("points") or []) if str(p).strip()]
            raw_items = draft.get("point_items")
            if not pts and isinstance(raw_items, list):
                from report_writing.domain.point_items_sync import points_from_point_items

                pts = points_from_point_items(raw_items)
            if not pts:
                pts = _fallback_points(
                    str(data.get("title") or key),
                    category,
                    level=int(data.get("level") or 1),
                )
            from report_writing.domain.point_items_sync import merge_generated_point_items

            # 重新生成要点：不继承旧行要素关联，并清空章级 element_ids
            pts, items = merge_generated_point_items(
                pts[:12], raw_items if isinstance(raw_items, list) else None, prev_items=[]
            )
            data["points"] = pts
            data["point_items"] = items
            data["element_ids"] = []
            describes = str(draft.get("describes") or "").strip()
            if not describes:
                describes = _fallback_describes(
                    str(data.get("title") or key), category, parent_title=parent_title
                )
            data["describes"] = describes
            kws = [str(k).strip() for k in (draft.get("keywords") or []) if str(k).strip()]
            data["keywords"] = kws[:10]
            slots = draft.get("material_slots") or draft.get("project_slots") or []
            if isinstance(slots, list) and slots:
                # 无蓝本路径暂存到 aux_prompt 前缀，便于用户对照补资料
                slot_lines = []
                for s in slots[:8]:
                    if isinstance(s, dict):
                        name = str(s.get("name") or "").strip()
                        hint = str(s.get("hint") or "").strip()
                        if name:
                            slot_lines.append(f"资料·{name}" + (f"：{hint}" if hint else ""))
                    elif str(s).strip():
                        slot_lines.append(f"资料·{str(s).strip()}")
                if slot_lines and not (data.get("aux_prompt") or "").strip():
                    data["aux_prompt"] = (
                        "【建议补充资料】\n"
                        + "\n".join(slot_lines)
                        + "\n\n"
                        + "\n".join(data["points"][:8])
                    )[:800]
            if not (data.get("aux_prompt") or "").strip():
                data["aux_prompt"] = "\n".join(data["points"][:8])[:800]
        updated.append(OutlineNode.from_dict(data))
    return updated


def apply_generated_points(
    home: Path,
    task_id: str,
    chapters: list[OutlineNode],
    *,
    emit: Any = None,
    target_keys: set[str] | list[str] | None = None,
) -> None:
    """落盘要点：整表写入后按章发出 study_chapter_progress（与蓝本分析同事件），便于文档窗定位。"""
    from report_writing.chat.user_process import emit_user_process
    from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

    path = home / "tasks" / task_id
    tid = (task_id or "").strip()
    save_outline(path, chapters)
    invalidate_doc_outline_cache(tid)

    want: set[str] | None = None
    if target_keys:
        want = {str(k).strip() for k in target_keys if str(k).strip()}
        if not want:
            want = None

    writable = [
        c
        for c in chapters
        if not bool(getattr(c, "structure_only", False))
        and (want is None or str(c.key or "").strip() in want)
    ]
    total = len(writable)
    for index, node in enumerate(writable, start=1):
        key = str(node.key or "").strip()
        if not key:
            continue
        seq = str(node.sequence or "").strip()
        title = str(node.title or key).strip()
        label = f"{seq} {title}".strip() if seq else title
        pts = list(node.points or [])
        kws = list(getattr(node, "keywords", None) or [])
        items = list(getattr(node, "point_items", None) or [])
        describes = str(getattr(node, "describes", "") or "").strip()
        analyzed = index
        unanalyzed = max(0, total - index)
        emit_user_process(emit, f"已起草「{label}」要点（{index}/{total}）…")
        if callable(emit):
            emit(
                {
                    "type": "study_chapter_progress",
                    "task_id": tid,
                    "chapter": key,
                    "title": label,
                    "sequence": seq,
                    "index": index,
                    "total": total,
                    "points": pts,
                    "point_items": items,
                    "keywords": kws,
                    "describes": describes,
                    "structure_only": False,
                    "clear_element_bindings": True,
                    "study_analyzed": analyzed,
                    "study_unanalyzed": unanalyzed,
                    "freeform_points": True,
                }
            )
    confirm_outline(path)
    invalidate_doc_outline_cache(tid)
    emit_user_process(emit, f"各章写作要点已起草（{total} 章）")


def cards_after_outline_confirmed(
    home: Path,
    session: ChatSession,
    inner_cards: list[ChatCard],
    task: WritingTask,
) -> list[ChatCard]:
    from report_writing.chat.freeform_blueprint import append_save_blueprint_offer

    meta = dict(session.freeform or {})
    meta["points_step"] = "ask"
    session.freeform = meta
    session.phase = ChatPhase.FREEFORM_POINTS.value
    cards: list[ChatCard] = [freeform_points_help_card(session, task)]
    cards.extend(inner_cards)
    return append_save_blueprint_offer(home, session, cards)


def finish_points_phase_cards(home: Path, session: ChatSession) -> list[ChatCard]:
    from report_writing.chat.freeform_blueprint import append_save_blueprint_offer

    task_id = (session.task_id or "").strip()
    session.phase = ChatPhase.READY_TO_WRITE.value
    meta = dict(session.freeform or {})
    meta["outline_confirmed"] = True
    session.freeform = meta
    # 书写节奏卡已在前端隐藏；给可点的「开始写作」，并与文档窗「开始写作」一致（batch 依次写）
    cards = [
        ChatCard(
            type="start_writing",
            title="开始写作",
            payload={
                **({"task_id": task_id} if task_id else {}),
                "writing_pace": "batch",
            },
        )
    ]
    return append_save_blueprint_offer(home, session, cards)
