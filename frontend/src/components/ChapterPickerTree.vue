<template>
  <div class="chapter-picker-tree">
    <div class="picker-toolbar">
      <a-button size="small" :disabled="busy || disabled" @click="selectAll(true)" type="text">
        {{ purpose === "study" ? "全选可学章" : "全选可写章" }}
      </a-button>
      <a-button size="small" :disabled="busy || disabled" @click="selectAll(false)" type="text">清空</a-button>
      <span class="picker-count">已选 {{ selectedKeys.length }} 章</span>
    </div>
    <div v-if="loading" class="picker-loading">正在加载章节树…</div>
    <a-alert v-else-if="error" type="error" :message="error" :closable="false" class="picker-error" show-icon />
    <UiTree
 v-else
 ref="treeRef"
 class="picker-tree"
 :data="treeData"
 node-key="key"
 show-checkbox
 :default-expand-all="flatCount <= 40"
 :expand-on-click-node="false"
 :props="{ label: 'label', children: 'children', disabled: 'disabled' }"
 @check="onCheck"
 >
      <template #default="{ data }">
        <span class="picker-tree-row">
          <span class="seq">{{ data.sequence || "" }}</span>
          <span class="name">{{ data.title }}</span>
          <a-tag v-if="data.structure_only" size="small">目录</a-tag>
          <a-tag v-else-if="data.unanalyzed" size="small" color="default">未分析</a-tag>
          <a-tag v-else-if="data.studied" size="small" color="success">已学</a-tag>
          <a-tag v-else-if="data.selectable && purpose !== 'study'" size="small" color="success">可写</a-tag>
          <a-tag v-else-if="data.selectable && purpose === 'study'" size="small" color="blue">可学</a-tag>
        </span>
      </template>
    </UiTree>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";

import { request } from "../api";

const props = defineProps({
  blueprintId: { type: String, required: true },
  disabled: { type: Boolean, default: false },
  /** write=开写勾选（已分析章）；study=学习分析（非目录章均可勾选） */
  purpose: { type: String, default: "write" },
  /** 若父级已带 chapters（旧卡片），可直接用，免再请求 */
  chapters: { type: Array, default: null },
});

const emit = defineEmits(["update:selectedKeys", "ready"]);

const treeRef = ref(null);
const loading = ref(false);
const error = ref("");
const flatChapters = ref([]);
const selectedKeys = ref([]);

const busy = computed(() => loading.value || props.disabled);
const flatCount = computed(() => flatChapters.value.length);

const treeData = computed(() => buildTree(flatChapters.value));

const selectableKeys = computed(() =>
  flatChapters.value.filter((ch) => ch?.selectable && ch?.key).map((ch) => String(ch.key))
);

function buildTree(chapters) {
  const nodes = (chapters || []).map((item) => ({
    ...item,
    label: [item.sequence, item.title].filter(Boolean).join(" ") || item.key,
    disabled: !item.selectable,
    children: [],
  }));
  const byKey = new Map(nodes.map((item) => [item.key, item]));
  const roots = [];
  for (const item of nodes) {
    if (item.parent && byKey.has(item.parent)) {
      byKey.get(item.parent).children.push(item);
    } else {
      roots.push(item);
    }
  }
  return roots;
}

function syncSelectedFromTree() {
  const allowed = new Set(selectableKeys.value);
  const tree = treeRef.value;
  let keys = [];
  if (tree?.getCheckedKeys) {
    keys = (tree.getCheckedKeys(false) || []).map(String).filter((k) => allowed.has(k));
  }
  selectedKeys.value = keys;
  emit("update:selectedKeys", keys);
}

function onCheck() {
  syncSelectedFromTree();
}

function selectAll(all) {
  const keys = all ? [...selectableKeys.value] : [];
  selectedKeys.value = keys;
  treeRef.value?.setCheckedKeys?.(keys);
  emit("update:selectedKeys", keys);
}

function getSelectedKeys() {
  syncSelectedFromTree();
  return [...selectedKeys.value];
}

async function loadChapters() {
  error.value = "";
  if (Array.isArray(props.chapters) && props.chapters.length) {
    flatChapters.value = normalizeRows(props.chapters);
    emit("ready", { count: flatChapters.value.length });
    return;
  }
  const bid = String(props.blueprintId || "").trim();
  if (!bid) {
    error.value = "缺少蓝本 id";
    return;
  }
  loading.value = true;
  try {
    if (props.purpose === "study") {
      const task = await request(`/api/writing/tasks/${encodeURIComponent(bid)}`);
      const pendingKeys = new Set(
        (task?.pending_study || []).map((row) => String(row.chapter_key || row.key || "")).filter(Boolean)
      );
      flatChapters.value = normalizeRows(
        (task?.outline?.chapters || []).map((ch) => {
          const key = String(ch.key || "").trim();
          const structureOnly = Boolean(ch.structure_only || ch.config?.structure_only);
          const hasPoints = (ch.points || []).length > 0;
          return {
            key,
            sequence: ch.sequence || "",
            title: ch.title || key,
            parent: ch.parent || "",
            structure_only: structureOnly,
            selectable: !structureOnly,
            unanalyzed: !structureOnly && !hasPoints,
            studied: !structureOnly && hasPoints,
            pending_study: pendingKeys.has(key),
          };
        })
      );
    } else {
      const data = await request(`/api/writing/tasks/${encodeURIComponent(bid)}/chapter-picker`);
      flatChapters.value = normalizeRows(Array.isArray(data?.chapters) ? data.chapters : []);
    }
    emit("ready", { count: flatChapters.value.length });
  } catch (err) {
    error.value = err?.message || "加载章节树失败";
    flatChapters.value = [];
  } finally {
    loading.value = false;
  }
}

function normalizeRows(rows) {
  return (rows || []).map((item) => ({
    ...item,
    selectable: Boolean(item.selectable),
  }));
}

watch(
  () => [props.blueprintId, props.purpose],
  () => {
    selectedKeys.value = [];
    loadChapters();
  }
);

onMounted(loadChapters);

defineExpose({ getSelectedKeys, selectAll });
</script>

<style scoped>
.picker-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 4px 0 8px;
}
.picker-count {
  margin-left: auto;
  font-size: 12px;
  color: #64748b;
}
.picker-loading,
.picker-error {
  margin-bottom: 10px;
  font-size: 13px;
  color: #64748b;
}
.picker-tree {
  max-height: 280px;
  overflow: auto;
  margin-bottom: 4px;
  padding: 8px 6px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid var(--ay-line, #e2e8f0);
}
.picker-tree-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 13px;
}
.picker-tree-row .seq {
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
  min-width: 2.5em;
}
.picker-tree-row .name {
  color: #0f172a;
}
</style>
