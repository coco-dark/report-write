"""用户、模型连接与 Token 用量。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from report_writing.domain.models import Entity


class UserProfile(Entity):
    id: str
    username: str = ""
    display_name: str = ""
    created_at: str = ""


class UserLlmConfig(Entity):
    """用户自配模型（OpenAI 兼容接口）。"""

    provider: str = "openai_compatible"  # openai_compatible | bailian | deepseek | custom
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    completions_path: str = "/chat/completions"
    timeout: int = 120
    temperature: float = 0.0
    max_tokens: int = 4096
    intent_model_name: str = ""  # 窄 JSON 分类器（澄清/槽位）；字段名是配置契约，空则 yaml intent-model-name
    enabled: bool = False  # True 时优先于系统百炼配置

    def effective_base_url(self) -> str:
        url = (self.base_url or "").strip().rstrip("/")
        if url:
            return url
        presets = {
            "bailian": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "openai_compatible": "https://api.openai.com/v1",
        }
        return presets.get(self.provider, presets["openai_compatible"])


class UserLlmConfigPublic(Entity):
    """返回给前端的配置（密钥打码）。"""

    provider: str = "openai_compatible"
    base_url: str = ""
    api_key_set: bool = False
    api_key_hint: str = ""
    model_name: str = ""
    completions_path: str = "/chat/completions"
    timeout: int = 120
    temperature: float = 0.0
    max_tokens: int = 4096
    intent_model_name: str = ""
    enabled: bool = False
    using_system_default: bool = True
    # 实际调用来源（与 enabled / 系统 yml 一致，便于界面说明）
    active_source: str = "system"  # system | user
    active_model_name: str = ""
    system_model_name: str = ""


class TokenDayBucket(Entity):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0


class TokenUsageSummary(Entity):
    user_id: str = ""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_calls: int = 0
    by_day: dict[str, TokenDayBucket] = Field(default_factory=dict)
    recent: list[dict[str, Any]] = Field(default_factory=list)


class RegisterRequest(Entity):
    username: str = ""
    password: str = ""
    display_name: str = ""


class LoginRequest(Entity):
    username: str = ""
    password: str = ""


class AuthResponse(Entity):
    user_id: str = ""
    username: str = ""
    display_name: str = ""


class ProfileUpdateRequest(Entity):
    display_name: str = ""
