<template>
  <a-modal
 :open="modelValue"
 width="780px"
 class="chapter-element-associate-dialog"
 :z-index="4800"
 @update:open="$emit('update:modelValue', $event)"
 @afterOpenChange="(open) => open && onOpen()"
 destroy-on-close centered>
    <template #title>
      <div class="dlg-head">
        <span class="dlg-title">{{
          associateScope === "point" ? "为本条写作要点关联要素" : "为本章关联要素"
        }}</span>
        <span v-if="chapterLabel" class="dlg-chapter">{{ chapterLabel }}</span>
      </div>
    </template>

    <a-alert
      type="info"
      :closable="false"
      class="intro-alert"
      show-icon
      :message="
        associateScope === 'point'
          ? '可先勾选已有要素；需要新建时再点「根据要点信息生成要素」或手动配置。'
          : '所选要素关联到整章（章级），不会自动挂到各条写作要点；要点内请单独点「关联要素」。'
      "
    />

    <div class="nav-actions" role="group" aria-label="快捷查看">
      <a-button size="small" @click="goViewElements" shape="round">查看要素</a-button>
      <a-button size="small" @click="goViewMaterials" shape="round">查看资料库</a-button>
      <a-button
        v-if="associateScope === 'point' && pointSeed"
        size="small"
        type="primary"
        ghost
        :loading="generatingFromPoint"
        shape="round"
        @click="generateFromPoint"
      >
        根据要点信息生成要素
      </a-button>
    </div>

    <section class="linked-strip">
      <span class="linked-label">
        已关联{{ associateScope === "point" ? "要点要素" : "章节要素" }}
        <strong>{{ localLinkedIds.length }}</strong> 个
        <em class="scope-pill" :class="associateScope === 'point' ? 'point' : 'chapter'">
          {{ associateScope === "point" ? "要点" : "章" }}
        </em>
      </span>
      <div v-if="linkedElements.length" class="linked-chips">
        <a-tag
 v-for="el in linkedElements"
 :key="el.id"
 closable
 size="small"
 @close="unlinkOne(el.id)"
 >
          <span class="chip-scope">{{ associateScope === "point" ? "要点" : "章" }}</span>
          {{ el.name || el.id }} · {{ elementKindLabel(el.kind) }}
        </a-tag>
      </div>
      <span v-else class="linked-empty">
        {{ associateScope === "point" ? "本条要点尚未关联要素" : "本章尚未关联章节要素" }}，请在下方添加
      </span>
    </section>

    <a-tabs v-model:activeKey="activeTab" class="main-tabs">
      <a-tab-pane tab="选择关联" key="pick">
        <section class="block">
          <div class="block-head">
            <span class="block-title">个人要素库</span>
            <a-input v-model:value="libraryFilter" size="small" allow-clear placeholder="搜索名称 / 分类" class="filter-input" />
          </div>
          <a-empty v-if="!libraryCandidates.length" description="个人库暂无可用条目" :image-style="{ height: 48 }"  />
          <div v-else class="pick-scroll" style="max-height:200px;overflow:auto">
            <a-checkbox-group v-model:value="libraryPickChecked" class="pick-list">
              <label v-for="el in libraryCandidates" :key="el.id" class="pick-row">
                <a-checkbox :value="el.id">
                  <span class="pick-name">{{ el.name || el.id }}</span>
                  <a-tag v-if="el.category" size="small" color="warning">{{ el.category }}</a-tag>
                  <a-tag size="small" color="default">{{ elementKindLabel(el.kind) }}</a-tag>
                  <span class="pick-summary">{{ el.purpose || elementSummary(el) }}</span>
                </a-checkbox>
              </label>
            </a-checkbox-group>
          </div>
          <a-button
 v-if="libraryPickChecked.length"
 type="primary"
 size="small"
 class="pick-add-btn"
 @click="addLibraryPicked"
 >
            导入并关联（{{ libraryPickChecked.length }}）
          </a-button>
        </section>

        <section class="block">
          <div class="block-head">
            <span class="block-title">本任务其它要素</span>
            <a-input v-model:value="filterText" size="small" allow-clear placeholder="搜索名称" class="filter-input" />
          </div>
          <a-empty v-if="!pickCandidates.length" description="没有可关联的其它任务要素" :image-style="{ height: 48 }"  />
          <div v-else class="pick-scroll" style="max-height:180px;overflow:auto">
            <a-checkbox-group v-model:value="pickChecked" class="pick-list">
              <label v-for="el in pickCandidates" :key="el.id" class="pick-row">
                <a-checkbox :value="el.id">
                  <span class="pick-name">{{ el.name || el.id }}</span>
                  <a-tag size="small" color="default">{{ elementKindLabel(el.kind) }}</a-tag>
                  <span class="pick-summary">{{ elementSummary(el) }}</span>
                </a-checkbox>
              </label>
            </a-checkbox-group>
          </div>
          <a-button
 v-if="pickChecked.length"
 type="primary"
 size="small"
 class="pick-add-btn"
 @click="addPicked"
 >
            关联所选（{{ pickChecked.length }}）
          </a-button>
        </section>
      </a-tab-pane>

      <a-tab-pane v-if="allowCreate" tab="新建并关联" key="create">
        <p v-if="configureHint" class="tab-hint">{{ configureHint }}</p>
        <p class="tab-hint">
          {{
            associateScope === "point"
              ? "可手动配置，或点上方「根据要点信息生成要素」预填后再保存关联。"
              : "与要素库相同配置表单；保存后才会写入任务并自动勾到本章。"
          }}
        </p>
        <div class="create-actions">
          <a-button type="primary" @click="openCreateElementDialog">+ 配置新要素</a-button>
          <a-button
            v-if="associateScope === 'point' && pointSeed"
            :loading="generatingFromPoint"
            @click="generateFromPoint"
          >
            根据要点信息生成要素
          </a-button>
        </div>
        <ul v-if="stagedConfigured.length" class="staged-list">
          <li v-for="el in stagedConfigured" :key="el.id">
            <a-tag size="small">{{ elementKindLabel(el.kind) }}</a-tag>
            {{ el.name || el.id }}
          </li>
        </ul>
      </a-tab-pane>
    </a-tabs>

    <template #footer>
      <a-button @click="goViewElements" shape="round">查看要素</a-button>
      <a-button @click="goViewMaterials" shape="round">查看资料库</a-button>
      <a-button @click="$emit('update:modelValue', false)">取消</a-button>
      <a-button type="primary" @click="confirm">确定</a-button>
    </template>
  </a-modal>

  <!-- 与关联弹窗同级挂到 body，z-index 更高，避免被「为本章关联要素」挡住 -->
  <a-modal
 v-model:open="createDialogVisible"
 title="配置新要素"
 width="760px"
 :z-index="5200"
 class="chapter-element-create-dialog"
 destroy-on-close centered>
    <a-form v-if="createDraft" layout="vertical">
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
    </a-form>
    <template #footer>
      <a-button @click="createDialogVisible = false">取消</a-button>
      <a-button type="primary" @click="saveCreateElement">
        {{ associateScope === "point" ? "保存并关联本要点" : "保存并关联本章" }}
      </a-button>
    </template>
  </a-modal>
</template>

<script setup>
import { computed, inject, ref } from "vue";
import { ElMessage } from "../ui.js";
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
  /** 要点作用域：供「根据要点信息生成要素」按钮调用；打开面板时不自动生成 */
  pointSeed: { type: Object, default: null },
  /** chapter=章级关联；point=仅挂到指定写作要点 */
  associateScope: { type: String, default: "chapter" },
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
const generatingFromPoint = ref(false);

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

function localDraftFromPointSeed(seed) {
  const slot = String(seed?.slot || "").trim();
  const pointText = String(seed?.pointText || "").trim();
  const title = String(seed?.title || "").trim();
  const sequence = String(seed?.sequence || "").trim();
  const name = (slot || pointText || "写作要素").slice(0, 24);
  const label = `${sequence} ${title}`.trim() || "本章";
  const purpose = `用于「${label}」中「${name}」要点的资料要素`;
  const blob = `${slot} ${pointText}`;
  const looksFile = /责任书|报告|方案|合同|协议|批复|文件|资料|证书|清单|表格|证明|纪要|通知|制度|标准/.test(
    blob
  );
  if (looksFile) {
    return {
      kind: "file",
      name,
      category: name,
      purpose,
      tags: [name],
      query: String(slot || pointText || name).trim().slice(0, 48),
      value: "",
      prompt: "",
      kv_pairs: [{ key: "", value: "" }],
    };
  }
  return {
    kind: "kv",
    name,
    category: name,
    purpose,
    tags: [name],
    query: "",
    value: "",
    prompt: "",
    kv_pairs: [{ key: slot || name, value: "" }],
  };
}

function openCreateDraftFromSeed(seed) {
  if (!seed || typeof seed !== "object") return;
  const kind = String(seed.kind || "kv").trim() || "kv";
  const base = blankElementDraft(kind, { library: true });
  const merged = {
    ...base,
    ...seed,
    kind,
    id: String(seed.id || "").trim() || `el-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
  };
  createDraft.value = merged.kind === "kv" ? normalizeKvElementDraft(merged) : merged;
  activeTab.value = "create";
  createDialogVisible.value = true;
}

/** 仅要点作用域：用户点击后才根据要点生成要素草稿 */
async function generateFromPoint() {
  if (props.associateScope !== "point" || !props.pointSeed || generatingFromPoint.value) return;
  const seed = props.pointSeed;
  const tid = String(props.taskId || "").trim();
  generatingFromPoint.value = true;
  let draft = localDraftFromPointSeed(seed);
  try {
    if (tid) {
      const data = await request(
        `/api/writing/tasks/${encodeURIComponent(tid)}/elements/draft-from-point`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chapter_key: String(seed.chapterKey || "").trim(),
            title: String(seed.title || "").trim(),
            sequence: String(seed.sequence || "").trim(),
            describes: String(seed.describes || "").trim(),
            point_text: String(seed.pointText || "").trim(),
            slot: String(seed.slot || "").trim(),
            point_id: String(seed.pointId || "").trim(),
          }),
        }
      );
      if (data?.draft && typeof data.draft === "object") {
        draft = { ...draft, ...data.draft };
      }
      ElMessage.success(data?.from_llm ? "已根据要点生成要素草稿，请确认" : "已预填要素草稿，请确认");
    } else {
      ElMessage.success("已预填要素草稿，请确认");
    }
  } catch (error) {
    ElMessage.warning(error?.message || "已用要点预填要素草稿，请核对");
  } finally {
    generatingFromPoint.value = false;
  }
  openCreateDraftFromSeed(draft);
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
  generatingFromPoint.value = false;
  loadLibrary();
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
    const toPoint = String(props.associateScope || "").trim() === "point";
    ElMessage.success(
      toPoint
        ? `已新建 ${stagedConfigured.value.length} 个要素并关联到要点`
        : `已新建 ${stagedConfigured.value.length} 个要素并关联到本章`
    );
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
  color: var(--ay-muted);
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
  color: var(--ay-blue);
}
.scope-pill {
  display: inline-flex;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 999px;
  font-style: normal;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.5;
  vertical-align: middle;
}
.scope-pill.chapter {
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #a7f3d0;
}
.scope-pill.point {
  background: #eef2ff;
  color: #4338ca;
  border: 1px solid #c7d2fe;
}
.linked-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex: 1;
}
.chip-scope {
  display: inline-block;
  margin-right: 4px;
  padding: 0 5px;
  border-radius: 999px;
  background: #e2e8f0;
  color: #334155;
  font-size: 10px;
  font-weight: 700;
}
.linked-empty {
  color: var(--ay-muted);
  font-size: 12px;
}
.main-tabs {
  margin-top: 4px;
}
.tab-hint {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--ay-muted);
  line-height: 1.45;
}
.create-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
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
  border: 1px solid var(--ay-line);
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
  border-bottom: 1px solid var(--ay-line);
  cursor: pointer;
}
.pick-row:last-child {
  border-bottom: none;
}
.pick-row :deep(.ant-checkbox-wrapper) {
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
  color: var(--ay-muted);
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
