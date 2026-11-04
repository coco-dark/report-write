"""资料包：按用户工作区存储；槽位填文件；可入库到资料库供选用。"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from report_writing.domain.models import FileSlotDef, MaterialsPack, MaterialsSlotFill
from report_writing.services.users import current_user_id, user_workspace

_log = logging.getLogger("uvicorn.error")
_SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_user_workspace(home: Path) -> Path:
    """兼容 data 根目录与已解析的用户 workspace。

    对话/流水线常把 home 设为 ``…/users/<uid>/workspace``；API 则传 data 根。
    若再套一层 ``user_workspace``，会落到错误的嵌套目录，槽位文件「明明有却找不到」。
    """
    root = Path(home)
    parts = root.parts
    if len(parts) >= 3 and parts[-1] == "workspace" and parts[-3] == "users":
        return root
    return user_workspace(root, current_user_id())


def _packs_root(home: Path) -> Path:
    ws = _as_user_workspace(home)
    root = ws / "materials_packs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index_path(home: Path) -> Path:
    return _packs_root(home) / "index.json"


def _pack_dir(home: Path, pack_id: str) -> Path:
    return _packs_root(home) / (pack_id or "").strip()


def _load_index(home: Path) -> list[dict[str, Any]]:
    path = _index_path(home)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return list(data["items"])
    if isinstance(data, list):
        return data
    return []


def _save_index(home: Path, items: list[dict[str, Any]]) -> None:
    path = _index_path(home)
    path.write_text(
        json.dumps({"items": items, "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _validate_pack(raw: dict[str, Any]) -> MaterialsPack | None:
    try:
        return MaterialsPack.model_validate(raw)
    except Exception:
        return None


def list_packs(home: Path, *, in_library: bool | None = None) -> list[MaterialsPack]:
    out: list[MaterialsPack] = []
    for raw in _load_index(home):
        pack = _validate_pack(raw)
        if pack is None:
            continue
        if in_library is not None and bool(pack.in_library) != bool(in_library):
            continue
        out.append(pack)
    out.sort(key=lambda p: str(p.updated_at or p.created_at or ""), reverse=True)
    return out


def get_pack(home: Path, pack_id: str) -> MaterialsPack | None:
    pid = (pack_id or "").strip()
    if not pid:
        return None
    for pack in list_packs(home):
        if pack.id == pid:
            return pack
    return None


def create_pack(
    home: Path,
    *,
    name: str,
    in_library: bool = False,
    slot_schema: list[Any] | None = None,
    blueprint_id: str = "",
) -> MaterialsPack:
    title = (name or "").strip() or "未命名资料包"
    schema = _normalize_schema(slot_schema or [])
    bp = (blueprint_id or "").strip()
    pack = MaterialsPack(
        id=f"mp-{uuid.uuid4().hex[:12]}",
        name=title,
        user_id=current_user_id(),
        in_library=bool(in_library),
        slot_schema=schema,
        slot_order=[int(s.no) for s in schema if int(s.no) >= 1],
        blueprint_ids=[bp] if bp else [],
        slots_fill=[],
        created_at=_now(),
        updated_at=_now(),
    )
    _pack_dir(home, pack.id).mkdir(parents=True, exist_ok=True)
    items = _load_index(home)
    items.append(pack.model_dump())
    _save_index(home, items)
    return pack


def save_pack(home: Path, pack: MaterialsPack) -> MaterialsPack:
    pid = (pack.id or "").strip()
    if not pid:
        raise ValueError("资料包 id 无效")
    pack.id = pid
    pack.updated_at = _now()
    if not pack.created_at:
        pack.created_at = pack.updated_at
    if not pack.user_id:
        pack.user_id = current_user_id()
    items = _load_index(home)
    found = False
    merged: list[dict[str, Any]] = []
    for raw in items:
        if str(raw.get("id") or "").strip() == pid:
            merged.append(pack.model_dump())
            found = True
        else:
            merged.append(raw)
    if not found:
        merged.append(pack.model_dump())
    _save_index(home, merged)
    return pack


def set_pack_in_library(home: Path, pack_id: str, in_library: bool) -> MaterialsPack:
    pack = get_pack(home, pack_id)
    if pack is None:
        raise KeyError(pack_id)
    pack.in_library = bool(in_library)
    return save_pack(home, pack)


def rename_pack(home: Path, pack_id: str, name: str) -> MaterialsPack:
    pack = get_pack(home, pack_id)
    if pack is None:
        raise KeyError(pack_id)
    title = (name or "").strip()
    if title:
        pack.name = title
    return save_pack(home, pack)


def delete_pack(home: Path, pack_id: str) -> bool:
    pid = (pack_id or "").strip()
    if not pid:
        return False
    items = _load_index(home)
    kept = [raw for raw in items if str(raw.get("id") or "").strip() != pid]
    if len(kept) == len(items):
        return False
    _save_index(home, kept)
    folder = _pack_dir(home, pid)
    if folder.is_dir():
        try:
            shutil.rmtree(folder)
        except OSError as error:
            _log.warning("删除资料包目录失败 %s: %s", pid, error)
    return True


def _safe_filename(name: str) -> str:
    base = Path(name or "file").name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "file"
    return cleaned[:180]


def upload_slot_file(
    home: Path,
    pack_id: str,
    slot_no: int,
    *,
    filename: str,
    data: bytes,
) -> MaterialsPack:
    pack = get_pack(home, pack_id)
    if pack is None:
        raise KeyError(pack_id)
    no = int(slot_no)
    if no < 1:
        raise ValueError("槽位编号从 1 开始")
    file_id = f"mf-{uuid.uuid4().hex[:10]}"
    safe = _safe_filename(filename)
    slot_dir = _pack_dir(home, pack.id) / f"slot_{no}"
    slot_dir.mkdir(parents=True, exist_ok=True)
    dest = slot_dir / f"{file_id}_{safe}"
    dest.write_bytes(data or b"")
    # 相对工作区路径，便于迁移
    ws = _as_user_workspace(home)
    try:
        rel = str(dest.relative_to(ws))
    except ValueError:
        rel = str(dest)
    fills = [f for f in (pack.slots_fill or []) if int(getattr(f, "slot_no", 0) or 0) != no]
    fills.append(
        MaterialsSlotFill(
            slot_no=no,
            file_id=file_id,
            file_name=safe,
            source_path=rel,
            parse_status="pending",
            parse_error="",
            parsed_at="",
        )
    )
    fills.sort(key=lambda f: int(f.slot_no or 0))
    pack.slots_fill = fills
    return save_pack(home, pack)


def clear_slot_file(home: Path, pack_id: str, slot_no: int) -> MaterialsPack:
    pack = get_pack(home, pack_id)
    if pack is None:
        raise KeyError(pack_id)
    no = int(slot_no)
    pack.slots_fill = [f for f in (pack.slots_fill or []) if int(getattr(f, "slot_no", 0) or 0) != no]
    slot_dir = _pack_dir(home, pack.id) / f"slot_{no}"
    if slot_dir.is_dir():
        try:
            shutil.rmtree(slot_dir)
        except OSError:
            pass
    return save_pack(home, pack)


def resolve_slot_file_path(home: Path, pack: MaterialsPack, slot_no: int) -> Path | None:
    no = int(slot_no)
    fill = next(
        (f for f in (pack.slots_fill or []) if int(getattr(f, "slot_no", 0) or 0) == no),
        None,
    )
    if fill is None:
        return None
    raw = str(getattr(fill, "source_path", "") or "").strip()
    path: Path | None = None
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _as_user_workspace(home) / path
        if path.is_file():
            return path
    # source_path 失效时，仍到槽位目录里找已上传文件（配置了槽位却检索不到）
    slot_dir = _pack_dir(home, pack.id) / f"slot_{no}"
    if slot_dir.is_dir():
        files = [
            p
            for p in slot_dir.iterdir()
            if p.is_file() and not p.name.endswith((".rag-parse.md", ".rag-parse.html"))
        ]
        if files:
            files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            return files[0]
    return None


def _normalize_schema(raw_slots: list[Any]) -> list[FileSlotDef]:
    by_no: dict[int, FileSlotDef] = {}
    for slot in raw_slots or []:
        if hasattr(slot, "model_dump"):
            data = slot.model_dump()
        elif isinstance(slot, dict):
            data = dict(slot)
        else:
            data = {
                "no": getattr(slot, "no", 1),
                "name": getattr(slot, "name", ""),
                "query": getattr(slot, "query", ""),
            }
        try:
            no = int(data.get("no") or 1)
        except (TypeError, ValueError):
            no = 1
        if no < 1:
            continue
        name = str(data.get("name") or "").strip()
        query = str(data.get("query") or "").strip()
        prev = by_no.get(no)
        if prev is None:
            by_no[no] = FileSlotDef(no=no, name=name, query=query)
            continue
        # 同号合并：非空覆盖；空值不抹掉已有名称/检索词
        by_no[no] = FileSlotDef(
            no=no,
            name=name or prev.name,
            query=query or prev.query,
        )
    # 保留首次出现顺序。按编号排序会让「第一次保存」丢掉用户刚排好的槽位/要素顺序。
    order: list[int] = []
    for slot in raw_slots or []:
        if hasattr(slot, "model_dump"):
            data = slot.model_dump()
        elif isinstance(slot, dict):
            data = dict(slot)
        else:
            data = {"no": getattr(slot, "no", 1)}
        try:
            no = int(data.get("no") or 0)
        except (TypeError, ValueError):
            continue
        if no >= 1 and no in by_no and no not in order:
            order.append(no)
    return [by_no[k] for k in order]


def collect_file_slot_schema(elements: list[Any]) -> list[dict[str, Any]]:
    """合并任务/蓝本上所有 file_slot 要素的槽位定义（按 no）。"""
    return [s.model_dump() for s in _normalize_schema(_iter_element_slots(elements))]


def _iter_element_slots(elements: list[Any]) -> list[Any]:
    out: list[Any] = []
    for el in elements or []:
        kind = str(getattr(el, "kind", None) or (el.get("kind") if isinstance(el, dict) else "") or "")
        if kind.strip() != "file_slot":
            continue
        slots = getattr(el, "file_slots", None)
        if slots is None and isinstance(el, dict):
            slots = el.get("file_slots")
        out.extend(list(slots or []))
    return out


def _ordered_slot_nos(pack: MaterialsPack | None, nos: list[int]) -> list[int]:
    """按 pack.slot_order 排展示顺序；未列入的按编号追加。"""
    known = [int(n) for n in nos if int(n) >= 1]
    known_set = set(known)
    raw_order = list(getattr(pack, "slot_order", None) or []) if pack else []
    ordered: list[int] = []
    seen: set[int] = set()
    for item in raw_order:
        try:
            no = int(item)
        except (TypeError, ValueError):
            continue
        if no < 1 or no not in known_set or no in seen:
            continue
        ordered.append(no)
        seen.add(no)
    for no in sorted(known_set):
        if no not in seen:
            ordered.append(no)
    return ordered


def normalize_slot_order(pack: MaterialsPack) -> list[int]:
    """用当前 schema∪fill 校正 slot_order，写回 pack 并返回。"""
    nos: list[int] = []
    for slot in list(pack.slot_schema or []):
        try:
            no = int(getattr(slot, "no", 0) or 0)
        except (TypeError, ValueError):
            continue
        if no >= 1:
            nos.append(no)
    for fill in list(pack.slots_fill or []):
        try:
            no = int(getattr(fill, "slot_no", 0) or 0)
        except (TypeError, ValueError):
            continue
        if no >= 1:
            nos.append(no)
    ordered = _ordered_slot_nos(pack, nos)
    pack.slot_order = ordered
    return ordered


def reorder_pack_slots(home: Path, pack_id: str, order: list[Any]) -> MaterialsPack:
    """设置资料包槽位展示顺序。order 为已有 slot_no 的排列（可只含子集，其余按编号追加）。"""
    pack = get_pack(home, pack_id)
    if pack is None:
        raise ValueError("资料包不存在")
    known = {
        int(s.no)
        for s in _normalize_schema(list(pack.slot_schema or []))
        if int(s.no) >= 1
    }
    for fill in list(pack.slots_fill or []):
        try:
            no = int(getattr(fill, "slot_no", 0) or 0)
        except (TypeError, ValueError):
            continue
        if no >= 1:
            known.add(no)
    if not known:
        pack.slot_order = []
        return save_pack(home, pack)
    wanted: list[int] = []
    seen: set[int] = set()
    for item in order or []:
        try:
            no = int(item)
        except (TypeError, ValueError):
            continue
        if no not in known or no in seen:
            continue
        wanted.append(no)
        seen.add(no)
    if not wanted:
        raise ValueError("顺序无效：请传入现有槽位编号")
    # 未点名的槽位保持相对编号顺序，接在后面
    for no in sorted(known):
        if no not in seen:
            wanted.append(no)
    pack.slot_order = wanted
    return save_pack(home, pack)


def effective_slot_rows(pack: MaterialsPack | None, schema: list[Any] | None = None) -> list[dict[str, Any]]:
    """展示用槽位行：传入 schema ∪ pack.slot_schema ∪ 已填充槽；按 slot_order 排序。"""
    by_no: dict[int, dict[str, Any]] = {}

    def _put(no: int, name: str = "", query: str = "") -> None:
        if no < 1:
            return
        prev = by_no.get(no) or {"no": no, "name": "", "query": "", "file_id": "", "file_name": ""}
        if name:
            prev["name"] = name
        if query:
            prev["query"] = query
        by_no[no] = prev

    for slot in _normalize_schema(schema or []):
        _put(int(slot.no), slot.name, slot.query)
    for slot in list(getattr(pack, "slot_schema", None) or []) if pack else []:
        if isinstance(slot, FileSlotDef):
            _put(int(slot.no), slot.name, slot.query)
        elif isinstance(slot, dict):
            try:
                no = int(slot.get("no") or 1)
            except (TypeError, ValueError):
                no = 1
            _put(no, str(slot.get("name") or ""), str(slot.get("query") or ""))
    for fill in list(getattr(pack, "slots_fill", None) or []) if pack else []:
        try:
            no = int(getattr(fill, "slot_no", 0) or 0)
        except (TypeError, ValueError):
            continue
        if no < 1:
            continue
        prev = by_no.get(no) or {"no": no, "name": "", "query": "", "file_id": "", "file_name": ""}
        prev["file_id"] = str(getattr(fill, "file_id", "") or "")
        prev["file_name"] = str(getattr(fill, "file_name", "") or "")
        by_no[no] = prev
    return [by_no[k] for k in _ordered_slot_nos(pack, list(by_no.keys())) if k in by_no]


def apply_slot_schema(
    home: Path,
    pack: MaterialsPack,
    schema: list[Any],
    *,
    blueprint_id: str = "",
    prune: bool = True,
    preserve_pack_names: bool = False,
) -> MaterialsPack:
    """同步资料包槽位编号/名称；prune 时去掉已删除的空槽。

    资料包不保存检索词（query 一律清空）；检索词只在要素 file_slots 上。
    preserve_pack_names：要素侧同步时打开——空 name 不覆盖包内已有名称，并保留包内已有槽位号。
    """
    existing = list(pack.slot_schema or [])
    existing_by_no = {
        int(s.no): s
        for s in _normalize_schema(existing)
        if int(s.no) >= 1
    }
    incoming = _normalize_schema(schema)
    # 第一次写入：用传入顺序当展示顺序，不要改成按编号升序
    if not list(pack.slot_order or []) and incoming:
        pack.slot_order = [int(s.no) for s in incoming if int(s.no) >= 1]
    if preserve_pack_names:
        # 包内已有槽位 ∪ 要素槽位；同号时非空 name 覆盖，空 name 保留包名
        incoming = _normalize_schema(existing + list(incoming))
    wanted = [
        FileSlotDef(no=int(s.no), name=str(s.name or "").strip(), query="")
        for s in incoming
    ]
    if preserve_pack_names:
        # 再兜一层：要素空名不应抹掉包名
        merged: list[FileSlotDef] = []
        for slot in wanted:
            prev = existing_by_no.get(int(slot.no))
            name = str(slot.name or "").strip()
            if not name and prev is not None:
                name = str(prev.name or "").strip()
            merged.append(FileSlotDef(no=int(slot.no), name=name, query=""))
        wanted = merged
    wanted_nos = {int(s.no) for s in wanted}
    bp = (blueprint_id or "").strip()
    if bp:
        ids = [str(x).strip() for x in (pack.blueprint_ids or []) if str(x).strip()]
        if bp not in ids:
            ids.append(bp)
        pack.blueprint_ids = ids
    if prune:
        keep_fills: list[MaterialsSlotFill] = []
        for fill in list(pack.slots_fill or []):
            try:
                no = int(getattr(fill, "slot_no", 0) or 0)
            except (TypeError, ValueError):
                continue
            if no in wanted_nos:
                keep_fills.append(fill)
                continue
            # 蓝本已删该槽：有文件则仍保留填充，避免误删用户资料；无文件丢弃
            if str(getattr(fill, "file_id", "") or "").strip() or str(getattr(fill, "file_name", "") or "").strip():
                keep_fills.append(fill)
                # 有文件的孤儿槽：保留原 schema 名称，避免刷新后只剩「已解析」无名称
                if no >= 1 and no not in wanted_nos:
                    prev = existing_by_no.get(no)
                    wanted.append(
                        FileSlotDef(
                            no=no,
                            name=str(prev.name or "").strip() if prev is not None else "",
                            query="",
                        )
                    )
                    wanted_nos.add(no)
            else:
                slot_dir = _pack_dir(home, pack.id) / f"slot_{no}"
                if slot_dir.is_dir():
                    try:
                        shutil.rmtree(slot_dir)
                    except OSError:
                        pass
        pack.slots_fill = keep_fills
    wanted.sort(key=lambda s: int(s.no))
    pack.slot_schema = wanted
    normalize_slot_order(pack)
    return save_pack(home, pack)


def sync_packs_with_element_schema(
    home: Path,
    elements: list[Any],
    *,
    blueprint_id: str = "",
    pack_ids: list[str] | None = None,
    include_library: bool = True,
) -> list[MaterialsPack]:
    """蓝本/任务要素槽位变更后，同步到绑定包与资料库中关联包。"""
    schema = collect_file_slot_schema(elements)
    bp = (blueprint_id or "").strip()
    targets: dict[str, MaterialsPack] = {}
    for pid in pack_ids or []:
        pack = get_pack(home, str(pid).strip())
        if pack is not None:
            targets[pack.id] = pack
    if bp:
        for pack in list_packs(home):
            if bp in [str(x).strip() for x in (pack.blueprint_ids or [])]:
                targets[pack.id] = pack
    if include_library and bp:
        for pack in list_packs(home, in_library=True):
            # 资料库中已关联该蓝本，或尚未标记蓝本但被上面 pack_ids 命中
            if pack.id in targets or bp in [str(x).strip() for x in (pack.blueprint_ids or [])]:
                targets[pack.id] = pack
    updated: list[MaterialsPack] = []
    for pack in targets.values():
        # 要素 file_slots 常只有 no、空 name；必须保留包内已命名槽位，且勿 prune 掉包内自建槽
        updated.append(
            apply_slot_schema(
                home,
                pack,
                schema,
                blueprint_id=bp,
                prune=False,
                preserve_pack_names=True,
            )
        )
    return updated


def sync_packs_for_task(home: Path, task: Any) -> list[MaterialsPack]:
    """写作任务/蓝本保存要素后：同步当前绑定包 + 同源蓝本相关资料包。"""
    elements = list(getattr(task, "element_nodes", None) or [])
    pack_id = str(getattr(task, "materials_pack_id", "") or "").strip()
    bp = str(
        getattr(task, "source_blueprint_id", None)
        or (task.id if str(getattr(task, "id", "") or "").startswith("bp") else "")
        or ""
    ).strip()
    # 蓝本自身 id
    tid = str(getattr(task, "id", "") or "").strip()
    if not bp and tid.startswith("bp"):
        bp = tid
    pack_ids = [pack_id] if pack_id else []
    # 同源写作任务上绑定的包
    if bp:
        # 扫本地 tasks：同源蓝本写作任务上绑定的资料包
        tasks_root = Path(home) / "tasks"
        if tasks_root.is_dir():
            for child in tasks_root.iterdir():
                meta = child / "task.json"
                if not meta.is_file():
                    continue
                try:
                    raw = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if str(raw.get("source_blueprint_id") or "").strip() != bp and str(raw.get("id") or "") != bp:
                    continue
                pid = str(raw.get("materials_pack_id") or "").strip()
                if pid and pid not in pack_ids:
                    pack_ids.append(pid)
    return sync_packs_with_element_schema(
        home,
        elements,
        blueprint_id=bp,
        pack_ids=pack_ids,
        include_library=True,
    )


def parse_slot_file(home: Path, pack_id: str, slot_no: int, *, force: bool = False) -> MaterialsPack:
    """手动解析槽位文件，写入旁路缓存；检索时优先读已解析正文。"""
    from report_writing.infra.rag_parse import parse_full_text

    pack = get_pack(home, pack_id)
    if pack is None:
        raise KeyError(pack_id)
    no = int(slot_no)
    fill = next(
        (f for f in (pack.slots_fill or []) if int(getattr(f, "slot_no", 0) or 0) == no),
        None,
    )
    if fill is None:
        raise ValueError(f"槽位 {no} 尚未上传文件")
    path = resolve_slot_file_path(home, pack, no)
    if path is None or not path.is_file():
        raise ValueError("槽位文件不存在，无法解析")
    if (
        not force
        and str(getattr(fill, "parse_status", "") or "") == "parsed"
        and Path(str(path) + ".rag-parse.md").is_file()
    ):
        return pack
    cache_md = Path(str(path) + ".rag-parse.md")
    cache_html = Path(str(path) + ".rag-parse.html")
    try:
        text = ""
        try:
            text = parse_full_text(path, result_type="markdown") or ""
        except Exception as md_error:
            _log.warning("资料包槽位 markdown 解析失败，改试 html %s: %s", path.name, md_error)
            html = parse_full_text(path, result_type="html") or ""
            if not html.strip():
                raise ValueError(f"解析失败：{md_error}") from md_error
            cache_html.write_text(html, encoding="utf-8")
            text = html
        else:
            if text.strip():
                cache_md.write_text(text, encoding="utf-8")
        if not (text or "").strip():
            raise ValueError("解析没有返回可用正文")
        fill.parse_status = "parsed"
        fill.parse_error = ""
        fill.parsed_at = _now()
    except Exception as error:
        fill.parse_status = "error"
        fill.parse_error = str(error)[:1000]
        fill.parsed_at = ""
        save_pack(home, pack)
        raise
    return save_pack(home, pack)


def _read_slot_paragraphs(path: Path) -> list[str]:
    cache_md = Path(str(path) + ".rag-parse.md")
    cache_html = Path(str(path) + ".rag-parse.html")
    if cache_md.is_file():
        try:
            text = cache_md.read_text(encoding="utf-8", errors="ignore")
            return [p.strip() for p in text.split("\n\n") if p.strip()] or ([text.strip()] if text.strip() else [])
        except Exception:
            pass
    if cache_html.is_file():
        try:
            text = cache_html.read_text(encoding="utf-8", errors="ignore")
            return [p.strip() for p in text.split("\n\n") if p.strip()] or ([text.strip()] if text.strip() else [])
        except Exception:
            pass
    try:
        from report_writing.services.documents import read_material

        return list(read_material(path) or [])
    except Exception as error:
        _log.debug("read slot file failed %s: %s", path, error)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            return [p.strip() for p in text.split("\n\n") if p.strip()]
        except Exception:
            return []


def search_in_slot_file(path: Path, query: str, *, limit: int = 5) -> list[str]:
    """在槽位文件正文中按要素检索词取摘录（优先已解析缓存）。"""
    q = (query or "").strip()
    if not path.is_file():
        return []
    paragraphs = _read_slot_paragraphs(path)
    body = "\n".join(paragraphs or [])
    if not body.strip():
        return []
    if not q:
        head = body.strip()[:600]
        return [head] if head else []
    tokens = [t for t in re.split(r"[\s,，、;；]+", q) if t]
    scored: list[tuple[int, str]] = []
    for para in paragraphs or []:
        text = (para or "").strip()
        if len(text) < 8:
            continue
        score = sum(1 for t in tokens if t and t in text)
        if score:
            scored.append((score, text[:800]))
    scored.sort(key=lambda item: (-item[0], -len(item[1])))
    if scored:
        return [t for _, t in scored[:limit]]
    head = body.strip()[:600]
    return [head] if head else []
