<template>
  <div class="page ay-page ay-page-scroll auth-page">
    <header class="ay-hero">
      <div>
        <h1>注册</h1>
        <p>创建账号后拥有独立的蓝本、机器人、资料库与对话数据。</p>
      </div>
    </header>
    <div class="card-pane narrow">
      <a-form layout="vertical" @submit.prevent="submit">
        <a-form-item label="用户名">
          <a-input v-model:value="form.username" placeholder="3–32 位字母、数字或下划线" autocomplete="username" />
        </a-form-item>
        <a-form-item label="显示名称（可选）">
          <a-input v-model:value="form.display_name" placeholder="在界面中展示的名称" />
        </a-form-item>
        <a-form-item label="密码">
          <a-input-password v-model:value="form.password" autocomplete="new-password" />
        </a-form-item>
        <a-form-item label="确认密码">
          <a-input-password v-model:value="form.password2" autocomplete="new-password" />
        </a-form-item>
        <a-button type="primary" :loading="busy" html-type="submit">注册并登录</a-button>
        <a-button @click="goLogin" type="link">已有账号？去登录</a-button>
      </a-form>
    </div>
  </div>
</template>

<script setup>
import { inject, reactive, ref } from "vue";
import { ElMessage } from "../ui.js";

import { request, setAuth } from "../api";

const navigateTo = inject("navigateTo");
const busy = ref(false);
const form = reactive({ username: "", display_name: "", password: "", password2: "" });

function goLogin() {
  navigateTo?.("login");
}

async function submit() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning("请填写用户名和密码");
    return;
  }
  if (form.password.length < 6) {
    ElMessage.warning("密码至少 6 位");
    return;
  }
  if (form.password !== form.password2) {
    ElMessage.warning("两次密码不一致");
    return;
  }
  busy.value = true;
  try {
    const data = await request("/api/writing/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.username.trim(),
        password: form.password,
        display_name: (form.display_name || form.username).trim(),
      }),
    });
    setAuth(data);
    ElMessage.success("注册成功");
    navigateTo?.("chat");
  } catch (error) {
    ElMessage.error(error.message || "注册失败");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.auth-page .narrow {
  max-width: 400px;
}
.card-pane {
  margin-top: 12px;
  padding: 22px 24px;
  border-radius: 12px;
  border: 1px solid var(--ay-line);
  background: #fff;
}
</style>
