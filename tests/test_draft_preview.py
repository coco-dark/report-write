"""已写章节 HTML 预览：按任务隔离组装。"""

from __future__ import annotations

from report_writing.domain.content_blocks import ensure_html_fragment, promote_tables_in_html
from report_writing.domain.models import (
    ChapterStatus,
    ChapterState,
    OutlineNode,
    OutlineTree,
    WritingTask,
)
from report_writing.services.render import build_written_chapters_preview


def test_blueprint_preview_uses_writing_clone_bodies():
    """蓝本 id 预览应读写作 clone 正文（模拟 get_draft_preview 合并逻辑）。"""
    blueprint = WritingTask(
        id="bpPreviewMerge",
        title="蓝本",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="c1", sequence="1", title="总则"),
                OutlineNode(key="c2", sequence="2", title="建设规模"),
            ]
        ),
        chapters=[],
    )
    writing = WritingTask(
        id="wPreviewMerge",
        title="蓝本（对话写作）",
        source_blueprint_id="bpPreviewMerge",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", sequence="1", title="总则")]),
        chapters=[
            ChapterState(
                key="c1",
                title="总则",
                status=ChapterStatus.CONFIRMED,
                official_text="<p>已写总则</p>",
            )
        ],
    )
    # 合并蓝本大纲顺序 + clone 正文（与 API 行为一致）
    writing.outline = blueprint.outline
    out = build_written_chapters_preview(writing, written_only=True, confirmed_only=False)
    assert out["written_count"] >= 1
    assert "已写总则" in out["html"]

    # 仅蓝本无正文 → written_count=0（说明必须解析 clone）
    empty = build_written_chapters_preview(blueprint, written_only=True, confirmed_only=False)
    assert empty["written_count"] == 0


def test_build_written_chapters_preview_only_written_and_ordered():
    task = WritingTask(
        id="wPreviewA",
        title="安全预评价-A",
        outline=OutlineTree(
            chapters=[
                OutlineNode(key="c1", sequence="1", title="总则"),
                OutlineNode(key="c2", sequence="2", title="建设规模"),
                OutlineNode(key="c3", sequence="2.1", title="仅目录", structure_only=True),
            ]
        ),
        chapters=[
            ChapterState(
                key="c1",
                title="总则",
                status=ChapterStatus.AWAITING_CONFIRM,
                official_text="<p>第一章正文</p>",
            ),
            ChapterState(
                key="c2",
                title="建设规模",
                status=ChapterStatus.PENDING,
            ),
        ],
    )
    out = build_written_chapters_preview(task, written_only=True, confirmed_only=False)
    assert out["task_id"] == "wPreviewA"
    assert out["written_count"] == 1
    assert "第一章正文" in out["html"]
    assert "建设规模" not in out["html"]
    assert "仅目录" not in out["html"]
    assert [c["key"] for c in out["chapters"]] == ["c1"]


def test_preview_orders_by_sequence_not_writing_order():
    """写作先后把 2.3 写在 2.2 前面时，全文预览仍按章节序号 2.2 → 2.3。"""
    task = WritingTask(
        id="wPreviewOrder",
        title="顺序预览",
        outline=OutlineTree(
            chapters=[
                # 故意按写作先后排列（先写了 2.3 再写 2.2）
                OutlineNode(key="c232", sequence="2.3.2", title="站址自然条件"),
                OutlineNode(key="c22", sequence="2.2", title="建设规模"),
                OutlineNode(key="c231", sequence="2.3.1", title="站址概况"),
            ]
        ),
        chapters=[
            ChapterState(
                key="c232",
                title="站址自然条件",
                status=ChapterStatus.CONFIRMED,
                official_text="<p>正文-自然条件</p>",
            ),
            ChapterState(
                key="c22",
                title="建设规模",
                status=ChapterStatus.CONFIRMED,
                official_text="<p>正文-建设规模</p>",
            ),
            ChapterState(
                key="c231",
                title="站址概况",
                status=ChapterStatus.CONFIRMED,
                official_text="<p>正文-站址概况</p>",
            ),
        ],
    )
    out = build_written_chapters_preview(task, written_only=True, confirmed_only=False)
    assert [c["key"] for c in out["chapters"]] == ["c22", "c231", "c232"]
    pos_scale = out["html"].find("正文-建设规模")
    pos_site = out["html"].find("正文-站址概况")
    pos_nature = out["html"].find("正文-自然条件")
    assert 0 <= pos_scale < pos_site < pos_nature


def test_preview_keeps_html_table():
    task = WritingTask(
        id="wPreviewTable",
        title="含表预览",
        outline=OutlineTree(chapters=[OutlineNode(key="c1", sequence="2.2", title="建设规模")]),
        chapters=[
            ChapterState(
                key="c1",
                title="建设规模",
                status=ChapterStatus.AWAITING_CONFIRM,
                official_text=(
                    "<p>见表。</p>"
                    '<table border="1"><thead><tr><th>项目</th><th>单位</th><th>数值</th></tr></thead>'
                    "<tbody><tr><td>电压等级</td><td>kV</td><td>220</td></tr></tbody></table>"
                ),
            )
        ],
    )
    out = build_written_chapters_preview(task, written_only=True, confirmed_only=False)
    assert "<table" in out["html"]
    assert "<th>项目</th>" in out["html"]
    assert "220" in out["html"]


def test_promote_space_aligned_engineering_table():
    plain = (
        "表2.2-1 工程特性表\n"
        "项目          单位          数值\n"
        "一、变电部分\n"
        "工程名称                      咸宁闽王220kV输变电工程\n"
        "电压等级      kV              220\n"
        "二、线路部分\n"
        "线路长度      km              40.92\n"
    )
    html = ensure_html_fragment(plain)
    assert "<table" in html
    assert "<th>" in html or "<td>" in html
    assert "电压等级" in html
    assert "220" in html

    # 已落库的多段 <p> 伪表格，预览时也应提升
    legacy = (
        "<p>项目  单位  数值</p>"
        "<p>工程名称    咸宁闽王220kV输变电工程</p>"
        "<p>电压等级  kV  220</p>"
    )
    promoted = promote_tables_in_html(legacy)
    assert "<table" in promoted
    assert "电压等级" in promoted
