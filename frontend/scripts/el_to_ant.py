#!/usr/bin/env python3
"""Mechanical Element Plus → Ant Design Vue tag/attr rewrite."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"

TAG_MAP = {
    "el-button": "a-button",
    "el-form": "a-form",
    "el-form-item": "a-form-item",
    "el-input-number": "a-input-number",
    "el-select": "a-select",
    "el-option": "a-select-option",
    "el-dialog": "a-modal",
    "el-tag": "a-tag",
    "el-empty": "a-empty",
    "el-col": "a-col",
    "el-row": "a-row",
    "el-checkbox-group": "a-checkbox-group",
    "el-checkbox": "a-checkbox",
    "el-tree": "UiTree",
    "el-tooltip": "a-tooltip",
    "el-tab-pane": "a-tab-pane",
    "el-tabs": "a-tabs",
    "el-upload": "a-upload",
    "el-switch": "a-switch",
    "el-radio-button": "a-radio-button",
    "el-radio-group": "a-radio-group",
    "el-radio": "a-radio",
    "el-alert": "a-alert",
    "el-step": "a-step",
    "el-steps": "a-steps",
    "el-autocomplete": "UiAutoComplete",
    "el-scrollbar": "div",
    "el-timeline-item": "a-timeline-item",
    "el-timeline": "a-timeline",
    "el-progress": "a-progress",
    "el-card": "a-card",
}

VALUE_TAGS = {
    "a-input",
    "a-textarea",
    "a-input-password",
    "a-input-number",
    "a-select",
    "a-radio-group",
    "a-checkbox-group",
    "a-auto-complete",
    "a-mentions",
    "a-date-picker",
}
CHECKED_TAGS = {"a-switch", "a-checkbox"}
OPEN_TAGS = {"a-modal"}
ACTIVE_TAGS = {"a-tabs"}

BOOL_ATTR = re.compile(r"(?<![\w:-])(text|round|link|plain|circle|text-bg)(?=(\s|/|>))")


def split_sfc(src: str) -> tuple[str, str]:
    m = re.search(r"<script\b", src)
    if not m:
        return src, ""
    return src[: m.start()], src[m.start() :]


def has_attr(attrs: str, name: str) -> bool:
    return re.search(rf'(^|\s):?{re.escape(name)}(=|\s|/|$)', attrs) is not None


def get_attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'(?:^|\s):?{re.escape(name)}="([^"]*)"', attrs)
    return m.group(1) if m else None


def drop_attr(attrs: str, name: str) -> str:
    return re.sub(rf'\s*:?{re.escape(name)}(?:="[^"]*")?', "", attrs)


def has_bool(attrs: str, name: str) -> bool:
    return re.search(rf'(^|\s){re.escape(name)}(?=(\s|/|>|$))', attrs) is not None


def drop_bool(attrs: str, name: str) -> str:
    return re.sub(rf'\s+{re.escape(name)}(?=(\s|/|>|$))', "", attrs)


def transform_button(attrs: str) -> str:
    typ = get_attr(attrs, "type")
    is_text = has_bool(attrs, "text")
    is_link = has_bool(attrs, "link")
    is_round = has_bool(attrs, "round")
    is_circle = has_bool(attrs, "circle")
    is_plain = has_bool(attrs, "plain")
    attrs = drop_bool(attrs, "text")
    attrs = drop_bool(attrs, "link")
    attrs = drop_bool(attrs, "round")
    attrs = drop_bool(attrs, "circle")
    attrs = drop_bool(attrs, "plain")
    attrs = attrs.replace('native-type=', "html-type=")
    if is_link or (is_text and typ == "primary"):
        attrs = drop_attr(attrs, "type")
        attrs += ' type="link"'
    elif is_text:
        if typ == "danger":
            attrs = drop_attr(attrs, "type")
            attrs += ' type="text" danger'
        else:
            attrs = drop_attr(attrs, "type")
            attrs += ' type="text"'
    elif typ == "danger":
        attrs = drop_attr(attrs, "type")
        attrs += " danger"
    elif typ in {"success", "warning", "info"}:
        attrs = drop_attr(attrs, "type")
        attrs += f' type="default" class="btn-{typ}"'
    if is_round:
        attrs += ' shape="round"'
    if is_circle:
        attrs += ' shape="circle"'
    if is_plain and typ == "danger":
        attrs += " danger"
    if 'size="default"' in attrs:
        attrs = attrs.replace('size="default"', "")
    return attrs


def transform_input_tag(full: str) -> str:
    """Turn el-input into a-input / a-textarea / a-input-password."""
    m = re.match(r"<el-input(\s[^>]*)?(/)?>", full, re.S)
    if not m:
        return full
    attrs = m.group(1) or ""
    self_close = bool(m.group(2))
    typ = get_attr(attrs, "type")
    tag = "a-input"
    if typ == "textarea":
        tag = "a-textarea"
        attrs = drop_attr(attrs, "type")
        attrs = attrs.replace("autosize=", "auto-size=")
        attrs = drop_bool(attrs, "autosize")
    elif typ == "password":
        tag = "a-input-password"
        attrs = drop_attr(attrs, "type")
        attrs = drop_bool(attrs, "show-password")
    attrs = drop_bool(attrs, "show-password")
    attrs = attrs.replace("clearable", "allow-clear")
    close = " />" if self_close or full.endswith("/>") else ">"
    if self_close:
        return f"<{tag}{attrs}{close}"
    return f"<{tag}{attrs}>"


def transform_option(full: str) -> str:
    m = re.match(r"<el-option(\s[^>]*)?(/)?>", full, re.S)
    if not m:
        return full
    attrs = m.group(1) or ""
    self_close = bool(m.group(2)) or full.rstrip().endswith("/>")
    label = get_attr(attrs, "label")
    bound_label = None
    bm = re.search(r':label="([^"]*)"', attrs)
    if bm:
        bound_label = bm.group(1)
    attrs = drop_attr(attrs, "label")
    if self_close:
        if bound_label:
            return f"<a-select-option{attrs}>{ '{{ ' + bound_label + ' }}' }</a-select-option>"
        if label is not None:
            return f"<a-select-option{attrs}>{label}</a-select-option>"
        return f"<a-select-option{attrs} />"
    return f"<a-select-option{attrs}>"


def transform_tab_pane(attrs: str) -> str:
    label = get_attr(attrs, "label")
    name = get_attr(attrs, "name")
    if re.search(r':label="', attrs):
        attrs = re.sub(r':label=', ":tab=", attrs, count=1)
    elif label is not None:
        attrs = drop_attr(attrs, "label")
        attrs += f' tab="{label}"'
    if re.search(r':name="', attrs):
        attrs = re.sub(r':name=', ":key=", attrs, count=1)
    elif name is not None:
        attrs = drop_attr(attrs, "name")
        attrs += f' key="{name}"'
    return attrs


def transform_select(attrs: str) -> str:
    multiple = has_bool(attrs, "multiple")
    allow_create = has_bool(attrs, "allow-create")
    attrs = drop_bool(attrs, "multiple")
    attrs = drop_bool(attrs, "allow-create")
    attrs = drop_bool(attrs, "filterable")
    attrs = drop_bool(attrs, "default-first-option")
    attrs = drop_bool(attrs, "teleported")
    attrs = drop_bool(attrs, "collapse-tags")
    attrs = drop_bool(attrs, "collapse-tags-tooltip")
    if "filterable" in attrs:
        pass
    attrs = attrs.replace("clearable", "allow-clear")
    if "show-search" not in attrs and (multiple or "allow-clear" in attrs):
        pass
    if has_attr(attrs, "filterable") or "filterable" in attrs:
        attrs += " show-search"
    # already dropped filterable bool; if it was present add show-search
    # We dropped it, so track before drop... redo:
    return attrs, multiple, allow_create


def transform_dialog(attrs: str, has_footer: bool) -> str:
    attrs = attrs.replace(":close-on-click-modal=", ":mask-closable=")
    attrs = attrs.replace("close-on-click-modal=", "mask-closable=")
    attrs = drop_bool(attrs, "append-to-body")
    attrs = drop_bool(attrs, "align-center")
    attrs = drop_bool(attrs, "destroy-on-close")
    attrs += " destroy-on-close"
    if "align-center" in attrs or True:
        attrs += " centered"
    if not has_footer and "footer=" not in attrs:
        attrs += ' :footer="null"'
    return attrs


def transform_alert(attrs: str) -> str:
    if re.search(r':title=', attrs):
        attrs = re.sub(r':title=', ":message=", attrs, count=1)
    elif get_attr(attrs, "title") is not None:
        title = get_attr(attrs, "title")
        attrs = drop_attr(attrs, "title")
        attrs += f' message="{title}"'
    attrs = drop_bool(attrs, "show-icon")
    attrs += " show-icon"
    return attrs


def transform_tag(attrs: str) -> str:
    typ = get_attr(attrs, "type")
    mapping = {
        "success": "success",
        "warning": "warning",
        "danger": "error",
        "info": "default",
        "primary": "blue",
    }
    if typ in mapping:
        attrs = drop_attr(attrs, "type")
        attrs += f' color="{mapping[typ]}"'
    attrs = drop_attr(attrs, "effect")
    attrs = drop_bool(attrs, "effect")
    return attrs


def transform_upload(attrs: str) -> str:
    attrs = attrs.replace(":http-request=", ":custom-request=")
    attrs = attrs.replace("http-request=", "custom-request=")
    attrs = attrs.replace(":on-change=", ":on-change=")
    attrs = drop_bool(attrs, "drag")
    if "drag" in attrs:
        pass
    return attrs


def transform_tree_attrs(attrs: str) -> str:
    attrs = attrs.replace(":data=", ":data=")
    attrs = attrs.replace("node-key=", "node-key=")
    attrs = attrs.replace("show-checkbox", "show-checkbox")
    attrs = attrs.replace("default-expand-all", "default-expand-all")
    attrs = attrs.replace(":default-expand-all=", ":default-expand-all=")
    attrs = attrs.replace("highlight-current", "highlight-current")
    attrs = attrs.replace(":current-node-key=", ":current-node-key=")
    attrs = attrs.replace("current-node-key=", "current-node-key=")
    attrs = attrs.replace(":expand-on-click-node=", ":expand-on-click-node=")
    attrs = attrs.replace(":check-on-click-node=", ":check-on-click-node=")
    attrs = attrs.replace(":props=", ":props=")
    return attrs


def rewrite_vmodels(tag: str, attrs: str) -> str:
    if tag in VALUE_TAGS:
        attrs = attrs.replace("v-model=", "v-model:value=")
        attrs = attrs.replace(":model-value=", ":value=")
    elif tag in CHECKED_TAGS:
        attrs = attrs.replace("v-model=", "v-model:checked=")
        attrs = attrs.replace(":model-value=", ":checked=")
    elif tag in OPEN_TAGS:
        attrs = attrs.replace("v-model=", "v-model:open=")
        attrs = attrs.replace(":model-value=", ":open=")
    elif tag in ACTIVE_TAGS:
        attrs = attrs.replace("v-model=", "v-model:activeKey=")
        attrs = attrs.replace(":model-value=", ":activeKey=")
    elif tag == "UiAutoComplete":
        pass
    elif tag == "UiTree":
        pass
    return attrs


def dialog_has_footer(src: str, start: int) -> bool:
    end = src.find("</el-dialog>", start)
    if end < 0:
        end = src.find("</a-modal>", start)
    chunk = src[start:end] if end > 0 else src[start : start + 2500]
    return "#footer" in chunk


def transform_open_tag(tag: str, attrs: str, self_close: bool, src: str, idx: int) -> str:
    orig = tag
    if orig == "el-button":
        attrs = transform_button(attrs)
    elif orig == "el-select":
        multiple = has_bool(attrs, "multiple")
        allow_create = has_bool(attrs, "allow-create")
        filterable = has_bool(attrs, "filterable")
        attrs = drop_bool(attrs, "multiple")
        attrs = drop_bool(attrs, "allow-create")
        attrs = drop_bool(attrs, "filterable")
        attrs = drop_bool(attrs, "default-first-option")
        attrs = drop_bool(attrs, "teleported")
        attrs = drop_bool(attrs, "collapse-tags")
        attrs = drop_bool(attrs, "collapse-tags-tooltip")
        attrs = attrs.replace("clearable", "allow-clear")
        attrs = re.sub(r'\s+popper-class=', " popup-class-name=", attrs)
        if allow_create:
            attrs += ' mode="tags"'
        elif multiple:
            attrs += ' mode="multiple"'
        if filterable:
            attrs += " show-search"
    elif orig == "el-dialog":
        attrs = transform_dialog(attrs, dialog_has_footer(src, idx))
    elif orig == "el-form":
        attrs = attrs.replace('label-position="top"', 'layout="vertical"')
        attrs = attrs.replace(":label-position=", ":layout=")
        attrs = attrs.replace('label-position="left"', 'layout="horizontal"')
    elif orig == "el-tab-pane":
        attrs = transform_tab_pane(attrs)
    elif orig == "el-alert":
        attrs = transform_alert(attrs)
    elif orig == "el-tag":
        attrs = transform_tag(attrs)
    elif orig == "el-upload":
        was_drag = has_bool(attrs, "drag")
        attrs = transform_upload(attrs)
        attrs = drop_bool(attrs, "drag")
        if was_drag:
            attrs += ' class="ant-upload-drag-host"'
    elif orig == "el-tree":
        attrs = transform_tree_attrs(attrs)
    elif orig == "el-tooltip":
        attrs = attrs.replace(":content=", ":title=")
        attrs = attrs.replace(" content=", " title=")
        attrs = drop_attr(attrs, "show-after")
    elif orig == "el-checkbox":
        attrs = attrs.replace(":label=", ":value=")
        attrs = attrs.replace(" label=", " value=")
        attrs = attrs.replace("indeterminate=", "indeterminate=")
        attrs = attrs.replace(":indeterminate=", ":indeterminate=")
    elif orig == "el-steps":
        attrs = attrs.replace(":active=", ":current=")
        attrs = attrs.replace(" active=", " current=")
        attrs = drop_attr(attrs, "finish-status")
        attrs = drop_attr(attrs, "align-center")
    elif orig == "el-progress":
        attrs = attrs.replace(":percentage=", ":percent=")
        attrs = attrs.replace("percentage=", "percent=")
    elif orig == "el-timeline-item":
        attrs = drop_attr(attrs, "type")
        attrs = attrs.replace(":timestamp=", ":label=")
        attrs = attrs.replace(" timestamp=", " label=")
        attrs = drop_attr(attrs, "placement")
    elif orig == "el-autocomplete":
        attrs = re.sub(r'\s+popper-class=', " popper-class=", attrs)
        attrs = attrs.replace("clearable", "clearable")
    elif orig == "el-input-number":
        attrs = drop_attr(attrs, "controls-position")
    elif orig == "el-switch":
        attrs = attrs.replace("active-text=", "checked-children=")
        attrs = attrs.replace("inactive-text=", "un-checked-children=")
        attrs = attrs.replace(":active-text=", ":checked-children=")
        attrs = attrs.replace(":inactive-text=", ":un-checked-children=")

    new_tag = TAG_MAP.get(orig, orig)
    if orig == "el-upload" and "ant-upload-drag-host" in attrs:
        new_tag = "a-upload-dragger"
        attrs = attrs.replace("ant-upload-drag-host", "").replace('class=""', "")
    attrs = rewrite_vmodels(new_tag, attrs)
    attrs = re.sub(r" {2,}", " ", attrs)
    if self_close:
        return f"<{new_tag}{attrs} />"
    return f"<{new_tag}{attrs}>"


def transform_template(src: str) -> str:
    # el-input first (special tags)
    def input_sub(m):
        return transform_input_tag(m.group(0))

    src = re.sub(r"<el-input\b[^>]*?/?>", input_sub, src)

    def option_sub(m):
        return transform_option(m.group(0))

    src = re.sub(r"<el-option\b[^>]*?/?>", option_sub, src)

    def open_sub(m):
        tag = m.group(1)
        attrs = m.group(2) or ""
        self_close = bool(m.group(3))
        return transform_open_tag(tag, attrs, self_close, src, m.start())

    tags = "|".join(re.escape(t) for t in TAG_MAP)
    src = re.sub(rf"<({tags})\b([^>]*?)(/)?>", open_sub, src)

    for old, new in TAG_MAP.items():
        if old == "el-option":
            src = src.replace(f"</{old}>", f"</{new}>")
            continue
        if old == "el-input":
            continue
        src = src.replace(f"</{old}>", f"</{new}>")
        if old == "el-upload":
            src = src.replace("</el-upload>", "</a-upload>")
    src = src.replace("</el-input>", "</a-input>")
    src = src.replace("</el-option>", "</a-select-option>")
    src = src.replace('<template #title>', "<template #message>")
    src = src.replace('el-upload__text', "ant-upload-text")
    src = src.replace("el-upload__tip", "ant-upload-hint")
    src = patch_vmodels(src)
    src = patch_textarea_closes(src)
    src = patch_dragger_closes(src)
    return src


def patch_vmodels(src: str) -> str:
    def sub(tag_set, old, new):
        nonlocal src
        tags = "|".join(tag_set)

        def repl(m):
            return m.group(0).replace(old, new)

        src = re.sub(rf"<(?:{tags})\b[^>]*>", repl, src)

    sub(VALUE_TAGS, "v-model=", "v-model:value=")
    sub(VALUE_TAGS, ":model-value=", ":value=")
    sub(CHECKED_TAGS, "v-model=", "v-model:checked=")
    sub(CHECKED_TAGS, ":model-value=", ":checked=")
    sub(OPEN_TAGS, "v-model=", "v-model:open=")
    sub(ACTIVE_TAGS, "v-model=", "v-model:activeKey=")
    return src


def patch_textarea_closes(src: str) -> str:
    out = []
    i = 0
    open_textarea = 0
    while i < len(src):
        if src.startswith("<a-textarea", i):
            open_textarea += 1
            out.append("<a-textarea")
            i += len("<a-textarea")
            continue
        if src.startswith("</a-input>", i) and open_textarea:
            open_textarea -= 1
            out.append("</a-textarea>")
            i += len("</a-input>")
            continue
        if src.startswith("</a-textarea>", i) and open_textarea:
            open_textarea -= 1
            out.append("</a-textarea>")
            i += len("</a-textarea>")
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def patch_dragger_closes(src: str) -> str:
    out = []
    i = 0
    open_d = 0
    while i < len(src):
        if src.startswith("<a-upload-dragger", i):
            open_d += 1
            out.append("<a-upload-dragger")
            i += len("<a-upload-dragger")
            continue
        if src.startswith("</a-upload>", i) and open_d:
            open_d -= 1
            out.append("</a-upload-dragger>")
            i += len("</a-upload>")
            continue
        if src.startswith("</a-upload-dragger>", i) and open_d:
            open_d -= 1
            out.append("</a-upload-dragger>")
            i += len("</a-upload-dragger>")
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def transform_style(src: str) -> str:
    repl = {
        ".el-tabs__header": ".ant-tabs-nav",
        ".el-tabs__nav-wrap": ".ant-tabs-nav-wrap",
        ".el-tabs__nav-scroll": ".ant-tabs-nav-wrap",
        ".el-tabs__nav": ".ant-tabs-nav-list",
        ".el-tabs__item": ".ant-tabs-tab",
        ".el-tabs__item.is-active": ".ant-tabs-tab-active",
        ".el-tabs__active-bar": ".ant-tabs-ink-bar",
        ".el-tabs__content": ".ant-tabs-content",
        ".el-button--primary": ".ant-btn-primary",
        ".el-button": ".ant-btn",
        ".el-input__wrapper": ".ant-input-affix-wrapper, .ant-input",
        ".el-select__wrapper": ".ant-select-selector",
        ".el-input-number": ".ant-input-number",
        ".el-textarea__inner": ".ant-input",
        ".el-form-item__content": ".ant-form-item-control",
        ".el-checkbox__label": ".ant-checkbox + span",
        ".el-checkbox__input": ".ant-checkbox",
        ".el-checkbox__inner": ".ant-checkbox-inner",
        ".el-checkbox": ".ant-checkbox-wrapper",
        ".el-tree-node__content": ".ant-tree-node-content-wrapper",
        ".el-tree-node.is-current > .el-tree-node__content": ".ant-tree-node-selected",
        ".el-tree-node__expand-icon": ".ant-tree-switcher",
        ".el-tree-node__expand-icon.is-leaf": ".ant-tree-switcher-noop",
        ".el-tree-node": ".ant-tree-treenode",
        ".el-dialog": ".ant-modal",
        ".el-dialog__body": ".ant-modal-body",
        ".el-upload-dragger": ".ant-upload-drag",
        ".el-radio-button": ".ant-radio-button-wrapper",
        ".el-radio-button__inner": ".ant-radio-button-wrapper",
        ".el-popper": ".ant-select-dropdown",
    }
    for a, b in repl.items():
        src = src.replace(a, b)
    return src


def transform_script(src: str, rel_ui: str) -> str:
    src = src.replace('from "element-plus"', f'from "{rel_ui}"')
    src = src.replace("from 'element-plus'", f"from '{rel_ui}'")
    return src


def ui_path(path: Path) -> str:
    if path.parent.name in {"pages", "components"}:
        return "../ui.js"
    return "./ui.js"


def process_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "el-" not in text and "element-plus" not in text and "ElMessage" not in text:
        return
    head, tail = split_sfc(text)
    # only first template + maybe more before script
    head = transform_template(head)
    # style in tail
    parts = re.split(r"(<style\b)", tail, maxsplit=1)
    script = parts[0]
    style = ""
    if len(parts) > 1:
        style = parts[1] + (parts[2] if len(parts) > 2 else "")
        style = transform_style(style)
    script = transform_script(script, ui_path(path))
    # leftover closing tags / inputs inside script? skip
    out = head + script + style
    # fix a-textarea closing
    out = out.replace("</a-input>", "</a-input>")
    # If we opened a-textarea, close should be a-textarea. Heuristic:
    # original </el-input> after type=textarea already became </a-input>.
    # Fix pairs by a second pass on template only — skip for now; grep later.
    path.write_text(out, encoding="utf-8")
    print("updated", path.relative_to(ROOT.parent))


def main() -> None:
    for path in sorted(ROOT.rglob("*.vue")):
        process_file(path)
    for path in sorted(ROOT.rglob("*.js")):
        if path.name in {"ui.js"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "element-plus" not in text:
            continue
        path.write_text(transform_script(text, ui_path(path)), encoding="utf-8")
        print("updated", path.relative_to(ROOT.parent))


if __name__ == "__main__":
    main()
