<template>
  <div class="auth-shell">
    <div class="auth-bg" aria-hidden="true">
      <span class="orb orb-a" />
      <span class="orb orb-b" />
      <span class="grid-lines" />
    </div>

    <div class="auth-layout">
      <section class="auth-intro">
        <div class="intro-logo">{{ BRAND_NAME.charAt(0) }}</div>
        <h1>{{ BRAND_TITLE }}</h1>
        <p class="intro-lead">登录后进入专属工作区：与 AI 协作写文档、管理资料与要素、统一样式并导出 Word。</p>
        <ul class="intro-points">
          <li><span class="dot" />资料库、渲染模版、Word 样式按账号隔离</li>
          <li><span class="dot" />机器人可绑定个人资料与样式</li>
          <li><span class="dot" />Token 用量与模型配置独立统计</li>
        </ul>
      </section>

      <section class="auth-card">
        <div class="mode-switch" role="tablist">
          <button
            type="button"
            role="tab"
            class="mode-btn"
            :class="{ active: mode === 'login' }"
            :aria-selected="mode === 'login'"
            @click="mode = 'login'"
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            class="mode-btn"
            :class="{ active: mode === 'register' }"
            :aria-selected="mode === 'register'"
            @click="mode = 'register'"
          >
            注册
          </button>
        </div>

        <div v-show="mode === 'login'" class="form-block">
          <h2>欢迎回来</h2>
          <p class="form-hint">使用账号密码登录系统</p>
          <a-form layout="vertical" size="large" @submit.prevent="submitLogin">
            <a-form-item label="用户名">
              <a-input
                v-model:value="loginForm.username"
                placeholder="3–32 位字母、数字或下划线"
                autocomplete="username"
              />
            </a-form-item>
            <a-form-item label="密码">
              <a-input-password
                v-model:value="loginForm.password"
                autocomplete="current-password"
                @keyup.enter="submitLogin"
              />
            </a-form-item>
            <a-button type="primary" class="auth-submit" :loading="busy" html-type="submit">登录</a-button>
          </a-form>
        </div>

        <div v-show="mode === 'register'" class="form-block">
          <h2>创建账号</h2>
          <p class="form-hint">注册即拥有独立工作区与默认样式</p>
          <a-form layout="vertical" size="large" @submit.prevent="submitRegister">
            <a-form-item label="用户名">
              <a-input
                v-model:value="regForm.username"
                placeholder="3–32 位字母、数字或下划线"
                autocomplete="username"
              />
            </a-form-item>
            <a-form-item label="显示名称（可选）">
              <a-input v-model:value="regForm.display_name" placeholder="在侧栏与界面中展示" />
            </a-form-item>
            <a-form-item label="密码">
              <a-input-password v-model:value="regForm.password" autocomplete="new-password" />
            </a-form-item>
            <a-form-item label="确认密码">
              <a-input-password
                v-model:value="regForm.password2"
                autocomplete="new-password"
                @keyup.enter="submitRegister"
              />
            </a-form-item>
            <a-button type="primary" class="auth-submit" :loading="busy" html-type="submit">注册并登录</a-button>
          </a-form>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, watch } from "vue";
import { ElMessage } from "../ui.js";

import { request, setAuth } from "../api";
import { BRAND_NAME, BRAND_TITLE } from "../brand";

const props = defineProps({
  initialTab: { type: String, default: "login" },
});

const emit = defineEmits(["success"]);

const mode = ref(props.initialTab === "register" ? "register" : "login");
const busy = ref(false);
const loginForm = reactive({ username: "", password: "" });
const regForm = reactive({ username: "", display_name: "", password: "", password2: "" });

watch(
  () => props.initialTab,
  (tab) => {
    mode.value = tab === "register" ? "register" : "login";
  }
);

async function submitLogin() {
  if (!loginForm.username.trim() || !loginForm.password) {
    ElMessage.warning("请填写用户名和密码");
    return;
  }
  busy.value = true;
  try {
    const data = await request("/api/writing/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginForm.username.trim(), password: loginForm.password }),
    });
    setAuth(data);
    ElMessage.success("登录成功");
    emit("success");
  } catch (error) {
    ElMessage.error(error.message || "登录失败");
  } finally {
    busy.value = false;
  }
}

async function submitRegister() {
  if (!regForm.username.trim() || !regForm.password) {
    ElMessage.warning("请填写用户名和密码");
    return;
  }
  if (regForm.password.length < 6) {
    ElMessage.warning("密码至少 6 位");
    return;
  }
  if (regForm.password !== regForm.password2) {
    ElMessage.warning("两次密码不一致");
    return;
  }
  busy.value = true;
  try {
    const data = await request("/api/writing/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: regForm.username.trim(),
        password: regForm.password,
        display_name: (regForm.display_name || regForm.username).trim(),
      }),
    });
    setAuth(data);
    ElMessage.success("注册成功");
    emit("success");
  } catch (error) {
    ElMessage.error(error.message || "注册失败");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.auth-shell {
  position: relative;
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px 20px;
  overflow: hidden;
  background: #0f172a;
}
.auth-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(60px);
  opacity: 0.55;
}
.orb-a {
  width: 420px;
  height: 420px;
  top: -120px;
  left: -80px;
  background: #4e6ef2;
}
.orb-b {
  width: 360px;
  height: 360px;
  right: -60px;
  bottom: -100px;
  background: #22d3ee;
  opacity: 0.35;
}
.grid-lines {
  position: absolute;
  inset: 0;
  background-image: linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, #000 20%, transparent 75%);
}
.auth-layout {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(320px, 400px);
  gap: 40px;
  width: 100%;
  max-width: 960px;
  align-items: center;
}
.auth-intro {
  color: #e2e8f0;
  padding: 12px 8px;
}
.intro-logo {
  width: 56px;
  height: 56px;
  margin-bottom: 20px;
  border-radius: 16px;
  background: linear-gradient(145deg, #4e6ef2, #7c9bff);
  color: #fff;
  font-size: 26px;
  font-weight: 800;
  line-height: 56px;
  text-align: center;
  box-shadow: 0 16px 40px rgba(78, 110, 242, 0.45);
}
.auth-intro h1 {
  margin: 0 0 12px;
  font-size: clamp(26px, 4vw, 34px);
  font-weight: 800;
  letter-spacing: -0.02em;
  color: #f8fafc;
}
.intro-lead {
  margin: 0 0 22px;
  max-width: 28em;
  font-size: 15px;
  line-height: 1.65;
  color: #94a3b8;
}
.intro-points {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.intro-points li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 14px;
  color: #cbd5e1;
}
.dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  margin-top: 6px;
  border-radius: 50%;
  background: linear-gradient(145deg, #60a5fa, #4e6ef2);
  box-shadow: 0 0 12px rgba(96, 165, 250, 0.6);
}
.auth-card {
  padding: 28px 26px 30px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.97);
  border: 1px solid rgba(255, 255, 255, 0.65);
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.35);
  backdrop-filter: blur(12px);
}
.mode-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 22px;
  padding: 4px;
  border-radius: 12px;
  background: #f1f5f9;
}
.mode-btn {
  padding: 10px 12px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  font-size: 14px;
  font-weight: 600;
  color: #64748b;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
}
.mode-btn.active {
  background: #fff;
  color: #1e40af;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
}
.form-block h2 {
  margin: 0 0 6px;
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}
.form-hint {
  margin: 0 0 18px;
  font-size: 13px;
  color: #64748b;
}
.auth-submit {
  width: 100%;
  margin-top: 6px;
  height: 42px;
  font-weight: 600;
}
@media (max-width: 820px) {
  .auth-layout {
    grid-template-columns: 1fr;
    max-width: 420px;
  }
  .auth-intro {
    text-align: center;
  }
  .intro-logo {
    margin-left: auto;
    margin-right: auto;
  }
  .intro-lead {
    margin-left: auto;
    margin-right: auto;
  }
  .intro-points {
    align-items: center;
  }
}
</style>
