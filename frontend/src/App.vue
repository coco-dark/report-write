<template>
  <a-config-provider :locale="zhCN" :theme="antdTheme">
  <AuthPage
    v-if="!loggedIn"
    :initial-tab="authTab"
    @success="onAuthSuccess"
  />
  <div v-else class="shell">
    <nav class="side-nav" aria-label="主导航">
      <button type="button" class="logo" :title="`${brandName} · 对话`" @click="navigateTo('chat')">
        {{ userInitial }}
      </button>
      <div class="nav-main">
        <button
          v-for="item in mainMenus"
          :key="item.id"
          type="button"
          class="nav-item"
          :class="{ active: active === item.id }"
          :title="item.title || item.label"
          @click="navigateTo(item.id)"
        >
          <span class="nav-ico" v-html="item.icon" />
          <span class="nav-label">{{ item.label }}</span>
        </button>
      </div>
      <div ref="userMenuWrap" class="nav-user-wrap">
        <button
          type="button"
          class="nav-item nav-item-user"
          :class="{ active: active === 'user' || userMenuOpen }"
          title="用户菜单"
          @click="toggleUserMenu"
        >
          <span class="nav-ico nav-avatar">{{ userInitial }}</span>
          <span class="nav-label">用户</span>
        </button>
        <div v-if="userMenuOpen" class="user-menu" role="menu" @click.stop>
          <div class="user-menu-head">
            <strong>{{ userDisplayName }}</strong>
            <span class="user-menu-sub">Token 合计 {{ tokenSummary.total_tokens || 0 }}</span>
          </div>
          <button type="button" class="user-menu-item" @click.stop="openUserTab('tokens')">Token 统计</button>
          <button type="button" class="user-menu-item" @click.stop="openUserTab('llm')">设置</button>
          <button type="button" class="user-menu-item" @click.stop="openUserTab('account')">账号</button>
          <button type="button" class="user-menu-item danger" @click.stop="doLogout">退出登录</button>
        </div>
      </div>
    </nav>

    <div class="workspace">
      <ChatPage
        v-if="active === 'chat'"
        :resume-token="chatResumeToken"
        :resume-session-id="chatResumeSessionId"
        :resume-blueprint-id="chatResumeBlueprintId"
      />
      <RobotsPage v-else-if="active === 'robots'" />
      <BlueprintPage
        v-else-if="active === 'blueprint'"
        :from-chat="blueprintFromChat"
        :chat-session-id="blueprintChatSessionId"
        :initial-blueprint-id="blueprintInitialId"
      />
      <MaterialsLibraryPage v-else-if="active === 'materials'" />
      <ElementLibraryPage v-else-if="active === 'elements'" />
      <RenderTemplatePage v-else-if="active === 'render'" />
      <WordStylePage v-else-if="active === 'word-style'" />
      <UserPage
        v-else-if="active === 'user'"
        :key="userPageTab"
        :initial-tab="userPageTab"
        panel-only
      />
    </div>
  </div>
  </a-config-provider>
</template>

<script setup>
import { computed, onMounted, onUnmounted, provide, ref } from "vue";
import zhCN from "ant-design-vue/es/locale/zh_CN";

import { ElMessage, antdTheme } from "./ui.js";
import { clearAuth, getAuth, isLoggedIn, request } from "./api";
import { BRAND_NAME } from "./brand";
import BlueprintPage from "./pages/BlueprintPage.vue";
import ChatPage from "./pages/ChatPage.vue";
import ElementLibraryPage from "./pages/ElementLibraryPage.vue";
import MaterialsLibraryPage from "./pages/MaterialsLibraryPage.vue";
import AuthPage from "./pages/AuthPage.vue";
import RenderTemplatePage from "./pages/RenderTemplatePage.vue";
import RobotsPage from "./pages/RobotsPage.vue";
import UserPage from "./pages/UserPage.vue";
import WordStylePage from "./pages/WordStylePage.vue";

const brandName = BRAND_NAME;
const active = ref("chat");
const chatResumeToken = ref(0);
const chatResumeSessionId = ref("");
const chatResumeBlueprintId = ref("");
const blueprintFromChat = ref(false);
const blueprintChatSessionId = ref("");
const blueprintInitialId = ref("");
const APP_PAGES = ["chat", "robots", "blueprint", "materials", "elements", "render", "word-style", "user"];
const VALID_PAGES = [...APP_PAGES];
const PENDING_ROUTE_KEY = "rw_pending_route";
const loggedIn = ref(isLoggedIn());
const authTab = ref("login");
const userDisplayName = ref(isLoggedIn() ? getAuth()?.display_name || getAuth()?.username || "" : "");
const userMenuOpen = ref(false);
const userMenuWrap = ref(null);
const userPageTab = ref("account");
const tokenSummary = ref({ total_tokens: 0, total_calls: 0 });

const userInitial = computed(() => {
  const name = (userDisplayName.value || "").trim();
  if (!name) return "用";
  return name.charAt(0);
});

async function refreshUserLabel() {
  loggedIn.value = isLoggedIn();
  if (!loggedIn.value) {
    userDisplayName.value = "未登录";
    return;
  }
  const auth = getAuth();
  userDisplayName.value = auth?.display_name || auth?.username || "用户";
  try {
    const profile = await request("/api/writing/users/me");
    if (profile?.display_name || profile?.username) {
      userDisplayName.value = profile.display_name || profile.username;
    }
  } catch {
    /* 保持本地缓存名称 */
  }
}

function onAuthChange() {
  loggedIn.value = isLoggedIn();
  if (loggedIn.value) {
    refreshUserLabel();
    restorePendingRoute();
  } else {
    userDisplayName.value = "";
    rememberRouteForLogin();
    syncAuthUrl();
  }
}

function rememberRouteForLogin() {
  const params = readQuery();
  const page = params.get("page") || active.value;
  if (!page || !APP_PAGES.includes(page)) return;
  const snapshot = {
    page,
    from: params.get("from") || "",
    session_id: params.get("session_id") || "",
    blueprint_id: params.get("blueprint_id") || "",
    user_tab: params.get("user_tab") || "",
  };
  sessionStorage.setItem(PENDING_ROUTE_KEY, JSON.stringify(snapshot));
}

function restorePendingRoute() {
  let target = "chat";
  let query = {};
  try {
    const raw = sessionStorage.getItem(PENDING_ROUTE_KEY);
    if (raw) {
      const snap = JSON.parse(raw);
      if (snap?.page && APP_PAGES.includes(snap.page)) {
        target = snap.page;
        if (snap.from) query.from = snap.from;
        if (snap.session_id) query.session_id = snap.session_id;
        if (snap.blueprint_id) query.blueprint_id = snap.blueprint_id;
        if (snap.user_tab) query.user_tab = snap.user_tab;
      }
    }
  } catch {
    /* 忽略损坏的缓存 */
  }
  sessionStorage.removeItem(PENDING_ROUTE_KEY);
  navigateTo(target, query);
}

function syncAuthUrl() {
  const params = new URLSearchParams();
  if (authTab.value === "register") params.set("auth", "register");
  writeQuery(params);
}

function onAuthSuccess() {
  loggedIn.value = true;
  refreshUserLabel();
  restorePendingRoute();
}

function readAuthTabFromUrl() {
  const params = readQuery();
  authTab.value = params.get("auth") === "register" ? "register" : "login";
}

async function loadTokenSummary() {
  try {
    tokenSummary.value = (await request("/api/writing/users/me/token-usage")) || {};
  } catch {
    tokenSummary.value = { total_tokens: 0, total_calls: 0 };
  }
}

function toggleUserMenu() {
  userMenuOpen.value = !userMenuOpen.value;
  if (userMenuOpen.value) loadTokenSummary();
}

function closeUserMenu() {
  userMenuOpen.value = false;
}

function openUserTab(tab) {
  const name = tab || "account";
  userPageTab.value = name;
  active.value = "user";
  closeUserMenu();
  navigateTo("user", { user_tab: name });
}

function doLogout() {
  rememberRouteForLogin();
  clearAuth();
  loggedIn.value = false;
  userDisplayName.value = "";
  tokenSummary.value = { total_tokens: 0, total_calls: 0 };
  closeUserMenu();
  authTab.value = "login";
  syncAuthUrl();
  ElMessage.info("已退出登录");
}

function onDocumentClick(event) {
  if (!userMenuOpen.value) return;
  const el = userMenuWrap.value;
  if (el && !el.contains(event.target)) closeUserMenu();
}

onMounted(() => {
  if (isLoggedIn()) {
    applyRouteFromUrl();
    refreshUserLabel();
  } else {
    readAuthTabFromUrl();
    rememberRouteForLogin();
    syncAuthUrl();
  }
  window.addEventListener("rw-auth-change", onAuthChange);
  document.addEventListener("click", onDocumentClick);
});

onUnmounted(() => {
  window.removeEventListener("rw-auth-change", onAuthChange);
  document.removeEventListener("click", onDocumentClick);
});

function readQuery() {
  return new URLSearchParams(window.location.search || "");
}

function writeQuery(params) {
  const qs = params.toString();
  const url = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash || ""}`;
  window.history.replaceState({}, "", url);
}

function navigateTo(page, query = {}) {
  if (!isLoggedIn()) {
    if (page === "register") authTab.value = "register";
    else authTab.value = "login";
    syncAuthUrl();
    return;
  }
  const target = VALID_PAGES.includes(page) ? page : "chat";
  active.value = target;
  closeUserMenu();

  const params = new URLSearchParams();
  params.set("page", target);

  for (const [key, value] of Object.entries(query || {})) {
    if (value != null && String(value).trim()) params.set(key, String(value));
  }

  if (target === "blueprint") {
    const from = query.from || params.get("from") || "";
    blueprintFromChat.value = from === "chat";
    blueprintChatSessionId.value = query.session_id || params.get("session_id") || "";
    blueprintInitialId.value = query.blueprint_id || params.get("blueprint_id") || "";
    if (blueprintFromChat.value) params.set("from", "chat");
    if (blueprintChatSessionId.value) params.set("session_id", blueprintChatSessionId.value);
    if (blueprintInitialId.value) params.set("blueprint_id", blueprintInitialId.value);
  } else {
    params.delete("from");
    if (target !== "chat") {
      params.delete("session_id");
      params.delete("blueprint_id");
    }
  }

  if (target === "chat" && (query.from === "blueprint" || query.session_id)) {
    chatResumeSessionId.value = query.session_id || "";
    chatResumeBlueprintId.value = query.blueprint_id || "";
    chatResumeToken.value += 1;
    params.set("from", "blueprint");
    if (chatResumeSessionId.value) params.set("session_id", chatResumeSessionId.value);
    if (chatResumeBlueprintId.value) params.set("blueprint_id", chatResumeBlueprintId.value);
  } else if (target === "chat") {
    params.delete("from");
    params.delete("session_id");
    params.delete("blueprint_id");
  }

  if (target === "user") {
    const tab = query.user_tab || params.get("user_tab") || userPageTab.value || "account";
    userPageTab.value = tab;
    params.set("user_tab", tab);
  } else {
    params.delete("user_tab");
  }

  writeQuery(params);
}

function applyRouteFromUrl() {
  if (!isLoggedIn()) return;
  const params = readQuery();
  const page = params.get("page") || "chat";
  const from = params.get("from") || "";
  const sessionId = params.get("session_id") || "";
  const blueprintId = params.get("blueprint_id") || "";
  const userTab = params.get("user_tab") || "";

  if (userTab) userPageTab.value = userTab;

  if (page === "chat" && from === "blueprint" && sessionId) {
    active.value = "chat";
    chatResumeSessionId.value = sessionId;
    chatResumeBlueprintId.value = blueprintId;
    chatResumeToken.value += 1;
    return;
  }

  if (page === "blueprint") {
    blueprintFromChat.value = from === "chat";
    blueprintChatSessionId.value = sessionId;
    blueprintInitialId.value = blueprintId;
  }

  active.value = VALID_PAGES.includes(page) ? page : "chat";
}

if (isLoggedIn()) applyRouteFromUrl();

provide("navigateTo", navigateTo);

const mainMenus = [
  {
    id: "chat",
    label: "对话",
    icon: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-4 3.2V16H6.5A2.5 2.5 0 0 1 4 13.5z"/></svg>`,
  },
  {
    id: "robots",
    label: "机器人",
    icon: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="8" width="12" height="10" rx="2"/><circle cx="9.5" cy="12.5" r="1"/><circle cx="14.5" cy="12.5" r="1"/><path d="M12 4v2M9 18v2M15 18v2M4 12h2M18 12h2"/></svg>`,
  },
  {
    id: "blueprint",
    label: "蓝本",
    icon: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 3.5h7.5L19 8v12.5H7z"/><path d="M14.5 3.5V8H19"/><path d="M10 12h6M10 15.5h6"/></svg>`,
  },
  {
    id: "materials",
    label: "资料库",
    title: "资料库",
    icon: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7.5h16v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M8 7.5V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v1.5"/><path d="M8 12h8M8 15.5h5"/></svg>`,
  },
  {
    id: "elements",
    label: "要素库",
    icon: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="8" cy="12" r="3"/><circle cx="16" cy="8" r="2.5"/><circle cx="16" cy="16" r="2.5"/><path d="M11 12h2M14.2 9.4l1.4 1.2M14.2 14.6l1.4-1.2"/></svg>`,
  },
  {
    id: "render",
    label: "渲染",
    icon: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 12.5h8M8 16h5"/></svg>`,
  },
  {
    id: "word-style",
    label: "Word样式",
    icon: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 4h9l3 3v13H6z"/><path d="M15 4v4h4"/><path d="M8 12h8M8 15h6"/></svg>`,
  },
];
</script>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  height: 100%;
  min-height: 0;
  background: var(--ay-bg);
}
.side-nav {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 14px 0 18px;
  border-right: 1px solid var(--ay-line);
  background: #fbfcfe;
  flex-shrink: 0;
  min-height: 0;
}
.nav-main {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
}
.logo {
  width: 40px;
  height: 40px;
  margin-bottom: 14px;
  padding: 0;
  border: 0;
  border-radius: 12px;
  background: linear-gradient(145deg, #4e6ef2, #6b85f5);
  color: #fff;
  font-size: 18px;
  font-weight: 700;
  line-height: 40px;
  text-align: center;
  box-shadow: 0 8px 18px rgba(78, 110, 242, 0.28);
  cursor: pointer;
}
.logo:hover {
  filter: brightness(1.05);
}
.nav-user-wrap {
  position: relative;
  margin-top: auto;
  flex-shrink: 0;
}
.nav-item-user {
  flex-shrink: 0;
}
.user-menu {
  position: absolute;
  left: calc(100% + 8px);
  bottom: 0;
  z-index: 40;
  min-width: 168px;
  max-height: min(320px, calc(100vh - 24px));
  overflow-y: auto;
  padding: 8px;
  border-radius: 12px;
  border: 1px solid var(--ay-line);
  background: #fff;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.12);
}
.user-menu-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px 10px;
  border-bottom: 1px solid var(--ay-line);
  margin-bottom: 4px;
}
.user-menu-head strong {
  font-size: 13px;
  color: var(--ay-ink);
}
.user-menu-sub {
  font-size: 11px;
  color: var(--ay-muted);
}
.user-menu-item {
  display: block;
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  font-size: 13px;
  color: var(--ay-ink);
  cursor: pointer;
}
.user-menu-item:hover {
  background: #f3f6fb;
}
.user-menu-item.danger {
  color: #c45656;
}
.nav-avatar {
  width: 28px;
  height: 28px;
  border-radius: 9px;
  background: #eef2ff;
  color: var(--ay-blue);
  font-size: 14px;
  font-weight: 700;
  line-height: 28px;
  text-align: center;
}
.nav-item-user.active .nav-avatar {
  background: #fff;
  color: var(--ay-blue);
}
.nav-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 58px;
  padding: 10px 4px 8px;
  border: 0;
  border-radius: 14px;
  background: transparent;
  color: var(--ay-muted);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover {
  background: #f0f3f9;
  color: var(--ay-ink);
}
.nav-item.active {
  background: var(--ay-blue-soft);
  color: var(--ay-blue);
}
.nav-ico {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
}
.nav-label {
  font-size: 11px;
  line-height: 1.2;
}
.workspace {
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.workspace > * {
  flex: 1 1 auto;
  min-height: 0;
  max-height: 100%;
}
@media (max-width: 860px) {
  .shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr);
  }
  .side-nav {
    flex-direction: row;
    justify-content: flex-start;
    align-items: stretch;
    gap: 4px;
    padding: 10px 10px 8px;
    overflow-x: auto;
    overflow-y: visible;
    border-right: 0;
    border-bottom: 1px solid #e6eaf0;
  }
  .nav-main {
    flex-direction: row;
    flex: 1 1 auto;
    overflow-x: auto;
    overflow-y: visible;
  }
  .nav-user-wrap {
    margin-top: 0;
    margin-left: auto;
  }
  .user-menu {
    left: auto;
    right: 0;
    bottom: calc(100% + 6px);
  }
  .logo {
    flex-shrink: 0;
    align-self: center;
    margin: 0 6px 0 0;
  }
  .nav-item {
    flex-shrink: 0;
    width: 54px;
    padding: 8px 4px 6px;
  }
  .nav-label {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .workspace {
    height: auto;
    min-height: 0;
  }
}
</style>
