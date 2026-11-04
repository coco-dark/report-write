"""百炼 / OpenAI 兼容接口错误识别与用户提示。"""

from __future__ import annotations

_RATE_LIMIT_HINT = (
    "百炼模型当前请求频率或配额已达上限（429 限流）。请稍等 1～2 分钟再试，"
    "或到阿里云百炼控制台查看用量与 RPM；也可在 config/bailian.local.yml 换模型或开通更高配额。"
)


def is_model_not_found_error(exc: BaseException | None = None, text: str = "") -> bool:
    blob = f"{exc!r} {text}".lower()
    return "modelnotfound" in blob or "model not exist" in blob or "invalidrequesterror" in blob and "model" in blob


def is_rate_limit_error(exc: BaseException | None = None, text: str = "") -> bool:
    blob = f"{exc!r} {text}".lower()
    if "429" in blob or "limitrequests" in blob or "limit_requests" in blob:
        return True
    markers = (
        "rate limit",
        "rate-limit",
        "request limit",
        "exceeded your current request",
        "too many requests",
        "限流",
        "请求过于频繁",
        "配额",
    )
    return any(m in blob for m in markers)


def user_message_for_llm_error(exc: BaseException) -> str | None:
    if is_model_not_found_error(exc):
        return (
            "百炼返回「模型不存在」(404)。请在 config/bailian.local.yml 使用控制台已开通的 model 名称，"
            "例如 deepseek-v4.1-flash 或 deepseek-v4-flash-0731；"
            "勿填 deepseek-v4-flash（会映射为 v4.1-flash）。"
        )
    if is_rate_limit_error(exc):
        return _RATE_LIMIT_HINT
    raw = str(exc or "").strip()
    if not raw:
        return None
    if len(raw) > 400:
        raw = raw[:400] + "…"
    return f"这边出了点问题：{raw}。你可以稍后再试，或点下方按钮重试。"
