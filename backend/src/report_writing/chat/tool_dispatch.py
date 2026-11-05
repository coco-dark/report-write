"""卡片 / 按钮 / 单测已确定 tool+args 之后的执行入口。

实现仍在 `intent_dispatch.py`。全文件改名（Phase D）之前，新代码从本模块 import。
口语主路径不要用本模块另选 Tool。
"""

from __future__ import annotations

from report_writing.chat import intent_dispatch as _impl

__all__ = [name for name in dir(_impl) if not name.startswith("_")]


def __getattr__(name: str):
    return getattr(_impl, name)
