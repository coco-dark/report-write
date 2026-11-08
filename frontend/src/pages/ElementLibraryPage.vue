<template>
  <div class="page element-library-page">
    <header class="page-head">
      <div>
        <h1>要素库</h1>
        <p class="hint">左侧按分类浏览，右侧拓扑连线；键值类要素可配置多行键值。</p>
      </div>
      <div class="head-actions">
        <a-button type="primary" @click="openCreate">新建要素</a-button>
        <a-button :loading="saving" @click="saveAll">保存全部</a-button>
      </div>
    </header>

    <div class="library-body">
      <aside class="category-panel">
        <div class="panel-head">
          <span class="panel-title">分类</span>
          <a-input v-model:value="filterText" allow-clear size="small" placeholder="搜索" />
        </div>
        <button
          type="button"
          class="cat-row"
          :class="{ active: !filterCategory }"
          @click="filterCategory = ''"
        >
          <span>全部</span>
          <span class="count">{{ items.length }}</span>
        </button>
        <button
          v-for="row in categoryRows"
          :key="row.name"
          type="button"
          class="cat-row"
          :class="{ active: filterCategory === row.name }"
          @click="filterCategory = row.name"
        >
          <span>{{ row.name }}</span>
          <span class="count">{{ row.count }}</span>
        </button>
        <div v-if="!categoryRows.length" class="panel-empty">暂无分类，编辑要素时填写</div>

        <div class="panel-divider">本分类要素</div>
        <button
          v-for="item in filteredItems"
          :key="item.id"
          type="button"
          class="item-row"
          :class="{ active: focusItemId === item.id }"
          @click="focusItem(item.id)"
        >
          <span class="item-name">{{ item.name || item.id }}</span>
          <a-tag size="small">{{ kindShort(item.kind) }}</a-tag>
        </button>
        <div v-if="!filteredItems.length" class="panel-empty">无要素</div>
      </aside>

      <div class="canvas-wrap">
        <VueFlow
          id="element-library-flow"
          v-model:nodes="flowNodes"
          v-model:edges="flowEdges"
          :node-types="nodeTypes"
          :default-viewport="{ zoom: 0.9, x: 24, y: 24 }"
          fit-view-on-init
          @node-click="onNodeClick"
          @connect="onConnect"
        >
          <Background pattern-color="#dcdfe6" :gap="18" />
          <Controls />
          <MiniMap pannable zoomable />
        </VueFlow>
        <a-empty v-if="!items.length" class="empty-overlay" description="暂无要素"  />
      </div>
    </div>

    <a-modal v-model:open="dialogVisible" :title="draft?.id ? '编辑要素' : '新建要素'" width="760px" destroy-on-close centered>
      <a-form v-if="draft" layout="vertical">
        <ElementConfigFields
          v-model="draft"
          show-library-meta
          :tag-options="allTags"
          :category-options="categoryRows.map((r) => r.name).filter((n) => n !== '未分类')"
          :show-node-ref="false"
        />
        <a-form-item v-if="draft.link_ids?.length" label="已关联要素">
          <a-tag v-for="lid in draft.link_ids" :key="lid" size="small" style="margin: 2px">{{ linkLabel(lid) }}</a-tag>
        </a-form-item>
      </a-form>
      <template #footer>
        <a-button v-if="draft?.id" @click="removeCurrent" danger>删除</a-button>
        <a-button @click="dialogVisible = false">取消</a-button>
        <a-button type="primary" @click="applyDraft">确定</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import { VueFlow, useVueFlow } from "@vue-flow/core";
import { MiniMap } from "@vue-flow/minimap";
import { computed, markRaw, nextTick, onMounted, ref, watch } from "vue";
import { ElMessage } from "../ui.js";

import { authHeaders, request } from "../api";
import ElementConfigFields from "../components/ElementConfigFields.vue";
import ElementFlowNode from "../components/ElementFlowNode.vue";
import {
  blankElementDraft,
  elementKindLabel,
  kvPairsFromElement,
  normalizeIdList,
  normalizeKvElementDraft,
} from "../elementUi";

import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import "@vue-flow/controls/dist/style.css";
import "@vue-flow/minimap/dist/style.css";

const SIZE = 108;
const GAP = 48;

const items = ref([]);
const allTags = ref([]);
const filterText = ref("");
const filterCategory = ref("");
const focusItemId = ref("");
const saving = ref(false);
const dialogVisible = ref(false);
const draft = ref(null);
const flowNodes = ref([]);
const flowEdges = ref([]);
const nodeTypes = { element: markRaw(ElementFlowNode) };

const { fitView, setCenter } = useVueFlow({ id: "element-library-flow" });

const filteredItems = computed(() => {
  const q = filterText.value.trim().toLowerCase();
  const cat = filterCategory.value;
  return items.value.filter((item) => {
    const itemCat = (item.category || "").trim() || "未分类";
    if (cat && itemCat !== cat) return false;
    if (!q) return true;
    const blob = `${item.name} ${item.purpose} ${item.category} ${(item.tags || []).join(" ")}`.toLowerCase();
    return blob.includes(q);
  });
});

const categoryRows = computed(() => {
  const map = new Map();
  for (const item of items.value) {
    const cat = (item.category || "").trim() || "未分类";
    map.set(cat, (map.get(cat) || 0) + 1);
  }
  return [...map.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => a.name.localeCompare(b.name, "zh"));
});

function kindShort(kind) {
  return elementKindLabel(kind);
}

function linkLabel(id) {
  const found = items.value.find((i) => i.id === id);
  return found?.name || id;
}

function draftFromItem(item) {
  const copy = {
    ...item,
    tags: [...(item.tags || [])],
    link_ids: [...(item.link_ids || [])],
    knowledge_folder_ids: [...(item.knowledge_folder_ids || [])],
    kv_pairs: kvPairsFromElement(item).map((p) => ({ ...p })),
  };
  return copy;
}

async function load() {
  const data = await request("/api/writing/element-library");
  items.value = data.items || [];
  allTags.value = data.tags || [];
  rebuildGraph();
}

function rebuildGraph() {
  const list = filteredItems.value;
  const byCat = new Map();
  list.forEach((item) => {
    const cat = (item.category || "").trim() || "未分类";
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat).push(item);
  });
  const positions = new Map();
  let catIndex = 0;
  for (const [, group] of byCat) {
    group.forEach((item, index) => {
      const col = index % 3;
      const row = Math.floor(index / 3);
      positions.set(item.id, {
        x: 40 + catIndex * (SIZE + GAP + 80) + col * (SIZE + GAP),
        y: 40 + row * (SIZE + GAP),
      });
    });
    catIndex += 1;
  }
  flowNodes.value = list.map((item) => ({
    id: item.id,
    type: "element",
    position: positions.get(item.id) || { x: 40, y: 40 },
    data: {
      name: item.name,
      title: item.name,
      kind: item.kind,
      category: item.category,
    },
    class: focusItemId.value === item.id ? "node-focused" : "",
  }));
  const edges = [];
  const seen = new Set();
  for (const item of list) {
    for (const tid of item.link_ids || []) {
      if (!list.some((x) => x.id === tid)) continue;
      const key = [item.id, tid].sort().join("->");
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({
        id: `link:${key}`,
        source: item.id,
        target: tid,
        type: "smoothstep",
      });
    }
  }
  flowEdges.value = edges;
}

watch([filteredItems, focusItemId], () => rebuildGraph());

function blankItem() {
  const item = blankElementDraft("kv", { library: true });
  item.name = "新要素";
  item.category = filterCategory.value || "";
  return item;
}

function openCreate() {
  draft.value = blankItem();
  dialogVisible.value = true;
}

function onNodeClick({ node }) {
  const found = items.value.find((i) => i.id === node.id);
  if (found) {
    focusItemId.value = found.id;
    draft.value = draftFromItem(found);
    dialogVisible.value = true;
  }
}

function focusItem(id) {
  focusItemId.value = id;
  nextTick(() => {
    const node = flowNodes.value.find((n) => n.id === id);
    if (node?.position) {
      try {
        setCenter(node.position.x + SIZE / 2, node.position.y + SIZE / 2, { zoom: 1.1, duration: 300 });
      } catch {
        fitView({ padding: 0.25, duration: 200 });
      }
    }
  });
}

function onConnect(conn) {
  if (!conn.source || !conn.target || conn.source === conn.target) return;
  const a = items.value.find((i) => i.id === conn.source);
  const b = items.value.find((i) => i.id === conn.target);
  if (!a || !b) return;
  if (!a.link_ids.includes(b.id)) a.link_ids = [...(a.link_ids || []), b.id];
  if (!b.link_ids.includes(a.id)) b.link_ids = [...(b.link_ids || []), a.id];
  saveAll();
}

function normalizeNameKey(name) {
  return String(name || "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function applyDraft() {
  if (!draft.value?.name?.trim() && draft.value?.kind !== "kv") {
    ElMessage.warning("请填写名称");
    return;
  }
  let copy = {
    ...draft.value,
    tags: [...(draft.value.tags || [])],
    link_ids: [...(draft.value.link_ids || [])],
    knowledge_folder_ids: normalizeIdList(draft.value.knowledge_folder_ids),
  };
  if (copy.kind === "kv") {
    copy = normalizeKvElementDraft(copy);
    if (!copy.kv_pairs?.some((p) => p.key?.trim())) {
      ElMessage.warning("请至少填写一行键值");
      return;
    }
    if (!copy.name?.trim()) copy.name = copy.kv_pairs[0].key;
  }
  const nameKey = normalizeNameKey(copy.name);
  if (!nameKey) {
    ElMessage.warning("请填写要素名称");
    return;
  }
  const dup = items.value.find(
    (i) => i.id !== copy.id && normalizeNameKey(i.name) === nameKey
  );
  if (dup) {
    ElMessage.warning(`要素库已有同名「${dup.name}」，请换一个名称`);
    return;
  }
  const cat = String(copy.category || "").trim();
  if (cat) {
    const existCat = categoryRows.value.find(
      (r) => r.name !== "未分类" && r.name.toLowerCase() === cat.toLowerCase()
    );
    if (existCat) copy.category = existCat.name;
  }
  const idx = items.value.findIndex((i) => i.id === copy.id);
  if (idx >= 0) items.value[idx] = copy;
  else items.value.push(copy);
  dialogVisible.value = false;
  saveAll();
}

async function removeCurrent() {
  if (!draft.value?.id) return;
  try {
    await request(`/api/writing/element-library/items/${encodeURIComponent(draft.value.id)}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    items.value = items.value.filter((i) => i.id !== draft.value.id);
    dialogVisible.value = false;
    ElMessage.success("已删除");
    await load();
  } catch (e) {
    ElMessage.error(e.message || "删除失败");
  }
}

async function saveAll() {
  saving.value = true;
  try {
    const payload = items.value.map((item) => {
      if (item.kind === "kv") {
        return normalizeKvElementDraft({ ...item, kv_pairs: [...(item.kv_pairs || [])] });
      }
      if (item.kind === "rag_search" || item.kind === "file") {
        return {
          ...item,
          kind: "file",
          files: Array.isArray(item.files) ? item.files : [],
          knowledge_folder_ids: [],
        };
      }
      return item;
    });
    const data = await request("/api/writing/element-library/items", {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ items: payload }),
    });
    items.value = data.items || items.value;
    await load();
    ElMessage.success("已保存");
  } catch (e) {
    ElMessage.error(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  load().catch((e) => ElMessage.error(e.message || "加载失败"));
});
</script>

<style scoped>
.element-library-page {
  padding: 16px 20px 24px;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}
.page-head h1 {
  margin: 0;
  font-size: 20px;
}
.library-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 12px;
}
.category-panel {
  border: 1px solid var(--ay-line);
  border-radius: 12px;
  background: #fafbfc;
  padding: 10px 8px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.panel-head {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 4px 8px;
}
.panel-title {
  font-weight: 600;
  font-size: 14px;
}
.cat-row,
.item-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  cursor: pointer;
  text-align: left;
}
.cat-row:hover,
.item-row:hover {
  background: #eef2f7;
}
.cat-row.active,
.item-row.active {
  background: var(--ay-blue-soft);
  color: var(--ay-blue);
}
.count {
  font-size: 12px;
  color: var(--ay-muted);
}
.panel-divider {
  margin-top: 8px;
  padding: 8px 6px 4px;
  font-size: 12px;
  color: var(--ay-muted);
  border-top: 1px solid var(--ay-line);
}
.item-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.panel-empty {
  font-size: 12px;
  color: var(--ay-muted);
  padding: 8px;
}
.canvas-wrap {
  min-height: 420px;
  border: 1px solid var(--ay-line);
  border-radius: 12px;
  overflow: hidden;
  position: relative;
}
.canvas-wrap :deep(.vue-flow) {
  height: 100%;
  min-height: 420px;
}
.canvas-wrap :deep(.node-focused) {
  filter: drop-shadow(0 0 6px rgba(64, 158, 255, 0.55));
}
.empty-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
</style>
