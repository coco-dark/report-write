"""任务要素图片：上传、文生图、下载。"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from report_writing.api.deps import data_home
from report_writing.services.store import TaskStore

router = APIRouter(tags=["element_images"])


def _load_task(task_id: str):
    tid = str(task_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="需要 task_id")
    path = data_home() / "tasks" / tid
    if not (path / "task.json").is_file():
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskStore(path).load()


def _ensure_image_element_stub(task, element_id: str):
    """配置确认前允许按要素 id 上传/文生图：若无节点则建占位 image 节点。"""
    from report_writing.domain.models import ElementNode

    eid = str(element_id or "").strip()
    if not eid:
        raise HTTPException(status_code=400, detail="需要 element_id")
    merged = {e.id: e for e in (task.element_nodes or []) if e.id}
    if eid in merged:
        return merged[eid]
    stub = ElementNode(id=eid, name="图片要素", kind="image", image_source="upload")
    merged[eid] = stub
    task.element_nodes = list(merged.values())
    TaskStore(data_home() / "tasks" / task.id).save(task)
    return stub


def _save_element(task, element_id: str, mutator):
    store = TaskStore(data_home() / "tasks" / task.id)
    merged = {e.id: e for e in (task.element_nodes or []) if e.id}
    node = merged.get(element_id)
    if node is None:
        raise HTTPException(status_code=404, detail="要素不存在")
    mutator(node)
    task.element_nodes = list(merged.values())
    store.save(task)
    return node


@router.get("/api/writing/tasks/{task_id}/element-images/{element_id}")
def get_task_element_image(task_id: str, element_id: str):
    from report_writing.services.element_images import absolute_image_path

    task = _load_task(task_id)
    eid = str(element_id or "").strip()
    node = next((e for e in (task.element_nodes or []) if e.id == eid), None)
    if node is None:
        raise HTTPException(status_code=404, detail="要素不存在")
    path = absolute_image_path(data_home(), task.id, node)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    media = "image/png"
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        media = "image/jpeg"
    elif suffix == ".webp":
        media = "image/webp"
    elif suffix == ".gif":
        media = "image/gif"
    return FileResponse(path, media_type=media, filename=path.name)


@router.post("/api/writing/tasks/{task_id}/elements/{element_id}/image/upload")
async def upload_task_element_image(
    task_id: str,
    element_id: str,
    file: UploadFile = File(...),
) -> dict:
    from report_writing.services.element_images import save_image_bytes

    task = _load_task(task_id)
    eid = str(element_id or "").strip()
    _ensure_image_element_stub(task, eid)
    raw = await file.read()
    try:
        rel = save_image_bytes(
            data_home(),
            task.id,
            eid,
            raw,
            filename=str(file.filename or "image.png"),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    def _patch(node):
        node.kind = "image"
        node.image_source = "upload"
        node.image_path = rel

    node = _save_element(task, eid, _patch)
    return {"ok": True, "image_path": rel, "element": node.model_dump()}


@router.post("/api/writing/tasks/{task_id}/elements/{element_id}/image/generate")
def generate_task_element_image(task_id: str, element_id: str, body: dict | None = None) -> dict:
    from report_writing.services.element_images import generate_image_from_prompt

    task = _load_task(task_id)
    eid = str(element_id or "").strip()
    node = _ensure_image_element_stub(task, eid)
    prompt = str((body or {}).get("prompt") or node.prompt or node.name or "").strip()
    try:
        rel = generate_image_from_prompt(data_home(), task.id, eid, prompt)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    def _patch(n):
        n.kind = "image"
        n.image_source = "llm"
        n.image_path = rel
        n.prompt = prompt

    node = _save_element(task, eid, _patch)
    return {"ok": True, "image_path": rel, "element": node.model_dump()}
