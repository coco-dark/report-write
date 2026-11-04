"""用户、模型配置与 Token 统计 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from report_writing.api.deps import data_home
from report_writing.domain.user import LoginRequest, ProfileUpdateRequest, RegisterRequest, UserLlmConfig, UserProfile
from report_writing.services.users import (
    current_user_id,
    ensure_default_user,
    get_token_usage,
    get_user,
    login_user,
    public_llm_config,
    register_user,
    save_llm_config,
    update_user_profile,
)

router = APIRouter(tags=["users"])


@router.post("/api/writing/auth/register")
def post_register(body: RegisterRequest) -> dict:
    try:
        auth = register_user(
            data_home(),
            body.username,
            body.password,
            body.display_name,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return auth.to_dict()


@router.post("/api/writing/auth/login")
def post_login(body: LoginRequest) -> dict:
    try:
        auth = login_user(data_home(), body.username, body.password)
    except ValueError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return auth.to_dict()


@router.put("/api/writing/users/me", response_model=UserProfile)
def put_me(body: ProfileUpdateRequest) -> UserProfile:
    home = data_home()
    ensure_default_user(home)
    try:
        return update_user_profile(home, current_user_id(), body.display_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="用户不存在") from error


@router.get("/api/writing/users/me", response_model=UserProfile)
def get_me() -> UserProfile:
    home = data_home()
    ensure_default_user(home)
    uid = current_user_id()
    user = get_user(home, uid)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/api/writing/users/me/llm-config")
def get_my_llm_config() -> dict:
    home = data_home()
    ensure_default_user(home)
    return public_llm_config(home, current_user_id()).to_dict()


@router.put("/api/writing/users/me/llm-config")
def put_my_llm_config(body: UserLlmConfig) -> dict:
    home = data_home()
    ensure_default_user(home)
    try:
        return save_llm_config(home, current_user_id(), body).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/api/writing/users/me/token-usage")
def get_my_token_usage() -> dict:
    home = data_home()
    ensure_default_user(home)
    summary = get_token_usage(home, current_user_id())
    payload = summary.to_dict()
    payload["by_day"] = {key: bucket.to_dict() for key, bucket in (summary.by_day or {}).items()}
    return payload
