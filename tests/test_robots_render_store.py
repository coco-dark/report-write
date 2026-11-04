"""机器人与渲染模版：无库时仍用 data 目录 JSON。"""

from __future__ import annotations

from pathlib import Path

from report_writing.services.render import delete_template, get_template, list_templates, save_template
from report_writing.services.robots import create_robot, delete_robot, get_robot, list_robots, update_robot


def test_robot_file_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    robot = create_robot(
        tmp_path,
        {
            "id": "r_test01",
            "name": "测试机器人",
            "description": "说明",
            "chat_prompt": "你好",
            "blueprint_ids": ["bp_a"],
        },
    )
    assert robot.id == "r_test01"
    assert (tmp_path / "robots" / "r_test01.json").is_file()

    listed = list_robots(tmp_path)
    assert any(item.id == "r_test01" for item in listed)

    loaded = get_robot(tmp_path, "r_test01")
    assert loaded is not None
    assert loaded.name == "测试机器人"

    updated = update_robot(tmp_path, "r_test01", {"name": "改名机器人"})
    assert updated.name == "改名机器人"

    delete_robot(tmp_path, "r_test01")
    assert get_robot(tmp_path, "r_test01") is None


def test_render_template_file_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    saved = save_template(
        tmp_path,
        {
            "id": "rt_test01",
            "name": "测试模版",
            "html": "<p>{{overview}}</p>",
        },
    )
    assert saved["id"] == "rt_test01"

    items = list_templates(tmp_path)
    assert any(item.id == "rt_test01" for item in items)

    loaded = get_template(tmp_path, "rt_test01")
    assert loaded is not None
    assert "<p>{{overview}}</p>" in loaded.get("html", "")

    delete_template(tmp_path, "rt_test01")
    assert get_template(tmp_path, "rt_test01") is None
