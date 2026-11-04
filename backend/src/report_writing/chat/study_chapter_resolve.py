"""把「第1章」「2.2章」「1到2章」等解析为 outline chapter key。

范围（整章/本章/子章）由大脑给出，不在这里用正则判意图。
"""

from __future__ import annotations

from report_writing.domain.models import WritingTask

STUDY_SCOPES = frozenset({"whole", "children", "self"})

_RANGE_SEPS = ("到", "至", "-", "—", "~")
_CN_NUM: dict[str, int] = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _sequence_token_to_str(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    if t.isdigit():
        return t
    if len(t) == 1 and t in _CN_NUM:
        return str(_CN_NUM[t])
    if t.startswith("十") and len(t) == 2 and t[1] in _CN_NUM:
        return str(10 + _CN_NUM[t[1]])
    if t.endswith("十") and len(t) == 2 and t[0] in _CN_NUM:
        return str(_CN_NUM[t[0]] * 10)
    if "十" in t and len(t) == 3 and t[1] == "十":
        a, _, b = t[0], t[1], t[2]
        if a in _CN_NUM and b in _CN_NUM:
            return str(_CN_NUM[a] * 10 + _CN_NUM[b])
    return ""


def _consume_number(text: str, i: int) -> tuple[str, int]:
    """消费一个数字段（可含小数点层级，如 2 / 2.2 / 2.2.1）。"""
    n = len(text)
    if i >= n:
        return "", i
    if text[i].isdigit():
        j = i
        while j < n and (text[j].isdigit() or text[j] in "．."):
            # 避免把「2.」末尾孤立点算进序号
            if text[j] in "．.":
                if j + 1 >= n or not text[j + 1].isdigit():
                    break
                j += 1
                continue
            j += 1
        raw = text[i:j].replace("．", ".")
        # 去掉尾部多余点
        while raw.endswith("."):
            raw = raw[:-1]
        return raw, j
    j = i
    while j < n and text[j] in _CN_NUM:
        j += 1
    if j == i:
        return "", i
    norm = _sequence_token_to_str(text[i:j])
    if not norm:
        return "", i
    return norm, j


def _skip_space(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i] in " \t":
        i += 1
    return i


def _top_level_sequence(seq: str) -> str:
    s = (seq or "").strip()
    if not s:
        return ""
    return s.split(".")[0].split("．")[0]


def _is_self_or_descendant(seq: str, root: str) -> bool:
    s = (seq or "").strip()
    r = (root or "").strip()
    if not s or not r:
        return False
    return s == r or s.startswith(r + ".")


def keys_for_top_sequences(
    task: WritingTask | None,
    sequences: list[str],
    *,
    children_only: bool = False,
    scope: str = "",
) -> list[str]:
    """按章序号取学习范围（支持顶层「2」与子节「2.2」）。

    whole（默认）：该序号整棵子树（2、2.1、2.2… 或 2.2、2.2.1…）。
    children：只要子孙，不含该节点本身。
    self：只要该节点，不含下属。
    """
    if task is None or not task.outline or not task.outline.chapters:
        return []
    want = [str(x).strip().replace("．", ".") for x in sequences if str(x).strip()]
    if not want:
        return []
    mode = str(scope or "").strip()
    if mode not in STUDY_SCOPES:
        mode = "children" if children_only else "whole"
    keys: list[str] = []
    seen: set[str] = set()
    for root in want:
        for node in task.outline.chapters:
            if bool(getattr(node, "structure_only", False)):
                continue
            seq = str(getattr(node, "sequence", "") or "").strip().replace("．", ".")
            key = str(getattr(node, "key", "") or "").strip()
            if not seq or not key or key in seen:
                continue
            if not _is_self_or_descendant(seq, root):
                continue
            if mode == "self" and seq != root:
                continue
            if mode == "children" and seq == root:
                continue
            keys.append(key)
            seen.add(key)
    return keys


def parse_study_sequences_from_message(message: str) -> list[str]:
    """从原话抽出章序号（含 2.2）；优先用大脑给的 chapter_sequences，这里只做缺省扫描。"""
    text = (message or "").strip()
    if not text:
        return []
    n = len(text)
    # 范围：仅顶层整数「1到2章」；带点的「2.1到2.3」也支持
    i = 0
    while i < n:
        start = i
        if text.startswith("第", i):
            i += 1
        i = _skip_space(text, i)
        left, j = _consume_number(text, i)
        if not left:
            i = start + 1
            continue
        k = _skip_space(text, j)
        sep = ""
        for token in _RANGE_SEPS:
            if text.startswith(token, k):
                sep = token
                k += len(token)
                break
        if sep:
            k = _skip_space(text, k)
            if text.startswith("第", k):
                k += 1
            k = _skip_space(text, k)
            right, k2 = _consume_number(text, k)
            if right and "." not in left and "." not in right and left.isdigit() and right.isdigit():
                a, b = int(left), int(right)
                if a <= b:
                    return [str(x) for x in range(a, b + 1)]
                return [str(x) for x in range(b, a + 1)]
            if right:
                # 非纯整数范围：至少收下两端
                return list(dict.fromkeys([left, right]))
        i = start + 1

    seqs: list[str] = []
    i = 0
    while i < n:
        start = i
        if text.startswith("第", i):
            i += 1
        i = _skip_space(text, i)
        num, j = _consume_number(text, i)
        if not num:
            i = start + 1
            continue
        j = _skip_space(text, j)
        # 「2.2章」「第2.2章节」「学习2.2」
        if j < n and text[j] == "章":
            seqs.append(num)
            i = j + 1
            if i < n and text[i] == "节":
                i += 1
            continue
        # 无「章」字：若紧跟学习语境尾部或停顿，也收下带点序号（学习2.2）
        if "." in num:
            seqs.append(num)
            i = j
            continue
        i = start + 1
    return list(dict.fromkeys(seqs))


def resolve_study_chapter_keys(
    task: WritingTask | None,
    message: str,
    *,
    llm_sequences: list[str] | None = None,
    children_only: bool | None = None,
    scope: str = "",
) -> list[str]:
    seqs = [str(s).strip().replace("．", ".") for s in (llm_sequences or []) if str(s).strip()]
    if not seqs:
        seqs = parse_study_sequences_from_message(message)
    mode = str(scope or "").strip()
    if mode in STUDY_SCOPES:
        return keys_for_top_sequences(task, seqs, scope=mode)
    return keys_for_top_sequences(task, seqs, children_only=bool(children_only))
