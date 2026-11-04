"""要素库修改后同步到任务内 library_item_id 克隆。"""

from __future__ import annotations

from report_writing.chat.writing_control_route import _browse_element_items
from report_writing.controller import ensure_task
from report_writing.domain.models import ChatSession, ElementLibraryItem, ElementNode
from report_writing.controller.outline_flow import save_element_nodes
from report_writing.services.element_library import (
    apply_element_to_library_item,
    apply_library_item_to_element,
    get_library_item,
    save_library_items,
    sync_library_items_to_tasks,
    sync_task_elements_to_library,
    upsert_library_item,
)
from report_writing.services.store import TaskStore


def test_apply_library_item_updates_definition_keeps_result():
    el = ElementNode(
        id="el-1",
        name="旧名",
        kind="kv",
        value="旧值",
        result="已生成结果",
        library_item_id="lib-el-1",
    )
    lib = ElementLibraryItem(id="lib-el-1", name="新名", kind="kv", value="新值", category="基础")
    assert apply_library_item_to_element(el, lib) is True
    assert el.name == "新名"
    assert el.value == "新值"
    assert el.category == "基础"
    assert el.result == "已生成结果"
    assert el.id == "el-1"
    assert el.library_item_id == "lib-el-1"
    assert apply_library_item_to_element(el, lib) is False


def test_save_library_items_syncs_task_clones(tmp_path):
    tid = "wtest1"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(
            id="el-a",
            name="旧工程名",
            kind="kv",
            value="A",
            library_item_id="lib-el-proj",
        ),
        ElementNode(id="el-local", name="仅任务内", kind="kv", value="x"),
    ]
    TaskStore(path).save(task)

    save_library_items(
        tmp_path,
        [
            ElementLibraryItem(
                id="lib-el-proj",
                name="工程名称",
                kind="kv",
                value="新城变电站",
                category="项目",
            )
        ],
    )

    reloaded = TaskStore(path).load()
    by_id = {e.id: e for e in reloaded.element_nodes}
    assert by_id["el-a"].name == "工程名称"
    assert by_id["el-a"].value == "新城变电站"
    assert by_id["el-a"].category == "项目"
    assert by_id["el-local"].name == "仅任务内"


def test_upsert_syncs_single_item(tmp_path):
    tid = "bp-sync"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(id="el-1", name="旧", kind="kv", value="1", library_item_id="lib-el-x"),
    ]
    TaskStore(path).save(task)

    upsert_library_item(
        tmp_path,
        ElementLibraryItem(id="lib-el-x", name="新", kind="kv", value="2"),
    )
    reloaded = TaskStore(path).load()
    assert reloaded.element_nodes[0].name == "新"
    assert reloaded.element_nodes[0].value == "2"


def test_browse_skips_stale_task_clone_when_library_has_item(tmp_path):
    save_library_items(
        tmp_path,
        [ElementLibraryItem(id="lib-el-1", name="库中最新名", kind="kv", value="v")],
        sync_tasks=False,
    )
    tid = "bp1"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(id="el-clone", name="任务里旧名", kind="kv", value="v", library_item_id="lib-el-1"),
        ElementNode(id="el-only", name="任务专有", kind="kv", value="x"),
    ]
    TaskStore(path).save(task)
    session = ChatSession(id="s1", phase="intent", task_id=tid, blueprint_id=tid)
    lib_rows, task_rows, _ = _browse_element_items(tmp_path, session)
    assert [r["name"] for r in lib_rows] == ["库中最新名"]
    assert [r["name"] for r in task_rows] == ["任务专有"]


def test_browse_relinks_renamed_library_by_content(tmp_path):
    """入库后库内改名：按 query 指纹回链，本任务不再出现旧名。"""
    save_library_items(
        tmp_path,
        [
            ElementLibraryItem(
                id="lib-el-rag",
                name="项目基本信息",
                kind="rag_search",
                query="项目概况 建设规模",
            )
        ],
        sync_tasks=False,
    )
    tid = "bp2"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(
            id="el-rag",
            name="知识库要素",
            kind="rag_search",
            query="项目概况 建设规模",
        ),
    ]
    TaskStore(path).save(task)
    session = ChatSession(id="s1", phase="intent", task_id=tid)
    lib_rows, task_rows, _ = _browse_element_items(tmp_path, session)
    assert [r["name"] for r in lib_rows] == ["项目基本信息"]
    assert task_rows == []
    reloaded = TaskStore(path).load().element_nodes[0]
    assert reloaded.library_item_id == "lib-el-rag"
    assert reloaded.name == "项目基本信息"


def test_link_task_element_on_import(tmp_path):
    from report_writing.services.element_library import link_task_element_to_library_item

    tid = "bp3"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(id="el-1", name="知识库要素", kind="rag_search", query="q1"),
    ]
    TaskStore(path).save(task)
    lib = ElementLibraryItem(id="lib-el-x", name="项目基本信息", kind="rag_search", query="q1")
    assert link_task_element_to_library_item(
        tmp_path, task_id=tid, element_id="el-1", item=lib
    )
    el = TaskStore(path).load().element_nodes[0]
    assert el.library_item_id == "lib-el-x"
    assert el.name == "项目基本信息"


def test_sync_library_items_to_tasks_counts(tmp_path):
    tid = "w2"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(id="e1", name="a", library_item_id="lib-1"),
        ElementNode(id="e2", name="b", library_item_id="lib-2"),
    ]
    TaskStore(path).save(task)
    n = sync_library_items_to_tasks(
        tmp_path,
        [
            ElementLibraryItem(id="lib-1", name="A1", kind="kv"),
            ElementLibraryItem(id="lib-missing", name="nope", kind="kv"),
        ],
    )
    assert n == 1
    assert TaskStore(path).load().element_nodes[0].name == "A1"


def test_apply_element_to_library_item_keeps_id_and_links():
    lib = ElementLibraryItem(
        id="lib-el-1",
        name="旧名",
        kind="kv",
        value="旧",
        link_ids=["lib-el-2"],
        created_at="2020-01-01T00:00:00Z",
    )
    el = ElementNode(
        id="el-1",
        name="新名",
        kind="kv",
        value="新",
        category="基础",
        purpose="说明",
        library_item_id="lib-el-1",
    )
    assert apply_element_to_library_item(el, lib) is True
    assert lib.id == "lib-el-1"
    assert lib.name == "新名"
    assert lib.value == "新"
    assert lib.category == "基础"
    assert lib.purpose == "说明"
    assert lib.link_ids == ["lib-el-2"]
    assert lib.created_at == "2020-01-01T00:00:00Z"
    assert apply_element_to_library_item(el, lib) is False


def test_save_element_nodes_syncs_back_to_library(tmp_path):
    """章节配置改库克隆要素后，个人要素库应同步更新。"""
    save_library_items(
        tmp_path,
        [
            ElementLibraryItem(
                id="lib-el-proj",
                name="工程名称",
                kind="kv",
                value="旧值",
                category="项目",
            )
        ],
        sync_tasks=False,
    )
    tid = "w-sync-back"
    path = tmp_path / "tasks" / tid
    ensure_task(path)
    task = TaskStore(path).load()
    task.element_nodes = [
        ElementNode(
            id="el-a",
            name="工程名称",
            kind="kv",
            value="旧值",
            category="项目",
            library_item_id="lib-el-proj",
        ),
        ElementNode(id="el-local", name="仅任务内", kind="kv", value="x"),
    ]
    TaskStore(path).save(task)

    save_element_nodes(
        path,
        [
            ElementNode(
                id="el-a",
                name="工程名称（已改）",
                kind="kv",
                value="新城变电站",
                category="项目",
                purpose="标题用",
                library_item_id="lib-el-proj",
            ),
            ElementNode(id="el-local", name="仅任务内", kind="kv", value="x"),
        ],
    )

    lib = get_library_item(tmp_path, "lib-el-proj")
    assert lib is not None
    assert lib.name == "工程名称（已改）"
    assert lib.value == "新城变电站"
    assert lib.purpose == "标题用"


def test_sync_task_elements_to_library_propagates_to_other_tasks(tmp_path):
    save_library_items(
        tmp_path,
        [ElementLibraryItem(id="lib-el-x", name="旧", kind="kv", value="1")],
        sync_tasks=False,
    )
    path_a = tmp_path / "tasks" / "ta"
    path_b = tmp_path / "tasks" / "tb"
    ensure_task(path_a)
    ensure_task(path_b)
    for path, eid in ((path_a, "el-a"), (path_b, "el-b")):
        task = TaskStore(path).load()
        task.element_nodes = [
            ElementNode(id=eid, name="旧", kind="kv", value="1", library_item_id="lib-el-x"),
        ]
        TaskStore(path).save(task)

    n = sync_task_elements_to_library(
        tmp_path,
        [ElementNode(id="el-a", name="新", kind="kv", value="2", library_item_id="lib-el-x")],
    )
    assert n == 1
    assert get_library_item(tmp_path, "lib-el-x").name == "新"
    assert TaskStore(path_b).load().element_nodes[0].name == "新"
    assert TaskStore(path_b).load().element_nodes[0].value == "2"
