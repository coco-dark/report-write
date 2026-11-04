<template>
  <el-dialog
    :model-value="modelValue"
    width="780px"
    destroy-on-close
    append-to-body
    align-center
    class="chapter-element-associate-dialog"
    :z-index="4800"
    @update:model-value="$emit('update:modelValue', $event)"
    @open="onOpen"
  >
    <template #header>
      <div class="dlg-head">
        <span class="dlg-title">为本章关联要素</span>
        <span v-if="chapterLabel" class="dlg-chapter">{{ chapterLabel }}</span>
      </div>
    </template>

    <el-alert type="info" :closable="false" show-icon class="intro-alert">
      {{
        seedCreateDraft
          ? "已根据待补项预填要素信息，请核对后保存并关联到本章；也可切到「选择关联」勾选已有要素。"
          : "本章只选择要启用的要素。新建要素须先完成配置并保存，才会进入任务拓扑。"
      }}
    </el-alert>

    <div class="nav-actions" role="group" aria-label="快捷查看">
      <el-button round plain size="small" @click="goViewElements">查看要素</el-button>
      <el-button round plain size="small" @click="goViewMaterials">查看资料库</el-button>
    </div>

    <section class="linked-strip">
      <span class="linked-label">已关联 <strong>{{ localLinkedIds.length }}</strong> 个</span>
      <div v-if="linkedElements.length" class="linked-chips">
        <el-tag
          v-for="el in linkedElements"
          :key="el.id"
          closable
          size="small"
          @close="unlinkOne(el.id)"
        >
          {{ el.name || el.id }} · {{ elementKindLabel(el.kind) }}
        </el-tag>
      </div>
      <span v-else class="linked-empty">尚未关联，请在下方添加</span>
    </section>

    <el-tabs v-model="activeTab" class="main-tabs">
      <el-tab-pane label="选择关联" name="pick">
        <section class="block">
          <div class="block-head">
            <span class="block-title">个人要素库</span>
            <el-input v-model="libraryFilter" size="small" clearable placeholder="搜索名称 / 分类" class="filter-input" />
          </div>
          <el-empty v-if="!libraryCandidates.length" description="个人库暂无可用条目" :image-size="48" />
          <el-scrollbar v-else max-height="200px" class="pick-scroll">
            <el-checkbox-group v-model="libraryPickChecked" class="pick-list">
              <label v-for="el in libraryCandidates" :key="el.id" class="pick-row">
                <el-checkbox :label="el.id">
                  <span class="pick-name">{{ el.name || el.id }}</span>
                  <el-tag v-if="el.category" size="small" type="warning" effect="plain">{{ el.category }}</el-tag>
                  <el-tag size="small" type="info" effect="plain">{{ elementKindLabel(el.kind) }}</el-tag>
                  <span class="pick-summary">{{ el.purpose || elementSummary(el) }}</span>
                </el-checkbox>
              </label>
            </el-checkbox-group>
          </el-scrollbar>
          <el-button
            v-if="libraryPickChecked.length"
            type="primary"
            plain
            size="small"
            class="pick-add-btn"
            @click="addLibraryPicked"
          >
            导入并关联（{{ libraryPickChecked.length }}）
          </el-button>
        </section>

        <section class="block">
          <div class="block-head">
            <span class="block-title">本任务其它要素</span>
            <el-input v-model="filterText" size="small" clearable placeholder="搜索名称" class="filter-input" />
          </div>
          <el-empty v-if="!pickCandidates.length" description="没有可关联的其它任务要素" :image-size="48" />
          <el-scrollbar v-else max-height="180px" class="pick-scroll">
            <el-checkbox-group v-model="pickChecked" class="pick-list">
              <label v-for="el in pickCandidates" :key="el.id" class="pick-row">
                <el-checkbox :label="el.id">
                  <span class="pick-name">{{ el.name || el.id }}</span>
                  <el-tag size="small" type="info" effect="plain">{{ elementKindLabel(el.kind) }}</el-tag>
                  <span class="pick-summary">{{ elementSummary(el) }}</span>
                </el-checkbox>
              </label>
            </el-checkbox-group>
          </el-scrollbar>
          <el-button
            v-if="pickChecked.length"
            type="primary"
            plain
            size="small"
            class="pick-add-btn"
            @click="addPicked"
          >
            关联所选（{{ pickChecked.length }}）
          </el-button>
        </section>
      </el-tab-pane>

      <el-tab-pane v-if="allowCreate" label="新建并关联" name="create">
        <p v-if="configureHint" class="tab-hint">{{ configureHint }}</p>
        <p class="tab-hint">与要素库相同配置表单；保存后才会写入任务并自动勾到本章。</p>
        <el-button type="primary" plain @click="openCreateElementDialog">+ 配置新要素</el-button>
        <ul v-if="stagedConfigured.length" class="staged-list">
          <li v-for="el in stagedConfigured" :key="el.id">
            <el-tag size="small">{{ elementKindLabel(el.kind) }}</el-tag>
            {{ el.name || el.id }}
          </li>
        </ul>
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <el-button round plain @click="goViewElements">查看要素</el-button>
      <el-button round plain @click="goViewMaterials">查看资料库</el-button>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" @click="confirm">确定</el-button>
    </template>
  </el-dialog>

  <!-- 与关联弹窗同级挂到 body，z-index 更高，避免被「为本章关联要素」挡住 -->
  <el-dialog
    v-model="createDialogVisible"
    title="配置新要素"
    width="760px"
    destroy-on-close
    append-to-body
    align-center
    :z-index="5200"
    class="chapter-element-create-dialog"
  >
    <el-form v-if="createDraft" label-position="top">
      <ElementConfigFields
        v-model="createDraft"
        show-library-meta
        :tag-options="libraryTags"
        :category-options="libraryCategories"
        :show-node-ref="true"
        :ref-target-options="refTargetOptions"
        :pack-slot-options="packSlotOptions"
        :task-id="taskId"
        :element-id="createDraft?.id"
      />
    </el-form>
    <template #footer>
      <el-button @click="createDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="saveCreateElement">保存并关联本章</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, inject, nextTick, ref } from "vue";
import { ElMessage } from "element-plus";
import { request } from "../api";
import ElementConfigFields from "./ElementConfigFields.vue";
import {
  blankElementDraft,
  cloneLibraryItemToTaskElement,
  elementDraftHasPendingPicks,
  elementKindLabel,
  elementSummary,
  materializeNewElements,
  normalizeKvElementDraft,
  stripElementDraftUiKeys,
} from "../elementUi";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  chapterLabel: { type: String, default: "" },
  allElements: { type: Array, default: () => [] },
  linkedIds: { type: Array, default: () => [] },
  allowCreate: { type: Boolean, default: true },
  configureHint: { type: String, default: "" },
  packSlotOptions: { type: Array, default: () => [] },
  robotId: { type: String, default: "" },
  /** 写作任务 id：图片要素上传/文生图 API 需要 */
  taskId: { type: String, default: "" },
  /** 从要点待补项生成的要素草稿（打开时直接进入配置确认） */
  seedCreateDraft: { type: Object, default: null },
});

const emit = defineEmits(["update:modelValue", "confirm"]);
const navigateTo = inject("navigateTo", null);

function goViewElements() {
  navigateTo?.("elements");
}

function goViewMaterials() {
  navigateTo?.("materials");
}

const activeTab = ref("pick");
const localLinkedIds = ref([]);
const stagedElements = ref([]);
const stagedConfigured = ref([]);
const filterText = ref("");
const libraryFilter = ref("");
const pickChecked = ref([]);
const libraryPickChecked = ref([]);
const libraryItems = ref([]);
const libraryTags = ref([]);
const libraryCategories = ref([]);
const createDialogVisible = ref(false);
const createDraft = ref(null);

const refTargetOptions = computed(() =>
  [...(props.allElements || []), ...stagedElements.value, ...stagedConfigured.value].map((el) => ({
    value: el.id,
    label: `${el.name || el.id}（${elementKindLabel(el.kind)}）`,
  })),
);

function elementById(id) {
  return (
    props.allElements.find((item) => item.id === id) ||
    stagedElements.value.find((item) => item.id === id) ||
    stagedConfigured.value.find((item) => item.id === id) ||
    null
  );
}

const linkedElements = computed(() =>
  localLinkedIds.value.map((id) => elementById(id)).filter(Boolean),
);

const pickCandidates = computed(() => {
  const linked = new Set(localLinkedIds.value);
  const q = filterText.value.trim().toLowerCase();
  return (props.allElements || []).filter((el) => {
    if (linked.has(el.id)) return false;
    if (!q) return true;
    const name = String(el.name || el.id || "").toLowerCase();
    return name.includes(q);
  });
});

const libraryCandidates = computed(() => {
  const linked = new Set(localLinkedIds.value);
  const q = libraryFilter.value.trim().toLowerCase();
  return (libraryItems.value || []).filter((lib) => {
    const existing = (props.allElements || []).find((t) => t.library_item_id === lib.id);
    if (existing && linked.has(existing.id)) return false;
    if (!q) return true;
    const blob = `${lib.name} ${lib.category} ${lib.purpose} ${(lib.tags || []).join(" ")}`.toLowerCase();
    return blob.includes(q);
  });
});

async function loadLibrary() {
  try {
    const rid = String(props.robotId || "").trim();
    const url = rid
      ? `/api/writing/robots/${encodeURIComponent(rid)}/element-library`
      : "/api/writing/element-library";
    const data = await request(url);
    libraryItems.value = data.items || [];
    libraryTags.value = data.tags || [];
    libraryCategories.value = data.categories || [];
  } catch {
    libraryItems.value = [];
    libraryTags.value = [];
    libraryCategories.value = [];
  }
}

function applySeedCreateDraft() {
  const seed = props.seedCreateDraft;
  if (!seed || typeof seed !== "object") return;
  const kind = String(seed.kind || "kv").trim() || "kv";
  const base = blankElementDraft(kind, { library: true });
  createDraft.value = {
    ...base,
    ...seed,
    id: `el-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
  };
  if (createDraft.value.kind === "kv") {
    createDraft.value = normalizeKvElementDraft(createDraft.value);
  }
  activeTab.value = "create";
  nextTick(() => {
    createDialogVisible.value = true;
  });
}

function onOpen() {
  activeTab.value = "pick";
  localLinkedIds.value = [...(props.linkedIds || [])];
  stagedElements.value = [];
  stagedConfigured.value = [];
  filterText.value = "";
  libraryFilter.value = "";
  pickChecked.value = [];
  libraryPickChecked.value = [];
  createDialogVisible.value = false;
  createDraft.value = null;
  loadLibrary();
  if (props.seedCreateDraft) {
    applySeedCreateDraft();
  }
}

function unlinkOne(id) {
  localLinkedIds.value = localLinkedIds.value.filter((x) => x !== id);
}

function addPicked() {
  const add = pickChecked.value.filter((id) => !localLinkedIds.value.includes(id));
  if (!add.length) return;
  localLinkedIds.value = [...localLinkedIds.value, ...add];
  pickChecked.value = [];
}

function addLibraryPicked() {
  let added = 0;
  for (const libId of libraryPickChecked.value) {
    const lib = libraryItems.value.find((item) => item.id === libId);
    if (!lib) continue;
    const existing = (props.allElements || []).find((t) => t.library_item_id === libId);
    const taskId = existing?.id || null;
    if (taskId) {
      if (!localLinkedIds.value.includes(taskId)) {
        localLinkedIds.value = [...localLinkedIds.value, taskId];
        added += 1;
      }
      continue;
    }
    const clone = cloneLibraryItemToTaskElement(lib);
    if (!stagedElements.value.some((e) => e.id === clone.id)) {
      stagedElements.value = [...stagedElements.value, clone];
    }
    if (!localLinkedIds.value.includes(clone.id)) {
      localLinkedIds.value = [...localLinkedIds.value, clone.id];
      added += 1;
    }
  }
  libraryPickChecked.value = [];
  if (added) ElMessage.success(`已关联 ${added} 个个人库要素`);
}

function openCreateElementDialog() {
  createDraft.value = blankElementDraft("kv", { library: true });
  createDraft.value.id = `el-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  createDialogVisible.value = true;
}

function saveCreateElement() {
  if (!createDraft.value) return;
  let payload = { ...createDraft.value };
  if (elementDraftHasPendingPicks(payload)) {
    ElMessage.warning("请先为名称相似的目录或要素选择一项");
    return;
  }
  if (payload.kind === "kv") {
    payload = normalizeKvElementDraft(payload);
    if (!payload.kv_pairs?.some((p) => String(p.key || "").trim())) {
      ElMessage.warning("请至少填写一行键值");
      return;
    }
    if (!String(payload.name || "").trim()) payload.name = payload.kv_pairs[0].key;
  } else if (!String(payload.name || "").trim()) {
    ElMessage.warning("请填写要素名称");
    return;
  }
  if (payload.kind === "file_slot") {
    const picked = (payload.file_slots || []).some((slot) => Number(slot?.no) > 0);
    if (!picked) {
      ElMessage.warning("请从资料包槽位中至少选择一项引用");
      return;
    }
  }
  if (payload.kind === "file" || payload.kind === "rag_search") {
    if (!Array.isArray(payload.files) || !payload.files.length) {
      ElMessage.warning("文件类型必须上传至少一个对应文件");
      return;
    }
  }
  const created = materializeNewElements([stripElementDraftUiKeys(payload)]);
  if (!created.length) {
    ElMessage.warning("请完善要素配置");
    return;
  }
  for (const el of created) {
    if (!stagedConfigured.value.some((x) => x.id === el.id)) {
      stagedConfigured.value = [...stagedConfigured.value, el];
    }
    if (!localLinkedIds.value.includes(el.id)) {
      localLinkedIds.value = [...localLinkedIds.value, el.id];
    }
  }
  createDialogVisible.value = false;
  createDraft.value = null;
  ElMessage.success("要素已配置，点「确定」保存章节关联");
}

function confirm() {
  const allNew = [...stagedElements.value, ...stagedConfigured.value];
  const ids = [...localLinkedIds.value];
  for (const el of allNew) {
    if (!ids.includes(el.id)) ids.push(el.id);
  }
  emit("confirm", { linkedIds: ids, newElements: allNew });
  emit("update:modelValue", false);
  if (stagedConfigured.value.length) {
    ElMessage.success(`已新建 ${stagedConfigured.value.length} 个要素并关联到本章`);
  }
}
</script>

<style scoped>
.dlg-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.dlg-title {
  font-weight: 600;
  font-size: 16px;
}
.dlg-chapter {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.intro-alert {
  margin-bottom: 12px;
}
.nav-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 12px;
}
.linked-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: #f5f7fa;
  border-radius: 10px;
  font-size: 13px;
}
.linked-label strong {
  color: var(--el-color-primary);
}
.linked-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex: 1;
}
.linked-empty {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.main-tabs {
  margin-top: 4px;
}
.tab-hint {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.45;
}
.block {
  margin-top: 12px;
}
.block-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.block-title {
  font-weight: 600;
  font-size: 14px;
}
.filter-input {
  max-width: 200px;
  margin-left: auto;
}
.pick-scroll {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}
.pick-list {
  display: flex;
  flex-direction: column;
  padding: 4px 8px;
}
.pick-row {
  display: block;
  padding: 8px 4px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
  cursor: pointer;
}
.pick-row:last-child {
  border-bottom: none;
}
.pick-row :deep(.el-checkbox) {
  align-items: flex-start;
  height: auto;
  white-space: normal;
}
.pick-name {
  font-weight: 500;
  margin-right: 6px;
}
.pick-summary {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.pick-add-btn {
  margin-top: 8px;
}
.staged-list {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
.staged-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
}
</style>
