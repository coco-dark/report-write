"""对话开写前：检查机器人绑定的蓝本 / 渲染 / Word 样式是否就绪。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from report_writing.domain.models import Robot
from report_writing.services.robots import (
    scoped_render_templates,
    scoped_word_styles,
    search_blueprints,
)


def assess_robot_resources(home: Path, robot: Robot | None) -> dict[str, Any]:
    """返回各资源是否满足开写/导出，及缺口说明。"""
    out: dict[str, Any] = {
        "has_blueprint_candidates": False,
        "blueprint_count": 0,
        # 兼容旧字段：知识库已下线，恒为未绑定/有文件
        "knowledge_bound": False,
        "knowledge_has_files": True,
        "knowledge_empty_folder_names": [],
        "render_bound": False,
        "render_has_templates": True,
        "render_count": 0,
        "word_style_bound": False,
        "word_style_has_items": True,
        "word_style_count": 0,
    }
    scope_robot_id = robot.id if robot is not None else None

    bps = search_blueprints(
        home,
        q="",
        robot_id=scope_robot_id or "",
        enabled_only=True,
    )
    out["blueprint_count"] = len(bps)
    out["has_blueprint_candidates"] = len(bps) > 0

    renders = scoped_render_templates(home, scope_robot_id)
    out["render_count"] = len(renders)
    out["render_has_templates"] = len(renders) > 0
    out["render_bound"] = len(renders) > 0

    styles = scoped_word_styles(home, scope_robot_id)
    out["word_style_count"] = len(styles)
    out["word_style_has_items"] = len(styles) > 0
    out["word_style_bound"] = len(styles) > 0

    return out


def blueprint_stage_cards(home: Path, robot: Robot | None) -> list:
    """步骤 1：仅蓝本缺口——引导创建或跳过，不堆其它资源卡片。"""
    from report_writing.chat.cards import _resource_choice_card, _skip_blueprint_card

    info = assess_robot_resources(home, robot)
    if info["has_blueprint_candidates"]:
        return []
    return [
        _resource_choice_card(
            "blueprint",
            title="是否创建蓝本？",
            message="这边还没有能直接用的蓝本。要在对话里建一份吗？上传样板就能提取目录并分析。",
            yes_label="是，在对话里创建蓝本",
            no_label="否，跳过蓝本直接写",
        ),
        _skip_blueprint_card(),
    ]


def knowledge_hint_card(home: Path, robot: Robot | None):
    """知识库已下线：不再提示。"""
    _ = (home, robot)
    return None


def resource_notice_cards(home: Path, robot: Robot | None) -> list:
    """兼容旧调用：等同仅蓝本阶段卡片。"""
    return blueprint_stage_cards(home, robot)


def robot_export_ready(home: Path, robot: Robot | None) -> bool:
    """导出不强制预检；无自定义样式时使用系统默认。"""
    return True


def export_phase_cards(home: Path, task_id: str, robot: Robot | None) -> list:
    """写完可导出；不再在对话里展示导出 / 渲染模版 / Word 样式卡片。"""
    _ = (home, task_id, robot)
    return []
