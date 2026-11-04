"""图片要素占位符展开。"""

from __future__ import annotations

from pathlib import Path

from report_writing.domain.models import ElementNode, WritingTask
from report_writing.services.element_images import (
    expand_image_placeholders_html,
    image_placeholder,
    save_image_bytes,
)


def test_image_placeholder_format():
    assert image_placeholder("el-1") == "{{img:el-1}}"


def test_expand_image_placeholders_html(tmp_path: Path):
    task = WritingTask(id="t1", title="T")
    el = ElementNode(id="el-pic", name="示意图", kind="image", image_source="upload")
    task.element_nodes = [el]
    save_image_bytes(tmp_path, "t1", "el-pic", b"\x89PNG\r\n\x1a\n", filename="x.png")
    el.image_path = "element_images/el-pic.png"
    html = f"<p>前文</p>{image_placeholder('el-pic')}<p>后文</p>"
    preview = expand_image_placeholders_html(html, tmp_path, task, for_export=False)
    assert "/api/writing/tasks/t1/element-images/el-pic" in preview
    assert "{{img:" not in preview
    exported = expand_image_placeholders_html(html, tmp_path, task, for_export=True)
    assert "file://" in exported or "rw-figure" in exported
