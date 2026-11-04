<template>
  <div class="page ay-page ay-page-scroll">
    <header class="ay-hero">
      <div>
        <h1>渲染模版</h1>
        <p>个人渲染模版（仅本账号）· 用 <code v-text="'{{' + '章节key' + '}}'" /> 占位填充章节 HTML，导出 Word；无模版时由大模型生成整篇 HTML。</p>
      </div>
      <el-button type="primary" round @click="openCreate">新建模版</el-button>
    </header>

    <div v-if="loading" class="hint">加载中…</div>
    <div v-else class="grid">
      <article
        v-for="item in templates"
        :key="item.id"
        class="card"
        :class="{ active: active === item.id }"
        @click="selectTemplate(item.id)"
      >
        <div class="preview" :style="{ background: item.tone || '#eef2ff' }" />
        <div class="card-body">
          <strong>{{ item.name }}</strong>
          <p>{{ item.desc || "暂无说明" }}</p>
          <div class="meta">
            <el-tag size="small" effect="plain">{{ item.format || "DOCX" }}</el-tag>
            <span>{{ item.updated }}</span>
          </div>
        </div>
      </article>
    </div>
    <el-empty v-if="!loading && !templates.length" description="还没有渲染模版" :image-size="72" />

    <section v-if="current" class="detail">
      <div class="detail-head">
        <h2>{{ current.name }}</h2>
        <div class="detail-actions">
          <el-button size="small" @click="openEdit">编辑</el-button>
          <el-button size="small" type="danger" plain @click="removeCurrent">删除</el-button>
        </div>
      </div>
      <ul class="meta-list">
        <li>编号：{{ current.id }}</li>
        <li v-if="current.margin">页边距：{{ current.margin }}</li>
        <li v-if="current.font">正文字号：{{ current.font }}</li>
        <li v-if="current.heading">标题样式：{{ current.heading }}</li>
        <li v-if="current.header">页眉：{{ current.header }}</li>
      </ul>
      <div class="placeholder-help">
        <strong>占位符</strong>
        <code v-text="ph.body" /> 正文 ·
        <code v-text="ph.title" /> 标题 ·
        <code v-text="ph.seq" /> 序号 ·
        也可用章节中文标题如 <code v-text="ph.named" />
      </div>
      <pre class="html-preview">{{ current.html || "（无 HTML）" }}</pre>
    </section>

    <el-dialog v-model="dialog" :title="form.id ? '编辑渲染模版' : '新建渲染模版'" width="720px" destroy-on-close>
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：安评正文 A4" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.desc" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="模版 HTML（含占位符）">
          <el-input
            v-model="form.html"
            type="textarea"
            :autosize="{ minRows: 10, maxRows: 22 }"
            placeholder="<h1>报告</h1>&#10;<h2>{{title:ch-1}}</h2>&#10;{{ch-1}}"
          />
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
const templates = ref([]);
const detail = ref(null);
const loading = ref(false);
const busy = ref(false);
const dialog = ref(false);
const ph = {
  body: "{{ch-1}}",
  title: "{{title:ch-1}}",
  seq: "{{seq:ch-1}}",
  named: "{{概述}}",
};
const form = reactive({
  id: "",
  name: "",
  desc: "",
  html: "",
});

const current = computed(() => {
  if (detail.value?.id === active.value) return detail.value;
  return templates.value.find((item) => item.id === active.value) || null;
});

async function reload() {
  loading.value = true;
  try {
    templates.value = (await request("/api/writing/render-templates")) || [];
    if (!active.value && templates.value.length) {
      await selectTemplate(templates.value[0].id);
    } else if (active.value) {
      await selectTemplate(active.value);
    }
  } catch (error) {
    ElMessage.error(error.message || "加载渲染模版失败");
  } finally {
    loading.value = false;
  }
}

async function selectTemplate(id) {
  active.value = id || "";
  if (!id) {
    detail.value = null;
    return;
  }
  try {
    detail.value = await request(`/api/writing/render-templates/${encodeURIComponent(id)}`);
  } catch (error) {
    detail.value = templates.value.find((item) => item.id === id) || null;
    ElMessage.error(error.message || "加载模版详情失败");
  }
}

function openCreate() {
  form.id = "";
  form.name = "";
  form.desc = "";
  form.html =
    "<h1>报告标题</h1>\n" +
    "<h2>{{title:ch-1}}（{{seq:ch-1}}）</h2>\n{{ch-1}}\n" +
    "<h2>{{title:ch-2}}（{{seq:ch-2}}）</h2>\n{{ch-2}}\n";
  dialog.value = true;
}

function openEdit() {
  if (!current.value) return;
  form.id = current.value.id || "";
  form.name = current.value.name || "";
  form.desc = current.value.desc || "";
  form.html = current.value.html || "";
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
      html: form.html,
      format: "DOCX",
    };
    const saved = form.id
      ? await request(`/api/writing/render-templates/${encodeURIComponent(form.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
      : await request("/api/writing/render-templates", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
    dialog.value = false;
    ElMessage.success("已保存");
    await reload();
    if (saved?.id) await selectTemplate(saved.id);
  } catch (error) {
    ElMessage.error(error.message || "保存失败");
  } finally {
    busy.value = false;
  }
}

async function removeCurrent() {
  if (!current.value?.id) return;
  try {
    await ElMessageBox.confirm(`确定删除模版「${current.value.name}」？`, "删除模版", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    });
  } catch {
    return;
  }
  try {
    await request(`/api/writing/render-templates/${encodeURIComponent(current.value.id)}`, {
      method: "DELETE",
    });
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
.page {
  box-sizing: border-box;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
}
.card {
  border: 1px solid var(--ay-line);
  border-radius: 14px;
  overflow: hidden;
  background: #fff;
  cursor: pointer;
}
.card.active {
  border-color: #c5d0f5;
  box-shadow: 0 8px 20px rgba(78, 110, 242, 0.08);
}
.preview {
  height: 88px;
}
.card-body {
  padding: 12px 14px 14px;
}
.card-body strong {
  display: block;
  margin-bottom: 4px;
}
.card-body p {
  margin: 0 0 8px;
  color: var(--ay-muted);
  font-size: 13px;
}
.meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--ay-muted);
}
.detail {
  margin-top: 20px;
  padding: 16px;
  border: 1px solid var(--ay-line);
  border-radius: 14px;
  background: #fff;
}
.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}
.detail-head h2 {
  margin: 0;
  font-size: 18px;
}
.detail-actions {
  display: flex;
  gap: 8px;
}
.meta-list {
  margin: 12px 0;
  padding-left: 18px;
  color: var(--ay-muted);
  font-size: 13px;
}
.placeholder-help {
  margin-bottom: 10px;
  font-size: 12px;
  color: #475569;
  line-height: 1.6;
}
.placeholder-help code {
  margin: 0 2px;
  padding: 1px 6px;
  border-radius: 6px;
  background: #f1f5f9;
  font-size: 11px;
}
.html-preview {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  border-radius: 10px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
.hint {
  color: var(--ay-muted);
}
</style>
