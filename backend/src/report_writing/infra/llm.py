"""模型入口：配置见 config/bailian.yml 与 config/application.yml。"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from report_writing.domain.models import BailianSettings
from report_writing.infra.bailian import (
    build_bailian_model,
    load_bailian_control_model_name,
    load_bailian_intent_model_name,
    load_bailian_settings,
    normalize_model_name,
)
from report_writing.infra.config_settings import (
    control_max_tokens,
    control_model_name,
    intent_max_tokens,
    llm_mode,
)
from report_writing.infra.token_tracking import wrap_token_tracking
from report_writing.services.users import current_user_id, resolve_bailian_settings


def _base_settings() -> BailianSettings:
    from report_writing.infra.project_config import data_home_path

    home = data_home_path()
    uid = current_user_id()
    user_settings = resolve_bailian_settings(home, uid)
    if user_settings is not None:
        return user_settings
    return load_bailian_settings()


def live_model(*, label: str = "chat") -> BaseChatModel | None:
    if llm_mode() == "scripted":
        return None
    try:
        model = build_bailian_model(_base_settings())
    except RuntimeError:
        return None
    return wrap_token_tracking(model, label=label)


def live_control_model(*, label: str = "control") -> BaseChatModel | None:
    if llm_mode() == "scripted":
        return None
    try:
        base = _base_settings()
    except RuntimeError:
        return None
    override_model = normalize_model_name(
        control_model_name() or load_bailian_control_model_name()
    )
    data = base.model_dump()
    if override_model:
        data["model_name"] = override_model
    data["max_tokens"] = control_max_tokens()
    tuned = BailianSettings.model_validate(data)
    try:
        return wrap_token_tracking(build_bailian_model(tuned), label=label)
    except RuntimeError:
        return None


def live_intent_model(*, label: str = "intent") -> BaseChatModel | None:
    if llm_mode() == "scripted":
        return None
    from report_writing.infra.project_config import data_home_path

    try:
        base = _base_settings()
    except RuntimeError:
        return None
    from report_writing.services.users import get_llm_config

    home = data_home_path()
    cfg = get_llm_config(home, current_user_id())
    intent_name = normalize_model_name(
        (cfg.intent_model_name or "").strip() or load_bailian_intent_model_name()
    )
    data = base.model_dump()
    data["model_name"] = intent_name
    data["max_tokens"] = intent_max_tokens()
    data["temperature"] = 0.0
    data["thinking_level"] = "NONE"
    data["chat_template_kwargs_enabled"] = False
    tuned = BailianSettings.model_validate(data)
    try:
        return wrap_token_tracking(build_bailian_model(tuned), label=label)
    except RuntimeError:
        return None


__all__ = ["live_model", "live_control_model", "live_intent_model"]
