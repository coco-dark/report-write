"""从样板/原文生成套话正文：固定表述保留，实例换成 {{槽名}}。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from report_writing.domain.chapter_compose import extracts_from_slots

_CMD_LINE = re.compile(
    r"^(?:请)?(?:帮我)?(?:给.{0,30}?)?(?:生成|做成|改成|写成|抽成)?套话(?:正文)?(?:一下)?[。！？.!\s]*$"
)
_AFTER_LABEL = re.compile(r"(?:原文|套话|样板|模板|模版|如下|下面)[：:]\s*([\s\S]+)")
_COMMAND_BITS = re.compile(
    r"根据(?:这个|该|本章)?(?:蓝本|模板|模版|样板|原文)|生成套话(?:正文)?|做成套话|套话正文"
)


@dataclass
class BoilerplateResult:
    text: str
    slots: list[dict[str, Any]] = field(default_factory=list)
    used_llm: bool = False


def extract_source_from_message(message: str) -> str:
    """从口语里抽出用户粘贴的原文；纯指令句则返回空，改用章节已有参考正文。"""
    text = (message or "").strip()
    if not text:
        return ""
    if _CMD_LINE.match(text) and "{{" not in text:
        return ""
    labeled = _AFTER_LABEL.search(text)
    if labeled and len(labeled.group(1).strip()) >= 8:
        return labeled.group(1).strip()
    cleaned = _COMMAND_BITS.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) >= 24:
        return cleaned
    return ""


def heuristic_boilerplate(source: str, slots: list[dict] | None = None) -> str:
    """无模型时：把「槽名：实例值」换成 {{槽名}}，已有占位保持不动。"""
    text = source or ""
    for slot in slots or []:
        name = str(slot.get("name") or "").strip()
        if not name or "{{" + name + "}}" in text:
            continue
        pat = re.compile(
            "(" + re.escape(name) + r"\s*[为是：:]\s*)(?!\{\{)([^\s。；;，,\n（()]{2,40})"
        )
        text = pat.sub(lambda m, n=name: m.group(1) + "{{" + n + "}}", text)
    return text


def ensure_boilerplate_text(
    source: str,
    *,
    slots: list[dict] | None = None,
    title: str = "",
    model=None,
) -> BoilerplateResult:
    raw = (source or "").strip()
    if not raw:
        return BoilerplateResult(text="", slots=list(slots or []))
    if "{{" in raw or "｛｛" in raw:
        return BoilerplateResult(text=raw, slots=list(slots or []))
    generated = generate_boilerplate(raw, slots=slots, title=title, model=model)
    if generated.text.strip():
        return generated
    return BoilerplateResult(text=heuristic_boilerplate(raw, slots), slots=list(slots or []))


def generate_boilerplate(
    source: str,
    *,
    slots: list[dict] | None = None,
    title: str = "",
    model=None,
) -> BoilerplateResult:
    raw = (source or "").strip()
    base_slots = list(slots or [])
    if not raw:
        return BoilerplateResult(text="", slots=base_slots)
    if model is not None:
        llm = _llm_boilerplate(raw, base_slots, title=title, model=model)
        if llm is not None and (llm.text or "").strip():
            return llm
    text = heuristic_boilerplate(raw, base_slots)
    return BoilerplateResult(text=text, slots=base_slots, used_llm=False)


def prepare_reference_from_analysis(
    excerpt: str,
    *,
    compose: str = "",
    slots: list[dict] | None = None,
    title: str = "",
    model=None,
) -> str:
    """分析后写入参考正文：固定/自动生成保留原文；其余只把实例换成 {{槽}}，骨架不改写。"""
    from report_writing.domain.chapter_compose import (
        COMPOSE_AUTO_GENERATE,
        COMPOSE_FIXED,
        normalize_compose,
        uses_slot_reference,
    )

    raw = (excerpt or "").strip()
    if not raw:
        return ""
    mode = normalize_compose(compose)
    if mode in {COMPOSE_FIXED, COMPOSE_AUTO_GENERATE} or not uses_slot_reference(mode):
        return raw
    filled = ensure_boilerplate_text(raw, slots=slots, title=title, model=model)
    text = (filled.text or "").strip()
    return text or raw


def apply_boilerplate_to_node(node: Any, result: BoilerplateResult) -> None:
    from report_writing.domain.content_blocks import ensure_html_fragment

    text = (result.text or "").strip()
    node.reference_text = ensure_html_fragment(text) if text else ""
    merged = extracts_from_slots(list(getattr(node, "extracts", None) or []) + list(result.slots or []))
    if merged:
        node.extracts = merged


def _llm_boilerplate(source: str, slots: list[dict], *, title: str, model) -> BoilerplateResult | None:
    from langchain_core.messages import HumanMessage, SystemMessage

    slot_hint = json.dumps(slots[:20], ensure_ascii=False) if slots else "[]"
    instructions = (
        "# Role\n你是套话抽取助手。\n"
        "# Task\n把报告样板/原文改成可跨项目复用的套话正文。\n"
        "# Rules\n"
        "- 必须保留原文语句、语序、换行与表格骨架，禁止扩写、概括、改写套话。\n"
        "- 工程名、地址、投资额、型号、日期、文号、单位名等实例换成 {{类型名}}。\n"
        "- 类型名优先用已有 slots 的 name；没有则起 2～8 字类型名，禁止把实例值当 name。\n"
        "- 已是 {{槽}} 的不要改。保留原文换行。\n"
        "# Output\n只返回 JSON，不要 markdown："
        "{\"boilerplate\":\"...\",\"slots\":[{\"name\":\"工程名称\",\"hint\":\"...\",\"required\":true}]}"
    )
    prompt = (
        f"章节标题：{title or '（未知）'}\n"
        f"已有槽位：{slot_hint}\n"
        f"原文：\n{source[:8000]}"
    )
    try:
        message = model.invoke([SystemMessage(content=instructions), HumanMessage(content=prompt)])
        content = getattr(message, "content", None) or str(message)
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return None
        payload = json.loads(match.group(0))
        if not isinstance(payload, dict):
            return None
        text = str(payload.get("boilerplate") or payload.get("text") or "").strip()
        raw_slots = payload.get("slots") or payload.get("project_slots") or []
        out_slots = []
        if isinstance(raw_slots, list):
            for item in raw_slots:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                out_slots.append(
                    {
                        "name": name[:40],
                        "kind": str(item.get("kind") or "文本").strip() or "文本",
                        "hint": str(item.get("hint") or "按本项目资料填写").strip(),
                        "required": bool(item.get("required", True)),
                    }
                )
        if not text:
            return None
        return BoilerplateResult(text=text, slots=out_slots or slots, used_llm=True)
    except Exception:
        return None
