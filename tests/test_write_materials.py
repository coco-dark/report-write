from report_writing.domain.models import ChapterRequirement, OutlineNode
from report_writing.domain.point_items_sync import (
    aux_prompt_for_writing_from_node,
    build_point_write_plan,
    describes_for_writing_from_node,
)
from report_writing.domain.write_materials import (
    WRITE_ELEMENT_EACH_MAX,
    assemble_write_materials,
    pack_element_rows,
    pack_point_write_plan,
    queries_for_write,
)


def test_describes_and_aux_not_truncated_for_write():
    long_desc = "\n".join(f"{i}. 规则条目" + ("详" * 80) for i in range(1, 10))
    long_aux = "补充提示：" + ("约束" * 200)
    node = OutlineNode(key="c1", title="建设规模", describes=long_desc, aux_prompt=long_aux)
    assert describes_for_writing_from_node(node) == long_desc
    assert aux_prompt_for_writing_from_node(node) == long_aux


def test_assemble_write_materials_keeps_full_fields():
    describes = "写作风格：客观说明。\n第五项名称按实际性质书写，如：××（通例，不是具体条目清单）"
    aux = "总述独立成段，不要评价。"
    reference = "样板参考正文" + ("段" * 300)
    element_value = "本工程为既有变电站改建，本期建设规模…" + ("详" * 100)
    requirement = ChapterRequirement(
        key="c1",
        title="建设规模",
        points=["总述", "工程特性表"],
        describes=describes,
        aux_prompt=aux,
        reference_text=reference,
        element_results=[
            {"id": "el-1", "name": "工程性质", "kind": "text", "value": element_value},
            {"id": "el-2", "name": "工程名称", "kind": "text", "value": "某变电站工程"},
        ],
        point_items=[
            {"id": "p1", "text": "总述", "element_id": "el-2", "element_name": "工程名称"},
            {"id": "p2", "text": "工程特性表", "element_id": "el-1", "element_name": "工程性质"},
        ],
    )
    plan = build_point_write_plan(
        requirement.point_items, requirement.element_results, points=requirement.points
    )
    assert plan[1]["element_value"] == element_value
    materials = assemble_write_materials(
        requirement, point_write_plan=plan, reference_text=reference, aux_prompt=aux
    )
    assert describes in materials
    assert "如：××" in materials
    assert aux in materials
    assert reference in materials
    assert element_value in materials
    assert "某变电站工程" in materials
    assert "总述" in materials
    assert "工程特性表" in materials
    assert "至少 5 条" not in materials


def test_long_element_is_excerpted_by_chapter_points():
    needle = "本期建设两台主变扩建"
    filler = "无关附录文字。" * 4000
    long_value = filler[:12000] + needle + "。" + filler[12000:]
    assert len(long_value) > 20000
    requirement = ChapterRequirement(
        key="c1",
        title="建设规模",
        points=["工程特性表含两台主变扩建"],
        describes="先总述再列表。",
        element_results=[
            {"id": "el-1", "name": "可研报告摘录", "kind": "file_slot", "value": long_value},
            {"id": "el-2", "name": "工程名称", "kind": "text", "value": "东郊变电站工程"},
        ],
        point_items=[
            {"id": "p1", "text": "工程特性表含两台主变扩建", "element_id": "el-1"},
        ],
    )
    queries = queries_for_write(requirement)
    packed = pack_element_rows(
        requirement.element_results,
        queries=queries,
        prefer_ids={"el-1"},
    )
    by_id = {row["id"]: row for row in packed}
    assert needle in by_id["el-1"]["value"]
    assert len(by_id["el-1"]["value"]) < len(long_value)
    assert len(by_id["el-1"]["value"]) <= WRITE_ELEMENT_EACH_MAX + 80
    assert by_id["el-1"]["value_omitted"] == len(long_value)
    assert by_id["el-2"]["value"] == "东郊变电站工程"
    plan = pack_point_write_plan(
        build_point_write_plan(
            requirement.point_items, requirement.element_results, points=requirement.points
        ),
        queries=queries,
        packed_elements=packed,
    )
    materials = assemble_write_materials(
        requirement, point_write_plan=plan, element_results=packed
    )
    assert needle in materials
    assert "东郊变电站工程" in materials
    assert "原文共" in materials
    assert len(materials) < len(long_value)
