"""兼容入口：选择卡 UX 已迁到 `clarify_flow`。"""

from __future__ import annotations

from report_writing.chat import clarify_flow as _impl

__all__ = [name for name in dir(_impl) if not name.startswith("_")]


def __getattr__(name: str):
    return getattr(_impl, name)
