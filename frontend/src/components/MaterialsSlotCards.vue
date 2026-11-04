<template>
  <div class="slot-cards" :class="[layoutClass, { compact }]">
    <template v-if="isRail">
      <button
        v-if="editable && canAdd"
        type="button"
        class="slot-icon-add"
        :disabled="disabled"
        title="添加槽位"
        @click="$emit('add')"
      >
        +
      </button>
      <div class="slot-icon-scroll">
        <div
          v-for="(row, idx) in rows"
          :key="row.no"
          class="slot-icon-wrap"
        >
          <button
            type="button"
            class="slot-icon-chip"
            :class="{
              filled: !!row.file_name,
              parsed: row.parse_status === 'parsed',
              error: row.parse_status === 'error',
            }"
            :title="`${cardTitle(row)}（双击编辑）`"
            :disabled="disabled"
            @dblclick.prevent="openEditor(row)"
          >
            <span class="folder-ico" aria-hidden="true">
              <svg viewBox="0 0 48 40" class="folder-svg">
                <path
                  d="M4 12.5A3.5 3.5 0 0 1 7.5 9h9.2l2.4 2.6H40.5A3.5 3.5 0 0 1 44 15.1V32a3.5 3.5 0 0 1-3.5 3.5h-33A3.5 3.5 0 0 1 4 32z"
                  fill="currentColor"
                  opacity="0.14"
                />
                <path
                  d="M4 14h40v18.5A3.5 3.5 0 0 1 40.5 36h-33A3.5 3.5 0 0 1 4 32.5z"
                  fill="currentColor"
                  opacity="0.22"
                />
                <path
                  d="M7.5 9h9.2l2.4 2.6H40.5A2.5 2.5 0 0 1 43 14.1V15H5v-2.5A2.5 2.5 0 0 1 7.5 9z"
                  fill="currentColor"
                  opacity="0.35"
                />
              </svg>
              <span class="folder-no">{{ row.no }}</span>
            </span>
            <span class="slot-chip-meta">
              <span class="slot-chip-name">{{ row.name || `槽位 ${row.no}` }}</span>
              <span class="slot-chip-status" :class="row.parse_status || (row.file_name ? 'pending' : 'empty')">
                {{ railStatusLabel(row) }}
              </span>
            </span>
          </button>
          <div v-if="editable && canReorder && rows.length > 1" class="slot-reorder">
            <button
              type="button"
              class="slot-reorder-btn"
              title="左移"
              :disabled="disabled || idx <= 0"
              @click.stop="$emit('move', row.no, -1)"
            >
              ‹
            </button>
            <button
              type="button"
              class="slot-reorder-btn"
              title="右移"
              :disabled="disabled || idx >= rows.length - 1"
              @click.stop="$emit('move', row.no, 1)"
            >
              ›
            </button>
          </div>
        </div>
      </div>
    </template>

    <template v-else>
      <article
        v-for="row in rows"
        :key="row.no"
        class="slot-folder-card"
        :class="{ filled: !!row.file_name, editable }"
        :title="cardTitle(row)"
      >
        <div class="folder-ico" aria-hidden="true">
          <svg viewBox="0 0 48 40" class="folder-svg">
            <path
              d="M4 12.5A3.5 3.5 0 0 1 7.5 9h9.2l2.4 2.6H40.5A3.5 3.5 0 0 1 44 15.1V32a3.5 3.5 0 0 1-3.5 3.5h-33A3.5 3.5 0 0 1 4 32z"
              fill="currentColor"
              opacity="0.14"
            />
            <path
              d="M4 14h40v18.5A3.5 3.5 0 0 1 40.5 36h-33A3.5 3.5 0 0 1 4 32.5z"
              fill="currentColor"
              opacity="0.22"
            />
            <path
              d="M7.5 9h9.2l2.4 2.6H40.5A2.5 2.5 0 0 1 43 14.1V15H5v-2.5A2.5 2.5 0 0 1 7.5 9z"
              fill="currentColor"
              opacity="0.35"
            />
          </svg>
          <span class="folder-no">{{ row.no }}</span>
        </div>
        <div class="folder-body">
          <template v-if="editable">
            <input
              class="folder-name-input"
              :value="row.name"
              placeholder="槽位名称"
              @input="emitUpdate(row.no, { name: $event.target.value })"
            />
            <textarea
              v-if="showQuery"
              class="folder-query-input"
              :value="row.query"
              rows="2"
              placeholder="检索词（写作时在该槽文件中检索）"
              @input="emitUpdate(row.no, { query: $event.target.value })"
            />
          </template>
          <template v-else>
            <strong class="folder-title">{{ row.name || `槽位 ${row.no}` }}</strong>
            <p v-if="showQuery && row.query" class="folder-query">检索：{{ row.query }}</p>
          </template>
          <p class="folder-file" :class="{ empty: !row.file_name }">
            {{ row.file_name || "未上传" }}
          </p>
          <p
            v-if="canParse && row.file_name"
            class="folder-parse"
            :class="row.parse_status || 'pending'"
          >
            {{ parseStatusLabel(row.parse_status) }}
            <span v-if="row.parse_error" class="folder-parse-err"> · {{ row.parse_error }}</span>
          </p>
        </div>
        <div v-if="showActions" class="folder-actions">
          <label
            v-if="canUpload"
            class="folder-btn primary"
            :class="{ disabled: disabled }"
            :title="row.file_name ? '更换文件' : '上传文件'"
          >
            {{ row.file_name ? "更换" : "上传" }}
            <input
              type="file"
              hidden
              :disabled="disabled"
              @change="(e) => $emit('upload', row.no, e)"
            />
          </label>
          <button
            v-if="canParse && row.file_name"
            type="button"
            class="folder-btn"
            :disabled="disabled"
            :title="row.parse_status === 'parsed' ? '重新解析' : '解析'"
            @click="$emit('parse', row.no)"
          >
            {{ row.parse_status === "parsed" ? "重新解析" : "解析" }}
          </button>
          <button
            v-if="row.file_name && canClear"
            type="button"
            class="folder-btn"
            :disabled="disabled"
            title="清除"
            @click="$emit('clear', row.no)"
          >
            清除
          </button>
        <button
          v-if="editable && canReorder"
          type="button"
          class="folder-btn"
          :disabled="disabled || rows.length <= 1 || rowIndex(row.no) <= 0"
          title="左移"
          @click="$emit('move', row.no, -1)"
        >
          左移
        </button>
        <button
          v-if="editable && canReorder"
          type="button"
          class="folder-btn"
          :disabled="disabled || rows.length <= 1 || rowIndex(row.no) >= rows.length - 1"
          title="右移"
          @click="$emit('move', row.no, 1)"
        >
          右移
        </button>
        <button
          v-if="editable && canRemove"
          type="button"
          class="folder-btn danger"
          :disabled="disabled || rows.length <= 1"
          title="删除槽位"
          @click="$emit('remove', row.no)"
        >
          删除
        </button>
      </div>
      </article>
      <button
        v-if="editable && canAdd"
        type="button"
        class="slot-folder-add"
        :disabled="disabled"
        title="添加槽位"
        @click="$emit('add')"
      >
        + 添加槽位
      </button>
    </template>
  </div>

  <el-dialog
    v-if="isRail"
    v-model="editorVisible"
    :title="editorTitle"
    width="420px"
    append-to-body
    destroy-on-close
    class="slot-editor-dialog"
    @closed="editingNo = null"
  >
    <div v-if="editingRow" class="slot-editor">
      <label class="slot-editor-field">
        <span>槽位名称</span>
        <input
          class="folder-name-input"
          :value="editingRow.name"
          placeholder="槽位名称"
          :disabled="disabled || !editable"
          @input="emitUpdate(editingRow.no, { name: $event.target.value })"
        />
      </label>
      <p class="slot-editor-meta">
        <span>文件：</span>
        <strong :class="{ empty: !editingRow.file_name }">
          {{ editingRow.file_name || "未上传" }}
        </strong>
      </p>
      <p
        v-if="canParse && editingRow.file_name"
        class="folder-parse"
        :class="editingRow.parse_status || 'pending'"
      >
        解析：{{ parseStatusLabel(editingRow.parse_status) || "—" }}
        <span v-if="editingRow.parse_error" class="folder-parse-err">
          · {{ editingRow.parse_error }}
        </span>
      </p>
      <div v-if="showActions" class="folder-actions slot-editor-actions">
        <label
          v-if="canUpload"
          class="folder-btn primary"
          :class="{ disabled: disabled }"
        >
          {{ editingRow.file_name ? "更换文件" : "上传文件" }}
          <input
            type="file"
            hidden
            :disabled="disabled"
            @change="onEditorUpload"
          />
        </label>
        <button
          v-if="canParse && editingRow.file_name"
          type="button"
          class="folder-btn"
          :disabled="disabled"
          @click="$emit('parse', editingRow.no)"
        >
          {{ editingRow.parse_status === "parsed" ? "重新解析" : "解析" }}
        </button>
        <button
          v-if="editingRow.file_name && canClear"
          type="button"
          class="folder-btn"
          :disabled="disabled"
          @click="$emit('clear', editingRow.no)"
        >
          清除文件
        </button>
        <button
          v-if="editable && canReorder"
          type="button"
          class="folder-btn"
          :disabled="disabled || rows.length <= 1 || rowIndex(editingRow.no) <= 0"
          @click="$emit('move', editingRow.no, -1)"
        >
          左移
        </button>
        <button
          v-if="editable && canReorder"
          type="button"
          class="folder-btn"
          :disabled="disabled || rows.length <= 1 || rowIndex(editingRow.no) >= rows.length - 1"
          @click="$emit('move', editingRow.no, 1)"
        >
          右移
        </button>
        <button
          v-if="editable && canRemove"
          type="button"
          class="folder-btn danger"
          :disabled="disabled || rows.length <= 1"
          @click="removeFromEditor"
        >
          删除槽位
        </button>
      </div>
    </div>
    <template #footer>
      <button type="button" class="folder-btn" @click="editorVisible = false">关闭</button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  rows: { type: Array, default: () => [] },
  editable: { type: Boolean, default: false },
  showActions: { type: Boolean, default: true },
  canUpload: { type: Boolean, default: false },
  canClear: { type: Boolean, default: false },
  canAdd: { type: Boolean, default: false },
  canRemove: { type: Boolean, default: false },
  canReorder: { type: Boolean, default: false },
  /** 资料包槽位不配检索词；要素引用槽位才配 */
  showQuery: { type: Boolean, default: false },
  canParse: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  /** grid=大卡片；rail=图标横滑，双击弹窗编辑 */
  layout: { type: String, default: "grid" },
});

const emit = defineEmits(["upload", "clear", "add", "remove", "update", "parse", "move"]);

function rowIndex(no) {
  const n = Number(no) || 0;
  return (props.rows || []).findIndex((r) => Number(r?.no) === n);
}

const isRail = computed(() => String(props.layout || "") === "rail");
const layoutClass = computed(() => (isRail.value ? "is-rail" : "is-grid"));

const editorVisible = ref(false);
const editingNo = ref(null);

const editingRow = computed(() => {
  const no = editingNo.value;
  if (no == null) return null;
  return (props.rows || []).find((r) => Number(r?.no) === Number(no)) || null;
});

const editorTitle = computed(() => {
  const row = editingRow.value;
  if (!row) return "编辑槽位";
  const name = String(row.name || "").trim();
  return name ? `编辑槽位 · ${name}` : `编辑槽位 · ${row.no}`;
});

watch(
  () => props.rows,
  (list) => {
    if (!editorVisible.value || editingNo.value == null) return;
    const still = (list || []).some((r) => Number(r?.no) === Number(editingNo.value));
    if (!still) {
      editorVisible.value = false;
      editingNo.value = null;
    }
  },
  { deep: true }
);

function openEditor(row) {
  if (props.disabled) return;
  editingNo.value = Number(row?.no) || null;
  editorVisible.value = true;
}

function onEditorUpload(e) {
  const row = editingRow.value;
  if (!row) return;
  emit("upload", row.no, e);
}

function removeFromEditor() {
  const row = editingRow.value;
  if (!row) return;
  emit("remove", row.no);
  editorVisible.value = false;
  editingNo.value = null;
}

function emitUpdate(no, patch) {
  emit("update", no, patch);
}

function parseStatusLabel(status) {
  const s = String(status || "").trim();
  if (s === "parsed") return "已解析";
  if (s === "error") return "失败";
  if (s === "pending") return "待解析";
  return "";
}

function railStatusLabel(row) {
  if (!row?.file_name) return "未上传";
  return parseStatusLabel(row.parse_status) || "待解析";
}

function cardTitle(row) {
  const name = String(row?.name || `槽位 ${row?.no || ""}`).trim();
  const file = String(row?.file_name || "").trim() || "未上传";
  const parse = parseStatusLabel(row?.parse_status);
  return [name, file, parse].filter(Boolean).join(" · ");
}
</script>

<style scoped>
.slot-cards.is-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  width: 100%;
}
.slot-cards.is-grid.compact {
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.slot-cards.is-rail {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  width: 100%;
  max-width: 100%;
  overflow: hidden;
  padding: 2px 0 4px;
}
.slot-icon-scroll {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
}
.slot-icon-wrap {
  flex: 0 0 auto;
  display: inline-flex;
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
}
.slot-reorder {
  display: flex;
  justify-content: center;
  gap: 2px;
}
.slot-reorder-btn {
  flex: 1 1 auto;
  min-width: 0;
  height: 16px;
  padding: 0;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  background: #fff;
  color: #64748b;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
}
.slot-reorder-btn:hover:not(:disabled) {
  border-color: #93c5fd;
  color: #2563eb;
  background: #eff6ff;
}
.slot-reorder-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.slot-icon-chip {
  flex: 0 0 auto;
  position: relative;
  min-width: 92px;
  max-width: 140px;
  height: 40px;
  padding: 4px 8px 4px 6px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  color: #3b82f6;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  text-align: left;
}
.slot-icon-chip:hover:not(:disabled) {
  border-color: #93c5fd;
  background: #eff6ff;
}
.slot-icon-chip.filled,
.slot-icon-chip.parsed {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #2563eb;
}
.slot-icon-chip.error {
  border-color: #fecaca;
  background: #fef2f2;
  color: #dc2626;
}
.slot-icon-chip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.slot-icon-chip .folder-ico {
  flex: 0 0 auto;
  width: 22px;
  height: 18px;
}
.slot-icon-chip .folder-svg {
  width: 22px;
  height: 18px;
  display: block;
}
.slot-icon-chip .folder-no {
  right: -6px;
  bottom: -5px;
  min-width: 14px;
  height: 14px;
  font-size: 10px;
  line-height: 14px;
  padding: 0 3px;
}
.slot-chip-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 1.15;
}
.slot-chip-name {
  font-size: 11px;
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 96px;
}
.slot-chip-status {
  font-size: 10px;
  color: #94a3b8;
}
.slot-chip-status.pending {
  color: #d97706;
}
.slot-chip-status.parsed {
  color: #16a34a;
}
.slot-chip-status.error {
  color: #dc2626;
}
.slot-chip-status.empty {
  color: #94a3b8;
}
.slot-icon-add {
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  color: #3b82f6;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  z-index: 1;
}
.slot-icon-add:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.slot-folder-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 150px;
  padding: 12px;
  border-radius: 14px;
  border: 1px solid #e2e8f0;
  background: linear-gradient(180deg, #f8fafc 0%, #fff 48%);
  box-shadow: 0 1px 0 rgba(15, 23, 42, 0.04);
}
.slot-folder-card.filled {
  border-color: #bfdbfe;
  background: linear-gradient(180deg, #eff6ff 0%, #fff 55%);
}
.folder-ico {
  position: relative;
  width: 44px;
  height: 36px;
  color: #3b82f6;
}
.folder-svg {
  width: 40px;
  height: 34px;
  display: block;
}
.slot-folder-card.filled .folder-ico {
  color: #2563eb;
}
.folder-no {
  position: absolute;
  right: -2px;
  bottom: -2px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 999px;
  background: #1e40af;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}
.folder-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.folder-title {
  font-size: 13px;
  color: #0f172a;
  word-break: break-word;
}
.folder-query {
  margin: 0;
  font-size: 11px;
  color: #64748b;
  line-height: 1.35;
}
.folder-file {
  margin: 4px 0 0;
  font-size: 12px;
  color: #2563eb;
  word-break: break-all;
}
.folder-file.empty {
  color: #94a3b8;
}
.folder-parse {
  margin: 2px 0 0;
  font-size: 11px;
  color: #94a3b8;
}
.folder-parse.parsed {
  color: #16a34a;
}
.folder-parse.error {
  color: #dc2626;
}
.folder-parse-err {
  color: #dc2626;
}
.folder-name-input,
.folder-query-input {
  width: 100%;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 12px;
  background: #fff;
  resize: vertical;
  box-sizing: border-box;
}
.folder-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
}
.folder-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #dbe3ee;
  background: #fff;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  color: #334155;
}
.folder-btn.primary {
  border-color: #93c5fd;
  color: #1d4ed8;
  background: #eff6ff;
}
.folder-btn.danger {
  border-color: #fecaca;
  color: #b91c1c;
}
.folder-btn.disabled,
.folder-btn:disabled {
  opacity: 0.5;
  pointer-events: none;
}
.slot-folder-add {
  min-height: 150px;
  border: 1px dashed #cbd5e1;
  border-radius: 14px;
  background: #f8fafc;
  color: #3b82f6;
  font-size: 13px;
  cursor: pointer;
}
.slot-folder-add:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.slot-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.slot-editor-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: #64748b;
}
.slot-editor-meta {
  margin: 0;
  font-size: 12px;
  color: #64748b;
}
.slot-editor-meta strong {
  color: #2563eb;
  font-weight: 600;
  word-break: break-all;
}
.slot-editor-meta strong.empty {
  color: #94a3b8;
  font-weight: 500;
}
.slot-editor-actions {
  margin-top: 4px;
}
</style>
