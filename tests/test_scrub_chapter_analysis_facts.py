"""章节分析：元数据进 project_slots / extracts，配置字段不做正则 scrub。"""

from report_writing.controller.points import (
    _fold_project_slots,
    _normalize_project_slots,
    _parse_points_keywords_payload,
    audit_chapter_analysis_with_model,
)


def test_normalize_project_slots_from_extracts():
    slots = _normalize_project_slots(
        [
            {"name": "工程名称", "kind": "text", "hint": "取自可研/立项", "required": True},
            {"name": "工程名称"},  # 去重
            {"name": "主变型号", "hint": ""},
            "skip-me",
        ]
    )
    assert [s["name"] for s in slots] == ["工程名称", "主变型号"]
    assert slots[0]["hint"] == "取自可研/立项"
    assert slots[1]["hint"] == "按本项目资料填写"


def test_fold_project_slots_into_describes():
    describes = _fold_project_slots(
        "本章交代工程概况与主设备选型。",
        [
            {"name": "工程名称", "hint": "按立项文件"},
            {"name": "电压等级", "kind": "number"},
        ],
    )
    assert "工程概况" in describes
    assert "须按本项目资料填写的元数据" in describes
    assert "工程名称" in describes
    assert "电压等级" in describes
    # 幂等：再 fold 不重复追加
    again = _fold_project_slots(describes, [{"name": "工程名称"}])
    assert again.count("须按本项目资料填写的元数据") == 1


def test_parse_payload_keeps_slots_separate_from_points():
    raw = """{
      "describes": "交代本工程概况；主设备按项目资料填写。",
      "writing_style": ["客观陈述"],
      "points": ["交代本工程名称与电压等级", "主变型号与台数按项目资料"],
      "keywords": ["工程概况", "主变选型"],
      "project_slots": [
        {"name": "工程名称", "required": true},
        {"name": "主变型号", "hint": "取自设备清册"}
      ],
      "content_blocks": [],
      "reference_text": "襄阳连云220千伏输变电工程采用SFSZ-50000。"
    }"""
    points, keywords, describes, _blocks, reference, structure, style, slots = (
        _parse_points_keywords_payload(raw)
    )
    assert structure is False
    assert "襄阳连云" not in "".join(points)
    assert "SFSZ" not in "".join(keywords)
    assert [s["name"] for s in slots] == ["工程名称", "主变型号"]
    assert "襄阳连云" in reference  # 仅参考正文可保留样板
    assert style == ["客观陈述"]
    assert "概况" in describes


def test_audit_passthrough_without_model():
    points, keywords, describes, blocks, style, slots = audit_chapter_analysis_with_model(
        None,
        points=["按项目资料交代工程名称"],
        keywords=["工程概况"],
        describes="本章写概况。",
        blocks=[],
        writing_style=["简洁"],
        project_slots=[{"name": "工程名称", "hint": "按立项"}],
    )
    assert points == ["按项目资料交代工程名称"]
    assert keywords == ["工程概况"]
    assert describes == "本章写概况。"
    assert style == ["简洁"]
    assert slots[0]["name"] == "工程名称"
    assert blocks == []
