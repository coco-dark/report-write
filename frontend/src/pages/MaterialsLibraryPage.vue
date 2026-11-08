<template>
  <div class="ml">
    <header class="ml-hero">
      <div class="ml-hero-text">
        <h1>资料库</h1>
        <p>
          资料库是放资料包的地方。入库后可被多文档引用。资料包规划槽位、上传并解析文件；检索词在要素「引用槽位」上配置。
        </p>
      </div>
      <div class="ml-hero-actions">
        <button type="button" class="btn ghost" :disabled="loading" @click="reload">
          {{ loading ? "刷新中…" : "刷新" }}
        </button>
        <button type="button" class="btn primary" @click="createPack">新建资料包</button>
      </div>
    </header>

    <div class="ml-body">
      <section class="ml-section">
        <div class="section-head">
          <h2>已入库</h2>
          <span class="count">{{ libraryPacks.length }}</span>
        </div>
        <div v-if="!libraryPacks.length" class="empty">还没有已入库资料包。确认待入库或新建后勾选入库。</div>
        <ul v-else class="pack-list">
          <li v-for="pack in libraryPacks" :key="pack.id" class="pack-card">
            <div class="pack-main">
              <strong>{{ pack.name || pack.id }}</strong>
              <span class="muted">
                {{ pack.id }} · {{ slotCount(pack) }} 个槽位 · 已填 {{ filledCount(pack) }}
              </span>
            </div>
            <div class="pack-actions">
              <button type="button" class="btn soft" @click="toggleExpand(pack.id)">
                {{ expandedId === pack.id ? "收起槽位" : "管理槽位" }}
              </button>
              <button type="button" class="btn soft" @click="renamePack(pack)">改名</button>
              <button type="button" class="btn soft" @click="setLibrary(pack, false)">移出资料库</button>
              <button type="button" class="btn danger-soft" @click="removePack(pack)">删除</button>
            </div>
            <div v-if="expandedId === pack.id" class="pack-slots">
              <MaterialsSlotCards
                :rows="rowsForPack(pack)"
                can-upload
                can-clear
                can-parse
                :show-query="false"
                :disabled="busy"
                @upload="(no, e) => uploadSlot(pack, no, e)"
                @clear="(no) => clearSlot(pack, no)"
                @parse="(no) => parseSlot(pack, no)"
              />
            </div>
          </li>
        </ul>
      </section>

      <section class="ml-section">
        <div class="section-head">
          <h2>待入库</h2>
          <span class="count">{{ pendingPacks.length }}</span>
        </div>
        <div v-if="!pendingPacks.length" class="empty">没有待入库资料包。</div>
        <ul v-else class="pack-list">
          <li v-for="pack in pendingPacks" :key="pack.id" class="pack-card pending">
            <div class="pack-main">
              <strong>{{ pack.name || pack.id }}</strong>
              <span class="muted">
                {{ pack.id }} · {{ slotCount(pack) }} 个槽位 · 已填 {{ filledCount(pack) }}
              </span>
            </div>
            <div class="pack-actions">
              <button type="button" class="btn soft" @click="toggleExpand(pack.id)">
                {{ expandedId === pack.id ? "收起槽位" : "管理槽位" }}
              </button>
              <button type="button" class="btn primary" @click="setLibrary(pack, true)">确认入库</button>
              <button type="button" class="btn soft" @click="renamePack(pack)">改名</button>
              <button type="button" class="btn danger-soft" @click="removePack(pack)">删除</button>
            </div>
            <div v-if="expandedId === pack.id" class="pack-slots">
              <MaterialsSlotCards
                :rows="rowsForPack(pack)"
                can-upload
                can-clear
                can-parse
                :show-query="false"
                :disabled="busy"
                @upload="(no, e) => uploadSlot(pack, no, e)"
                @clear="(no) => clearSlot(pack, no)"
                @parse="(no) => parseSlot(pack, no)"
              />
            </div>
          </li>
        </ul>
        <div v-if="pendingPacks.length" class="bulk">
          <button type="button" class="btn soft" @click="confirmAllPending">全部确认入库</button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "../ui.js";

import { authHeaders, request } from "../api";
import MaterialsSlotCards from "../components/MaterialsSlotCards.vue";

const loading = ref(false);
const busy = ref(false);
const libraryPacks = ref([]);
const pendingPacks = ref([]);
const expandedId = ref("");

function slotCount(pack) {
  const schema = Array.isArray(pack?.slot_schema) ? pack.slot_schema.length : 0;
  const fills = Array.isArray(pack?.slots_fill) ? pack.slots_fill.length : 0;
  return Math.max(schema, fills);
}

function filledCount(pack) {
  return (pack?.slots_fill || []).filter((f) => f?.file_name || f?.file_id).length;
}

function rowsForPack(pack) {
  const byNo = new Map();
  for (const s of pack?.slot_schema || []) {
    const no = Number(s?.no) > 0 ? Number(s.no) : 0;
    if (no < 1) continue;
    byNo.set(no, {
      no,
      name: String(s?.name || ""),
      query: "",
      file_name: "",
      file_id: "",
      parse_status: "",
      parse_error: "",
    });
  }
  for (const f of pack?.slots_fill || []) {
    const no = Number(f?.slot_no) > 0 ? Number(f.slot_no) : 0;
    if (no < 1) continue;
    const prev = byNo.get(no) || {
      no,
      name: "",
      query: "",
      file_name: "",
      file_id: "",
      parse_status: "",
      parse_error: "",
    };
    prev.file_name = String(f?.file_name || "");
    prev.file_id = String(f?.file_id || "");
    prev.parse_status = String(f?.parse_status || "");
    prev.parse_error = String(f?.parse_error || "");
    byNo.set(no, prev);
  }
  return [...byNo.values()].sort((a, b) => a.no - b.no);
}

function replacePack(updated) {
  if (!updated?.id) return;
  const map = (list) => list.map((p) => (p.id === updated.id ? updated : p));
  libraryPacks.value = map(libraryPacks.value);
  pendingPacks.value = map(pendingPacks.value);
}

function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? "" : id;
}

async function reload() {
  loading.value = true;
  try {
    const [lib, pending] = await Promise.all([
      request("/api/writing/materials-packs?in_library=true"),
      request("/api/writing/materials-packs?in_library=false"),
    ]);
    libraryPacks.value = Array.isArray(lib?.items) ? lib.items : [];
    pendingPacks.value = Array.isArray(pending?.items) ? pending.items : [];
  } catch (error) {
    ElMessage.error(error?.message || "加载资料库失败");
  } finally {
    loading.value = false;
  }
}

async function createPack() {
  let name = "";
  try {
    const { value } = await ElMessageBox.prompt("资料包名称（新建默认待入库）", "新建资料包", {
      confirmButtonText: "创建",
      cancelButtonText: "取消",
      inputPlaceholder: "如：某某项目资料",
    });
    name = String(value || "").trim() || "未命名资料包";
  } catch {
    return;
  }
  try {
    await request("/api/writing/materials-packs", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name, in_library: false }),
    });
    ElMessage.success("已创建待入库资料包");
    await reload();
  } catch (error) {
    ElMessage.error(error?.message || "创建失败");
  }
}

async function renamePack(pack) {
  let name = pack?.name || "";
  try {
    const { value } = await ElMessageBox.prompt("资料包名称", "改名", {
      confirmButtonText: "保存",
      cancelButtonText: "取消",
      inputValue: name,
    });
    name = String(value || "").trim();
  } catch {
    return;
  }
  if (!name) return;
  try {
    await request(`/api/writing/materials-packs/${encodeURIComponent(pack.id)}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name }),
    });
    await reload();
  } catch (error) {
    ElMessage.error(error?.message || "改名失败");
  }
}

async function setLibrary(pack, inLibrary) {
  try {
    await request(`/api/writing/materials-packs/${encodeURIComponent(pack.id)}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ in_library: Boolean(inLibrary) }),
    });
    ElMessage.success(inLibrary ? "已入库" : "已移出资料库");
    await reload();
  } catch (error) {
    ElMessage.error(error?.message || "更新失败");
  }
}

async function confirmAllPending() {
  const ids = pendingPacks.value.map((p) => p.id).filter(Boolean);
  if (!ids.length) return;
  try {
    await request("/api/writing/materials-packs/confirm-library", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ pack_ids: ids }),
    });
    ElMessage.success(`已入库 ${ids.length} 个资料包`);
    await reload();
  } catch (error) {
    ElMessage.error(error?.message || "批量入库失败");
  }
}

async function removePack(pack) {
  try {
    await ElMessageBox.confirm(
      `删除资料包「${pack.name || pack.id}」？绑定该包的文档将失去文件引用。`,
      "删除资料包",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  try {
    await request(`/api/writing/materials-packs/${encodeURIComponent(pack.id)}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    ElMessage.success("已删除");
    await reload();
  } catch (error) {
    ElMessage.error(error?.message || "删除失败");
  }
}

async function uploadSlot(pack, slotNo, event) {
  const file = event?.target?.files?.[0];
  if (event?.target) event.target.value = "";
  if (!pack?.id || !file) return;
  busy.value = true;
  try {
    const form = new FormData();
    form.append("file", file);
    const updated = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pack.id)}/slots/${encodeURIComponent(slotNo)}/files`,
      { method: "POST", headers: authHeaders(), body: form }
    );
    replacePack(updated);
    ElMessage.success(`槽位 ${slotNo} 已更新`);
  } catch (error) {
    ElMessage.error(error?.message || "上传失败");
  } finally {
    busy.value = false;
  }
}

async function parseSlot(pack, slotNo) {
  if (!pack?.id) return;
  busy.value = true;
  try {
    const updated = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pack.id)}/slots/${encodeURIComponent(slotNo)}/parse`,
      { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: "{}" }
    );
    replacePack(updated);
    ElMessage.success(`槽位 ${slotNo} 已解析`);
  } catch (error) {
    ElMessage.error(error?.message || "解析失败");
  } finally {
    busy.value = false;
  }
}

async function clearSlot(pack, slotNo) {
  if (!pack?.id) return;
  busy.value = true;
  try {
    const updated = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pack.id)}/slots/${encodeURIComponent(slotNo)}/files`,
      { method: "DELETE", headers: authHeaders() }
    );
    replacePack(updated);
    ElMessage.success(`已清除槽位 ${slotNo}`);
  } catch (error) {
    ElMessage.error(error?.message || "清除失败");
  } finally {
    busy.value = false;
  }
}

onMounted(reload);
</script>

<style scoped>
.ml {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 24px 28px;
  overflow: auto;
}
.ml-hero {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 20px;
}
.ml-hero-text h1 {
  margin: 0 0 6px;
  font-size: 22px;
}
.ml-hero-text p {
  margin: 0;
  max-width: 720px;
  color: var(--ay-muted, #64748b);
  font-size: 13px;
  line-height: 1.5;
}
.ml-hero-actions {
  display: flex;
  gap: 8px;
}
.btn {
  border: 1px solid #d0d7e2;
  background: #fff;
  border-radius: 999px;
  padding: 8px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn.primary {
  background: #3b5bdb;
  border-color: #3b5bdb;
  color: #fff;
}
.btn.ghost,
.btn.soft {
  background: #f8fafc;
}
.btn.danger-soft {
  color: #b91c1c;
  border-color: #fecaca;
  background: #fff5f5;
}
.ml-body {
  display: grid;
  gap: 18px;
}
.ml-section {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #fff;
  padding: 14px 16px 16px;
}
.section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.section-head h2 {
  margin: 0;
  font-size: 15px;
}
.count {
  font-size: 12px;
  color: #64748b;
  background: #f1f5f9;
  border-radius: 999px;
  padding: 2px 8px;
}
.empty {
  color: #94a3b8;
  font-size: 13px;
}
.pack-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pack-card {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 12px;
  background: #fafbfc;
}
.pack-card.pending {
  background: #fffbeb;
  border-color: #fde68a;
}
.pack-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}
.muted {
  color: #64748b;
  font-size: 12px;
}
.pack-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pack-slots {
  margin-top: 12px;
}
.bulk {
  margin-top: 12px;
}
</style>
