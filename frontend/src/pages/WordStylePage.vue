<template>
  <div class="page ay-page ay-page-scroll">
    <header class="ay-hero">
      <div>
        <h1>Word 样式</h1>
        <p>个人 Word 样式（仅本账号）· 配置字号、字体与目录；在「机器人」中绑定后，对话导出可选用。</p>
      </div>
      <el-button type="primary" round @click="openCreate">新建样式</el-button>
    </header>

    <div v-if="loading" class="hint">加载中…</div>
    <div v-else class="grid">
      <article
        v-for="item in styles"
        :key="item.id"
        class="card"
        :class="{ active: active === item.id }"
        @click="selectStyle(item.id)"
      >
        <div class="card-body">
          <strong>{{ item.name }}</strong>
          <p>{{ item.desc || "暂无说明" }}</p>
          <div class="meta">
            <span>{{ item.id }}</span>
            <span>{{ item.updated }}</span>
          </div>
        </div>
      </article>
    </div>
    <el-empty v-if="!loading && !styles.length" description="还没有 Word 样式" :image-size="72" />

    <section v-if="current" class="detail">
      <div class="detail-head">
        <h2>{{ current.name }}</h2>
        <div class="detail-actions">
          <el-button size="small" @click="openEdit">编辑</el-button>
          <el-button size="small" type="danger" plain :disabled="current.id === 'ws_default'" @click="removeCurrent">
            删除
          </el-button>
        </div>
      </div>
      <ul v-if="detail" class="meta-list">
        <li>正文：{{ detail.settings?.body_font }} {{ detail.settings?.body_pt }}pt</li>
        <li>标题字体：{{ detail.settings?.title_font }}</li>
        <li>行距：{{ detail.settings?.line_spacing }}</li>
        <li>首行缩进：{{ detail.settings?.first_line_indent_cm }} cm</li>
        <li>含目录：{{ detail.settings?.include_toc ? "是" : "否" }}</li>
      </ul>
    </section>

    <el-dialog v-model="dialog" :title="form.id ? '编辑 Word 样式' : '新建 Word 样式'" width="640px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：安评正文 A4" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.desc" type="textarea" :rows="2" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="正文字体">
              <el-input v-model="form.settings.body_font" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="正文字号 (pt)">
              <el-input-number v-model="form.settings.body_pt" :min="8" :max="24" :step="0.5" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="标题字体">
              <el-input v-model="form.settings.title_font" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="段内小标题 (pt)">
              <el-input-number v-model="form.settings.subheading_pt" :min="8" :max="18" :step="0.5" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="报告标题 (pt)">
              <el-input-number v-model="form.settings.report_title_pt" :min="14" :max="28" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="目录标题 (pt)">
              <el-input-number v-model="form.settings.toc_title_pt" :min="12" :max="22" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="目录条目 (pt)">
              <el-input-number v-model="form.settings.toc_entry_pt" :min="10" :max="16" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="章标题字号（按大纲层级 1–6）">
          <div class="level-row">
            <label v-for="lv in 6" :key="lv">
              <span>{{ lv }}级</span>
              <el-input-number
                v-model="form.settings.chapter_title_pt[lv]"
                :min="10"
                :max="22"
                :step="0.5"
                size="small"
              />
            </label>
          </div>
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="行距">
              <el-input-number v-model="form.settings.line_spacing" :min="1" :max="2.5" :step="0.1" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="首行缩进 (cm)">
              <el-input-number
                v-model="form.settings.first_line_indent_cm"
                :min="0"
                :max="2"
                :step="0.1"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="目录缩进 (cm)">
              <el-input-number v-model="form.settings.toc_indent_cm" :min="0" :max="2" :step="0.1" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="导出含目录">
          <el-switch v-model="form.settings.include_toc" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { request } from "../api";

const active = ref("");
const styles = ref([]);
const detail = ref(null);
const loading = ref(false);
const busy = ref(false);
const dialog = ref(false);

const defaultSettings = () => ({
  body_pt: 12,
  report_title_pt: 20,
  toc_title_pt: 16,
  toc_entry_pt: 12,
  subheading_pt: 12,
  chapter_title_pt: { 1: 18, 2: 16, 3: 15, 4: 14, 5: 13, 6: 12 },
  title_font: "黑体",
  body_font: "宋体",
  first_line_indent_cm: 0.74,
  line_spacing: 1.5,
  toc_indent_cm: 0.55,
  include_toc: true,
});

const form = reactive({
  id: "",
  name: "",
  desc: "",
  settings: defaultSettings(),
});

const current = computed(() => styles.value.find((item) => item.id === active.value) || null);

function mergeSettings(raw) {
  const base = defaultSettings();
  const src = raw && typeof raw === "object" ? raw : {};
  const levels = { ...base.chapter_title_pt, ...(src.chapter_title_pt || {}) };
  for (let i = 1; i <= 6; i += 1) {
    if (levels[i] == null) levels[i] = base.chapter_title_pt[i];
    else levels[i] = Number(levels[i]);
  }
  return { ...base, ...src, chapter_title_pt: levels };
}

async function reload() {
  loading.value = true;
  try {
    styles.value = (await request("/api/writing/word-styles")) || [];
    if (!active.value && styles.value.length) {
      await selectStyle(styles.value[0].id);
    } else if (active.value) {
      await selectStyle(active.value);
    }
  } catch (error) {
    ElMessage.error(error.message || "加载失败");
  } finally {
    loading.value = false;
  }
}

async function selectStyle(id) {
  active.value = id || "";
  if (!id) {
    detail.value = null;
    return;
  }
  try {
    detail.value = await request(`/api/writing/word-styles/${encodeURIComponent(id)}`);
  } catch (error) {
    detail.value = null;
    ElMessage.error(error.message || "加载详情失败");
  }
}

function openCreate() {
  form.id = "";
  form.name = "";
  form.desc = "";
  form.settings = defaultSettings();
  dialog.value = true;
}

function openEdit() {
  if (!detail.value) return;
  form.id = detail.value.id || "";
  form.name = detail.value.name || "";
  form.desc = detail.value.desc || "";
  form.settings = mergeSettings(detail.value.settings);
  dialog.value = true;
}

async function submitForm() {
  if (!form.name.trim()) {
    ElMessage.warning("请填写名称");
    return;
  }
  busy.value = true;
  try {
    const body = {
      id: form.id || undefined,
      name: form.name.trim(),
      desc: form.desc.trim(),
      settings: mergeSettings(form.settings),
    };
    const saved = form.id
      ? await request(`/api/writing/word-styles/${encodeURIComponent(form.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : await request("/api/writing/word-styles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    dialog.value = false;
    ElMessage.success("已保存");
    await reload();
    if (saved?.id) await selectStyle(saved.id);
  } catch (error) {
    ElMessage.error(error.message || "保存失败");
  } finally {
    busy.value = false;
  }
}

async function removeCurrent() {
  if (!current.value?.id || current.value.id === "ws_default") return;
  try {
    await ElMessageBox.confirm(`确定删除样式「${current.value.name}」？`, "删除样式", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    });
  } catch {
    return;
  }
  try {
    await request(`/api/writing/word-styles/${encodeURIComponent(current.value.id)}`, { method: "DELETE" });
    active.value = "";
    detail.value = null;
    ElMessage.success("已删除");
    await reload();
  } catch (error) {
    ElMessage.error(error.message || "删除失败");
  }
}

onMounted(reload);
</script>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
  margin-top: 18px;
}
.card {
  border: 1px solid var(--ay-line);
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
  background: #fff;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.card.active {
  border-color: #4e6ef2;
  box-shadow: 0 0 0 1px rgba(78, 110, 242, 0.25);
}
.card p {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--ay-muted);
  line-height: 1.45;
}
.meta {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  font-size: 12px;
  color: var(--ay-muted);
}
.detail {
  margin-top: 24px;
  padding: 18px 20px;
  border-radius: 12px;
  border: 1px solid var(--ay-line);
  background: #fff;
}
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.meta-list {
  margin: 12px 0 0;
  padding-left: 18px;
  color: var(--ay-muted);
  font-size: 14px;
  line-height: 1.7;
}
.level-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px 14px;
}
.level-row label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--ay-muted);
}
.hint {
  margin-top: 16px;
  color: var(--ay-muted);
}
</style>
