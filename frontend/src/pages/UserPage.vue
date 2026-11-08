<template>
  <div class="page ay-page ay-page-scroll">
    <header class="ay-hero">
      <div>
        <h1>{{ panelOnly ? panelTitle : "用户中心" }}</h1>
        <p v-if="!panelOnly">
          登录后拥有独立工作区：对话、蓝本、机器人、渲染、Word 样式、资料库互不共享；可配置自己的模型并查看 Token 用量。
        </p>
        <p v-else class="hero-sub">{{ panelSubtitle }}</p>
      </div>
      <a-tag v-if="loggedIn && profile" color="success">{{ profile.display_name || profile.username }}</a-tag>
    </header>

    <a-tabs v-model:activeKey="tab" class="tabs" :class="{ 'tabs-panel-only': panelOnly }">
      <a-tab-pane tab="账号" key="account">
        <div v-if="!loggedIn" class="card-pane guest-pane">
          <p class="guest-hint">账号页用于查看与修改个人信息，请先登录。</p>
          <div class="guest-actions">
            <a-button type="primary" @click="goLogin">登录</a-button>
            <a-button @click="goRegister">注册</a-button>
          </div>
        </div>
        <div v-else class="card-pane narrow">
          <a-form layout="vertical">
            <a-form-item label="显示名称">
              <a-input v-model:value="profileForm.display_name" placeholder="在侧栏与界面中展示" />
            </a-form-item>
            <a-form-item label="用户名">
              <a-input :value="profile?.username || ''" disabled />
            </a-form-item>
            <a-form-item label="用户 ID">
              <a-input :value="profile?.id || ''" disabled />
            </a-form-item>
            <a-form-item label="注册时间">
              <a-input :value="profile?.created_at || '—'" disabled />
            </a-form-item>
            <a-button type="primary" :loading="busy" @click="saveProfile">保存</a-button>
          </a-form>
        </div>
      </a-tab-pane>

      <a-tab-pane tab="模型连接" key="llm">
        <div class="card-pane">
          <a-alert
            v-if="llm.using_system_default"
            type="info"
            :closable="false"
            class="mb"
            show-icon
          >
            <template #message>
              当前实际调用：<strong>系统默认百炼</strong>
              <span v-if="llm.active_model_name || llm.system_model_name">
                · 模型 {{ llm.active_model_name || llm.system_model_name }}
              </span>
            </template>
            <template #description>
              <p class="alert-body">
                关闭「使用我的模型配置」后不会使用下方已保存的 Key（仅作草稿保留）。
                个人配置保存后写入 <strong>MySQL</strong>（表 writing_user_llm_config），不再写入本地 llm.json。
                系统 Key 来自 <code>config/bailian.local.yml</code>，若与你在用户中心填过的是同一把 Key，账单上仍会看到同一账号，这不代表开关未生效。
              </p>
            </template>
          </a-alert>
          <a-alert
 v-else
 type="success"
 :closable="false"
 :message="`当前实际调用：你的配置 · 模型 ${llm.active_model_name || llm.model_name || '—'}`"
 class="mb"
 show-icon />
          <a-form layout="vertical" class="narrow" :class="{ 'form-disabled': !llmForm.enabled }">
            <a-form-item label="使用我的模型配置">
              <a-switch v-model:checked="llmForm.enabled"  />
            </a-form-item>
            <a-form-item label="平台">
              <a-select v-model:value="llmForm.provider" style="width: 100%" @change="applyProviderPreset">
                <a-select-option value="openai_compatible">OpenAI 兼容</a-select-option>
                <a-select-option value="bailian">阿里云百炼（兼容模式）</a-select-option>
                <a-select-option value="deepseek">DeepSeek</a-select-option>
                <a-select-option value="custom">自定义 Base URL</a-select-option>
              </a-select>
            </a-form-item>
            <a-form-item label="API Base URL">
              <a-input v-model:value="llmForm.base_url" placeholder="留空则用平台默认" />
            </a-form-item>
            <a-form-item label="API Key">
              <a-input-password
                v-model:value="llmForm.api_key"
                :placeholder="llm.api_key_set ? `已设置 ${llm.api_key_hint}` : 'sk-…'"
              />
            </a-form-item>
            <a-form-item label="模型名称">
              <a-input v-model:value="llmForm.model_name" placeholder="如 deepseek-chat / qwen-plus" />
            </a-form-item>
            <a-form-item label="意图模型名称（可选）">
              <a-input
                v-model:value="llmForm.intent_model_name"
                placeholder="默认 qwen-turbo（百炼便宜、适合意图 JSON）；留空走系统配置"
              />
              <p class="field-hint">仅用于对话意图分类、要素抽取等短 JSON 任务，可与主模型不同以省 Token。</p>
            </a-form-item>
            <a-form-item label="Completions 路径">
              <a-input v-model:value="llmForm.completions_path" placeholder="/chat/completions" />
            </a-form-item>
            <a-row :gutter="12">
              <a-col :span="8">
                <a-form-item label="超时 (秒)">
                  <a-input-number v-model:value="llmForm.timeout" :min="10" :max="600" style="width: 100%"  />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="温度">
                  <a-input-number v-model:value="llmForm.temperature" :min="0" :max="2" :step="0.1" style="width: 100%"  />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="Max tokens">
                  <a-input-number v-model:value="llmForm.max_tokens" :min="256" :max="128000" :step="256" style="width: 100%"  />
                </a-form-item>
              </a-col>
            </a-row>
            <a-button type="primary" :loading="busy" @click="saveLlm">保存配置</a-button>
          </a-form>
        </div>
      </a-tab-pane>

      <a-tab-pane tab="Token 统计" key="tokens">
        <div class="card-pane">
          <div class="stat-grid">
            <div class="stat">
              <span class="label">总调用</span>
              <strong>{{ usage.total_calls || 0 }}</strong>
            </div>
            <div class="stat">
              <span class="label">输入 Token</span>
              <strong>{{ usage.total_prompt_tokens || 0 }}</strong>
            </div>
            <div class="stat">
              <span class="label">输出 Token</span>
              <strong>{{ usage.total_completion_tokens || 0 }}</strong>
            </div>
            <div class="stat">
              <span class="label">合计</span>
              <strong>{{ usage.total_tokens || 0 }}</strong>
            </div>
          </div>
          <h3>按日</h3>
          <a-table
            :data-source="dayRows"
            :columns="dayColumns"
            :pagination="false"
            size="small"
            row-key="day"
            :locale="{ emptyText: '暂无记录' }"
          />
          <h3>最近调用</h3>
          <a-table
            :data-source="usage.recent || []"
            :columns="recentColumns"
            :pagination="false"
            size="small"
            row-key="at"
            :locale="{ emptyText: '暂无记录' }"
          />
        </div>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "../ui.js";

import { isLoggedIn, request } from "../api";

const props = defineProps({
  initialTab: { type: String, default: "account" },
  panelOnly: { type: Boolean, default: false },
});

const tab = ref(props.initialTab || "account");
const dayColumns = [
  { title: "日期", dataIndex: "day", key: "day", width: 120 },
  { title: "次数", dataIndex: "calls", key: "calls", width: 80 },
  { title: "输入", dataIndex: "prompt_tokens", key: "prompt_tokens" },
  { title: "输出", dataIndex: "completion_tokens", key: "completion_tokens" },
  { title: "合计", dataIndex: "total_tokens", key: "total_tokens" },
];
const recentColumns = [
  { title: "时间", dataIndex: "at", key: "at", width: 160 },
  { title: "场景", dataIndex: "label", key: "label", width: 100 },
  { title: "输入", dataIndex: "prompt_tokens", key: "prompt_tokens", width: 90 },
  { title: "输出", dataIndex: "completion_tokens", key: "completion_tokens", width: 90 },
  { title: "合计", dataIndex: "total_tokens", key: "total_tokens", width: 90 },
];

const panelTitle = computed(() => {
  const map = { account: "账号", llm: "设置", tokens: "Token 统计" };
  return map[tab.value] || "用户中心";
});

const panelSubtitle = computed(() => {
  const map = {
    account: "查看与修改个人信息",
    llm: "配置模型平台、地址与 API Key",
    tokens: "查看调用次数与 Token 用量",
  };
  return map[tab.value] || "";
});

const navigateTo = inject("navigateTo");
const loggedIn = computed(() => isLoggedIn());

watch(
  () => props.initialTab,
  (value) => {
    if (value) tab.value = value;
  }
);

watch(tab, (name) => {
  if (name === "tokens") loadUsage();
  if (name === "llm") loadLlm();
  if (name === "account" && loggedIn.value) loadProfile();
});

const busy = ref(false);
const profile = ref(null);
const profileForm = reactive({ display_name: "" });
const llm = ref({ using_system_default: true, api_key_set: false });
const usage = ref({ total_calls: 0, by_day: {}, recent: [] });

const llmForm = reactive({
  enabled: false,
  provider: "openai_compatible",
  base_url: "",
  api_key: "",
  model_name: "",
  intent_model_name: "",
  completions_path: "/chat/completions",
  timeout: 120,
  temperature: 0,
  max_tokens: 4096,
});

const dayRows = computed(() => {
  const by = usage.value.by_day || {};
  return Object.keys(by)
    .sort((a, b) => (a < b ? 1 : -1))
    .map((day) => ({ day, ...by[day] }));
});

const PRESETS = {
  openai_compatible: "https://api.openai.com/v1",
  bailian: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  deepseek: "https://api.deepseek.com/v1",
  custom: "",
};

function applyProviderPreset() {
  if (llmForm.provider !== "custom" && !llmForm.base_url.trim()) {
    llmForm.base_url = PRESETS[llmForm.provider] || "";
  }
}

async function loadProfile() {
  if (!loggedIn.value) {
    profile.value = null;
    return;
  }
  try {
    profile.value = await request("/api/writing/users/me");
    profileForm.display_name = profile.value?.display_name || profile.value?.username || "";
  } catch {
    profile.value = null;
  }
}

function goLogin() {
  navigateTo?.("login");
}

function goRegister() {
  navigateTo?.("register");
}

async function saveProfile() {
  if (!loggedIn.value) {
    goLogin();
    return;
  }
  busy.value = true;
  try {
    profile.value = await request("/api/writing/users/me", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: profileForm.display_name.trim() }),
    });
    ElMessage.success("账号信息已保存");
  } catch (error) {
    ElMessage.error(error.message || "保存失败");
  } finally {
    busy.value = false;
  }
}

async function loadLlm() {
  const data = await request("/api/writing/users/me/llm-config");
  llm.value = data || {};
  llmForm.enabled = !!data.enabled;
  llmForm.provider = data.provider || "openai_compatible";
  llmForm.base_url = data.base_url || "";
  llmForm.model_name = data.model_name || "";
  llmForm.intent_model_name = data.intent_model_name || "";
  llmForm.completions_path = data.completions_path || "/chat/completions";
  llmForm.timeout = data.timeout ?? 120;
  llmForm.temperature = data.temperature ?? 0;
  llmForm.max_tokens = data.max_tokens ?? 4096;
  llmForm.api_key = "";
}

async function loadUsage() {
  usage.value = (await request("/api/writing/users/me/token-usage")) || {};
}

async function reload() {
  if (loggedIn.value) await loadProfile();
  await Promise.all([loadLlm(), loadUsage()]);
}

async function saveLlm() {
  busy.value = true;
  try {
    const body = {
      enabled: llmForm.enabled,
      provider: llmForm.provider,
      base_url: llmForm.base_url.trim(),
      model_name: llmForm.model_name.trim(),
      intent_model_name: llmForm.intent_model_name.trim(),
      completions_path: llmForm.completions_path.trim() || "/chat/completions",
      timeout: llmForm.timeout,
      temperature: llmForm.temperature,
      max_tokens: llmForm.max_tokens,
    };
    if (llmForm.api_key.trim()) body.api_key = llmForm.api_key.trim();
    llm.value = await request("/api/writing/users/me/llm-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    llmForm.api_key = "";
    ElMessage.success("模型配置已保存");
  } catch (error) {
    ElMessage.error(error.message || "保存失败");
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  reload();
});
</script>

<style scoped>
.tabs {
  margin-top: 8px;
}
.tabs-panel-only :deep(.ant-tabs-nav) {
  display: none;
}
.hero-sub {
  margin: 0;
  color: var(--ay-muted);
  font-size: 14px;
}
.card-pane {
  margin-top: 12px;
  padding: 18px 20px;
  border-radius: 12px;
  border: 1px solid var(--ay-line);
  background: #fff;
}
.narrow {
  max-width: 520px;
}
.field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--ay-muted);
  line-height: 1.45;
}
.guest-pane {
  text-align: center;
}
.guest-hint {
  margin: 0 0 16px;
  color: var(--ay-muted);
}
.guest-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.stat {
  padding: 12px 14px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid var(--ay-line);
}
.stat .label {
  display: block;
  font-size: 12px;
  color: var(--ay-muted);
  margin-bottom: 4px;
}
.stat strong {
  font-size: 20px;
}
h3 {
  margin: 18px 0 8px;
  font-size: 15px;
}
.mb {
  margin-bottom: 14px;
}
.alert-body {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.5;
}
.alert-body code {
  font-size: 12px;
}
.form-disabled :deep(.ant-input-affix-wrapper, .ant-input),
.form-disabled :deep(.ant-select-selector),
.form-disabled :deep(.ant-input-number) {
  opacity: 0.72;
}
</style>
