"""Word 样式 HTTP 路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from report_writing.api.deps import task_root
from report_writing.domain.word_style import WordStyleSummary, WordStyleUpsert

router = APIRouter(tags=["word-styles"])


@router.get("/api/writing/word-styles", response_model=list[WordStyleSummary])
def get_word_styles(robot_id: str = "") -> list[WordStyleSummary]:
    from report_writing.services.robots import get_robot, scoped_word_styles

    home = task_root()
    rid = (robot_id or "").strip()
    if rid and get_robot(home, rid) is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    return scoped_word_styles(home, rid if rid else None)


@router.get("/api/writing/word-styles/{style_id}")
def get_word_style(style_id: str) -> dict:
    from report_writing.services.word_styles import get_word_style

    found = get_word_style(task_root(), style_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Word 样式不存在")
    return found


@router.post("/api/writing/word-styles")
def post_word_style(body: WordStyleUpsert) -> dict:
    from report_writing.services.word_styles import save_word_style

    try:
        return save_word_style(task_root(), body.to_dict())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.put("/api/writing/word-styles/{style_id}")
def put_word_style(style_id: str, body: WordStyleUpsert) -> dict:
    from report_writing.services.word_styles import get_word_style, save_word_style

    if get_word_style(task_root(), style_id) is None:
        raise HTTPException(status_code=404, detail="Word 样式不存在")
    payload = body.to_dict()
    payload["id"] = style_id
    try:
        return save_word_style(task_root(), payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.delete("/api/writing/word-styles/{style_id}")
def delete_word_style(style_id: str) -> dict:
    from report_writing.services.word_styles import delete_word_style

    try:
        delete_word_style(task_root(), style_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Word 样式不存在") from error
    return {"ok": True, "id": style_id}
