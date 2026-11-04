<template>
  <div class="chapter-composer" :class="{ 'is-readonly': readonly }">
    <div v-if="!readonly" class="fmt-bar">
      <button type="button" class="fmt" title="加粗" @mousedown.prevent="fmt('bold')"><b>B</b></button>
      <button type="button" class="fmt" title="斜体" @mousedown.prevent="fmt('italic')"><i>I</i></button>
      <button type="button" class="fmt" title="下划线" @mousedown.prevent="fmt('underline')"><u>U</u></button>
      <button type="button" class="fmt" title="无序列表" @mousedown.prevent="fmt('insertUnorderedList')">• 列表</button>
      <button type="button" class="fmt" title="有序列表" @mousedown.prevent="fmt('insertOrderedList')">1. 列表</button>
      <button type="button" class="fmt" title="清除格式" @mousedown.prevent="fmt('removeFormat')">清除</button>
    </div>
    <div
      ref="root"
      class="rich-editor chapter-html-editor"
      :class="{ 'is-readonly': readonly }"
      :contenteditable="readonly ? 'false' : 'true'"
      :data-placeholder="
        readonly
          ? '写作进行中，完成后可查看与修改正文'
          : '可直接修改正文，改完点「保存正文」'
      "
      @input="onInput"
      @paste="onPaste"
    />
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from "vue";

import { clipboardToHtml, insertHtmlAtCursor, sanitizeHtml, toDisplayHtml } from "../chatHtml";

const props = defineProps({
  modelValue: { type: String, default: "" },
  /** 章节草稿 HTML；外部更新且本地为空时回填 */
  sourceHtml: { type: String, default: "" },
  /** 写作/分析中只读展示，禁止编辑 */
  readonly: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue"]);

const root = ref(null);
let lastEmitted = "";

function displayOf(raw) {
  const text = String(raw || "");
  if (!text.trim()) return "";
  return sanitizeHtml(toDisplayHtml(text) || text) || "";
}

function writeDom(html) {
  if (!root.value) return;
  root.value.innerHTML = html || "";
}

function readDom() {
  return sanitizeHtml(root.value?.innerHTML || "") || "";
}

function emitValue(html) {
  lastEmitted = html;
  emit("update:modelValue", html);
}

function hydrate(force = false) {
  const fromModel = displayOf(props.modelValue);
  const fromSource = displayOf(props.sourceHtml);
  const preferred = fromModel || fromSource;
  if (!root.value) return;
  const currentPlain = (root.value.innerText || "").replace(/\u00a0/g, " ").trim();
  if (force || !currentPlain) {
    writeDom(preferred);
    if (preferred && preferred !== lastEmitted) emitValue(preferred);
  }
}

onMounted(() => {
  nextTick(() => hydrate(true));
});

watch(
  () => [props.modelValue, props.sourceHtml, props.readonly],
  () => {
    if (!root.value) return;
    // 写作过程/完成后强制刷新展示，避免停在上一章正文
    if (props.readonly) {
      hydrate(true);
      return;
    }
    if (document.activeElement === root.value) return;
    const incoming = displayOf(props.modelValue) || displayOf(props.sourceHtml);
    if (!incoming) return;
    if (incoming === lastEmitted) return;
    const current = readDom();
    if (!current.trim()) {
      writeDom(incoming);
      lastEmitted = incoming;
    }
  }
);

function onInput() {
  if (props.readonly) return;
  emitValue(readDom());
}

function onPaste(event) {
  if (props.readonly) {
    event.preventDefault();
    return;
  }
  const html = clipboardToHtml(event);
  if (!html) return;
  event.preventDefault();
  if (root.value) insertHtmlAtCursor(root.value, html);
  emitValue(readDom());
}

function fmt(command) {
  root.value?.focus();
  document.execCommand(command, false);
  emitValue(readDom());
}
</script>

<style scoped>
.chapter-composer {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fmt-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.fmt {
  border: 1px solid var(--ay-line, #e2e8f0);
  background: #f8fafc;
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
  color: var(--ay-ink, #0f172a);
  cursor: pointer;
  line-height: 1.2;
}
.fmt:hover {
  border-color: #c5d0f5;
  color: var(--ay-blue, #4e6ef2);
}
.chapter-html-editor {
  min-height: 160px;
  max-height: 360px;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid var(--ay-line, #e2e8f0);
  border-radius: 12px;
  background: #fff;
  line-height: 1.55;
  font-size: 13px;
  outline: none;
}
.chapter-html-editor.is-readonly {
  background: #f8fafc;
  cursor: default;
}
.chapter-html-editor:empty::before {
  content: attr(data-placeholder);
  color: #94a3b8;
  pointer-events: none;
}
.chapter-html-editor:focus {
  border-color: #4e6ef2;
  box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.12);
}
.chapter-html-editor :deep(p) {
  margin: 0 0 0.4em;
}
.chapter-html-editor :deep(p:last-child) {
  margin-bottom: 0;
}
.chapter-html-editor :deep(ul),
.chapter-html-editor :deep(ol) {
  margin: 0.3em 0;
  padding-left: 1.2em;
}
.chapter-html-editor :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.4em 0;
}
.chapter-html-editor :deep(th),
.chapter-html-editor :deep(td) {
  border: 1px solid #cbd5e1;
  padding: 4px 6px;
}
</style>
