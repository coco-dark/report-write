<template>
  <div class="tpl-wizard">
    <ChatFlowPipeline :nodes="flowNodes" :current="step" />

    <div v-if="step === 'choose'" class="pane">
      <p class="hint">{{ card.payload?.message || "选择已有模版或新建。" }}</p>
      <div v-if="items.length" class="pick-list">
        <div v-for="item in items" :key="item.id" class="pick-row">
          <div class="pick-main">
            <strong>{{ item.name }}</strong>
            <span>{{ item.id }}</span>
          </div>
          <div class="pick-actions">
            <el-button size="small" round :disabled="disabled || busy" @click="pickExisting(item.id)">
              选用
            </el-button>
            <el-button size="small" round :disabled="disabled || busy" @click="editExisting(item.id)">
              编辑
            </el-button>
          </div>
        </div>
      </div>
      <el-button class="chat-action-btn" type="primary" round :disabled="disabled" @click="goEdit">
        新建{{ resourceLabel }}
      </el-button>
    </div>

    <div v-else-if="step === 'edit' && resource === 'render_template'" class="pane">
      <el-form label-position="top" size="small">
        <el-form-item label="名称">
          <el-input v-model="renderForm.name" :disabled="disabled" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="renderForm.desc" type="textarea" :rows="2" :disabled="disabled" />
        </el-form-item>
        <el-form-item label="模版 HTML（占位符 {{章节key}}）">
          <el-input v-model="renderForm.html" type="textarea" :rows="8" :disabled="disabled" />
        </el-form-item>
      </el-form>
      <el-button class="chat-action-btn" type="primary" round :loading="busy" :disabled="disabled" @click="saveRender">
        保存并绑定到机器人
      </el-button>
    </div>

    <div v-else-if="step === 'edit' && resource === 'word_style'" class="pane">
      <el-form label-position="top" size="small">
        <el-form-item label="名称">
          <el-input v-model="styleForm.name" :disabled="disabled" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="styleForm.desc" type="textarea" :rows="2" :disabled="disabled" />
        </el-form-item>
        <el-form-item label="正文字号 (pt)">
          <el-input-number v-model="styleForm.bodySize" :min="9" :max="18" :disabled="disabled" />
        </el-form-item>
        <el-form-item label="正文字体">
          <el-input v-model="styleForm.bodyFont" placeholder="宋体" :disabled="disabled" />
        </el-form-item>
      </el-form>
      <el-button class="chat-action-btn" type="primary" round :loading="busy" :disabled="disabled" @click="saveStyle">
        保存并绑定到机器人
      </el-button>
    </div>

    <div v-else-if="step === 'done'" class="pane">
      <p class="hint done-hint">✓ {{ resourceLabel }}已绑定。导出请点页面底部「下载 Word」，或下方按钮。</p>
      <el-button
        class="chat-action-btn"
        type="primary"
        round
        :disabled="disabled"
        @click="emit('export-word')"
      >
        下载 Word
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import { request } from "../api";
import ChatFlowPipeline from "./ChatFlowPipeline.vue";

const TPL_STEP_ORDER = ["choose", "edit", "done"];

function tplStepIndex(step) {
  const i = TPL_STEP_ORDER.indexOf(String(step || "").trim());
  return i >= 0 ? i : -1;
}

const props = defineProps({
  card: { type: Object, required: true },
  robotId: { type: String, default: "" },
  disabled: { type: Boolean, default: false },
  liveWizard: { type: Object, default: null },
});

const emit = defineEmits(["saved", "live-step", "export-word"]);

const busy = ref(false);
const items = ref([]);
const editingId = ref("");

const payload = computed(() => {
  const base = props.card?.payload || {};
  const live = props.liveWizard || {};
  const res = String(base.resource || "").trim();
  const liveRes = String(live.resource || "").trim();
  if (!res || (liveRes && liveRes !== res)) return base;
  if (!live.step) return base;
  const baseStep = String(base.step || "choose").trim();
  const liveStep = String(live.step).trim();
  const bi = tplStepIndex(baseStep);
  const li = tplStepIndex(liveStep);
  if (bi >= 0 && li > bi && !busy.value) {
    return { ...base, ...live, resource: res, step: baseStep };
  }
  return { ...base, ...live, resource: res };
});
const resource = computed(() => payload.value.resource || "");
const step = computed(() => payload.value.step || "choose");

function bumpLive(payloadPatch) {
  const res = String(resource.value || payloadPatch?.resource || "").trim();
  if (!res) return;
  emit("live-step", {
    resource: res,
    step: payloadPatch?.step || step.value,
    item_id: payloadPatch?.item_id || payload.value.item_id || "",
  });
}

const resourceLabel = computed(() =>
  resource.value === "word_style" ? "Word 样式" : "渲染模版"
);

const flowNodes = computed(() => {
  if (resource.value === "word_style") {
    return [
      { id: "choose", label: "选择" },
      { id: "edit", label: "配置" },
      { id: "done", label: "完成" },
    ];
  }
  return [
    { id: "choose", label: "选择" },
    { id: "edit", label: "编辑模版" },
    { id: "done", label: "完成" },
  ];
});

const renderForm = reactive({
  name: "对话创建 · 渲染模版",
  desc: "",
  html:
    "<h1>报告标题</h1>\n<h2>{{title:ch-1}}（{{seq:ch-1}}）</h2>\n{{ch-1}}\n",
});

const styleForm = reactive({
  name: "对话创建 · Word 样式",
  desc: "",
  bodySize: 12,
  bodyFont: "宋体",
});

onMounted(loadItems);

async function loadItems() {
  try {
    const rid = (props.robotId || "").trim();
    const q = rid ? `?robot_id=${encodeURIComponent(rid)}` : "";
    if (resource.value === "render_template") {
      const path = rid
        ? `/api/writing/robots/${encodeURIComponent(rid)}/render-templates`
        : `/api/writing/render-templates${q}`;
      items.value = (await request(path)) || [];
    } else if (resource.value === "word_style") {
      const path = rid
        ? `/api/writing/robots/${encodeURIComponent(rid)}/word-styles`
        : `/api/writing/word-styles${q}`;
      items.value = (await request(path)) || [];
    }
  } catch {
    items.value = [];
  }
}

function goEdit() {
  editingId.value = "";
  bumpLive({ step: "edit" });
  emit("saved", { resource: resource.value, step: "edit" });
}

async function editExisting(id) {
  if (!id) return;
  busy.value = true;
  editingId.value = id;
  try {
    if (resource.value === "render_template") {
      const tpl = await request(`/api/writing/render-templates/${encodeURIComponent(id)}`);
      renderForm.name = tpl?.name || id;
      renderForm.desc = tpl?.desc || "";
      renderForm.html = tpl?.html || renderForm.html;
    } else if (resource.value === "word_style") {
      const st = await request(`/api/writing/word-styles/${encodeURIComponent(id)}`);
      const settings = st?.settings || {};
      styleForm.name = st?.name || id;
      styleForm.desc = st?.desc || "";
      styleForm.bodySize = settings.body_pt || settings.bodySize || 12;
      styleForm.bodyFont = settings.body_font || settings.bodyFont || "宋体";
    }
    bumpLive({ step: "edit", item_id: id });
    emit("saved", { resource: resource.value, step: "edit", item_id: id });
  } catch (error) {
    ElMessage.error(error.message || "加载失败");
  } finally {
    busy.value = false;
  }
}

async function pickExisting(id) {
  if (!id || !props.robotId) return;
  busy.value = true;
  try {
    await bindToRobot(id);
    ElMessage.success("已绑定");
    bumpLive({ step: "done", item_id: id });
    emit("saved", { resource: resource.value, step: "done", item_id: id });
  } catch (error) {
    ElMessage.error(error.message || "绑定失败");
  } finally {
    busy.value = false;
  }
}

async function bindToRobot(itemId) {
  const robot = await request(`/api/writing/robots/${encodeURIComponent(props.robotId)}`);
  const field = resource.value === "word_style" ? "word_style_ids" : "render_template_ids";
  const ids = [...(robot[field] || [])];
  if (!ids.includes(itemId)) ids.push(itemId);
  await request(`/api/writing/robots/${encodeURIComponent(props.robotId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...robot, [field]: ids }),
  });
}

async function saveRender() {
  if (!renderForm.name.trim()) {
    ElMessage.warning("请填写名称");
    return;
  }
  busy.value = true;
  try {
    const id = editingId.value || payload.value.item_id || "";
    const saved = id
      ? await request(`/api/writing/render-templates/${encodeURIComponent(id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: renderForm.name.trim(),
            desc: renderForm.desc.trim(),
            html: renderForm.html,
          }),
        })
      : await request("/api/writing/render-templates", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: renderForm.name.trim(),
            desc: renderForm.desc.trim(),
            html: renderForm.html,
          }),
        });
    const newId = saved?.id || id;
    if (newId && props.robotId) await bindToRobot(newId);
    bumpLive({ step: "done", item_id: newId });
    emit("saved", { resource: resource.value, step: "done", item_id: newId });
    ElMessage.success("渲染模版已保存");
  } catch (error) {
    ElMessage.error(error.message || "保存失败");
  } finally {
    busy.value = false;
  }
}

async function saveStyle() {
  if (!styleForm.name.trim()) {
    ElMessage.warning("请填写名称");
    return;
  }
  busy.value = true;
  try {
    const id = editingId.value || payload.value.item_id || "";
    const saved = id
      ? await request(`/api/writing/word-styles/${encodeURIComponent(id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: styleForm.name.trim(),
            desc: styleForm.desc.trim(),
            settings: {
              body_font: styleForm.bodyFont || "宋体",
              body_pt: styleForm.bodySize || 12,
            },
          }),
        })
      : await request("/api/writing/word-styles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: styleForm.name.trim(),
            desc: styleForm.desc.trim(),
            settings: {
              body_font: styleForm.bodyFont || "宋体",
              body_pt: styleForm.bodySize || 12,
            },
          }),
        });
    const newId = saved?.id || id;
    if (newId && props.robotId) await bindToRobot(newId);
    bumpLive({ step: "done", item_id: newId });
    emit("saved", { resource: resource.value, step: "done", item_id: newId });
    ElMessage.success("Word 样式已保存");
  } catch (error) {
    ElMessage.error(error.message || "保存失败");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.hint {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--ay-muted);
}
.done-hint {
  color: #047857;
  font-weight: 500;
}
.pick-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
  max-height: 160px;
  overflow: auto;
}
.pick-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--ay-line);
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
}
.pick-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.pick-main span {
  font-size: 11px;
  color: var(--ay-muted);
}
.pick-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.chat-action-btn {
  margin-top: 6px;
}
</style>
