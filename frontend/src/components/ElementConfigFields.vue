<template>
  <div v-if="model" class="element-config-fields">
    <el-row v-if="showLibraryMeta" :gutter="12">
      <el-col :span="12">
        <el-form-item label="分类">
          <el-autocomplete
            v-model="model.category"
            :fetch-suggestions="queryCategory"
            clearable
            placeholder="如：个人信息（可选用已有）"
            style="width: 100%"
            popper-class="rw-element-config-popper"
          />
        </el-form-item>
      </el-col>
      <el-col :span="12">
        <el-form-item label="标签">
          <el-select
            v-model="model.tags"
            multiple
            filterable
            allow-create
            default-first-option
            teleported
            style="width: 100%"
            popper-class="rw-element-config-popper"
          >
            <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
      </el-col>
    </el-row>
    <el-form-item v-if="showLibraryMeta" label="作用说明">
      <el-input v-model="model.purpose" type="textarea" :rows="2" placeholder="这个要素在写作里解决什么问题" />
    </el-form-item>

    <el-form-item label="名称">
      <el-input v-model="model.name" placeholder="要素在拓扑上的显示名" />
    </el-form-item>
    <el-form-item label="类型">
      <el-select
        v-model="model.kind"
        teleported
        style="width: 100%"
        popper-class="rw-element-config-popper"
        @change="onKindChange"
      >
        <el-option label="键值对" value="kv" />
        <el-option label="固定值" value="fixed" />
        <el-option label="文件" value="file" />
        <el-option label="引用槽位" value="file_slot" />
        <el-option label="LLM 生成" value="llm_generate" />
        <el-option label="图片" value="image" />
        <el-option v-if="showNodeRef" label="引用结果" value="node_ref" />
      </el-select>
    </el-form-item>

    <el-form-item v-if="model.kind === 'kv'" label="键值对列表" class="kv-form-item">
      <p class="field-hint">每行一条；值支持多行大段文本，写作时合并为要素内容。</p>
      <div class="kv-list">
        <article v-for="(row, index) in model.kv_pairs" :key="index" class="kv-list-item">
          <header class="kv-list-item-head">
            <span class="kv-list-index">{{ index + 1 }}</span>
            <el-button
              text
              type="danger"
              size="small"
              :disabled="model.kv_pairs.length <= 1"
              @click="removeKvRow(index)"
            >
              删除
            </el-button>
          </header>
          <div class="kv-list-item-body">
            <div class="kv-field">
              <label class="kv-field-label">键</label>
              <el-input v-model="row.key" placeholder="如：个人简介" />
            </div>
            <div class="kv-field kv-field-value">
              <label class="kv-field-label">值</label>
              <el-input
                v-model="row.value"
                type="textarea"
                :autosize="{ minRows: 4, maxRows: 16 }"
                placeholder="可粘贴长段落、列表说明等"
              />
            </div>
          </div>
        </article>
        <el-button class="kv-list-add" plain type="primary" @click="addKvRow">+ 添加一行</el-button>
      </div>
    </el-form-item>

    <template v-else-if="isFileKind">
      <el-form-item label="检索语句（可选）">
        <el-input
          v-model="model.query"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          placeholder="写作时在已解析文件中检索；留空则取文首摘要"
        />
      </el-form-item>
      <el-form-item label="对应文件" class="kv-form-item">
        <p class="field-hint warn">
          文件类型必须上传对应文件（可多份）。上传后不会自动解析；请点击「解析」，解析完成后才能读取相关内容。
        </p>
        <div class="file-attach-list">
          <article v-for="(row, index) in model.files || []" :key="row.file_id || index" class="file-attach-item">
            <div class="file-attach-meta">
              <strong>{{ row.file_name || row.file_id }}</strong>
              <span class="parse-status" :class="row.parse_status || 'pending'">
                {{ parseStatusLabel(row.parse_status) }}
              </span>
              <span v-if="row.parse_error" class="field-hint warn">{{ row.parse_error }}</span>
            </div>
            <div class="file-attach-actions">
              <el-button
                size="small"
                type="primary"
                plain
                :loading="parsingId === row.file_id"
                :disabled="!row.file_id || busy"
                @click="parseOne(row)"
              >
                {{ row.parse_status === "parsed" ? "重新解析" : "解析" }}
              </el-button>
              <el-button size="small" text type="danger" :disabled="busy" @click="removeAttached(index)">
                删除
              </el-button>
            </div>
          </article>
        </div>
        <label class="file-upload-btn" :class="{ disabled: busy }">
          {{ uploading ? "上传中…" : "+ 上传文件" }}
          <input
            type="file"
            multiple
            hidden
            accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.md"
            :disabled="busy"
            @change="onUploadFiles"
          />
        </label>
      </el-form-item>
    </template>

    <el-form-item v-if="model.kind === 'file_slot'" label="引用槽位" class="kv-form-item">
      <p class="field-hint">
        从资料包已规划的槽位中下拉选择，再填写检索词（写作时在该槽文件中检索取摘录）。槽位文件请在文档窗资料包区上传/解析。
      </p>
      <p v-if="!packSlotOptions.length" class="field-hint warn">
        当前没有可选槽位：请先在文档窗绑定资料包并添加槽位。
      </p>
      <div class="slot-ref-list">
        <article v-for="(row, index) in model.file_slots || []" :key="`fs-${index}`" class="slot-ref-item">
          <header class="slot-ref-head">
            <span class="kv-list-index">引用 {{ index + 1 }}</span>
            <el-button
              text
              type="danger"
              size="small"
              :disabled="(model.file_slots || []).length <= 1"
              @click="removeFileSlotAt(index)"
            >
              删除
            </el-button>
          </header>
          <div class="slot-ref-body">
            <div class="kv-field">
              <label class="kv-field-label">资料包槽位</label>
              <el-select
                :model-value="Number(row.no) > 0 ? Number(row.no) : undefined"
                filterable
                clearable
                teleported
                append-to-body
                style="width: 100%"
                placeholder="选择资料包槽位"
                popper-class="rw-element-config-popper"
                :disabled="!packSlotSelectOptions(index).length"
                @change="(val) => onPickPackSlot(index, val)"
              >
                <el-option
                  v-for="opt in packSlotSelectOptions(index)"
                  :key="opt.no"
                  :label="opt.label"
                  :value="opt.no"
                  :disabled="opt.disabled"
                />
              </el-select>
            </div>
            <div class="kv-field kv-field-value">
              <label class="kv-field-label">检索词</label>
              <el-input
                :model-value="row.query"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 6 }"
                placeholder="写作时在该槽文件中检索"
                @update:model-value="(v) => onSlotQueryInput(index, v)"
              />
            </div>
          </div>
        </article>
        <el-button
          class="kv-list-add"
          plain
          type="primary"
          :disabled="!canAddSlotRef"
          @click="addFileSlot"
        >
          + 添加引用
        </el-button>
      </div>
    </el-form-item>

    <el-form-item v-else-if="model.kind === 'llm_generate'" label="生成提示词">
      <el-input v-model="model.prompt" type="textarea" :rows="3" />
    </el-form-item>

    <template v-else-if="model.kind === 'image'">
      <el-form-item label="图片来源">
        <el-radio-group v-model="model.image_source">
          <el-radio-button value="upload">上传固定图</el-radio-button>
          <el-radio-button value="llm">文生图</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="model.image_source === 'llm'" label="画面描述">
        <el-input
          v-model="model.prompt"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 8 }"
          placeholder="描述要生成的插图内容、风格、构图等"
        />
      </el-form-item>
      <el-form-item label="图片文件">
        <p v-if="model.image_path" class="field-hint">已保存：{{ model.image_path }}</p>
        <div class="image-el-actions">
          <label v-if="model.image_source === 'upload'" class="file-upload-btn" :class="{ disabled: busy || !canPersistImage }">
            {{ imageUploading ? "上传中…" : "+ 上传图片" }}
            <input
              type="file"
              hidden
              accept=".png,.jpg,.jpeg,.webp,.gif"
              :disabled="busy || !canPersistImage"
              @change="onUploadImage"
            />
          </label>
          <el-button
            v-if="model.image_source === 'llm'"
            type="primary"
            plain
            :loading="imageGenerating"
            :disabled="busy || !canPersistImage || !String(model.prompt || '').trim()"
            @click="onGenerateImage"
          >
            生成并保存
          </el-button>
          <img
            v-if="imagePreviewUrl"
            class="image-el-preview"
            :src="imagePreviewUrl"
            alt="要素图片预览"
          />
        </div>
        <p v-if="!canPersistImage" class="field-hint warn">请先保存要素以取得 id，再上传或文生图。</p>
        <p class="field-hint">写作时在正文中原样输出占位符 <code v-pre>{{img:要素id}}</code>，预览与 Word 会替换为图片。</p>
      </el-form-item>
    </template>

    <el-form-item v-else-if="model.kind === 'fixed'" label="固定值">
      <el-input v-model="model.value" type="textarea" :autosize="{ minRows: 2, maxRows: 12 }" />
    </el-form-item>
    <el-form-item v-else-if="model.kind === 'node_ref' && showNodeRef" label="引用要素结果">
      <p class="field-hint">使用所选要素的写作结果（result）；若尚未产出，则按该要素类型解析内容。</p>
      <div
        v-for="(pick, pi) in model.ref_element_picks || []"
        :key="`rp-${pi}-${pick.query}`"
        class="folder-pick-block"
      >
        <p class="pick-title">要素「{{ pick.query }}」有多项匹配，请选择：</p>
        <el-radio-group v-model="pick.selected_id" class="pick-radio" @change="() => onRefPickSelected(pick)">
          <el-radio v-for="c in pick.candidates" :key="c.id" :value="c.id">
            {{ c.label || c.name || c.id }}
          </el-radio>
        </el-radio-group>
      </div>
      <el-select
        v-model="model.ref_key"
        filterable
        clearable
        teleported
        style="width: 100%"
        placeholder="选择本任务要素"
        popper-class="rw-element-config-popper"
      >
        <el-option v-for="item in refTargetOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
    </el-form-item>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import { authHeaders, request } from "../api";
import { kvPairsFromElement } from "../elementUi";

const model = defineModel({ type: Object, required: true });

const props = defineProps({
  tagOptions: { type: Array, default: () => [] },
  categoryOptions: { type: Array, default: () => [] },
  showLibraryMeta: { type: Boolean, default: false },
  showNodeRef: { type: Boolean, default: true },
  refTargetOptions: { type: Array, default: () => [] },
  /** 资料包槽位选项：[{ no, name }]，要素引用槽位从此下拉 */
  packSlotOptions: { type: Array, default: () => [] },
  taskId: { type: String, default: "" },
  elementId: { type: String, default: "" },
});

const imageUploading = ref(false);
const imageGenerating = ref(false);

const canPersistImage = computed(() => {
  const tid = String(props.taskId || "").trim();
  const eid = String(props.elementId || model.value?.id || "").trim();
  return Boolean(tid && eid);
});

const imagePreviewUrl = computed(() => {
  const tid = String(props.taskId || "").trim();
  const eid = String(props.elementId || model.value?.id || "").trim();
  if (!tid || !eid || !String(model.value?.image_path || "").trim()) return "";
  const bust = encodeURIComponent(String(model.value.image_path));
  return `/api/writing/tasks/${encodeURIComponent(tid)}/element-images/${encodeURIComponent(eid)}?v=${bust}`;
});

async function onUploadImage(event) {
  const file = event?.target?.files?.[0];
  if (event?.target) event.target.value = "";
  if (!file || !canPersistImage.value) return;
  const tid = String(props.taskId || "").trim();
  const eid = String(props.elementId || model.value?.id || "").trim();
  imageUploading.value = true;
  try {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch(
      `/api/writing/tasks/${encodeURIComponent(tid)}/elements/${encodeURIComponent(eid)}/image/upload`,
      { method: "POST", headers: authHeaders(), body }
    );
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (data?.image_path) model.value.image_path = data.image_path;
    model.value.image_source = "upload";
    model.value.kind = "image";
    ElMessage.success("图片已上传");
  } catch (e) {
    ElMessage.error(e?.message || "上传失败");
  } finally {
    imageUploading.value = false;
  }
}

async function onGenerateImage() {
  if (!canPersistImage.value) return;
  const tid = String(props.taskId || "").trim();
  const eid = String(props.elementId || model.value?.id || "").trim();
  imageGenerating.value = true;
  try {
    const data = await request(
      `/api/writing/tasks/${encodeURIComponent(tid)}/elements/${encodeURIComponent(eid)}/image/generate`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ prompt: String(model.value?.prompt || "").trim() }),
      }
    );
    if (data?.image_path) model.value.image_path = data.image_path;
    model.value.image_source = "llm";
    model.value.kind = "image";
    ElMessage.success("文生图已保存");
  } catch (e) {
    ElMessage.error(e?.message || "生成失败");
  } finally {
    imageGenerating.value = false;
  }
}

const uploading = ref(false);
const parsingId = ref("");
const busy = computed(() => uploading.value || Boolean(parsingId.value));
const isFileKind = computed(() => {
  const kind = String(model.value?.kind || "").trim();
  return kind === "file" || kind === "rag_search";
});

const packSlotOptions = computed(() => {
  const seen = new Set();
  const out = [];
  for (const s of props.packSlotOptions || []) {
    const no = Number(s?.no) > 0 ? Number(s.no) : 0;
    if (no < 1 || seen.has(no)) continue;
    seen.add(no);
    out.push({
      no,
      name: String(s?.name || "").trim(),
    });
  }
  return out;
});

const canAddSlotRef = computed(() => {
  if (!packSlotOptions.value.length) return false;
  const used = new Set(
    (model.value?.file_slots || [])
      .map((r) => Number(r?.no) || 0)
      .filter((n) => n > 0)
  );
  return packSlotOptions.value.some((o) => !used.has(o.no));
});

function packSlotLabel(opt) {
  const name = String(opt?.name || "").trim();
  return name ? `槽位 ${opt.no} · ${name}` : `槽位 ${opt.no}`;
}

function packSlotSelectOptions(rowIndex) {
  const used = new Set(
    (model.value?.file_slots || [])
      .map((r, i) => (i === rowIndex ? 0 : Number(r?.no) || 0))
      .filter((n) => n > 0)
  );
  const byNo = new Map();
  for (const opt of packSlotOptions.value) {
    byNo.set(opt.no, {
      no: opt.no,
      label: packSlotLabel(opt),
      disabled: used.has(opt.no),
    });
  }
  // 已保存但不在当前资料包选项里的引用，仍要能回显
  for (const [i, row] of (model.value?.file_slots || []).entries()) {
    const no = Number(row?.no) > 0 ? Number(row.no) : 0;
    if (no < 1 || byNo.has(no)) continue;
    byNo.set(no, {
      no,
      label: `${packSlotLabel({ no, name: row?.name })}（当前引用）`,
      disabled: i !== rowIndex && used.has(no),
    });
  }
  const ordered = [];
  const seen = new Set();
  for (const opt of packSlotOptions.value) {
    if (!byNo.has(opt.no) || seen.has(opt.no)) continue;
    ordered.push(byNo.get(opt.no));
    seen.add(opt.no);
  }
  for (const opt of byNo.values()) {
    if (seen.has(opt.no)) continue;
    ordered.push(opt);
  }
  return ordered;
}

function queryCategory(query, cb) {
  const q = String(query || "").trim().toLowerCase();
  const list = (props.categoryOptions || [])
    .filter((c) => !q || String(c).toLowerCase().includes(q))
    .map((value) => ({ value }));
  cb(list);
}

function parseStatusLabel(status) {
  const s = String(status || "pending").trim();
  if (s === "parsed") return "已解析";
  if (s === "error") return "解析失败";
  return "待解析";
}

function ensureKvPairs() {
  if (!model.value) return;
  if (model.value.kind === "rag_search") {
    model.value.kind = "file";
  }
  if (model.value.kind === "kv") {
    if (!Array.isArray(model.value.kv_pairs) || !model.value.kv_pairs.length) {
      model.value.kv_pairs = kvPairsFromElement(model.value).map((p) => ({ ...p }));
    }
  }
  if (!Array.isArray(model.value.tags)) model.value.tags = [];
  if (!Array.isArray(model.value.files)) model.value.files = [];
  if (!Array.isArray(model.value.file_slots)) model.value.file_slots = [];
  if (!Array.isArray(model.value.material_files)) model.value.material_files = [];
  ensureFileSlots();
}

function ensureFileSlots() {
  if (!model.value || model.value.kind !== "file_slot") return;
  if (!Array.isArray(model.value.file_slots) || !model.value.file_slots.length) {
    model.value.file_slots = [{ no: 0, name: "", query: "" }];
  } else {
    model.value.file_slots = model.value.file_slots.map((row) => ({
      no: Number(row?.no) > 0 ? Number(row.no) : 0,
      name: String(row?.name || ""),
      query: String(row?.query || ""),
    }));
  }
}

watch(model, ensureKvPairs, { immediate: true, deep: false });

function onKindChange(kind) {
  if (!model.value) return;
  if (kind === "rag_search") {
    model.value.kind = "file";
  }
  if (kind === "kv") ensureKvPairs();
  if (kind === "file" || kind === "rag_search") {
    if (!Array.isArray(model.value.files)) model.value.files = [];
  }
  if (kind === "file_slot") ensureFileSlots();
  if (kind === "image") {
    if (!model.value.image_source) model.value.image_source = "upload";
    if (model.value.image_path == null) model.value.image_path = "";
  }
  if (kind === "node_ref") {
    model.value.ref_kind = "element";
    model.value.ref_field = "result";
  }
}

function addKvRow() {
  ensureKvPairs();
  model.value.kv_pairs.push({ key: "", value: "" });
}

function removeKvRow(index) {
  model.value.kv_pairs.splice(index, 1);
}

function addFileSlot() {
  ensureFileSlots();
  if (!canAddSlotRef.value) return;
  model.value.file_slots.push({ no: 0, name: "", query: "" });
}

function removeFileSlotAt(index) {
  ensureFileSlots();
  model.value.file_slots.splice(index, 1);
  if (!model.value.file_slots.length) {
    model.value.file_slots = [{ no: 0, name: "", query: "" }];
  }
}

function onPickPackSlot(index, val) {
  ensureFileSlots();
  const row = model.value.file_slots?.[index];
  if (!row) return;
  const no = Number(val) > 0 ? Number(val) : 0;
  if (!no) {
    row.no = 0;
    row.name = "";
    return;
  }
  const opt = packSlotOptions.value.find((o) => o.no === no);
  row.no = no;
  row.name = String(opt?.name || row.name || "").trim();
}

function onSlotQueryInput(index, value) {
  ensureFileSlots();
  const row = model.value.file_slots?.[index];
  if (!row) return;
  row.query = String(value ?? "");
}

async function onUploadFiles(event) {
  const list = [...(event?.target?.files || [])];
  if (event?.target) event.target.value = "";
  if (!list.length || !model.value) return;
  if (!Array.isArray(model.value.files)) model.value.files = [];
  uploading.value = true;
  try {
    for (const file of list) {
      const form = new FormData();
      form.append("file", file);
      const attached = await request("/api/writing/element-files", {
        method: "POST",
        headers: authHeaders(),
        body: form,
      });
      model.value.files.push(attached);
    }
    ElMessage.success(`已上传 ${list.length} 个文件（待解析）`);
  } catch (error) {
    ElMessage.error(error?.message || "上传失败");
  } finally {
    uploading.value = false;
  }
}

async function parseOne(row) {
  const fid = String(row?.file_id || "").trim();
  if (!fid || !model.value) return;
  parsingId.value = fid;
  try {
    const updated = await request(`/api/writing/element-files/${encodeURIComponent(fid)}/parse`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
    });
    const idx = (model.value.files || []).findIndex((f) => String(f.file_id) === fid);
    if (idx >= 0) model.value.files[idx] = updated;
    ElMessage.success(`「${updated.file_name || fid}」已解析`);
  } catch (error) {
    ElMessage.error(error?.message || "解析失败");
    const idx = (model.value.files || []).findIndex((f) => String(f.file_id) === fid);
    if (idx >= 0) {
      model.value.files[idx] = {
        ...model.value.files[idx],
        parse_status: "error",
        parse_error: error?.message || "解析失败",
      };
    }
  } finally {
    parsingId.value = "";
  }
}

async function removeAttached(index) {
  if (!model.value?.files?.[index]) return;
  const row = model.value.files[index];
  const fid = String(row.file_id || "").trim();
  model.value.files.splice(index, 1);
  if (!fid) return;
  try {
    await request(`/api/writing/element-files/${encodeURIComponent(fid)}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
  } catch {
    /* 本地已移除即可 */
  }
}

function onRefPickSelected(pick) {
  if (!model.value || !pick?.selected_id) return;
  model.value.ref_key = pick.selected_id;
}
</script>

<style scoped>
.field-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.field-hint.warn {
  color: var(--el-color-warning);
}
.kv-form-item :deep(.el-form-item__content) {
  width: 100%;
}
.kv-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.kv-list-item {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: #fafbfc;
  overflow: hidden;
}
.kv-list-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #f0f2f5;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}
.kv-list-index {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}
.kv-list-item-body {
  display: grid;
  grid-template-columns: minmax(120px, 32%) 1fr;
  gap: 12px;
  padding: 12px;
  align-items: start;
}
.slot-ref-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.slot-ref-item {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: #fafbfc;
  overflow: hidden;
}
.slot-ref-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #f0f2f5;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}
.slot-ref-body {
  display: grid;
  grid-template-columns: minmax(160px, 36%) 1fr;
  gap: 12px;
  padding: 12px;
  align-items: start;
}
.file-slot-body {
  grid-template-columns: 100px minmax(120px, 28%) 1fr;
}
.kv-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.kv-field-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.kv-field-value :deep(.el-textarea__inner) {
  font-size: 13px;
  line-height: 1.55;
}
.kv-list-add {
  width: 100%;
  border-style: dashed;
}
.folder-pick-block {
  margin-bottom: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f5f7fa;
  border: 1px solid var(--el-border-color-extra-light);
}
.pick-title {
  margin: 0 0 8px;
  font-size: 13px;
}
.pick-radio {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.file-attach-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}
.file-attach-item {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: #fafbfc;
}
.file-attach-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.parse-status {
  font-size: 12px;
  color: #94a3b8;
}
.parse-status.parsed {
  color: #16a34a;
}
.parse-status.error {
  color: #dc2626;
}
.file-attach-actions {
  display: flex;
  gap: 4px;
  align-items: center;
}
.file-upload-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding: 8px 12px;
  border: 1px dashed var(--el-border-color);
  border-radius: 8px;
  color: var(--el-color-primary);
  cursor: pointer;
  font-size: 13px;
}
.file-upload-btn.disabled {
  opacity: 0.55;
  pointer-events: none;
}
.image-el-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
}
.image-el-preview {
  max-width: 240px;
  max-height: 180px;
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
  object-fit: contain;
}
</style>

<!-- 下拉挂到 body，须高于「配置新要素」弹窗(5200)，否则类型选项被挡住点不到 -->
<style>
.rw-element-config-popper.el-popper,
.rw-element-config-popper {
  z-index: 5600 !important;
}
</style>
