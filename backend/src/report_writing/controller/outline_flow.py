"""Task controller helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from report_writing.agents.supervisor import run_supervisor
from report_writing.services.corpus import load_template, normalize_biz_type
from report_writing.infra.llm import or_live_model
from report_writing.domain.models import (
    ChapterRequirement,
    ChapterState,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.domain.outline import (
    choose_best_outline,
    finalize_outline,
    load_outline_json,
    outline_as_text,
    outline_from_brief_chapters,
    outline_from_docx_path,
    outline_from_payload,
    outline_from_text,
    outline_tree_quality,
    read_template_text,
    read_text_for_outline,
    slice_toc_region,
    template_from_outline,
    try_outline_from_rag_html,
)
from report_writing.infra.postgres import open_library
from report_writing.services.store import BriefCache, TaskStore

_log = logging.getLogger("uvicorn.error")


def _toc_driven_rules_trustworthy(tree: OutlineTree | None, toc_meta: dict) -> bool:
    """目录页 / HYPERLINK 等规则抽出的树足够可信时，不再让模型改写结构。"""
    if tree is None or not tree.chapters:
        return False
    strategy = str((toc_meta or {}).get("strategy") or "")
    n = len(tree.chapters)
    q = outline_tree_quality(tree)
    if strategy in {"hyperlink_toc", "toc_heading"} and n >= 6 and q >= 32:
        return True
    if strategy == "dense_prefix" and n >= 10 and q >= 35:
        return True
    if n >= 20 and q >= 38:
        return True
    return False


def _prefer_toc_rules_tree(
    rule_tree: OutlineTree | None,
    tree: OutlineTree | None,
    toc_meta: dict,
) -> OutlineTree | None:
    """Word 样式目录常比 RAG 扁平，但正式「目录页」规则结果更贴近报告章节表。"""
    if rule_tree is None or not rule_tree.chapters:
        return tree
    if tree is None:
        return rule_tree
    strategy = str((toc_meta or {}).get("strategy") or "")
    if strategy not in {"hyperlink_toc", "toc_heading", "dense_prefix"}:
        return tree
    rq = outline_tree_quality(rule_tree)
    dq = outline_tree_quality(tree)
    rn = len(rule_tree.chapters)
    dn = len(tree.chapters)
    other_nested = sum(1 for item in tree.chapters if int(item.level or 1) >= 2)
    if dn >= max(20, int(rn * 1.8)) and other_nested >= max(8, int(rn * 0.5)):
        return tree
    if rn >= max(8, int(dn * 0.72)) and rq >= max(dq * 0.88, 28.0):
        return rule_tree
    return tree


def generate_outline(
    source_path: Path,
    task_dir: Path,
    biz_type: str,
    *,
    version: str = "",
    use_llm: bool = False,
    text: str | None = None,
    on_event=None,
    model=None,
) -> WritingTask:
    """上传文件 → RAG/本地解析全文 → 抽取目录树（可选用模型整理），等待人工确认。"""
    material_type = normalize_biz_type(biz_type)
    from report_writing.controller.write_flow import ensure_task

    store = TaskStore(task_dir)
    task = ensure_task(task_dir, biz_type=material_type)
    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "outline",
                "status": "running",
                "title": f"解析文档并生成目录（{source_path.name}）",
            }
        )
    if text is None:
        cache = Path(str(source_path) + ".rag-parse.md")
        if cache.is_file() and cache.stat().st_mtime >= source_path.stat().st_mtime and cache.stat().st_size > 0:
            text = cache.read_text(encoding="utf-8")
        else:
            text = read_template_text(source_path)
            try:
                cache.write_text(text or "", encoding="utf-8")
            except OSError:
                pass
    rag_text = text
    outline_text = read_text_for_outline(source_path, rag_markdown=rag_text)
    if len(outline_text or "") != len(rag_text or ""):
        _log.info(
            "目录抽取正文来源=local_docx outline_chars=%s rag_chars=%s",
            len(outline_text or ""),
            len(rag_text or ""),
        )
    text = outline_text
    toc_slice, toc_meta = slice_toc_region(text or "")
    _log.info(
        "目录大纲解析完成 file=%s chars=%s lines=%s use_llm=%s toc_strategy=%s toc_chars=%s",
        source_path.name,
        len(text or ""),
        (text or "").count("\n") + (1 if text else 0),
        use_llm,
        toc_meta.get("strategy"),
        len(toc_slice),
    )
    tree = None
    rule_error = None
    rule_tree = None
    try:
        rule_tree = outline_from_text(text)
    except Exception as error:
        rule_error = error
        _log.warning("RAG/规则抽目录失败：%s", error)
    docx_tree = outline_from_docx_path(source_path)
    html_tree = try_outline_from_rag_html(source_path)
    picked = choose_best_outline(("rules", rule_tree), ("docx", docx_tree), ("html", html_tree))
    label = ""
    if picked:
        label, tree = picked
    tree = _prefer_toc_rules_tree(rule_tree, tree, toc_meta)
    if tree is rule_tree:
        label = "rules"
    if tree is not None:
        _log.info(
            "目录多源择优 source=%s chapters=%s quality=%.1f docx=%s rules=%s",
            label or "rules",
            len(tree.chapters),
            outline_tree_quality(tree),
            len(docx_tree.chapters) if docx_tree else 0,
            len(rule_tree.chapters) if rule_tree else 0,
        )
    trust_rules = _toc_driven_rules_trustworthy(tree, toc_meta)
    from report_writing.infra.config_settings import outline_chapter_scan_max_chars, outline_multiround_enabled

    multiround = outline_multiround_enabled() and tree is not None and bool((text or "").strip())
    run_llm = bool(use_llm and not trust_rules and not multiround)
    if use_llm and trust_rules and not multiround:
        _log.info(
            "目录规则结果可信，跳过模型整理 strategy=%s chapters=%s quality=%.1f",
            toc_meta.get("strategy"),
            len(tree.chapters) if tree else 0,
            outline_tree_quality(tree) if tree else 0,
        )
    limit = int(toc_meta.get("max_chars") or 0)
    if run_llm and (toc_slice or text):
        llm_cap = max(16000, min(limit or 16000, 120000))
        llm_source = (toc_slice or (text or "")[:llm_cap])[:llm_cap]
        _log.info("目录分块模型整理 chars=%s", len(llm_source))
        try:
            llm_tree = _outline_with_llm_chunks(llm_source, model=model)
            if llm_tree and llm_tree.chapters:
                if tree is None:
                    tree = llm_tree
                elif outline_tree_quality(llm_tree) >= outline_tree_quality(tree) * 0.85:
                    tree = _merge_outline_prefer_primary(llm_tree, tree)
                else:
                    tree = _merge_outline_prefer_primary(tree, llm_tree)
        except Exception as error:
            _log.warning("分块模型整理目录失败：%s", error)
    if run_llm and tree is not None and (toc_slice or text):
        try:
            polished = _outline_polish_with_llm(
                tree, toc_slice or (text or "")[:60000], model=model
            )
            if polished and polished.chapters and outline_tree_quality(polished) >= outline_tree_quality(tree) * 0.95:
                tree = polished
        except Exception as error:
            _log.warning("目录校对模型失败：%s", error)
    if multiround:
        from report_writing.domain.outline_refine import expand_outline_chapterwise

        _log.info("目录多轮：第一轮规则粗抽 chapters=%s，开始按章第二轮", len(tree.chapters))
        if on_event is not None:
            on_event(
                {
                    "type": "step",
                    "step": "outline",
                    "status": "running",
                    "title": f"第一轮：目录页规则匹配（约 {len(tree.chapters)} 章）",
                    "result": {"round": 1, "chapters": len(tree.chapters)},
                }
            )
        tree = expand_outline_chapterwise(
            text or "",
            tree,
            use_llm=bool(use_llm),
            max_chars_per_chapter=outline_chapter_scan_max_chars(),
            on_event=on_event,
            model=model,
        )
    if tree is None:
        raise rule_error or ValueError("无法从文档提取目录")
    digest = version or hashlib.sha256(source_path.read_bytes()).hexdigest()[:16]
    task.template_id = task.template_id or f"{material_type}-{source_path.stem}"
    task.template_version = digest
    task.source_path = str(source_path)
    task.biz_type = material_type
    task.outline = tree
    task.outline_confirmed = False
    task.brief = None
    task.brief_confirmed = False
    task.chapters = []
    task.refresh_status()
    store.save(task)
    if on_event is not None:
        on_event(
            {
                "type": "step",
                "step": "outline",
                "status": "done",
                "title": f"目录结构已生成（{len(tree.chapters)} 章）",
                "result": {
                    "chapters": [item.to_dict() for item in tree.chapters],
                    "chapter_count": len(tree.chapters),
                    "source": source_path.name,
                },
            }
        )
    return task


# 分析/写作短预览误盖全量目录时，判定为意外裁剪的下限（少于则视为用户删了几章）
OUTLINE_ACCIDENTAL_SHRINK_MIN_DROP = 5


def _outline_node_keys(chapters: list[OutlineNode] | None) -> set[str]:
    return {
        str(getattr(node, "key", "") or "").strip()
        for node in (chapters or [])
        if str(getattr(node, "key", "") or "").strip()
    }


def _merge_outline_keep_existing(
    existing: list[OutlineNode],
    incoming: list[OutlineNode],
) -> list[OutlineNode]:
    """按 existing 顺序保留未出现在 incoming 的节点；同 key 用 incoming 覆盖。"""
    incoming_by = {
        str(node.key or "").strip(): node
        for node in incoming
        if str(node.key or "").strip()
    }
    seen: set[str] = set()
    merged: list[OutlineNode] = []
    for node in existing:
        key = str(node.key or "").strip()
        if key and key in incoming_by:
            merged.append(incoming_by[key])
            seen.add(key)
        else:
            merged.append(node)
            if key:
                seen.add(key)
    for node in incoming:
        key = str(node.key or "").strip()
        if key and key not in seen:
            merged.append(node)
            seen.add(key)
    return merged


def _preserve_chapter_element_bindings(
    existing: list[OutlineNode],
    incoming: list[OutlineNode],
) -> list[OutlineNode]:
    """整树 PUT / 短列表合并时：空 element_ids 不得冲掉已落盘关联。

    切会话、进度卡、关联后回写目录都可能带上空数组；取消关联走专用接口。
    """
    old_by = {
        str(node.key or "").strip(): node
        for node in existing
        if str(getattr(node, "key", "") or "").strip()
    }

    def _restore_point_links(old_node: OutlineNode, new_node: OutlineNode) -> None:
        old_items = list(getattr(old_node, "point_items", None) or [])
        new_items = list(getattr(new_node, "point_items", None) or [])
        if not old_items or not new_items:
            return
        by_id: dict[str, str] = {}
        by_text: dict[str, str] = {}
        for row in old_items:
            if not isinstance(row, dict):
                continue
            eid = str(row.get("element_id") or "").strip()
            if not eid:
                continue
            rid = str(row.get("id") or "").strip()
            if rid:
                by_id[rid] = eid
            text = str(row.get("text") or "").strip()
            if text:
                by_text[text] = eid
        restored = False
        next_items = []
        for row in new_items:
            if not isinstance(row, dict):
                next_items.append(row)
                continue
            nxt = dict(row)
            if not str(nxt.get("element_id") or "").strip():
                eid = by_id.get(str(nxt.get("id") or "").strip()) or by_text.get(
                    str(nxt.get("text") or "").strip()
                )
                if eid:
                    nxt["element_id"] = eid
                    restored = True
            next_items.append(nxt)
        if restored:
            new_node.point_items = next_items

    for node in incoming:
        key = str(getattr(node, "key", "") or "").strip()
        old = old_by.get(key)
        if old is None:
            continue
        new_ids = [str(x).strip() for x in (node.element_ids or []) if str(x).strip()]
        old_ids = [str(x).strip() for x in (old.element_ids or []) if str(x).strip()]
        if not new_ids and old_ids:
            node.element_ids = list(old_ids)
        elif new_ids and old_ids:
            old_set = set(old_ids)
            # 只提交新关联时不要冲掉已有；纯删减（incoming ⊆ old）仍按提交列表落盘
            if any(eid not in old_set for eid in new_ids):
                from report_writing.domain.elements import union_element_ids

                node.element_ids = union_element_ids(old_ids, new_ids)
        _restore_point_links(old, node)
    return incoming


def maybe_restore_outline_from_source_blueprint(task_dir: Path, task: WritingTask) -> WritingTask:
    """写作副本若被裁成源蓝本的小子集，从源蓝本补回缺失章（保留副本上已分析/已写字段）。"""
    from report_writing.controller.study_flow import _ensure_brief_ready
    from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

    bid = str(getattr(task, "source_blueprint_id", "") or "").strip()
    tid = str(getattr(task, "id", "") or task_dir.name).strip()
    if not bid or bid == tid:
        return task
    if not bool(getattr(task, "outline_confirmed", False)):
        return task
    src_path = task_dir.parent / bid
    if not src_path.exists():
        return task
    try:
        source = TaskStore(src_path).load()
    except Exception:
        return task
    src_chapters = list(source.outline.chapters or []) if source.outline else []
    cur_chapters = list(task.outline.chapters or []) if task.outline else []
    if not src_chapters or not cur_chapters:
        return task
    src_keys = _outline_node_keys(src_chapters)
    cur_keys = _outline_node_keys(cur_chapters)
    missing = len(src_keys) - len(cur_keys)
    if missing < OUTLINE_ACCIDENTAL_SHRINK_MIN_DROP:
        return task
    if not cur_keys or not cur_keys <= src_keys:
        return task
    _log.warning(
        "写作副本目录被裁剪，从源蓝本恢复 task=%s source=%s copy=%s blueprint=%s",
        tid,
        bid,
        len(cur_keys),
        len(src_keys),
    )
    restored = _merge_outline_keep_existing(src_chapters, cur_chapters)
    task.outline = finalize_outline(restored, preserve_parents=True)
    if task.outline.chapters:
        task.outline_confirmed = True
    _ensure_brief_ready(task)
    task.refresh_status()
    TaskStore(task_dir).save(task)
    invalidate_doc_outline_cache(tid)
    invalidate_doc_outline_cache(bid)
    return task


def save_outline(
    task_dir: Path,
    chapters: list[OutlineNode],
    *,
    replace_tree: bool = False,
) -> WritingTask:
    from report_writing.controller.study_flow import _ensure_brief_ready
    from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

    store = TaskStore(task_dir)
    task = store.load()
    incoming = list(chapters or [])
    existing = list(task.outline.chapters or []) if task.outline else []
    if (
        not replace_tree
        and bool(getattr(task, "outline_confirmed", False))
        and existing
        and incoming
    ):
        existing_keys = _outline_node_keys(existing)
        incoming_keys = _outline_node_keys(incoming)
        drop = len(existing_keys) - len(incoming_keys)
        if (
            incoming_keys
            and incoming_keys <= existing_keys
            and drop >= OUTLINE_ACCIDENTAL_SHRINK_MIN_DROP
        ):
            _log.warning(
                "拒绝用短目录替换已确认大纲 task=%s existing=%s incoming=%s，改为按 key 合并",
                getattr(task, "id", task_dir.name),
                len(existing_keys),
                len(incoming_keys),
            )
            incoming = _merge_outline_keep_existing(existing, incoming)
    incoming = _preserve_chapter_element_bindings(existing, incoming)
    tree = outline_from_payload([item.to_dict() for item in incoming])
    task.outline = tree
    from report_writing.domain.point_items_sync import normalize_chapter_point_items_on_node

    for node in task.outline.chapters or []:
        normalize_chapter_point_items_on_node(task, node)
    if task.outline_confirmed:
        _ensure_brief_ready(task)
        if task.brief is not None:
            from report_writing.domain.elements import sync_brief_from_outline

            sync_brief_from_outline(task)
    else:
        task.brief = None
        task.brief_confirmed = False
        task.chapters = []
    task.refresh_status()
    store.save(task)
    invalidate_doc_outline_cache(str(getattr(task, "id", "") or task_dir.name))
    source_bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
    if source_bp:
        invalidate_doc_outline_cache(source_bp)
    return task


def save_element_nodes(task_dir: Path, elements: list) -> WritingTask:
    from report_writing.controller.study_flow import _lock_brief_to_outline
    from report_writing.domain.elements import save_elements
    from report_writing.domain.models import ElementNode
    from report_writing.services.element_library import sync_task_elements_to_library

    store = TaskStore(task_dir)
    task = store.load()
    nodes = [item if isinstance(item, ElementNode) else ElementNode.model_validate(item) for item in elements]
    # 保存某一章会带上全部要素。其它章的要素若这次没带上槽位/文件，沿用已保存的，不要整单拒绝。
    _keep_saved_bindings_when_payload_omits_them(task, nodes)
    _align_file_slots_to_pack_order(task_dir.parent.parent, task, nodes)
    save_elements(task, nodes)
    if task.brief is not None and task.outline is not None:
        _lock_brief_to_outline(task.brief, task.outline)
        if bool(getattr(task, "outline_confirmed", False)):
            from report_writing.domain.elements import sync_brief_from_outline

            sync_brief_from_outline(task)
    store.save(task)
    # task_dir = <task_root>/tasks/<id>；要素库 API 以 task_root 为 home
    home = task_dir.parent.parent
    try:
        sync_task_elements_to_library(home, nodes)
    except Exception:
        # 库回写失败不阻断任务保存
        pass
    try:
        from report_writing.services.materials_pack import sync_packs_for_task

        sync_packs_for_task(home, task)
    except Exception:
        pass
    return task


def _align_file_slots_to_pack_order(home: Path, task, nodes: list) -> None:
    """已有资料包展示顺序时，要素引用槽位按该顺序保存，避免第一次保存后和槽位错位。"""
    pack_id = str(getattr(task, "materials_pack_id", "") or "").strip()
    if not pack_id:
        return
    try:
        from report_writing.services.materials_pack import get_pack

        pack = get_pack(home, pack_id)
    except Exception:
        return
    if pack is None:
        return
    order = [int(n) for n in (getattr(pack, "slot_order", None) or []) if int(n) >= 1]
    if not order:
        return
    rank = {no: index for index, no in enumerate(order)}
    for node in nodes or []:
        if str(getattr(node, "kind", "") or "").strip() != "file_slot":
            continue
        slots = list(getattr(node, "file_slots", None) or [])
        if len(slots) < 2:
            continue
        node.file_slots = sorted(
            slots,
            key=lambda slot: rank.get(int(getattr(slot, "no", 0) or 0), 10_000),
        )


def _binding_slot_nos(node) -> list[int]:
    nos: list[int] = []
    for slot in list(getattr(node, "file_slots", None) or []):
        try:
            no = int(getattr(slot, "no", None) or (slot.get("no") if isinstance(slot, dict) else 0) or 0)
        except (TypeError, ValueError):
            no = 0
        if no >= 1:
            nos.append(no)
    return nos


def _keep_saved_bindings_when_payload_omits_them(task, nodes: list) -> None:
    """本次没带上的槽位/文件沿用库里已有的。

    文档窗保存「建设规模」时会提交任务上的全部要素。另一章的「变电站站址」
    若在这次请求里丢了槽位编号，不能因此拒绝当前章，也不能把已配好的引用冲掉。
    """
    saved = {
        str(getattr(item, "id", "") or "").strip(): item
        for item in (getattr(task, "element_nodes", None) or [])
        if str(getattr(item, "id", "") or "").strip()
    }
    for node in nodes or []:
        prev = saved.get(str(getattr(node, "id", "") or "").strip())
        if prev is None:
            continue
        kind = str(getattr(node, "kind", "") or "").strip()
        if kind == "file_slot" and not _binding_slot_nos(node) and _binding_slot_nos(prev):
            node.file_slots = list(getattr(prev, "file_slots", None) or [])
        if kind in {"file", "rag_search"}:
            incoming = list(getattr(node, "files", None) or [])
            previous = list(getattr(prev, "files", None) or [])
            if not incoming and previous:
                node.files = previous


def import_outline(task_dir: Path, raw) -> WritingTask:
    from report_writing.controller.write_flow import ensure_task

    tree = load_outline_json(raw)
    store = TaskStore(task_dir)
    task = ensure_task(task_dir)
    task.outline = tree
    task.outline_confirmed = False
    task.brief = None
    task.brief_confirmed = False
    task.chapters = []
    task.refresh_status()
    store.save(task)
    return task


def outline_from_pasted_text(
    task_dir: Path,
    text: str,
    *,
    use_llm: bool = False,
    biz_type: str = "",
    model=None,
) -> WritingTask:
    from report_writing.controller.write_flow import ensure_task

    store = TaskStore(task_dir)
    task = ensure_task(task_dir, biz_type=biz_type or "002")
    cleaned = (text or "").strip()
    if not cleaned:
        raise RuntimeError("请粘贴目录文本")
    if use_llm:
        tree = _outline_with_llm(cleaned, model=model)
    else:
        tree = _outline_from_plain_lines(cleaned, model=model)
    if biz_type.strip():
        task.biz_type = normalize_biz_type(biz_type)
    task.outline = tree
    task.outline_confirmed = False
    task.brief = None
    task.brief_confirmed = False
    task.chapters = []
    task.refresh_status()
    store.save(task)
    return task


def confirm_outline(task_dir: Path) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is None or not task.outline.chapters:
        raise RuntimeError("请先生成或导入目录结构")
    # 确认只钉状态，不因 level 栈重挂父子
    task.outline = finalize_outline(list(task.outline.chapters), preserve_parents=True)
    task.outline_confirmed = True
    task.refresh_status()
    store.save(task)
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(str(getattr(task, "id", "") or task_dir.name))
    except Exception:
        pass
    return task


def revoke_outline(task_dir: Path) -> WritingTask:
    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is None:
        raise RuntimeError("还没有目录结构")
    task.outline_confirmed = False
    task.brief = None
    task.brief_confirmed = False
    task.chapters = []
    task.refresh_status()
    store.save(task)
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(str(getattr(task, "id", "") or task_dir.name))
    except Exception:
        pass
    return task


def generate_outline_from_sample(
    task_dir: Path,
    *,
    use_llm: bool = False,
    on_event=None,
    model=None,
) -> WritingTask:
    """用蓝本已解析样本生成目录大纲。"""
    from report_writing.controller.blueprint import resolve_sample_path, resolve_sample_text
    store = TaskStore(task_dir)
    task = store.load()
    source = resolve_sample_path(task)
    if source is None:
        raise RuntimeError("请先为蓝本上传样本")
    text = resolve_sample_text(task)
    task.sample_chars = len(text)
    task.sample_ready = bool(text.strip())
    store.save(task)
    return generate_outline(
        source,
        task_dir,
        task.biz_type or "002",
        use_llm=use_llm,
        text=text,
        on_event=on_event,
        model=model,
    )


def study_from_outline(
    task_dir: Path,
    cache_dir: Path,
    *,
    force_study: bool = False,
    on_event=None,
    model=None,
) -> WritingTask:
    """目录确认后，按树回原文切章并学习。"""
    from report_writing.controller.study_flow import _lock_brief_to_outline
    store = TaskStore(task_dir)
    task = store.load()
    if task.outline is None or not task.outline_confirmed:
        raise RuntimeError("请先确认目录结构")
    source = Path(task.source_path) if task.source_path else None
    if source is None or not source.is_file():
        raise RuntimeError("请先上传原文报告，再按目录分析")
    text = read_template_text(source)
    parsed = template_from_outline(
        task.outline,
        text,
        biz_type=task.biz_type,
        stem=source.stem,
        version=task.template_version or hashlib.sha256(source.read_bytes()).hexdigest()[:16],
    )
    template_path = Path(task.template_path) if task.template_path else source.parent / "template.json"
    if not task.template_path:
        template_path = source.parent / "template.json"
    template_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    task.template_path = str(template_path)
    task.template_id = parsed["id"]
    task.template_version = str(parsed["version"])
    template = load_template(template_path)
    if on_event is not None:
        unmatched = [
            item["title"]
            for item in chapters_match_report(task.outline, text)
            if not item["matched"]
        ]
        if unmatched:
            on_event(
                {
                    "type": "step",
                    "step": "study",
                    "status": "running",
                    "title": f"{len(unmatched)} 章未匹配到原文",
                    "result": {"note": "、".join(unmatched[:8])},
                }
            )
    managed = run_supervisor(
        phase="study",
        template=template,
        cache=BriefCache(cache_dir),
        model=or_live_model(model),
        force_study=force_study,
        on_event=on_event,
    )
    task.brief = managed.brief
    task.brief_from_cache = managed.brief_from_cache
    task.brief_confirmed = False
    task.study_runs = managed.study_runs
    task.chapters = managed.chapters
    task.steps = list(task.steps) + list(managed.steps)
    if task.brief is not None and task.outline is not None:
        _lock_brief_to_outline(task.brief, task.outline)
        from report_writing.controller.study_flow import _push_brief_points_to_outline

        # brief→outline：文档窗/向导以 outline.points 判定已学
        _push_brief_points_to_outline(task)
    task.refresh_status()
    store.save(task)
    return task


def chapters_match_report(tree: OutlineTree, text: str) -> list[dict]:
    from report_writing.domain.outline import chapters_from_outline

    return chapters_from_outline(tree, text)


def _outline_from_plain_lines(text: str, *, model=None) -> OutlineTree:
    nodes = []
    for line in text.splitlines():
        raw = line.rstrip()
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        level = indent // 2 + 1
        body = raw.strip()
        match = re.match(r"^(?P<sequence>\d+(?:\.\d+)*)\s+(?P<title>.+)$", body)
        if match:
            nodes.append(
                OutlineNode(
                    sequence=match.group("sequence"),
                    title=match.group("title").strip(),
                    level=match.group("sequence").count(".") + 1,
                )
            )
        else:
            nodes.append(OutlineNode(sequence="", title=body, level=level))
    return finalize_outline(nodes, model=model)


def _merge_outline_prefer_primary(primary: OutlineTree, secondary: OutlineTree) -> OutlineTree:
    """合并两棵目录：以 primary 为准，用 secondary 补缺序号。"""
    by_seq: dict[str, OutlineNode] = {}
    for item in list(primary.chapters or []) + list(secondary.chapters or []):
        seq = (item.sequence or "").strip()
        title = (item.title or "").strip()
        if not seq or not title:
            continue
        if seq not in by_seq:
            by_seq[seq] = OutlineNode(
                sequence=seq,
                title=title[:40],
                level=int(item.level or 1),
                parent_sequence=(item.parent_sequence or "").strip(),
            )
    if not by_seq:
        return primary
    return finalize_outline(list(by_seq.values()))


def _outline_polish_with_llm(tree: OutlineTree, toc_slice: str, *, model=None) -> OutlineTree | None:
    """用目录区原文校对规则/合并结果，删掉误抽条目。"""
    model = or_live_model(model)
    if model is None or not tree.chapters:
        return None
    if len(tree.chapters) > 200:
        return None
    excerpt = (toc_slice or "")[: min(len(toc_slice or ""), 48000)]
    entries = [
        {"sequence": n.sequence or "", "title": n.title or "", "level": int(n.level or 1)}
        for n in tree.chapters[:400]
    ]
    if not excerpt.strip() or not entries:
        return None
    payload = json.dumps(entries, ensure_ascii=False)
    sent = entries
    if len(payload) > 18000:
        sent = entries[:180]
        payload = json.dumps(sent, ensure_ascii=False)
    prompt = (
        "# Role\n你是报告目录校对助手。\n"
        "# Task\n根据目录区原文修正待校对列表：删除明显不是目录的句子；修正错乱序号与标题；"
        "保留全部真实章节与子节；不要合并章节、不要编造。\n"
        "# Inputs\n"
        f"<toc_source>\n{excerpt}\n</toc_source>\n"
        f"<draft_outline>\n{payload}\n</draft_outline>\n"
        "# Output\n只返回 JSON 数组，字段 sequence、title、level。"
    )
    try:
        message = model.invoke(prompt)
        content = getattr(message, "content", None) or str(message)
        match = re.search(r"\[[\s\S]*\]", content)
        parsed = json.loads(match.group(0) if match else content)
        if not isinstance(parsed, list):
            return None
        if len(parsed) < max(3, int(len(sent) * 0.88)):
            return None
        if len(parsed) > int(len(sent) * 1.2) + 5:
            return None
        polished = outline_from_payload(parsed, model=model)
        if outline_tree_quality(polished) >= outline_tree_quality(tree) * 0.95:
            return polished
        return None
    except Exception as error:
        _log.warning("目录校对解析失败：%s", error)
        return None


def _outline_with_llm_chunks(text: str, *, max_chunk: int = 9000, model=None) -> OutlineTree | None:
    """长目录分块送模型，按序号合并（避免单次 12k 截断漏章）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in raw.splitlines():
        extra = len(line) + 1
        if buf and size + extra > max_chunk:
            chunks.append("\n".join(buf))
            buf = []
            size = 0
        buf.append(line)
        size += extra
    if buf:
        chunks.append("\n".join(buf))
    if not chunks:
        return None
    merged: OutlineTree | None = None
    for index, chunk in enumerate(chunks):
        part = _outline_with_llm(chunk, model=model)
        if not part.chapters:
            continue
        merged = part if merged is None else _merge_outline_prefer_primary(merged, part)
        _log.info("目录分块 %s/%s entries=%s", index + 1, len(chunks), len(part.chapters))
    return merged


def _outline_with_llm(text: str, *, model=None) -> OutlineTree:
    model = or_live_model(model)
    if model is None:
        return _outline_from_plain_lines(text, model=model)
    # 已是目录切片时仍限长，防止异常切片过大
    clipped = (text or "")[:24000]
    prompt = (
        "下面是报告「目录区」文本（不是全文）。请整理成 JSON 数组。只返回 JSON，不要解释。"
        '每项字段：sequence（章节序号，没有则空字符串）、title、level（从 1 起）。'
        "只提取目录条目，不要把正文段落、表题、附件说明当成章节；"
        "保持原有顺序与层级，不要合并、不要省略子节、不要编造。\n\n"
        f"{clipped}"
    )
    try:
        message = model.invoke(prompt)
        content = getattr(message, "content", None) or str(message)
        match = re.search(r"\[[\s\S]*\]", content)
        payload = json.loads(match.group(0) if match else content)
        if not isinstance(payload, list):
            raise ValueError("模型没有返回数组")
        return outline_from_payload(payload, model=model)
    except Exception as error:
        _log.warning("模型整理目录失败，改用规则解析：%s", error)
        try:
            return outline_from_text(text)
        except Exception:
            return _outline_from_plain_lines(text, model=model)


