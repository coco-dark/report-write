"""含糊「继续」结构辅助（非口语主路径认领）。

口语「继续」由 ReAct + 进度相关 Tool / clarify 处理；
`is_progress_continue_message` 仅作短句结构判断，勿作意图抢路由。
"""

from __future__ import annotations

import re

_CONTINUE_RE = re.compile(
    r"^(继续写?|接着写?|往下|好的?|嗯+|ok)$",
    re.I,
)


def is_progress_continue_message(message: str) -> bool:
    t = re.sub(r"[\s。．！!？?，,、~…]+", "", (message or "").strip())
    t = re.sub(r"[吧呀啊呢哦哈]+$", "", t)
    if not t or len(t) > 12:
        return False
    return bool(_CONTINUE_RE.match(t))
