"""Render template HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from report_writing.api.deps import chat_invoke, load_task, task_root
from report_writing.domain.models import RenderRequest, RenderTemplateSummary, RenderTemplateUpsert

router = APIRouter(tags=["render"])

@router.get("/api/writing/render-templates", response_model=list[RenderTemplateSummary])
def get_render_templates(robot_id: str = "") -> list[RenderTemplateSummary]:
    from report_writing.services.robots import scoped_render_templates

    home = task_root()
    rid = (robot_id or "").strip()
    return scoped_render_templates(home, rid if rid else None)



@router.get("/api/writing/render-templates/{template_id}")
def get_render_template(template_id: str) -> dict:
    from report_writing.services.render import get_template

    found = get_template(task_root(), template_id)
    if found is None:
        raise HTTPException(status_code=404, detail="渲染模版不存在")
    return found



@router.post("/api/writing/render-templates")
def post_render_template(body: RenderTemplateUpsert) -> dict:
    from report_writing.services.render import save_template

    try:
        return save_template(task_root(), body.to_dict())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.put("/api/writing/render-templates/{template_id}")
def put_render_template(template_id: str, body: RenderTemplateUpsert) -> dict:
    from report_writing.services.render import get_template, save_template

    if get_template(task_root(), template_id) is None:
        raise HTTPException(status_code=404, detail="渲染模版不存在")
    payload = body.to_dict()
    payload["id"] = template_id
    try:
        return save_template(task_root(), payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error



@router.delete("/api/writing/render-templates/{template_id}")
def delete_render_template(template_id: str) -> dict:
    from report_writing.services.render import delete_template

    try:
        delete_template(task_root(), template_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="渲染模版不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"ok": True, "id": template_id}



@router.post("/api/writing/tasks/{task_id}/render")
def post_render_task(task_id: str, body: RenderRequest) -> dict:
    """预览渲染 HTML（有模版占位填充 / 无模版 LLM 或 plain）。"""
    from report_writing.services.render import render_task

    task = load_task(task_id)
    mode = (body.mode or "auto").strip().lower()
    tid = (body.template_id or "").strip()
    need_llm = mode == "llm" or (mode == "auto" and not tid)
    try:
        result = render_task(
            task_root(),
            task,
            template_id=tid,
            mode=mode,
            confirmed_only=body.confirmed_only,
            invoke=chat_invoke if need_llm else None,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"模版不存在: {error}") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return result.to_dict()



@router.get("/api/writing/tasks/{task_id}/render/download")
def download_render_task(
    task_id: str,
    template_id: str = "",
    style_id: str = "",
    mode: str = "auto",
    confirmed_only: bool = True,
):
    """下载渲染后的 Word（.docx）。"""
    from fastapi.responses import Response

    from report_writing.services.render import render_task_docx

    task = load_task(task_id)
    use_llm = (mode in {"", "auto", "llm"} and not template_id) or mode == "llm"
    try:
        data, result = render_task_docx(
            task_root(),
            task,
            template_id=template_id,
            style_id=style_id,
            mode=mode,
            confirmed_only=confirmed_only,
            invoke=chat_invoke if use_llm else None,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"模版不存在: {error}") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    filename = f"{task.title or task_id}-{result.mode}.docx"
    # RFC 5987 文件名
    from urllib.parse import quote

    encoded = quote(filename)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.get("/api/writing/tasks/{task_id}/render/download/pdf")
def download_render_task_pdf(
    task_id: str,
    template_id: str = "",
    style_id: str = "",
    mode: str = "auto",
    confirmed_only: bool = True,
):
    """下载渲染后的 PDF（与 Word 同源 HTML，含模版/插图占位展开）。"""
    from fastapi.responses import Response

    from report_writing.services.render import render_task_pdf

    task = load_task(task_id)
    use_llm = (mode in {"", "auto", "llm"} and not template_id) or mode == "llm"
    try:
        data, result = render_task_pdf(
            task_root(),
            task,
            template_id=template_id,
            style_id=style_id,
            mode=mode,
            confirmed_only=confirmed_only,
            invoke=chat_invoke if use_llm else None,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"模版不存在: {error}") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    filename = f"{task.title or task_id}-{result.mode}.pdf"
    from urllib.parse import quote

    encoded = quote(filename)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )



