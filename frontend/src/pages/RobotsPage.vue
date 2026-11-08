<template>
  <div class="robots ay-page ay-page-scroll">
    <header class="ay-hero">
      <div>
        <h1>机器人</h1>
        <p>绑定蓝本、渲染模版、Word 样式、个人要素与资料库，以及对话 / 分析 / 写作提示词；对话页选择后统一使用。</p>
      </div>
      <a-button type="primary" @click="openCreate" shape="round">新建机器人</a-button>
    </header>

    <div v-if="listLoading" class="hint">加载中…</div>
    <div v-else class="grid">
      <article
        v-for="item in robots"
        :key="item.id"
        class="card"
        :class="{ active: selectedId === item.id }"
        @click="openEdit(item.id)"
      >
        <strong>{{ item.name }}</strong>
        <p>{{ item.description || "暂无说明" }}</p>
        <div class="meta">
          <span>蓝本 {{ item.blueprint_count }}</span>
          <span>渲染 {{ item.render_template_count }}</span>
          <span>样式 {{ item.word_style_count || 0 }}</span>
          <span>要素 {{ item.element_library_count || 0 }}</span>
          <span>资料 {{ item.materials_pack_count || 0 }}</span>
        </div>
      </article>
    </div>
    <a-empty v-if="!listLoading && !robots.length" description="还没有机器人，先新建一个" :image-style="{ height: 80 }"  />

    <a-modal
 v-model:open="dialog"
 :title="existing ? '编辑机器人' : '新建机器人'"
 width="780px"
 class="robot-dialog"
 @afterClose="onDialogClosed"
 destroy-on-close centered>
      <a-steps :current="step" class="robot-steps">
        <a-step title="基本信息"  />
        <a-step title="提示词"  />
        <a-step title="资源绑定"  />
      </a-steps>

      <div v-show="step === 0" class="step-pane">
        <a-form layout="vertical">
          <a-form-item label="名称" required>
            <a-input v-model:value="form.name" placeholder="如：安评写作助手" />
          </a-form-item>
          <a-form-item label="说明">
            <a-textarea v-model:value="form.description" :rows="3" placeholder="用途说明：何时选用这个机器人" />
          </a-form-item>
        </a-form>
      </div>

      <div v-show="step === 1" class="step-pane">
        <a-form layout="vertical">
          <a-form-item label="需求说明">
            <p class="hint tiny">用自然语言描述机器人要做什么、语气与边界；再点生成，由模型写出三套提示词，可继续手改。</p>
            <a-textarea
              v-model:value="promptDraft"
              :rows="4"
              placeholder="例：面向安全预评价报告，对话要引导用户选蓝本与目录；分析时提取写法要点不要抄专名；写作输出完整 HTML…"
            />
            <div class="gen-bar">
              <a-button type="primary" :loading="generating === 'all'" :disabled="!!generating" @click="generatePrompts('all')">
                生成全部提示词
              </a-button>
              <a-button :loading="generating === 'chat'" :disabled="!!generating" @click="generatePrompts('chat')">仅对话</a-button>
              <a-button :loading="generating === 'study'" :disabled="!!generating" @click="generatePrompts('study')">仅分析</a-button>
              <a-button :loading="generating === 'write'" :disabled="!!generating" @click="generatePrompts('write')">仅写作</a-button>
            </div>
          </a-form-item>
          <a-form-item label="对话提示词">
            <a-textarea v-model:value="form.chat_prompt" :rows="4" placeholder="空则使用系统默认对话提示" />
          </a-form-item>
          <a-form-item label="分析（study）提示词">
            <a-textarea v-model:value="form.study_prompt" :rows="4" placeholder="空则使用默认 study 指令" />
          </a-form-item>
          <a-form-item label="写作（write）提示词">
            <a-textarea v-model:value="form.write_prompt" :rows="4" placeholder="空则使用默认 write 指令" />
          </a-form-item>
        </a-form>
      </div>

      <div v-show="step === 2" class="step-pane">
        <a-form layout="vertical">
          <a-form-item label="绑定蓝本">
            <a-select v-model:value="form.blueprint_ids" allow-clear placeholder="多选蓝本" style="width: 100%" mode="multiple" show-search>
              <a-select-option
                v-for="item in blueprints"
                :key="item.id"
                :value="item.id">{{ item.title || item.id }}</a-select-option>
            </a-select>
          </a-form-item>
          <a-form-item label="绑定渲染模版">
            <p class="hint tiny">对话导出时仅展示此处勾选的渲染模版；不选则展示全部模版。</p>
            <a-select
 v-model:value="form.render_template_ids"
 allow-clear
 placeholder="多选渲染模版"
 style="width: 100%"
 mode="multiple" show-search>
              <a-select-option v-for="item in renderTemplates" :key="item.id" :value="item.id">{{ item.name }}</a-select-option>
            </a-select>
          </a-form-item>
          <a-form-item label="绑定 Word 样式">
            <p class="hint tiny">对话导出 Word 时仅展示此处勾选的样式；不选则展示全部样式。</p>
            <a-select
 v-model:value="form.word_style_ids"
 allow-clear
 placeholder="多选 Word 样式"
 style="width: 100%"
 mode="multiple" show-search>
              <a-select-option v-for="item in wordStyles" :key="item.id" :value="item.id">{{ item.name }}</a-select-option>
            </a-select>
          </a-form-item>
          <a-form-item label="绑定个人要素">
            <p class="hint tiny">对话关联要素、查看要素库时仅展示此处勾选；不选则选中该机器人时个人库为空（本任务要素仍可见）。</p>
            <a-select
 v-model:value="form.element_library_ids"
 allow-clear
 placeholder="多选要素库条目"
 style="width: 100%"
 mode="multiple" show-search>
              <a-select-option
                v-for="item in elementLibrary"
                :key="item.id"
                :value="item.id">{{ elementOptionLabel(item) }}</a-select-option>
            </a-select>
          </a-form-item>
          <a-form-item label="绑定资料库（资料包）">
            <p class="hint tiny">对话选资料包、查看资料库时仅展示已入库且勾选的资料包；不选则选中该机器人时资料库列表为空。</p>
            <a-select
 v-model:value="form.materials_pack_ids"
 allow-clear
 placeholder="多选已入库资料包"
 style="width: 100%"
 mode="multiple" show-search>
              <a-select-option
                v-for="item in materialsPacks"
                :key="item.id"
                :value="item.id">{{ item.name || item.id }}</a-select-option>
            </a-select>
          </a-form-item>
        </a-form>
      </div>

      <template #footer>
        <div class="footer-row">
          <a-button v-if="existing" :loading="busy" @click="remove" danger>删除</a-button>
          <div class="footer-spacer" />
          <a-button @click="dialog = false">取消</a-button>
          <a-button v-if="step > 0" :disabled="busy || !!generating" @click="step -= 1">上一步</a-button>
          <a-button v-if="step < 2" type="primary" @click="nextStep">下一步</a-button>
          <a-button v-else type="primary" :loading="busy" @click="save">保存</a-button>
        </div>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "../ui.js";

import { request } from "../api";

const robots = ref([]);
const blueprints = ref([]);
const renderTemplates = ref([]);
const wordStyles = ref([]);
const elementLibrary = ref([]);
const materialsPacks = ref([]);
const listLoading = ref(false);
const dialog = ref(false);
const busy = ref(false);
const generating = ref("");
const selectedId = ref("");
const existing = ref(false);
const step = ref(0);
const promptDraft = ref("");

const form = reactive(blankForm());

function blankForm() {
  return {
    id: "",
    name: "",
    description: "",
    chat_prompt: "",
    study_prompt: "",
    write_prompt: "",
    blueprint_ids: [],
    render_template_ids: [],
    word_style_ids: [],
    knowledge_folder_ids: [],
    element_library_ids: [],
    materials_pack_ids: [],
  };
}

function elementOptionLabel(item) {
  const name = item?.name || item?.id || "";
  const cat = item?.category ? ` · ${item.category}` : "";
  return `${name}${cat}`;
}

function applyForm(data = {}) {
  Object.assign(form, blankForm(), {
    id: data.id || "",
    name: data.name || "",
    description: data.description || "",
    chat_prompt: data.chat_prompt || "",
    study_prompt: data.study_prompt || "",
    write_prompt: data.write_prompt || "",
    blueprint_ids: [...(data.blueprint_ids || [])],
    render_template_ids: [...(data.render_template_ids || [])].map(String),
    word_style_ids: [...(data.word_style_ids || [])].map(String),
    knowledge_folder_ids: [...(data.knowledge_folder_ids || [])],
    element_library_ids: [...(data.element_library_ids || [])],
    materials_pack_ids: [...(data.materials_pack_ids || [])],
  });
}

function onDialogClosed() {
  step.value = 0;
  promptDraft.value = "";
  generating.value = "";
}

function nextStep() {
  if (step.value === 0 && !form.name.trim()) {
    ElMessage.warning("请填写名称");
    return;
  }
  step.value += 1;
}

async function reload() {
  listLoading.value = true;
  try {
    const [robotList, bpList, rtList, wsList, elLib, mpLib] = await Promise.all([
      request("/api/writing/robots"),
      request("/api/writing/blueprints"),
      request("/api/writing/render-templates"),
      request("/api/writing/word-styles"),
      request("/api/writing/element-library"),
      request("/api/writing/materials-packs?in_library=true"),
    ]);
    robots.value = robotList || [];
    blueprints.value = bpList || [];
    renderTemplates.value = rtList || [];
    wordStyles.value = wsList || [];
    elementLibrary.value = elLib?.items || [];
    materialsPacks.value = mpLib?.items || [];
  } catch (error) {
    ElMessage.error(error.message || "加载失败");
  } finally {
    listLoading.value = false;
  }
}

function openCreate() {
  existing.value = false;
  selectedId.value = "";
  step.value = 0;
  promptDraft.value = "";
  applyForm();
  dialog.value = true;
}

async function openEdit(id) {
  selectedId.value = id;
  busy.value = true;
  try {
    const data = await request(`/api/writing/robots/${encodeURIComponent(id)}`);
    existing.value = true;
    step.value = 0;
    promptDraft.value = "";
    applyForm(data);
    dialog.value = true;
  } catch (error) {
    ElMessage.error(error.message || "加载机器人失败");
  } finally {
    busy.value = false;
  }
}

async function generatePrompts(kind = "all") {
  const text = promptDraft.value.trim() || [form.name, form.description].filter(Boolean).join("\n").trim();
  if (!text) {
    ElMessage.warning("请先填写需求说明（或名称/说明）");
    return;
  }
  generating.value = kind;
  try {
    const data = await request("/api/writing/robots/generate-prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        kind,
        name: form.name,
        description: form.description,
      }),
    });
    if (kind === "all" || kind === "chat") {
      if (data.chat_prompt) form.chat_prompt = data.chat_prompt;
    }
    if (kind === "all" || kind === "study") {
      if (data.study_prompt) form.study_prompt = data.study_prompt;
    }
    if (kind === "all" || kind === "write") {
      if (data.write_prompt) form.write_prompt = data.write_prompt;
    }
    ElMessage.success("提示词已生成，可继续编辑");
  } catch (error) {
    ElMessage.error(error.message || "生成失败");
  } finally {
    generating.value = "";
  }
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning("请填写名称");
    step.value = 0;
    return;
  }
  busy.value = true;
  try {
    const payload = {
      id: form.id || undefined,
      name: form.name.trim(),
      description: form.description.trim(),
      chat_prompt: form.chat_prompt,
      study_prompt: form.study_prompt,
      write_prompt: form.write_prompt,
      blueprint_ids: [...form.blueprint_ids],
      render_template_ids: [...form.render_template_ids].map(String),
      word_style_ids: [...form.word_style_ids].map(String),
      knowledge_folder_ids: [...form.knowledge_folder_ids],
      element_library_ids: [...form.element_library_ids],
      materials_pack_ids: [...form.materials_pack_ids],
    };
    const headers = { "Content-Type": "application/json" };
    if (existing.value && form.id) {
      await request(`/api/writing/robots/${encodeURIComponent(form.id)}`, {
        method: "PUT",
        headers,
        body: JSON.stringify(payload),
      });
      ElMessage.success("已保存");
    } else {
      await request("/api/writing/robots", {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      ElMessage.success("已创建");
    }
    dialog.value = false;
    await reload();
  } catch (error) {
    ElMessage.error(error.message || "保存失败");
  } finally {
    busy.value = false;
  }
}

async function remove() {
  if (!form.id) return;
  try {
    await ElMessageBox.confirm(`确定删除机器人「${form.name}」？`, "删除确认", { type: "warning" });
  } catch {
    return;
  }
  busy.value = true;
  try {
    await request(`/api/writing/robots/${encodeURIComponent(form.id)}`, { method: "DELETE" });
    ElMessage.success("已删除");
    dialog.value = false;
    await reload();
  } catch (error) {
    ElMessage.error(error.message || "删除失败");
  } finally {
    busy.value = false;
  }
}

onMounted(reload);
</script>

<style scoped>
.robots {
  box-sizing: border-box;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
  margin-top: 8px;
}
.card {
  padding: 16px;
  border: 1px solid var(--ay-line);
  border-radius: 14px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.card:hover,
.card.active {
  border-color: #c5d0f5;
  box-shadow: 0 8px 20px rgba(78, 110, 242, 0.08);
}
.card strong {
  display: block;
  margin-bottom: 6px;
  font-size: 16px;
}
.card p {
  margin: 0 0 10px;
  color: var(--ay-muted);
  font-size: 13px;
  line-height: 1.45;
  min-height: 2.9em;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--ay-muted);
}
.hint.tiny {
  margin: 0 0 6px;
  font-size: 12px;
  color: var(--ay-muted);
}
.robot-steps {
  margin: 4px 0 20px;
}
.step-pane {
  min-height: 280px;
  max-height: min(52vh, 520px);
  overflow: auto;
  padding-right: 4px;
}
.gen-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.footer-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.footer-spacer {
  flex: 1;
}
</style>
