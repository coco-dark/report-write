"""导出相关资源（渲染模版、Word 样式）CRUD — 总控 Agent 直接调度。"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain.tools import tool
from pydantic import Field

from report_writing.agents.state import ChatControlState


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _set_cards(state: ChatControlState, cards: list) -> None:
    state.cards = list(cards or [])


def build_export_resource_tools(state: ChatControlState) -> list:
    home = state.home

    @tool("list_render_templates", description="何时：用户要看有哪些 HTML 渲染模版（导出 Word 的版式占位）。不要：用本工具新建；配置向导用 open_render_template_wizard。")
    def list_render_templates() -> str:
        """

        何时：用户要看有哪些 HTML 渲染模版（导出 Word 的版式占位）。

        必填：无。

        可选：无。

        不要：用本工具新建；配置向导用 open_render_template_wizard。

        """
        from report_writing.services.robots import scoped_render_templates

        items = scoped_render_templates(home, state.session.robot_id or None)
        if not items:
            return _dump({"ok": True, "reply_hint": "还没有渲染模版，可用 upsert_render_template 新建或 open_render_template_wizard 开卡片向导。"})
        lines = [f"- {getattr(t, 'name', '') or t.id} ({t.id})" for t in items[:30]]
        return _dump({"ok": True, "templates": [t.model_dump(mode="json") for t in items[:30]], "reply_hint": "渲染模版：\n" + "\n".join(lines)})

    @tool("get_render_template", description="何时：读取单个渲染模版（含 html）。缺了：先 list 再请用户指定，不要猜。")
    def get_render_template(template_id: Annotated[str, Field(description="必填。渲染模版 id。缺了先列出再请用户指定。")]) -> str:
        """

        何时：读取单个渲染模版（含 html）。

        必填：template_id。

        可选：无。

        缺了：先 list 再请用户指定，不要猜。

        """
        from report_writing.services.render import get_template

        tid = (template_id or "").strip()
        if not tid:
            return _dump({"ok": False, "reply_hint": "需要 template_id。"})
        found = get_template(home, tid)
        if found is None:
            return _dump({"ok": False, "reply_hint": f"没找到渲染模版 {tid}。"})
        return _dump({"ok": True, "template": found, "reply_hint": f"已读取渲染模版 {found.get('name') or tid}。"})

    @tool("upsert_render_template", description="何时：创建或更新渲染模版。缺了：问模版名称；html 没有可开向导编辑。")
    def upsert_render_template(
        name: Annotated[str, Field(description="必填。模版名称。")],
        template_id: Annotated[str, Field(description="可选。空则新建。")] = "",
        desc: Annotated[str, Field(description="可选。说明。")] = "",
        html: Annotated[str, Field(description="可选。占位 HTML；没有可开向导。")] = "",
        bind_to_robot: Annotated[bool, Field(description="可选。默认 true，绑定当前机器人。")] = True,
    ) -> str:
        """

        何时：创建或更新渲染模版。

        必填：name。

        可选：template_id（空则新建）；desc；html；bind_to_robot 默认 true。

        缺了：问模版名称；html 没有可开向导编辑。

        """
        from report_writing.chat.resource_actions import _bind_robot_id
        from report_writing.services.render import save_template

        payload = {
            "id": (template_id or "").strip(),
            "name": (name or "").strip() or "未命名渲染模版",
            "desc": (desc or "").strip(),
            "html": html or "",
        }
        try:
            saved = save_template(home, payload)
        except ValueError as error:
            return _dump({"ok": False, "reply_hint": str(error)})
        tid = str(saved.get("id") or "").strip()
        if bind_to_robot and tid:
            _bind_robot_id(home, state.session, "render_template_ids", tid)
        return _dump(
            {
                "ok": True,
                "template_id": tid,
                "reply_hint": f"渲染模版「{saved.get('name') or tid}」已保存。",
            }
        )

    @tool("delete_render_template", description="何时：用户明确要删除渲染模版。不可恢复，没把握先澄清。缺了：问删哪一份，不要猜。")
    def delete_render_template(template_id: Annotated[str, Field(description="必填。要删除的模版 id。没把握先澄清。")]) -> str:
        """

        何时：用户明确要删除渲染模版。不可恢复，没把握先澄清。

        必填：template_id。

        可选：无。

        缺了：问删哪一份，不要猜。

        """
        from report_writing.services.render import delete_template

        tid = (template_id or "").strip()
        if not tid:
            return _dump({"ok": False, "reply_hint": "需要 template_id。"})
        try:
            delete_template(home, tid)
        except KeyError:
            return _dump({"ok": False, "reply_hint": f"渲染模版 {tid} 不存在。"})
        except ValueError as error:
            return _dump({"ok": False, "reply_hint": str(error)})
        return _dump({"ok": True, "reply_hint": f"已删除渲染模版 {tid}。"})

    @tool("open_render_template_wizard", description="何时：在对话里打开渲染模版卡片向导（选用或新建）。只浏览列表用 list_render_templates。")
    def open_render_template_wizard() -> str:
        """

        何时：在对话里打开渲染模版卡片向导（选用或新建）。只浏览列表用 list_render_templates。

        必填：无。

        可选：无。

        """
        from report_writing.chat.resource_actions import open_template_wizard_choose

        reply, cards = open_template_wizard_choose(state.session, "render_template")
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    @tool("list_word_styles", description="何时：用户要看有哪些 Word 导出样式。不要：用本工具代替配置向导。")
    def list_word_styles() -> str:
        """

        何时：用户要看有哪些 Word 导出样式。

        必填：无。

        可选：无。

        不要：用本工具代替配置向导。

        """
        from report_writing.services.robots import scoped_word_styles

        items = scoped_word_styles(home, state.session.robot_id or None)
        if not items:
            return _dump({"ok": True, "reply_hint": "还没有自定义 Word 样式，可用 upsert_word_style 新建或 open_word_style_wizard。"})
        lines = [f"- {getattr(t, 'name', '') or t.id} ({t.id})" for t in items[:30]]
        return _dump({"ok": True, "styles": [t.model_dump(mode="json") for t in items[:30]], "reply_hint": "Word 样式：\n" + "\n".join(lines)})

    @tool("get_word_style", description="何时：读取单个 Word 样式。缺了：先 list 再请用户指定。")
    def get_word_style(style_id: Annotated[str, Field(description="必填。Word 样式 id。")]) -> str:
        """

        何时：读取单个 Word 样式。

        必填：style_id。

        可选：无。

        缺了：先 list 再请用户指定。

        """
        from report_writing.services.word_styles import get_word_style

        sid = (style_id or "").strip()
        if not sid:
            return _dump({"ok": False, "reply_hint": "需要 style_id。"})
        found = get_word_style(home, sid)
        if found is None:
            return _dump({"ok": False, "reply_hint": f"没找到 Word 样式 {sid}。"})
        return _dump({"ok": True, "style": found, "reply_hint": f"已读取 Word 样式 {found.get('name') or sid}。"})

    @tool("upsert_word_style", description="何时：创建或更新 Word 样式。缺了：问样式名称；settings 没有可开向导。")
    def upsert_word_style(
        name: Annotated[str, Field(description="必填。样式名称。")],
        style_id: Annotated[str, Field(description="可选。空则新建。")] = "",
        desc: Annotated[str, Field(description="可选。")] = "",
        settings_json: Annotated[str, Field(description="可选。WordStyleSettings 字段 JSON。")] = "{}",
        bind_to_robot: Annotated[bool, Field(description="可选。默认 true。")] = True,
    ) -> str:
        """

        何时：创建或更新 Word 样式。

        必填：name。

        可选：style_id（空则新建）；desc；settings_json；bind_to_robot 默认 true。

        缺了：问样式名称；settings 没有可开向导。

        """
        from report_writing.chat.resource_actions import _bind_robot_id
        from report_writing.services.word_styles import save_word_style

        try:
            settings = json.loads(settings_json or "{}")
        except json.JSONDecodeError:
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        payload = {
            "id": (style_id or "").strip(),
            "name": (name or "").strip() or "未命名 Word 样式",
            "desc": (desc or "").strip(),
            "settings": settings,
        }
        try:
            saved = save_word_style(home, payload)
        except ValueError as error:
            return _dump({"ok": False, "reply_hint": str(error)})
        sid = str(saved.get("id") or "").strip()
        if bind_to_robot and sid:
            _bind_robot_id(home, state.session, "word_style_ids", sid)
        return _dump({"ok": True, "style_id": sid, "reply_hint": f"Word 样式「{saved.get('name') or sid}」已保存。"})

    @tool("delete_word_style", description="何时：用户明确要删除 Word 样式。不可恢复，没把握先澄清。缺了：问删哪一份。")
    def delete_word_style(style_id: Annotated[str, Field(description="必填。要删除的样式 id。")]) -> str:
        """

        何时：用户明确要删除 Word 样式。不可恢复，没把握先澄清。

        必填：style_id。

        可选：无。

        缺了：问删哪一份。

        """
        from report_writing.services.word_styles import delete_word_style

        sid = (style_id or "").strip()
        if not sid:
            return _dump({"ok": False, "reply_hint": "需要 style_id。"})
        try:
            delete_word_style(home, sid)
        except KeyError:
            return _dump({"ok": False, "reply_hint": f"Word 样式 {sid} 不存在。"})
        except ValueError as error:
            return _dump({"ok": False, "reply_hint": str(error)})
        return _dump({"ok": True, "reply_hint": f"已删除 Word 样式 {sid}。"})

    @tool("open_word_style_wizard", description="何时：在对话里打开 Word 样式卡片向导。未配置时导出仍可用默认样式，不要强推。")
    def open_word_style_wizard() -> str:
        """

        何时：在对话里打开 Word 样式卡片向导。未配置时导出仍可用默认样式，不要强推。

        必填：无。

        可选：无。

        """
        from report_writing.chat.resource_actions import open_template_wizard_choose

        reply, cards = open_template_wizard_choose(state.session, "word_style")
        _set_cards(state, cards)
        return _dump({"ok": True, "reply_hint": reply})

    return [
        list_render_templates,
        get_render_template,
        upsert_render_template,
        delete_render_template,
        open_render_template_wizard,
        list_word_styles,
        get_word_style,
        upsert_word_style,
        delete_word_style,
        open_word_style_wizard,
    ]
