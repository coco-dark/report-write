"""content_blocks / cells_sample 规范化。"""

from __future__ import annotations

from report_writing.domain.content_blocks import normalize_cells_sample, normalize_content_blocks
from report_writing.domain.models import ContentBlock


def test_normalize_cells_sample_string_rows():
    raw = ["本工程站址位于××。进出线条件××。", "另一行"]
    assert normalize_cells_sample(raw) == [
        ["本工程站址位于××。进出线条件××。"],
        ["另一行"],
    ]


def test_normalize_cells_sample_pipe_row():
    assert normalize_cells_sample(["A | B | C"]) == [["A", "B", "C"]]


def test_normalize_cells_sample_flat_scalars_as_rows():
    assert normalize_cells_sample(["站址", "××", "条件"]) == [["站址"], ["××"], ["条件"]]


def test_normalize_cells_sample_keeps_matrix():
    assert normalize_cells_sample([["a", "b"], ["c", "d"]]) == [["a", "b"], ["c", "d"]]


def test_normalize_content_blocks_accepts_string_cells_for_pydantic():
    blocks = normalize_content_blocks(
        [
            {
                "kind": "table",
                "rows": 1,
                "cols": 1,
                "cells_sample": ["本工程站址位于××。进出线条件××。"],
            }
        ]
    )
    validated = [ContentBlock.model_validate(item) for item in blocks]
    assert validated[0].kind == "table"
    assert validated[0].cells_sample == [["本工程站址位于××。进出线条件××。"]]


def test_content_block_validator_coerces_raw_string_rows():
    """不经 normalize_content_blocks 直接 validate 也应通过。"""
    block = ContentBlock.model_validate(
        {
            "kind": "table",
            "cells_sample": [
                "××省××市××县××镇××村",
                "××行政中心",
                "西北",
                "×.×",
                "开阔",
                "一般",
                "较好",
            ],
        }
    )
    assert block.cells_sample == [
        ["××省××市××县××镇××村"],
        ["××行政中心"],
        ["西北"],
        ["×.×"],
        ["开阔"],
        ["一般"],
        ["较好"],
    ]
