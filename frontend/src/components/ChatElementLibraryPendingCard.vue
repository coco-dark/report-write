<template>
  <div class="card element-library-pending-card">
    <div class="card-title">{{ card.title || "收入要素库？" }}</div>
    <p class="card-hint">
      {{
        localRows.some((r) => r.metaFromCreate)
          ? "勾选要收入的要素。名称+分类+标签与库中完全相同时会提示修改；分类/标签/作用已带入时可直接收入。不需要的要素可点「删除」从文档移除。"
          : "勾选要收入的要素。名称+分类+标签与库中完全相同会判重复。不需要的要素可点「删除」从文档移除；「暂不收录」仅移出待办并保留章节配置。"
      }}
    </p>
    <div v-if="localRows.length" class="pending-bulk">
      <label class="pending-bulk-all">
        <input
          type="checkbox"
          :checked="allChecked"
          :indeterminate.prop="someChecked && !allChecked"
          :disabled="busy"
          @change="toggleSelectAll($event.target.checked)"
        />
        <span>全选</span>
      </label>
      <span v-if="checkedCount" class="pending-bulk-count">已选 {{ checkedCount }}</span>
    </div>
    <p v-else class="card-hint">没有待收录的要素（已入库的已自动隐藏）。</p>
    <div v-for="row in localRows" :key="row.pending_id" class="pending-row">
      <div class="pending-head">
        <input
          type="checkbox"
          class="pending-check"
          :checked="row.selected"
          :disabled="busy"
          @change="row.selected = $event.target.checked"
        />
        <el-tag size="small">{{ elementKindLabel(row.element?.kind) }}</el-tag>
        <strong>{{ row.element?.name || row.element?.id }}</strong>
        <span class="muted">{{ row.chapter_title || row.chapter_key }}</span>
      </div>
      <div v-if="row.metaFromCreate" class="meta-summary">
        <el-tag v-if="row.category" size="small" type="warning" effect="plain">{{ row.category }}</el-tag>
        <el-tag v-for="t in row.tags" :key="t" size="small" effect="plain">{{ t }}</el-tag>
        <span v-if="row.purpose" class="meta-purpose">{{ row.purpose }}</span>
        <button type="button" class="meta-edit-link" @click="row.showEdit = !row.showEdit">
          {{ row.showEdit ? "收起修改" : "改一下" }}
        </button>
        <button
          type="button"
          class="meta-delete-link"
          :disabled="busy"
          @click="deleteRow(row)"
        >
          删除
        </button>
      </div>
      <el-row v-if="!row.metaFromCreate || row.showEdit" :gutter="8" class="pending-fields">
        <el-col :span="6">
          <el-input
            v-model="row.element.name"
            size="small"
            placeholder="名称"
            @change="() => onIdentityChange(row)"
          />
        </el-col>
        <el-col :span="6">
          <el-autocomplete
            v-model="row.category"
            size="small"
            :fetch-suggestions="queryCategory"
            clearable
            placeholder="分类（可选）"
            style="width: 100%"
            @change="() => onCategoryChange(row)"
            @select="() => onCategoryChange(row)"
          />
          <p v-if="row.categoryHint" class="field-tip">{{ row.categoryHint }}</p>
        </el-col>
        <el-col :span="6">
          <el-select
            v-model="row.tags"
            size="small"
            multiple
            filterable
            allow-create
            default-first-option
            collapse-tags
            collapse-tags-tooltip
            placeholder="标签（可选）"
            style="width: 100%"
            @change="() => onTagsChange(row)"
          >
            <el-option v-for="t in libraryTags" :key="t" :label="t" :value="t" />
          </el-select>
          <p v-if="row.tagsHint" class="field-tip">{{ row.tagsHint }}</p>
        </el-col>
        <el-col :span="6">
          <el-input v-model="row.purpose" size="small" placeholder="作用说明（可选）" />
        </el-col>
      </el-row>
      <p v-if="row.identityHint" class="field-tip warn-tip">{{ row.identityHint }}</p>
      <div v-if="!row.metaFromCreate" class="pending-row-actions">
        <button
          type="button"
          class="meta-delete-link"
          :disabled="busy"
          @click="deleteRow(row)"
        >
          删除
        </button>
      </div>
    </div>
    <div class="pending-actions">
      <el-button type="primary" round :loading="busy" :disabled="!checkedCount" @click="importSelected">
        收入我的要素库{{ checkedCount ? `（${checkedCount}）` : "" }}
      </el-button>
      <el-button round :loading="busy" :disabled="!checkedCount" @click="dismissSelected">
        暂不收录{{ checkedCount ? `（${checkedCount}）` : "" }}
      </el-button>
    </div>

    <el-dialog
      v-model="conflictDialog.visible"
      title="要素重复，请修改名称 / 分类 / 标签"
      width="520px"
      destroy-on-close
      :close-on-click-modal="false"
    >
      <p class="dup-hint">
        库中已有相同「名称 + 分类 + 标签」的条目。请修改下面三项后再收入；三者完全一样才会判为重复。
      </p>
      <div v-for="row in conflictDialog.rows" :key="row.pending_id" class="conflict-row">
        <el-form label-position="top">
          <el-form-item label="名称" required>
            <el-input v-model="row.name" maxlength="80" />
          </el-form-item>
          <el-form-item label="分类">
            <el-autocomplete
              v-model="row.category"
              :fetch-suggestions="queryCategory"
              clearable
              placeholder="分类"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="标签">
            <el-select
              v-model="row.tags"
              multiple
              filterable
              allow-create
              default-first-option
              collapse-tags
              collapse-tags-tooltip
              placeholder="标签"
              style="width: 100%"
            >
              <el-option v-for="t in libraryTags" :key="t" :label="t" :value="t" />
            </el-select>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="conflictDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="retryConflicts">改完再收入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { authHeaders, request } from "../api";
import { elementKindLabel } from "../elementUi";

const props = defineProps({
  card: { type: Object, required: true },
  sessionId: { type: String, default: "" },
});

const emit = defineEmits(["done", "changed"]);

const busy = ref(false);
const localRows = ref([]);
const libraryCategories = ref([]);
const libraryTags = ref([]);
const libraryItems = ref([]);
const conflictDialog = reactive({
  visible: false,
  rows: [],
});

const checkedCount = computed(() => localRows.value.filter((r) => r.selected).length);
const allChecked = computed(
  () => localRows.value.length > 0 && localRows.value.every((r) => r.selected)
);
const someChecked = computed(() => localRows.value.some((r) => r.selected));

function normName(name) {
  return String(name || "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function normTagSet(tags) {
  return new Set(
    (tags || [])
      .map((t) => String(t || "").trim().toLowerCase())
      .filter(Boolean)
  );
}

function sameTagSet(a, b) {
  const sa = normTagSet(a);
  const sb = normTagSet(b);
  if (sa.size !== sb.size) return false;
  for (const t of sa) if (!sb.has(t)) return false;
  return true;
}

function alreadyInLibrary(el) {
  // 已回链个人库的要素不再展示
  return Boolean(String(el?.library_item_id || "").trim());
}

function identityAlreadyCollected(name, category, tags) {
  const key = normName(name);
  if (!key || !libraryItems.value.length) return false;
  const cat = String(category || "").trim().toLowerCase();
  return libraryItems.value.some(
    (i) =>
      normName(i.name) === key &&
      String(i.category || "").trim().toLowerCase() === cat &&
      sameTagSet(i.tags, tags)
  );
}

function mapRows(items) {
  return (items || [])
    .filter((row) => {
      const el = row.element || {};
      if (alreadyInLibrary(el)) return false;
      return true;
    })
    .map((row) => {
      const el = { ...(row.element || {}) };
      const name = String(el.name || "").trim();
      // 优先待办字段，其次要素上新建时已填的分类/标签/作用
      const category = String(row.category || el.category || "").trim();
      const purpose = String(row.purpose || el.purpose || "").trim();
      const tagsRaw = Array.isArray(row.tags) && row.tags.length ? row.tags : el.tags;
      const tags = Array.isArray(tagsRaw)
        ? tagsRaw.map((t) => String(t || "").trim()).filter(Boolean)
        : [];
      return {
        ...row,
        element: el,
        tags,
        category,
        purpose: purpose || name,
        categoryHint: "",
        tagsHint: "",
        identityHint: identityAlreadyCollected(name, category, tags)
          ? "库中已有相同名称+分类+标签，请修改后再收入"
          : "",
        metaFromCreate: Boolean(category || purpose || tags.length),
        showEdit: false,
        selected: true,
      };
    });
}

function toggleSelectAll(on) {
  for (const row of localRows.value) row.selected = Boolean(on);
}

function onCategoryChange(row) {
  const cat = String(row.category || "").trim();
  if (!cat) {
    row.categoryHint = "";
    onIdentityChange(row);
    return;
  }
  const hit = libraryCategories.value.find((c) => c.toLowerCase() === cat.toLowerCase());
  row.categoryHint = hit ? `将使用已有分类「${hit}」` : "将新建此分类";
  if (hit) row.category = hit;
  onIdentityChange(row);
}

function onTagsChange(row) {
  const tags = row.tags || [];
  const existing = tags.filter((t) =>
    libraryTags.value.some((x) => x.toLowerCase() === String(t).toLowerCase())
  );
  const fresh = tags.filter(
    (t) => !libraryTags.value.some((x) => x.toLowerCase() === String(t).toLowerCase())
  );
  if (existing.length && fresh.length) {
    row.tagsHint = `已有标签：${existing.join("、")}；新建：${fresh.join("、")}`;
  } else if (existing.length) {
    row.tagsHint = `将使用已有标签：${existing.join("、")}`;
  } else if (fresh.length) {
    row.tagsHint = `将新建标签：${fresh.join("、")}`;
  } else {
    row.tagsHint = "";
  }
  onIdentityChange(row);
}

function onIdentityChange(row) {
  const name = String(row.element?.name || "").trim();
  row.identityHint = identityAlreadyCollected(name, row.category, row.tags)
    ? "库中已有相同名称+分类+标签，请修改后再收入"
    : "";
}

function queryCategory(query, cb) {
  const q = String(query || "").trim().toLowerCase();
  const list = libraryCategories.value
    .filter((c) => !q || c.toLowerCase().includes(q))
    .map((value) => ({ value }));
  cb(list);
}

async function loadLibraryMeta() {
  try {
    const data = await request("/api/writing/element-library", { headers: authHeaders() });
    libraryCategories.value = data.categories || [];
    libraryTags.value = data.tags || [];
    libraryItems.value = data.items || [];
    // 用卡片原始 items 再滤一遍，避免已映射行缺字段；已入库同名会被隐藏
    const source = props.card?.payload?.items || localRows.value;
    localRows.value = mapRows(source);
    for (const row of localRows.value) {
      onCategoryChange(row);
      onTagsChange(row);
    }
  } catch {
    /* 库元数据失败不阻断收录 */
  }
}

watch(
  () => props.card?.payload?.items,
  (items) => {
    localRows.value = mapRows(items);
    for (const row of localRows.value) {
      onCategoryChange(row);
      onTagsChange(row);
    }
  },
  { immediate: true, deep: true }
);

onMounted(loadLibraryMeta);

function selectedRows() {
  return localRows.value.filter((r) => r.selected);
}

function openConflictDialog(conflicts) {
  const byId = new Map(localRows.value.map((r) => [r.pending_id, r]));
  conflictDialog.rows = (conflicts || [])
    .map((c) => {
      const row = byId.get(String(c.pending_id || ""));
      if (!row) return null;
      return {
        pending_id: row.pending_id,
        task_id: row.task_id || "",
        purpose: row.purpose || "",
        element: { ...(row.element || {}) },
        name: String(c.name || row.element?.name || "").trim(),
        category: String(c.category || row.category || "").trim(),
        tags: Array.isArray(c.tags)
          ? [...c.tags]
          : [...(row.tags || [])],
      };
    })
    .filter(Boolean);
  if (!conflictDialog.rows.length) return;
  conflictDialog.visible = true;
}

async function postImportEntries(entries) {
  return request("/api/writing/element-library/import-batch", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ entries, session_id: props.sessionId, force: false }),
  });
}

function applyImportResult(data) {
  const importedIds = new Set(
    (data?.items || [])
      .map((it) => String(it?.source_element_id || it?.id || "").trim())
      .filter(Boolean)
  );
  const importedNames = new Set(
    (data?.items || []).map((it) => normName(it.name)).filter(Boolean)
  );
  const conflictPending = new Set(
    (data?.conflicts || []).map((c) => String(c.pending_id || "")).filter(Boolean)
  );
  localRows.value = localRows.value.filter((r) => {
    if (conflictPending.has(r.pending_id)) return true;
    if (importedNames.has(normName(r.element?.name))) return false;
    if (importedIds.has(String(r.element?.id || ""))) return false;
    if (
      alreadyInLibrary(r.element) ||
      identityAlreadyCollected(r.element?.name, r.category, r.tags)
    ) {
      return false;
    }
    return true;
  });
}

async function importSelected() {
  const rows = selectedRows();
  if (!rows.length) {
    ElMessage.info("请先勾选要收入的要素");
    return;
  }
  for (const row of rows) {
    if (!String(row.element?.name || "").trim()) {
      ElMessage.warning("要素名称不能为空");
      return;
    }
    if (identityAlreadyCollected(row.element?.name, row.category, row.tags)) {
      row.showEdit = true;
      onIdentityChange(row);
      ElMessage.warning("有要素与库中名称+分类+标签重复，请先修改这三项");
      openConflictDialog(
        rows
          .filter((r) => identityAlreadyCollected(r.element?.name, r.category, r.tags))
          .map((r) => ({
            pending_id: r.pending_id,
            name: r.element?.name,
            category: r.category,
            tags: r.tags,
          }))
      );
      return;
    }
  }
  busy.value = true;
  try {
    const entries = rows.map((row) => ({
      pending_id: row.pending_id,
      task_id: row.task_id || "",
      category: row.category,
      tags: row.tags,
      purpose: row.purpose,
      element: { ...row.element, name: row.element?.name },
      name: row.element?.name,
      replace_existing: false,
    }));
    const data = await postImportEntries(entries);
    const n = Number(data?.imported || 0);
    if (n > 0) {
      ElMessage.success(`已收入 ${n} 个要素到个人要素库`);
    }
    await loadLibraryMeta();
    applyImportResult(data);
    if ((data?.conflicts || []).length) {
      ElMessage.warning("部分要素与库中名称+分类+标签重复，请修改后再收入");
      openConflictDialog(data.conflicts);
    }
    emit("changed");
    if (!localRows.value.length && !conflictDialog.visible) emit("done");
  } catch (e) {
    let detail = e?.detail || e?.data?.detail;
    if (!detail && typeof e?.message === "string") {
      try {
        detail = JSON.parse(e.message);
      } catch {
        detail = e.message;
      }
    }
    if (detail && typeof detail === "object" && detail.conflicts) {
      ElMessage.warning(detail.message || "要素与库中重复，请修改名称/分类/标签");
      openConflictDialog(detail.conflicts);
    } else {
      ElMessage.error(typeof detail === "string" ? detail : e.message || "收录失败");
    }
  } finally {
    busy.value = false;
  }
}

async function retryConflicts() {
  const rows = conflictDialog.rows || [];
  if (!rows.length) {
    conflictDialog.visible = false;
    return;
  }
  for (const row of rows) {
    if (!String(row.name || "").trim()) {
      ElMessage.warning("要素名称不能为空");
      return;
    }
  }
  busy.value = true;
  try {
    const entries = rows.map((row) => ({
      pending_id: row.pending_id,
      task_id: row.task_id || "",
      category: row.category,
      tags: row.tags,
      purpose: row.purpose,
      element: { ...row.element, name: row.name },
      name: row.name,
      replace_existing: false,
    }));
    // 同步回列表行，便于再次冲突时继续改
    const byId = new Map(localRows.value.map((r) => [r.pending_id, r]));
    for (const row of rows) {
      const live = byId.get(row.pending_id);
      if (!live) continue;
      live.element = { ...live.element, name: row.name };
      live.category = row.category;
      live.tags = [...(row.tags || [])];
      onIdentityChange(live);
    }
    const data = await postImportEntries(entries);
    const n = Number(data?.imported || 0);
    if (n > 0) ElMessage.success(`已收入 ${n} 个要素到个人要素库`);
    await loadLibraryMeta();
    applyImportResult(data);
    const left = data?.conflicts || [];
    if (left.length) {
      ElMessage.warning("仍有重复项，请继续修改名称/分类/标签");
      openConflictDialog(left);
    } else {
      conflictDialog.visible = false;
      conflictDialog.rows = [];
    }
    emit("changed");
    if (!localRows.value.length && !conflictDialog.visible) emit("done");
  } catch (e) {
    let detail = e?.detail || e?.data?.detail;
    if (!detail && typeof e?.message === "string") {
      try {
        detail = JSON.parse(e.message);
      } catch {
        detail = e.message;
      }
    }
    if (detail && typeof detail === "object" && detail.conflicts) {
      ElMessage.warning(detail.message || "仍有重复，请继续修改");
      openConflictDialog(detail.conflicts);
    } else {
      ElMessage.error(typeof detail === "string" ? detail : e.message || "收录失败");
    }
  } finally {
    busy.value = false;
  }
}

async function deleteRow(row) {
  if (!row?.pending_id || busy.value) return;
  const name = String(row.element?.name || row.element?.id || "该要素").trim();
  try {
    await ElMessageBox.confirm(
      `确定删除「${name}」？将从文档副本移除该要素及章节关联，且不可恢复。`,
      "删除要素",
      {
        confirmButtonText: "删除",
        cancelButtonText: "取消",
        type: "warning",
      }
    );
  } catch {
    return;
  }
  busy.value = true;
  try {
    const data = await request("/api/writing/element-library/delete-pending", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        session_id: props.sessionId,
        pending_ids: [row.pending_id],
      }),
    });
    localRows.value = localRows.value.filter((r) => r.pending_id !== row.pending_id);
    ElMessage.success("已删除该要素");
    emit("changed", data);
    if (!localRows.value.length) emit("done");
  } catch (e) {
    ElMessage.error(e.message || "删除失败");
  } finally {
    busy.value = false;
  }
}

async function dismissSelected() {
  const rows = selectedRows();
  if (!rows.length) {
    ElMessage.info("请先勾选暂不收录的要素");
    return;
  }
  busy.value = true;
  try {
    await request("/api/writing/element-library/dismiss-pending", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        session_id: props.sessionId,
        pending_ids: rows.map((r) => r.pending_id),
        // 把卡片上已填的分类/标签/作用写回任务要素，章节配置不丢
        entries: rows.map((row) => ({
          pending_id: row.pending_id,
          category: row.category || "",
          tags: [...(row.tags || [])],
          purpose: row.purpose || "",
          element: row.element,
        })),
      }),
    });
    const removed = new Set(rows.map((r) => r.pending_id));
    localRows.value = localRows.value.filter((r) => !removed.has(r.pending_id));
    ElMessage.success("已从待办移除；章节上的要素配置已保留");
    emit("changed");
    if (!localRows.value.length) emit("done");
  } catch (e) {
    ElMessage.error(e.message || "操作失败");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
/* 子组件拿不到 ChatPage scoped 的 .card-title/.card-hint，在此对齐会话正文 13px */
.element-library-pending-card {
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
}
.element-library-pending-card .card-title {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.4;
}
.element-library-pending-card .card-hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
.element-library-pending-card .pending-row {
  margin: 10px 0;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: #fafbfc;
  font-size: 13px;
}
.pending-bulk {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 8px 0 4px;
  font-size: 13px;
}
.pending-bulk-all {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #64748b;
  cursor: pointer;
  user-select: none;
}
.pending-bulk-count {
  font-size: 13px;
  color: #64748b;
}
.pending-check {
  width: 14px;
  height: 14px;
  margin: 0;
  flex: 0 0 auto;
}
.pending-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
  font-size: 13px;
}
.pending-head strong {
  font-size: 13px;
  font-weight: 600;
}
.pending-head .muted {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.pending-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.pending-actions :deep(.el-button) {
  font-size: 13px;
}
.field-tip {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}
.field-tip.warn,
.field-tip.warn-tip {
  color: var(--el-color-warning-dark-2);
}
.dup-hint {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff7e6;
  color: #ad6800;
  font-size: 13px;
  line-height: 1.45;
}
.conflict-row + .conflict-row {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.meta-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  font-size: 13px;
}
.meta-purpose {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.meta-edit-link {
  border: 0;
  background: transparent;
  color: #4338ca;
  font-size: 13px;
  cursor: pointer;
  padding: 0 4px;
}
.meta-edit-link:hover {
  text-decoration: underline;
}
.meta-delete-link {
  border: 0;
  background: transparent;
  color: #b91c1c;
  font-size: 13px;
  cursor: pointer;
  padding: 0 4px;
}
.meta-delete-link:hover:not(:disabled) {
  text-decoration: underline;
}
.meta-delete-link:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pending-row-actions {
  margin-top: 4px;
}
</style>
