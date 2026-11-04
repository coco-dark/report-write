"""读取 config/bailian.yml（+ bailian.local.yml），并建成百炼兼容 Chat 模型。"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from report_writing.domain.models import BailianSettings
from report_writing.infra.project_config import bailian_deepseek_section


def _merged_deepseek() -> dict:
    return dict(bailian_deepseek_section())


def load_bailian_settings() -> BailianSettings:
    merged = _merged_deepseek()
    base_url = _normalize_base_url(str(merged.get("base-url") or ""))
    completions = str(merged.get("completions-path") or "/chat/completions").strip() or "/chat/completions"
    return BailianSettings(
        api_key=str(merged.get("api-key") or "").strip(),
        base_url=base_url,
        completions_path=completions if completions.startswith("/") else f"/{completions}",
        model_name=normalize_model_name(str(merged.get("model-name") or "").strip())
        or normalize_model_name(DEFAULT_CHAT_MODEL_NAME),
        timeout=int(merged.get("timeout") or 60),
        temperature=float(merged.get("temperature") if merged.get("temperature") is not None else 0),
        max_tokens=int(merged.get("max-tokens") or 4096),
        thinking_level=str(merged.get("thinking-level") or "NONE"),
        chat_template_kwargs_enabled=bool(merged.get("chat-template-kwargs-enabled")),
    )


# 百炼控制台实际可调用的 Flash 型号（见阿里云 DeepSeek API 文档）
DEFAULT_CHAT_MODEL_NAME = "deepseek-v4.1-flash"

# 口语/旧配置别名 → 控制台 model 参数
_MODEL_ALIASES: dict[str, str] = {
    "deepseek-v4-flash": "deepseek-v4.1-flash",
    "deepseek-v4-flash-latest": "deepseek-v4-flash-0731",
}


def normalize_model_name(name: str) -> str:
    key = (name or "").strip()
    if not key:
        return ""
    return _MODEL_ALIASES.get(key, key)


def load_bailian_intent_model_name() -> str:
    merged = _merged_deepseek()
    name = str(merged.get("intent-model-name") or "").strip()
    if not name:
        name = str(merged.get("model-name") or "").strip()
    return normalize_model_name(name) or normalize_model_name(DEFAULT_CHAT_MODEL_NAME)


def load_bailian_control_model_name() -> str:
    merged = _merged_deepseek()
    name = str(merged.get("control-model-name") or "").strip()
    if not name:
        name = str(merged.get("model-name") or "").strip()
    return normalize_model_name(name)


def build_bailian_model(settings: BailianSettings | None = None) -> BaseChatModel:
    resolved = settings or load_bailian_settings()
    model_id = normalize_model_name(resolved.model_name) or normalize_model_name(
        DEFAULT_CHAT_MODEL_NAME
    )
    if not resolved.api_key or not model_id:
        raise RuntimeError(
            "未配置大模型：请在 config/bailian.local.yml 填写 bailian.deepseek.api-key 与 model-name。"
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_id,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        temperature=resolved.temperature,
        max_tokens=resolved.max_tokens,
        timeout=resolved.timeout,
        extra_body=resolved.request_extras(),
    )


def live_model() -> BaseChatModel | None:
    from report_writing.infra.config_settings import llm_mode

    if llm_mode() == "scripted":
        return None
    return build_bailian_model()


def _normalize_base_url(raw: str) -> str:
    trimmed = raw.strip().rstrip("/")
    if not trimmed:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if trimmed.endswith("/compatible-mode"):
        return trimmed + "/v1"
    return trimmed
