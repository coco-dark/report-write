"""要素附件文件：上传不解析；手动解析。"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from report_writing.api.deps import data_home

router = APIRouter(tags=["element_files"])


@router.post("/api/writing/element-files")
async def upload_element_file(file: UploadFile = File(...)) -> dict:
    from report_writing.services.element_files import upload_file

    raw = await file.read()
    try:
        attached = upload_file(
            data_home(),
            filename=str(file.filename or "file"),
            data=raw,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return attached.model_dump()


@router.post("/api/writing/element-files/{file_id}/parse")
def parse_element_file(file_id: str, force: bool = False) -> dict:
    from report_writing.services.element_files import parse_file

    try:
        attached = parse_file(data_home(), file_id, force=bool(force))
    except KeyError:
        raise HTTPException(status_code=404, detail="文件不存在") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return attached.model_dump()


@router.delete("/api/writing/element-files/{file_id}")
def delete_element_file(file_id: str) -> dict:
    from report_writing.services.element_files import delete_file

    ok = delete_file(data_home(), file_id)
    if not ok:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"ok": True, "file_id": file_id}


@router.get("/api/writing/element-files/{file_id}")
def get_element_file(file_id: str) -> dict:
    from report_writing.services.element_files import get_file

    attached = get_file(data_home(), file_id)
    if attached is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return attached.model_dump()
