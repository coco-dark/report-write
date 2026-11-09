"""Writing robot HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from report_writing.api.deps import task_root
from report_writing.domain.models import (
    GenerateRobotPromptsRequest,
    GenerateRobotPromptsResponse,
    RenderTemplateSummary,
    Robot,
    RobotSummary,
)
from report_writing.domain.word_style import WordStyleSummary

router = APIRouter(tags=["robots"])

@router.get("/api/writing/robots", response_model=list[RobotSummary])
def get_robots() -> list[RobotSummary]:
    from report_writing.services.robots import list_robots

    return list_robots(task_root())



@router.post("/api/writing/robots", response_model=Robot)
def post_robot(body: Robot) -> Robot:
    from report_writing.services.robots import create_robot

    try:
        return create_robot(task_root(), body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.get("/api/writing/robots/{robot_id}", response_model=Robot)
def get_robot_detail(robot_id: str) -> Robot:
    from report_writing.services.robots import get_robot

    robot = get_robot(task_root(), robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    return robot


@router.get("/api/writing/robots/{robot_id}/render-templates", response_model=list[RenderTemplateSummary])
def get_robot_render_templates(robot_id: str) -> list[RenderTemplateSummary]:
    from report_writing.services.robots import get_robot, list_render_templates_for_robot

    if get_robot(task_root(), robot_id) is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    return list_render_templates_for_robot(task_root(), robot_id)


@router.get("/api/writing/robots/{robot_id}/word-styles", response_model=list[WordStyleSummary])
def get_robot_word_styles(robot_id: str) -> list[WordStyleSummary]:
    from report_writing.services.robots import get_robot, list_word_styles_for_robot

    if get_robot(task_root(), robot_id) is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    return list_word_styles_for_robot(task_root(), robot_id)


@router.get("/api/writing/robots/{robot_id}/element-library")
def get_robot_element_library(robot_id: str) -> dict:
    from report_writing.services.element_library import list_categories, list_tags
    from report_writing.services.robots import get_robot, scoped_element_library_items

    home = task_root()
    if get_robot(home, robot_id) is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    items = scoped_element_library_items(home, robot_id)
    return {
        "items": [i.model_dump() for i in items],
        "categories": list_categories(home),
        "tags": list_tags(home),
    }


@router.get("/api/writing/robots/{robot_id}/materials-packs")
def get_robot_materials_packs(
    robot_id: str,
    in_library: bool | None = Query(None),
) -> dict:
    from report_writing.services.robots import get_robot, scoped_materials_packs

    home = task_root()
    if get_robot(home, robot_id) is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    packs = scoped_materials_packs(home, robot_id, in_library=in_library)
    return {
        "items": [p.model_dump() for p in packs],
        "count": len(packs),
    }


@router.put("/api/writing/robots/{robot_id}", response_model=Robot)
def put_robot(robot_id: str, body: Robot) -> Robot:
    from report_writing.services.robots import update_robot

    try:
        return update_robot(task_root(), robot_id, body)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="机器人不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.delete("/api/writing/robots/{robot_id}")
def remove_robot(robot_id: str) -> dict:
    from report_writing.services.robots import delete_robot

    try:
        delete_robot(task_root(), robot_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="机器人不存在") from error
    return {"ok": True}



@router.post("/api/writing/robots/generate-prompts", response_model=GenerateRobotPromptsResponse)
def post_generate_robot_prompts(body: GenerateRobotPromptsRequest) -> GenerateRobotPromptsResponse:
    from report_writing.infra.llm import live_model
    from report_writing.infra.llm_invoke import invoke_prompt
    from report_writing.services.robots import generate_prompts

    model = live_model()

    def invoke(system: str, user: str) -> str:
        if model is None:
            raise HTTPException(status_code=503, detail="未配置大模型，无法生成提示词")
        return invoke_prompt(model, system, user)

    try:
        return generate_prompts(body, invoke=invoke)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



