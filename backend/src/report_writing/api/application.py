"""FastAPI application factory and static shell."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from report_writing.api.deps import _PAGE, _STATIC, _log, task_root
from report_writing.api import (  # noqa: F401 — routers
    blueprints,
    chat,
    element_files,
    element_images,
    element_library,
    materials_packs,
    render,
    robots,
    tasks,
    users,
    word_styles,
)
from report_writing.services.users import ensure_default_user, reset_current_user_id, set_current_user_id

app = FastAPI(title="智能文档协作", version="0.1.0")


@app.middleware("http")
async def attach_user_context(request: Request, call_next):
    uid = (request.headers.get("X-User-Id") or "default").strip() or "default"
    token = set_current_user_id(uid)
    try:
        return await call_next(request)
    finally:
        reset_current_user_id(token)

_assets = _STATIC / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


@app.on_event("startup")
def log_rag_domain() -> None:
    from report_writing.infra.rag_parse import rag_domain
    from report_writing.services.data_sync import sync_local_data

    from report_writing.services.users import reset_current_user_id, set_current_user_id, user_workspace

    from report_writing.infra.project_config import data_home_path

    from report_writing.services.users import sync_users_from_disk

    data_home = data_home_path()
    ensure_default_user(data_home)
    synced_users = sync_users_from_disk(data_home)
    if synced_users:
        _log.info("用户账号已同步到数据库：%s", synced_users)
    token = set_current_user_id("default")
    try:
        counts = sync_local_data(user_workspace(data_home, "default"))
    finally:
        reset_current_user_id(token)
    if counts:
        _log.info("本地 data 已同步到数据库：%s", counts)
    _log.info("RAG 使用测试环境 %s", rag_domain() or "未配置")
    try:
        from report_writing.services.chat_session import flush_session_revision_to_mysql
        from report_writing.services.session_redis_store import (
            ensure_session_flush_worker,
            register_flush_handler,
        )

        register_flush_handler(flush_session_revision_to_mysql)
        ensure_session_flush_worker()
    except Exception as error:
        _log.warning("会话异步刷库线程未启动: %s", error)


@app.get("/")
def home() -> FileResponse:
    if not _PAGE.is_file():
        raise HTTPException(status_code=404, detail="页面尚未构建，请在 frontend 目录执行 npm run build")
    return FileResponse(
        _PAGE,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


app.include_router(element_files.router)
app.include_router(element_images.router)
app.include_router(tasks.router)
app.include_router(blueprints.router)
app.include_router(robots.router)
app.include_router(render.router)
app.include_router(word_styles.router)
app.include_router(users.router)
app.include_router(chat.router)
app.include_router(element_library.router)
app.include_router(materials_packs.router)
