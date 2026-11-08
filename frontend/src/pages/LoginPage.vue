<template>
  <div class="page ay-page ay-page-scroll auth-page">
    <header class="ay-hero">
      <div>
        <h1>登录</h1>
        <p>登录后使用独立工作区与 Token 统计、模型设置。</p>
      </div>
    </header>
    <div class="card-pane narrow">
      <a-form layout="vertical" @submit.prevent="submit">
        <a-form-item label="用户名">
          <a-input v-model:value="form.username" placeholder="3–32 位字母、数字或下划线" autocomplete="username" />
        </a-form-item>
        <a-form-item label="密码">
          <a-input-password v-model:value="form.password" autocomplete="current-password" />
        </a-form-item>
        <a-button type="primary" :loading="busy" html-type="submit">登录</a-button>
        <a-button @click="goRegister" type="link">没有账号？去注册</a-button>
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
const form = reactive({ username: "", password: "" });

function goRegister() {
  navigateTo?.("register");
}

async function submit() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning("请填写用户名和密码");
    return;
  }
  busy.value = true;
  try {
    const data = await request("/api/writing/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.username.trim(), password: form.password }),
    });
    setAuth(data);
    ElMessage.success("登录成功");
    navigateTo?.("chat");
  } catch (error) {
    ElMessage.error(error.message || "登录失败");
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
