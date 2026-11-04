"""Word 导出样式配置（字号、字体、缩进等）。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from report_writing.domain.models import Entity


DEFAULT_CHAPTER_TITLE_PT = {1: 18, 2: 16, 3: 15, 4: 14, 5: 13, 6: 12}


class WordStyleSettings(Entity):
    body_pt: float = 12
    report_title_pt: float = 20
    toc_title_pt: float = 16
    toc_entry_pt: float = 12
    subheading_pt: float = 12
    chapter_title_pt: dict[int, float] = Field(default_factory=lambda: dict(DEFAULT_CHAPTER_TITLE_PT))
    title_font: str = "黑体"
    body_font: str = "宋体"
    first_line_indent_cm: float = 0.74
    line_spacing: float = 1.5
    toc_indent_cm: float = 0.55
    include_toc: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> WordStyleSettings:
        raw = dict(data or {})
        levels = raw.get("chapter_title_pt") or {}
        merged = dict(DEFAULT_CHAPTER_TITLE_PT)
        if isinstance(levels, dict):
            for key, value in levels.items():
                try:
                    merged[int(key)] = float(value)
                except (TypeError, ValueError):
                    continue
        raw["chapter_title_pt"] = merged
        return cls.model_validate(raw)


class WordStyleSummary(Entity):
    id: str
    name: str = ""
    desc: str = ""
    updated: str = ""


class WordStyleUpsert(Entity):
    id: str = ""
    name: str = ""
    desc: str = ""
    settings: WordStyleSettings = Field(default_factory=WordStyleSettings)

    def to_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["settings"] = self.settings.to_dict()
        return payload
