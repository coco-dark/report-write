"""带限流重试的模型单次调用。"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

_log = logging.getLogger("uvicorn.error")

_MAX_RETRIES = 2
_BASE_SLEEP = 1.2


def invoke_messages(model: BaseChatModel, messages: list[BaseMessage]) -> str:
    from report_writing.infra.llm_errors import is_rate_limit_error

    last: BaseException | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            mid = getattr(model, "model_name", None) or getattr(model, "model", None) or "?"
            _log.info("LLM invoke_messages model=%s", mid)
            message = model.invoke(messages)
            return str(getattr(message, "content", None) or message)
        except Exception as error:
            last = error
            if not is_rate_limit_error(error) or attempt >= _MAX_RETRIES:
                raise
            sleep_s = _BASE_SLEEP * (2**attempt)
            _log.warning("LLM 429 限流，%.1fs 后重试 (%s/%s)", sleep_s, attempt + 1, _MAX_RETRIES)
            time.sleep(sleep_s)
    raise last  # type: ignore[misc]
