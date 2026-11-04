"""Blueprint catalog HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from report_writing.api.deps import load_task, task_dir, task_root
from report_writing.controller import (
    create_blueprint,
    delete_blueprint,
    list_blueprints,
    set_blueprint_enabled,
)
from report_writing.domain.models import (
    BlueprintEnabledRequest,
    BlueprintSummary,
    CreateBlueprintRequest,
    WritingTask,
)

router = APIRouter(tags=["blueprints"])

@router.get("/api/writing/blueprints/search", response_model=list[BlueprintSummary])
def search_blueprints_api(q: str = "", robot_id: str = "") -> list[BlueprintSummary]:
    from report_writing.services.robots import search_blueprints

    return search_blueprints(task_root(), q=q, robot_id=robot_id, enabled_only=True)



@router.get("/api/writing/blueprints", response_model=list[BlueprintSummary])
def get_blueprints(include_disabled: bool = True) -> list[BlueprintSummary]:
    return list_blueprints(task_root() / "tasks", include_disabled=include_disabled)


@router.get("/api/writing/blueprints/check-title")
def get_check_blueprint_title(title: str = "") -> dict:
    """收录前校验蓝本名称是否已存在。"""
    from report_writing.services.blueprint_catalog import (
        find_blueprints_by_title,
        normalize_blueprint_title,
    )

    clean = normalize_blueprint_title(title)
    if not clean:
        raise HTTPException(status_code=400, detail="蓝本名称不能为空")
    hits = find_blueprints_by_title(clean)
    return {
        "title": clean,
        "exists": bool(hits),
        "items": [{"id": i.id, "title": i.title, "category": i.category} for i in hits],
    }


@router.post("/api/writing/blueprints", response_model=WritingTask)
def post_blueprint(body: CreateBlueprintRequest) -> WritingTask:
    import secrets
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    task_id = f"bp{stamp}{secrets.token_hex(2)}"
    try:
        return create_blueprint(
            task_dir(task_id),
            title=body.title,
            category=body.category,
            tags=body.tags,
            description=body.description,
            biz_type=body.biz_type or "002",
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.put("/api/writing/blueprints/{blueprint_id}/enabled", response_model=WritingTask)
def put_blueprint_enabled(blueprint_id: str, body: BlueprintEnabledRequest) -> WritingTask:
    load_task(blueprint_id)
    return set_blueprint_enabled(task_dir(blueprint_id), enabled=bool(body.enabled))



@router.delete("/api/writing/blueprints/{blueprint_id}")
def remove_blueprint(blueprint_id: str) -> dict:
    try:
        load_task(blueprint_id)
        delete_blueprint(task_dir(blueprint_id))
    except HTTPException:
        raise
    except KeyError as error:
        raise HTTPException(status_code=404, detail="蓝本不存在") from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"ok": True, "id": blueprint_id}



