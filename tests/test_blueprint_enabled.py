"""蓝本启用/停用与删除。"""

from __future__ import annotations

from pathlib import Path

from report_writing.controller import (
    create_blueprint,
    delete_blueprint,
    list_blueprints,
    set_blueprint_enabled,
)
from report_writing.services.robots import search_blueprints
from report_writing.services.store import TaskStore


def test_enabled_only_search_and_delete(tmp_path: Path):
    home = tmp_path
    tasks = home / "tasks"
    a = create_blueprint(tasks / "bp_a", title="启用蓝本", description="用于写安全预评价报告")
    b = create_blueprint(tasks / "bp_b", title="停用蓝本", description="用于写安全预评价报告")
    assert a.enabled is True
    set_blueprint_enabled(tasks / "bp_b", enabled=False)
    assert TaskStore(tasks / "bp_b").load().enabled is False

    all_items = list_blueprints(tasks, include_disabled=True)
    assert {i.id for i in all_items} >= {"bp_a", "bp_b"}
    enabled_only = list_blueprints(tasks, include_disabled=False)
    assert {i.id for i in enabled_only} == {"bp_a"}

    hits = search_blueprints(home, q="安全预评价", enabled_only=True)
    assert [i.id for i in hits] == ["bp_a"]

    delete_blueprint(tasks / "bp_b")
    assert not (tasks / "bp_b" / "task.json").exists()
    left = list_blueprints(tasks, include_disabled=True)
    assert {i.id for i in left} == {"bp_a"}
