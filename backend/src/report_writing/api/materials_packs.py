"""资料包 / 资料库 HTTP API。"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from report_writing.api.deps import data_home, load_task, task_dir
from report_writing.controller import ensure_task
from report_writing.domain.models import WritingTask

router = APIRouter(tags=["materials_packs"])


@router.get("/api/writing/materials-packs")
def list_materials_packs(
    in_library: bool | None = Query(None, description="true=资料库；false=待入库；省略=全部"),
    robot_id: str | None = Query(None, description="对话机器人 id，限定资料包范围"),
) -> dict:
    from report_writing.services.robots import scoped_materials_packs

    packs = scoped_materials_packs(data_home(), robot_id, in_library=in_library)
    return {
        "items": [p.model_dump() for p in packs],
        "count": len(packs),
    }


@router.post("/api/writing/materials-packs")
def create_materials_pack(body: dict) -> dict:
    from report_writing.services.materials_pack import create_pack

    data = dict(body or {})
    name = str(data.get("name") or "").strip()
    in_library = bool(data.get("in_library", False))
    schema = data.get("slot_schema") if isinstance(data.get("slot_schema"), list) else None
    blueprint_id = str(data.get("blueprint_id") or "").strip()
    pack = create_pack(
        data_home(),
        name=name or "未命名资料包",
        in_library=in_library,
        slot_schema=schema,
        blueprint_id=blueprint_id,
    )
    return pack.model_dump()


@router.post("/api/writing/materials-packs/{pack_id}/sync-schema")
def sync_materials_pack_schema(pack_id: str, body: dict) -> dict:
    """用蓝本/任务槽位定义同步资料包（增删改）。"""
    from report_writing.services.materials_pack import apply_slot_schema, get_pack

    pack = get_pack(data_home(), pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="资料包不存在")
    data = dict(body or {})
    schema = data.get("slot_schema") if isinstance(data.get("slot_schema"), list) else []
    blueprint_id = str(data.get("blueprint_id") or "").strip()
    prune = bool(data.get("prune", True))
    updated = apply_slot_schema(
        data_home(),
        pack,
        schema,
        blueprint_id=blueprint_id,
        prune=prune,
    )
    return updated.model_dump()


@router.post("/api/writing/materials-packs/{pack_id}/slots/reorder")
def reorder_materials_pack_slots(pack_id: str, body: dict) -> dict:
    """调整资料包槽位展示顺序（不改编号、不搬文件）。body.order = [slot_no, ...]。"""
    from report_writing.services.materials_pack import reorder_pack_slots

    data = dict(body or {})
    order = data.get("order") if isinstance(data.get("order"), list) else []
    try:
        updated = reorder_pack_slots(data_home(), pack_id, order)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return updated.model_dump()


@router.get("/api/writing/materials-packs/{pack_id}")
def get_materials_pack(pack_id: str) -> dict:
    from report_writing.services.materials_pack import get_pack

    pack = get_pack(data_home(), pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="资料包不存在")
    return pack.model_dump()


@router.patch("/api/writing/materials-packs/{pack_id}")
def patch_materials_pack(pack_id: str, body: dict) -> dict:
    from report_writing.services.materials_pack import get_pack, rename_pack, save_pack, set_pack_in_library

    data = dict(body or {})
    pack = get_pack(data_home(), pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="资料包不存在")
    if "name" in data:
        pack = rename_pack(data_home(), pack_id, str(data.get("name") or ""))
    if "in_library" in data:
        pack = set_pack_in_library(data_home(), pack_id, bool(data.get("in_library")))
    elif "name" not in data:
        pack = save_pack(data_home(), pack)
    return pack.model_dump()


@router.delete("/api/writing/materials-packs/{pack_id}")
def delete_materials_pack(pack_id: str) -> dict:
    from report_writing.services.materials_pack import delete_pack

    ok = delete_pack(data_home(), pack_id)
    if not ok:
        raise HTTPException(status_code=404, detail="资料包不存在")
    return {"ok": True, "id": pack_id}


@router.post("/api/writing/materials-packs/{pack_id}/slots/{slot_no}/files")
async def upload_materials_slot_file(
    pack_id: str,
    slot_no: int,
    file: UploadFile = File(...),
) -> dict:
    from report_writing.services.materials_pack import upload_slot_file

    raw = await file.read()
    try:
        pack = upload_slot_file(
            data_home(),
            pack_id,
            int(slot_no),
            filename=str(file.filename or "file"),
            data=raw,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="资料包不存在") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return pack.model_dump()


@router.delete("/api/writing/materials-packs/{pack_id}/slots/{slot_no}/files")
def delete_materials_slot_file(pack_id: str, slot_no: int) -> dict:
    from report_writing.services.materials_pack import clear_slot_file

    try:
        pack = clear_slot_file(data_home(), pack_id, int(slot_no))
    except KeyError:
        raise HTTPException(status_code=404, detail="资料包不存在") from None
    return pack.model_dump()


@router.post("/api/writing/materials-packs/{pack_id}/slots/{slot_no}/parse")
def parse_materials_slot_file(pack_id: str, slot_no: int, body: dict | None = None) -> dict:
    """解析资料包槽位文件（上传后手动解析；检索词仍在要素上）。"""
    from report_writing.services.materials_pack import parse_slot_file

    data = dict(body or {})
    force = bool(data.get("force"))
    try:
        pack = parse_slot_file(data_home(), pack_id, int(slot_no), force=force)
    except KeyError:
        raise HTTPException(status_code=404, detail="资料包不存在") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return pack.model_dump()


@router.put("/api/writing/tasks/{task_id}/materials-pack", response_model=WritingTask)
def put_task_materials_pack(task_id: str, body: dict) -> WritingTask:
    """绑定/解绑资料包（仅写作文档）。skip=true 表示明确不用资料包。"""
    from report_writing.services.materials_pack import get_pack
    from report_writing.services.store import TaskStore
    from report_writing.services.writing_task_catalog import save_writing_task

    data = dict(body or {})
    skip = bool(data.get("skip"))
    pack_id = str(data.get("pack_id") or data.get("materials_pack_id") or "").strip()
    task = load_task(task_id)
    is_writing = bool(str(getattr(task, "source_blueprint_id", "") or "").strip()) or str(
        getattr(task, "id", "") or ""
    ).startswith("w")
    if not is_writing:
        raise HTTPException(
            status_code=400,
            detail="资料包按写作文档绑定，不能绑到蓝本上；请先开写再配置",
        )
    if skip:
        task.materials_pack_id = ""
    elif pack_id:
        pack = get_pack(data_home(), pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="资料包不存在")
        task.materials_pack_id = pack_id
        try:
            from report_writing.services.materials_pack import apply_slot_schema, collect_file_slot_schema

            # 要素只合并编号进包；空 name 不覆盖包内已有名称；不 prune、不写检索词
            schema = [
                {"no": s.get("no"), "name": s.get("name") or "", "query": ""}
                for s in collect_file_slot_schema(list(task.element_nodes or []))
            ]
            bp = str(getattr(task, "source_blueprint_id", "") or "").strip()
            apply_slot_schema(
                data_home(),
                pack,
                schema,
                blueprint_id=bp,
                prune=False,
                preserve_pack_names=True,
            )
        except Exception:
            pass
    else:
        task.materials_pack_id = ""
    directory = task_dir(task_id)
    ensure_task(directory)
    TaskStore(directory).save(task)
    try:
        save_writing_task(task)
    except Exception:
        pass
    try:
        from report_writing.services.doc_outline_cache import invalidate_doc_outline_cache

        invalidate_doc_outline_cache(task_id)
    except Exception:
        pass
    return task


@router.post("/api/writing/materials-packs/confirm-library")
def confirm_packs_into_library(body: dict) -> dict:
    """批量将待入库资料包标记为已入库。"""
    from report_writing.services.materials_pack import set_pack_in_library

    ids = [str(x).strip() for x in ((body or {}).get("pack_ids") or []) if str(x).strip()]
    updated = []
    for pid in ids:
        try:
            updated.append(set_pack_in_library(data_home(), pid, True).model_dump())
        except KeyError:
            continue
    return {"items": updated, "count": len(updated)}
