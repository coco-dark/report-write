"""机器人绑定：个人要素与资料包范围。"""

from __future__ import annotations

from pathlib import Path

from report_writing.domain.models import ElementLibraryItem
from report_writing.services.robots import (
    create_robot,
    filter_element_library_for_robot,
    filter_materials_packs_for_robot,
    get_robot,
    scoped_element_library_items,
    scoped_materials_packs,
    update_robot,
)


class _Pack:
    def __init__(self, pid: str):
        self.id = pid
        self.name = pid


def test_robot_scopes_element_library_and_packs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")

    robot = create_robot(
        tmp_path,
        {
            "id": "r_scope",
            "name": "范围测试",
            "element_library_ids": ["el_a"],
            "materials_pack_ids": ["mp_a"],
        },
    )
    assert robot.element_library_ids == ["el_a"]
    assert robot.materials_pack_ids == ["mp_a"]

    items = [
        ElementLibraryItem(id="el_a", name="A"),
        ElementLibraryItem(id="el_b", name="B"),
    ]
    packs = [_Pack("mp_a"), _Pack("mp_b")]

    scoped_el = filter_element_library_for_robot(items, robot)
    assert [i.id for i in scoped_el] == ["el_a"]

    scoped_mp = filter_materials_packs_for_robot(packs, robot)
    assert [p.id for p in scoped_mp] == ["mp_a"]

    assert filter_element_library_for_robot(items, None) == items
    assert filter_materials_packs_for_robot(packs, None) == packs

    empty_robot = get_robot(tmp_path, "r_scope")
    assert empty_robot is not None
    update_robot(tmp_path, "r_scope", {"element_library_ids": [], "materials_pack_ids": []})
    empty_robot = get_robot(tmp_path, "r_scope")
    assert filter_element_library_for_robot(items, empty_robot) == []
    assert filter_materials_packs_for_robot(packs, empty_robot) == []


def test_scoped_list_helpers_without_robot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_WRITING_DATABASE", "file")
    assert scoped_element_library_items(tmp_path, None) == []
    assert scoped_materials_packs(tmp_path, None, in_library=True) == []
