"""包装 Chat 模型，记录 Token 用量。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from report_writing.services.users import current_user_id, record_token_usage

# 与 api/deps._log_event 一致，确保 uvicorn 控制台可见
_log = logging.getLogger("uvicorn.error")


def _data_home() -> Path:
    from report_writing.infra.project_config import data_home_path

    return data_home_path()


def wrap_token_tracking(model: BaseChatModel, *, label: str = "chat") -> BaseChatModel:
    return _TrackingProxy(model, label=label)


def _model_id(model: BaseChatModel) -> str:
    for attr in ("model_name", "model", "model_id"):
        val = getattr(model, attr, None)
        if val:
            return str(val).strip()
    return ""


def _log_llm_call(*, label: str, model_id: str, streaming: bool = False) -> None:
    kind = "LLM 流式调用" if streaming else "LLM 调用"
    _log.info("%s label=%s model=%s", kind, label, model_id or "?")


def _wrap_runnable(inner: Any, *, label: str, model_id: str) -> _RunnableWrap:
    return _RunnableWrap(inner, label=label, model_id=model_id)


class _RunnableWrap:
    """bind / bind_tools 返回的 Runnable 也要打日志与记 Token。"""

    def __init__(self, inner: Any, *, label: str, model_id: str) -> None:
        self._inner = inner
        self._label = label
        self._model_id = model_id

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        _log_llm_call(label=self._label, model_id=self._model_id)
        message = self._inner.invoke(input, config=config, **kwargs)
        _record_from_message(message, label=self._label)
        return message

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        _log_llm_call(label=self._label, model_id=self._model_id, streaming=True)
        last = None
        for chunk in self._inner.stream(input, config=config, **kwargs):
            last = chunk
            yield chunk
        if last is not None:
            _record_from_message(last, label=self._label)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        _log_llm_call(label=self._label, model_id=self._model_id)
        message = await self._inner.ainvoke(input, config=config, **kwargs)
        _record_from_message(message, label=self._label)
        return message

    async def astream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        _log_llm_call(label=self._label, model_id=self._model_id, streaming=True)
        last = None
        async for chunk in self._inner.astream(input, config=config, **kwargs):
            last = chunk
            yield chunk
        if last is not None:
            _record_from_message(last, label=self._label)

    def bind_tools(self, *args: Any, **kwargs: Any) -> _RunnableWrap:
        return _wrap_runnable(
            self._inner.bind_tools(*args, **kwargs),
            label=self._label,
            model_id=self._model_id,
        )

    def bind(self, **kwargs: Any) -> _RunnableWrap:
        return _wrap_runnable(self._inner.bind(**kwargs), label=self._label, model_id=self._model_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _TrackingProxy:
    """透明代理，在 invoke / stream 后写入用量。"""

    def __init__(self, inner: BaseChatModel, label: str = "chat") -> None:
        self._inner = inner
        self._label = label
        self._model_id = _model_id(inner)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        _log_llm_call(label=self._label, model_id=self._model_id)
        message = self._inner.invoke(input, config=config, **kwargs)
        _record_from_message(message, label=self._label)
        return message

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        _log_llm_call(label=self._label, model_id=self._model_id, streaming=True)
        last = None
        for chunk in self._inner.stream(input, config=config, **kwargs):
            last = chunk
            yield chunk
        if last is not None:
            _record_from_message(last, label=self._label)

    def bind_tools(self, *args: Any, **kwargs: Any) -> _RunnableWrap:
        return _wrap_runnable(
            self._inner.bind_tools(*args, **kwargs),
            label=self._label,
            model_id=self._model_id,
        )

    def bind(self, **kwargs: Any) -> _RunnableWrap:
        return _wrap_runnable(self._inner.bind(**kwargs), label=self._label, model_id=self._model_id)

    def with_structured_output(self, *args: Any, **kwargs: Any) -> _RunnableWrap:
        return _wrap_runnable(
            self._inner.with_structured_output(*args, **kwargs),
            label=self._label,
            model_id=self._model_id,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _record_from_message(message: Any, *, label: str) -> None:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        _record_from_usage_dict(usage, label=label)
        return
    meta = getattr(message, "response_metadata", None) or {}
    if isinstance(meta, dict):
        tu = meta.get("token_usage") or meta.get("usage") or {}
        if isinstance(tu, dict) and tu:
            _record_from_usage_dict(tu, label=label)


def _record_from_usage_dict(usage: dict[str, Any], *, label: str) -> None:
    prompt = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("prompt_token_count")
        or 0
    )
    completion = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("completion_token_count")
        or 0
    )
    if prompt == 0 and completion == 0:
        total = int(usage.get("total_tokens") or usage.get("total_token_count") or 0)
        if total > 0:
            completion = total
    if prompt == 0 and completion == 0:
        return
    record_token_usage(
        _data_home(),
        current_user_id(),
        prompt_tokens=prompt,
        completion_tokens=completion,
        label=label,
    )
