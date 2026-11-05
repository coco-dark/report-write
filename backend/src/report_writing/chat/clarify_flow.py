"""选择卡 UX 的新模块名。

实现仍在 `intent_clarify.py`。全文件改名（Phase D）之前，新代码从本模块 import。
这不是口语「意图分类」；点选由总控 `resolve_intent_clarify` Tool 落槽。
"""

from __future__ import annotations

from report_writing.chat import intent_clarify as _impl

__all__ = [name for name in dir(_impl) if not name.startswith("_")]


def __getattr__(name: str):
    return getattr(_impl, name)
