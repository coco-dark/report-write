<template>
  <div class="card element-configure-card">
    <div class="card-title">{{ card.title || "确认要素配置" }}</div>
    <p class="card-hint">{{ card.payload?.hint }}</p>
    <p v-if="current?.agent_hint" class="agent-hint">助手理解：{{ current.agent_hint }}</p>
    <div v-if="draftPreview" class="draft-preview">
      <a-tag size="small">{{ elementKindLabel(draftPreview.kind) }}</a-tag>
      <strong>{{ draftPreview.name || "未命名" }}</strong>
      <span class="muted">{{ draftPreview.purpose || draftPreview.category || "" }}</span>
    </div>
    <a-button type="primary" @click="openDialog" shape="round">确认要素配置</a-button>

    <a-modal
 v-model:open="dialogVisible"
 :title="dialogTitle"
 width="760px"
 class="element-configure-dialog"
 @afterClose="onDialogClosed"
 destroy-on-close centered>
      <p v-if="current?.chapter_title" class="dlg-chapter">关联章节：{{ current.chapter_title }}</p>
      <a-form v-if="draft" layout="vertical">
        <ElementConfigFields
          v-model="draft"
          show-library-meta
          :tag-options="libraryTags"
          :show-node-ref="true"
          :ref-target-options="refTargetOptions"
          :pack-slot-options="packSlotOptions"
          :task-id="configureTaskId"
          :element-id="draft?.id"
        />
      </a-form>
      <template #footer>
        <a-button @click="dialogVisible = false">取消</a-button>
        <a-button type="primary" :loading="busy" @click="saveDraft">确认并关联本章</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "../ui.js";

import { authHeaders, request } from "../api";
import ElementConfigFields from "./ElementConfigFields.vue";
import {
  blankElementDraft,
  elementDraftHasPendingPicks,
  elementKindLabel,
  normalizeKvElementDraft,
  stripElementDraftUiKeys,
} from "../elementUi";
import { humanLabel, isElementIdToken } from "../pointItemsSync";

const props = defineProps({
  card: { type: Object, required: true },
  sessionId: { type: String, default: "" },
  taskElements: { type: Array, default: () => [] },
  /** 进入历史会话时为 true，避免自动弹出；本轮新增要素配置为 false */
  suppressAutoOpen: { type: Boolean, default: false },
});

const emit = defineEmits(["done", "saved"]);

const dialogVisible = ref(false);
const busy = ref(false);
const draft = ref(null);
const current = ref(null);
const libraryTags = ref([]);
const queue = ref([]);
const packSlotOptions = ref([]);

const draftPreview = computed(() => queue.value[0]?.draft || null);

const configureTaskId = computed(() =>
  String(current.value?.task_id || props.card?.payload?.task_id || "").trim()
);

async function refreshPackSlotOptions() {
  const tid = configureTaskId.value;
  if (!tid) {
    packSlotOptions.value = [];
    return;
  }
  try {
    const task = await request(`/api/writing/tasks/${encodeURIComponent(tid)}`);
    const pid = String(task?.materials_pack_id || "").trim();
    if (!pid) {
      packSlotOptions.value = [];
      return;
    }
    const pack = await request(`/api/writing/materials-packs/${encodeURIComponent(pid)}`);
    const byNo = new Map();
    for (const s of pack?.slot_schema || []) {
      const no = Number(s?.no) > 0 ? Number(s.no) : 0;
      if (no < 1) continue;
      byNo.set(no, { no, name: String(s?.name || "").trim() });
    }
    for (const f of pack?.slots_fill || []) {
      const no = Number(f?.slot_no) > 0 ? Number(f.slot_no) : 0;
      if (no < 1) continue;
      if (!byNo.has(no)) byNo.set(no, { no, name: "" });
    }
    packSlotOptions.value = [...byNo.values()].sort((a, b) => a.no - b.no);
  } catch {
    packSlotOptions.value = [];
  }
}

const dialogTitle = computed(() => {
  if (!draft.value) return "要素配置";
  const k = elementKindLabel(draft.value.kind);
  return `配置${k}要素`;
});

const refTargetOptions = computed(() => {
  const selfId = String(draft.value?.id || "").trim();
  return (props.taskElements || [])
    .filter((el) => el.id && el.id !== selfId)
    .map((el) => ({
      value: el.id,
      label: `${el.name || el.id}（${elementKindLabel(el.kind)}）`,
    }));
});

function syncQueue() {
  queue.value = [...(props.card?.payload?.items || [])];
  if (!current.value && queue.value.length) {
    current.value = queue.value[0];
  }
}

function maybeAutoOpenDialog() {
  if (props.suppressAutoOpen || !queue.value.length || dialogVisible.value) return;
  if (!current.value) current.value = queue.value[0];
  dialogVisible.value = true;
  loadDraftFromCurrent();
}

watch(() => props.card?.payload?.items, syncQueue, { immediate: true, deep: true });

watch(
  () =>
    (props.card?.payload?.items || [])
      .map((item) => String(item?.pending_id || ""))
      .filter(Boolean)
      .join("|"),
  (next, prev) => {
    syncQueue();
    // 本轮队列出现新 pending 时再自动弹；进入历史会话不弹
    if (!next || next === prev) return;
    maybeAutoOpenDialog();
  }
);

async function loadLibraryMeta() {
  try {
    const data = await request("/api/writing/element-library");
    libraryTags.value = data.tags || [];
  } catch {
    libraryTags.value = [];
  }
}

onMounted(() => {
  loadLibraryMeta();
  maybeAutoOpenDialog();
});

function loadDraftFromCurrent() {
  if (!current.value) return;
  const base = blankElementDraft("kv", { library: true });
  draft.value = {
    ...base,
    ...JSON.parse(JSON.stringify(current.value.draft || {})),
    kv_pairs: (current.value.draft?.kv_pairs || base.kv_pairs).map((p) => ({ ...p })),
    tags: [...(current.value.draft?.tags || [])],
    knowledge_folder_ids: [...(current.value.draft?.knowledge_folder_ids || [])],
    knowledge_folder_picks: (current.value.draft?.knowledge_folder_picks || []).map((p) => ({
      ...p,
      candidates: [...(p.candidates || [])],
    })),
    knowledge_folder_unmatched: [...(current.value.draft?.knowledge_folder_unmatched || [])],
    ref_element_picks: (current.value.draft?.ref_element_picks || []).map((p) => ({
      ...p,
      candidates: [...(p.candidates || [])],
    })),
  };
  if (!String(draft.value.id || "").trim()) {
    draft.value.id = `el-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  }
  refreshPackSlotOptions();
}

function openDialog() {
  if (!queue.value.length) return;
  current.value = queue.value[0];
  loadDraftFromCurrent();
  dialogVisible.value = true;
}

function onDialogClosed() {
  if (!busy.value && queue.value.length) {
    // 保持卡片，用户可再次打开
  }
}

async function saveDraft() {
  if (!current.value || !draft.value || !props.sessionId) return;
  let payload = { ...draft.value };
  const fallbackName = humanLabel(
    payload.name,
    current.value?.draft?.name,
    current.value?.slot_label,
    current.value?.draft?.category,
    payload.category
  );
  if (fallbackName) payload.name = fallbackName;
  else if (isElementIdToken(payload.name)) payload.name = "";
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
  if (elementDraftHasPendingPicks(payload)) {
    ElMessage.warning("请先为名称相似的目录或要素选择一项");
    return;
  }
  if (payload.kind === "node_ref" && !String(payload.ref_key || "").trim()) {
    ElMessage.warning("请选择要引用结果的要素");
    return;
  }
  busy.value = true;
  try {
    const result = await request("/api/writing/chat/element-configure/confirm", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        session_id: props.sessionId,
        pending_id: current.value.pending_id,
        element: stripElementDraftUiKeys(payload),
      }),
    });
    const pendingN = Number(result?.element_library_pending_count || 0);
    ElMessage.success(
      pendingN > 0
        ? "要素已关联本章，可在文档窗口「待收录要素」收入个人要素库"
        : "要素已保存并关联到本章"
    );
    queue.value = queue.value.filter((x) => x.pending_id !== current.value.pending_id);
    emit("saved", result || {});
    if (queue.value.length) {
      current.value = queue.value[0];
      loadDraftFromCurrent();
    } else {
      dialogVisible.value = false;
      emit("done", result || {});
    }
  } catch (e) {
    ElMessage.error(e.message || "保存失败");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.element-configure-card .card-hint,
.agent-hint {
  font-size: 13px;
  color: var(--ay-muted);
  line-height: 1.5;
  margin: 8px 0 12px;
}
.agent-hint {
  padding: 8px 10px;
  background: #f5f7fa;
  border-radius: 8px;
}
.dlg-chapter {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--ay-muted);
}
.draft-preview {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #f5f7fa;
  border-radius: 8px;
  font-size: 13px;
}
.draft-preview .muted {
  color: var(--ay-muted);
}
</style>
