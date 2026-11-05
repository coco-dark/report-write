"""兼容入口：tool+args 执行已迁到 `tool_dispatch`。"""

from __future__ import annotations

from report_writing.chat import tool_dispatch as _impl

__all__ = [name for name in dir(_impl) if not name.startswith("_")]


def __getattr__(name: str):
    return getattr(_impl, name)
