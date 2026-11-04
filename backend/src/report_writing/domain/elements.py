"""独立要素节点的落盘、关联解析与章节子树运行范围。"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from report_writing.services.corpus import Corpus
from report_writing.domain.models import ChapterRequirement, ElementNode, OutlineNode, OutlineTree, WritingTask
from report_writing.infra.postgres import load_database_settings

_log = logging.getLogger("uvicorn.error")

_ELEMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_element_node (
    task_id varchar(128) NOT NULL,
    element_id varchar(64) NOT NULL,
    name varchar(256) NOT NULL,
    kind varchar(32) NOT NULL,
    config json NOT NULL,
    result text,
    PRIMARY KEY (task_id, element_id),
    KEY writing_element_task_idx (task_id)
)
"""


def new_element_id() -> str:
    return f"el-{uuid.uuid4().hex[:10]}"


def ensure_element_ids(elements: list[ElementNode]) -> list[ElementNode]:
    cleaned = []
    for item in elements:
        node = item if isinstance(item, ElementNode) else ElementNode.model_validate(item)
        if not node.id:
            node.id = new_element_id()
        cleaned.append(node)
    return cleaned


def save_elements(task: WritingTask, elements: list[ElementNode]) -> WritingTask:
    task.element_nodes = ensure_element_ids(elements)
    _sync_elements_table(task.id, task.element_nodes)
    return task


def chapter_subtree_keys(tree: OutlineTree | None, root_key: str, *, include_children: bool = True) -> list[str]:
    if tree is None or not tree.chapters:
        return [root_key] if root_key else []
    if not include_children:
        return [root_key]
    children: dict[str, list[str]] = {}
    order = [item.key for item in tree.chapters]
    for item in tree.chapters:
        if item.parent:
            children.setdefault(item.parent, []).append(item.key)
    for parent, kids in children.items():
        children[parent] = sorted(kids, key=lambda key: order.index(key) if key in order else 0)
    selected: list[str] = []

    def walk(key: str) -> None:
        selected.append(key)
        for child in children.get(key, []):
            walk(child)

    if root_key and any(item.key == root_key for item in tree.chapters):
        walk(root_key)
    return selected


def sync_brief_from_outline(task: WritingTask) -> None:
    if task.brief is None or task.outline is None:
        return
    by_key = {item.key: item for item in task.outline.chapters}
    for chapter in task.brief.chapters:
        node = by_key.get(chapter.key)
        if node is None:
            continue
        chapter.sequence = node.sequence
        chapter.title = node.title
        chapter.level = node.level
        chapter.parent = node.parent
        chapter.material_files = list(node.material_files or [])
        chapter.knowledge_folder_ids = []
        chapter.element_ids = list(node.element_ids or [])
        from report_writing.domain.point_items_sync import (
            aux_prompt_for_writing_from_node,
            describes_for_writing_from_node,
            points_for_writing_from_node,
        )

        write_points = points_for_writing_from_node(node)
        chapter.points = write_points
        chapter.generate = "\n".join(write_points)
        chapter.describes = describes_for_writing_from_node(node)
        chapter.aux_prompt = aux_prompt_for_writing_from_node(node)
        chapter.keywords = []
        chapter.reference_text = (getattr(node, "reference_text", None) or "").strip()
        chapter.structure_only = bool(getattr(node, "structure_only", False))
        chapter.fixed_output = bool(getattr(node, "fixed_output", False))
        chapter.search_terms_separate = bool(getattr(node, "search_terms_separate", False))
        if chapter.fixed_output:
            chapter.keywords = []
        node_blocks = list(getattr(node, "content_blocks", None) or [])
        if node_blocks:
            from report_writing.domain.models import ContentBlock

            chapter.content_blocks = [
                item if isinstance(item, ContentBlock) else ContentBlock.model_validate(item)
                for item in node_blocks
            ]


def resolve_elements_for_chapter(
    requirement: ChapterRequirement,
    elements: list[ElementNode],
    corpus: Corpus | None,
    *,
    outline: OutlineTree | None = None,
    seen: set[str] | None = None,
    robot=None,
    project_folder_ids: list[str] | None = None,
    materials_pack_id: str = "",
    home: Path | None = None,
    task_id: str = "",
) -> ChapterRequirement:
    by_id = {item.id: item for item in elements if item.id}
    results = []
    element_ids = [str(item).strip() for item in (requirement.element_ids or []) if str(item).strip()]
    # brief 上的关联可能落后于大纲：界面已挂上的槽位要素以大纲为准，避免检索被跳过
    if outline is not None:
        node = next((item for item in outline.chapters if item.key == requirement.key), None)
        for extra in list(getattr(node, "element_ids", None) or []) if node is not None else []:
            extra_id = str(extra or "").strip()
            if extra_id and extra_id not in element_ids:
                element_ids.append(extra_id)
    for element_id in element_ids:
        node = by_id.get(element_id)
        if node is None:
            continue
        value = _resolve_one(
            node,
            corpus,
            fallback_files=requirement.material_files,
            elements=by_id,
            outline=outline,
            seen=seen or set(),
            robot=robot,
            project_folder_ids=project_folder_ids,
            materials_pack_id=materials_pack_id,
            home=home,
            task_id=task_id,
        )
        node.result = value
        kind = (node.kind or "").strip()
        query = str(node.query or "").strip()
        # 引用槽位：检索词在 file_slots[].query，过程条要展示这些词
        if kind == "file_slot":
            slot_queries = []
            for slot in list(getattr(node, "file_slots", None) or []):
                q = str(
                    getattr(slot, "query", None)
                    or (slot.get("query") if isinstance(slot, dict) else "")
                    or getattr(slot, "name", None)
                    or (slot.get("name") if isinstance(slot, dict) else "")
                    or ""
                ).strip()
                if q and q not in slot_queries:
                    slot_queries.append(q)
            if slot_queries:
                query = "；".join(slot_queries)
        row: dict[str, Any] = {
            "id": node.id,
            "name": node.name,
            "kind": kind,
            "query": query,
            "ref_key": node.ref_key,
            "value": value,
        }
        if kind == "file_slot":
            gaps = file_slot_missing_nos(
                node, materials_pack_id=materials_pack_id, home=home
            )
            if gaps:
                row["slot_missing"] = gaps
                row["slot_skip_search"] = True
        results.append(row)
    requirement.element_ids = element_ids
    requirement.element_results = results
    return requirement


def effective_element_folder_ids(
    node: ElementNode,
    *,
    project_folder_ids: list[str] | None = None,
    robot=None,
) -> list[str] | None:
    """按 knowledge_scope 合并要素通用库与任务项目资料根。"""
    from report_writing.services.robots import resolve_knowledge_folders

    return resolve_knowledge_folders(
        list(getattr(node, "knowledge_folder_ids", None) or []),
        robot,
        project_folder_ids=project_folder_ids,
        knowledge_scope=str(getattr(node, "knowledge_scope", None) or "both"),
    )


def strip_project_scoped_folders_on_clone(task: WritingTask) -> WritingTask:
    """开写 clone：项目向要素清空文件夹钉选，改由 writing.project_folder_ids 提供。"""
    nodes = list(task.element_nodes or [])
    changed = False
    for node in nodes:
        scope = str(getattr(node, "knowledge_scope", None) or "both").strip().lower()
        if scope == "project" and list(getattr(node, "knowledge_folder_ids", None) or []):
            node.knowledge_folder_ids = []
            changed = True
        # both/shared：保留要素上的通用库文件夹
    if changed:
        task.element_nodes = nodes
    # 蓝本不应自带项目根；clone 后由用户/开写流程写入
    if list(getattr(task, "project_folder_ids", None) or []):
        # 若蓝本误存了项目根，清空以免串到新项目（调用方可再 set）
        task.project_folder_ids = []
        task.project_folder_label = ""
        changed = True
    return task


def _slot_no(slot: Any) -> int:
    try:
        return int(
            getattr(slot, "no", None)
            or (slot.get("no") if isinstance(slot, dict) else 1)
            or 1
        )
    except (TypeError, ValueError):
        return 1


def file_slot_missing_nos(
    node: ElementNode,
    *,
    materials_pack_id: str = "",
    home: Path | None = None,
) -> list[int]:
    """要素引用的槽位号在资料包中找不到文件时返回这些编号（不对齐 / 未上传）。"""
    pack_id = (materials_pack_id or "").strip()
    slots = list(getattr(node, "file_slots", None) or [])
    if not slots:
        return []
    if not pack_id or home is None:
        return sorted({_slot_no(s) for s in slots if _slot_no(s) > 0})
    from report_writing.services.materials_pack import get_pack, resolve_slot_file_path

    pack = get_pack(home, pack_id)
    if pack is None:
        return sorted({_slot_no(s) for s in slots if _slot_no(s) > 0})
    missing: list[int] = []
    seen: set[int] = set()
    for slot in slots:
        no = _slot_no(slot)
        if no < 1 or no in seen:
            continue
        seen.add(no)
        if resolve_slot_file_path(home, pack, no) is None:
            missing.append(no)
    return missing


def _resolve_file_slot(
    node: ElementNode,
    *,
    materials_pack_id: str = "",
    home: Path | None = None,
) -> str:
    """按资料包槽位文件 + 要素预设检索词取摘录。

    找不到对应槽位文件时不检索（跳过该槽），不回落到其它语料搜索。
    """
    pack_id = (materials_pack_id or "").strip()
    if not pack_id or home is None:
        return ""
    slots = list(getattr(node, "file_slots", None) or [])
    if not slots:
        return ""
    from report_writing.services.materials_pack import (
        get_pack,
        resolve_slot_file_path,
        search_in_slot_file,
    )

    pack = get_pack(home, pack_id)
    if pack is None:
        return ""
    parts: list[str] = []
    for slot in slots:
        no = _slot_no(slot)
        query = str(
            getattr(slot, "query", None)
            or (slot.get("query") if isinstance(slot, dict) else "")
            or getattr(slot, "name", None)
            or (slot.get("name") if isinstance(slot, dict) else "")
            or node.name
            or ""
        ).strip()
        path = resolve_slot_file_path(home, pack, no)
        if path is None:
            # 槽位未对齐 / 未上传：不对该槽做任何搜索
            continue
        excerpts = search_in_slot_file(path, query or path.name, limit=3)
        if excerpts:
            label = str(
                getattr(slot, "name", None)
                or (slot.get("name") if isinstance(slot, dict) else "")
                or f"槽位{no}"
            ).strip()
            block = "\n".join(excerpts)
            parts.append(f"【{label}】\n{block}" if label else block)
    return "\n\n".join(parts).strip()


def _resolve_one(
    node: ElementNode,
    corpus: Corpus | None,
    *,
    fallback_files: list[str] | None = None,
    elements: dict[str, ElementNode] | None = None,
    outline: OutlineTree | None = None,
    seen: set[str] | None = None,
    robot=None,
    project_folder_ids: list[str] | None = None,
    materials_pack_id: str = "",
    home: Path | None = None,
    task_id: str = "",
) -> str:
    kind = (node.kind or "fixed").strip()
    if kind == "kv":
        pairs = list(getattr(node, "kv_pairs", None) or [])
        lines: list[str] = []
        for item in pairs:
            if hasattr(item, "key"):
                k, v = str(item.key or "").strip(), str(item.value or "").strip()
            elif isinstance(item, dict):
                k, v = str(item.get("key") or "").strip(), str(item.get("value") or "").strip()
            else:
                continue
            if not k:
                continue
            lines.append(f"{k}：{v}" if v else k)
        if lines:
            return "\n".join(lines)
        name = (node.name or "").strip()
        val = (node.value or "").strip()
        if name and val:
            return f"{name}：{val}"
        return val or name
    if kind == "fixed":
        return (node.value or "").strip()
    if kind == "file_slot":
        return _resolve_file_slot(
            node,
            materials_pack_id=materials_pack_id,
            home=home,
        )
    if kind in {"file", "rag_search"}:
        # file：要素上挂载多文件，需手动解析；rag_search 为旧 kind 兼容
        if home is None:
            return ""
        from report_writing.services.element_files import search_parsed_files

        query = (node.query or node.name or "").strip()
        text, _unparsed = search_parsed_files(
            home,
            list(getattr(node, "files", None) or []),
            query,
            limit=5,
        )
        return text
    if kind == "llm_generate":
        prompt = (node.prompt or node.name or "").strip()
        if not prompt:
            return ""
        try:
            from report_writing.infra.llm import live_model

            model = live_model()
            if model is None:
                return prompt
            message = model.invoke(f"请根据提示生成简短要素内容，不要解释：\n{prompt}")
            return str(getattr(message, "content", None) or message).strip()
        except Exception as error:
            _log.warning("要素 LLM 生成失败 %s: %s", node.id, error)
            return ""
    if kind == "image":
        from report_writing.services.element_images import resolve_image_for_writing

        tid = str(task_id or "").strip()
        if home is None or not tid:
            from report_writing.services.element_images import image_placeholder

            ph = image_placeholder(node.id)
            return f"插图占位符：{ph}"
        return resolve_image_for_writing(home, tid, node)
    if kind == "node_ref":
        return _resolve_ref(
            node,
            elements or {},
            outline,
            seen or set(),
            corpus,
            fallback_files,
            robot=robot,
            project_folder_ids=project_folder_ids,
            materials_pack_id=materials_pack_id,
            home=home,
        )
    return (node.value or "").strip()


def _resolve_ref(
    node: ElementNode,
    elements: dict[str, ElementNode],
    outline: OutlineTree | None,
    seen: set[str],
    corpus: Corpus | None,
    fallback_files: list[str] | None,
    *,
    robot=None,
    project_folder_ids: list[str] | None = None,
    materials_pack_id: str = "",
    home: Path | None = None,
) -> str:
    ref_key = (node.ref_key or "").strip()
    if not ref_key or ref_key in seen:
        return ""
    seen = set(seen)
    seen.add(ref_key)
    ref_kind = (node.ref_kind or "chapter").strip()
    default_field = "result" if ref_kind == "element" else "points"
    field = (node.ref_field or default_field).strip()
    if ref_kind == "element":
        target = elements.get(ref_key)
        if target is None:
            return ""
        if field == "result" and target.result:
            return target.result.strip()
        return _resolve_one(
            target,
            corpus,
            fallback_files=fallback_files,
            elements=elements,
            outline=outline,
            seen=seen,
            robot=robot,
            project_folder_ids=project_folder_ids,
            materials_pack_id=materials_pack_id,
            home=home,
            task_id=task_id,
        )
    if outline is None:
        return ""
    chapter = next((item for item in outline.chapters if item.key == ref_key), None)
    if chapter is None:
        return ""
    if field == "describes":
        return (chapter.describes or "").strip()
    if field == "generate":
        return "\n".join(chapter.points or [])
    if field == "points":
        return "\n".join(chapter.points or [])
    return (getattr(chapter, field, None) or "").strip() if isinstance(getattr(chapter, field, None), str) else ""


def _sync_elements_table(task_id: str, elements: list[ElementNode]) -> None:
    settings = load_database_settings()
    if settings is None:
        return
    try:
        from report_writing.infra.postgres import _connect

        with _connect(settings) as connection:
            connection.execute(_ELEMENT_SCHEMA)
            connection.execute("DELETE FROM writing_element_node WHERE task_id = %s", (task_id,))
            for node in elements:
                config = {
                    "value": node.value,
                    "query": node.query,
                    "prompt": node.prompt,
                    "material_files": list(node.material_files or []),
                    "knowledge_folder_ids": list(getattr(node, "knowledge_folder_ids", None) or []),
                    "knowledge_scope": str(getattr(node, "knowledge_scope", None) or "both"),
                    "required": bool(node.required),
                    "file_slots": [
                        s.model_dump() if hasattr(s, "model_dump") else dict(s)
                        for s in list(getattr(node, "file_slots", None) or [])
                    ],
                    "files": [
                        f.model_dump() if hasattr(f, "model_dump") else dict(f)
                        for f in list(getattr(node, "files", None) or [])
                    ],
                    "kv_pairs": [
                        k.model_dump() if hasattr(k, "model_dump") else dict(k)
                        for k in list(getattr(node, "kv_pairs", None) or [])
                    ],
                    "category": str(getattr(node, "category", "") or ""),
                    "tags": list(getattr(node, "tags", None) or []),
                    "purpose": str(getattr(node, "purpose", "") or ""),
                }
                connection.execute(
                    """
                    INSERT INTO writing_element_node (task_id, element_id, name, kind, config, result)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        task_id,
                        node.id,
                        node.name or node.id,
                        node.kind or "rag_search",
                        json.dumps(config, ensure_ascii=False),
                        node.result or "",
                    ),
                )
    except Exception as error:
        _log.warning("要素表同步失败 task=%s: %s", task_id, error)


def outline_node_payload(node: OutlineNode) -> dict:
    return {
        "key": node.key,
        "sequence": node.sequence,
        "title": node.title,
        "level": node.level,
        "parent": node.parent,
        "parent_sequence": node.parent_sequence,
        "points": list(node.points or []),
        "keywords": list(getattr(node, "keywords", None) or []),
        "describes": node.describes or "",
        "content_blocks": [
            item.to_dict() if hasattr(item, "to_dict") else item
            for item in (getattr(node, "content_blocks", None) or [])
        ],
        "reference_text": getattr(node, "reference_text", "") or "",
        "aux_prompt": node.aux_prompt or "",
        "material_files": list(node.material_files or []),
        "knowledge_folder_ids": list(getattr(node, "knowledge_folder_ids", None) or []),
        "element_ids": list(node.element_ids or []),
        "elements": [item.to_dict() if hasattr(item, "to_dict") else item for item in (node.elements or [])],
    }
