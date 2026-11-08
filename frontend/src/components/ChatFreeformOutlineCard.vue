<template>
  <div class="card freeform-outline-card">
    <div class="card-title">{{ title || "章节目录" }}</div>
    <p class="card-hint">
      <template v-if="editable">
        目录树与拓扑图请在右侧<strong>文档窗口</strong>查看和调整。满意后点「确认大纲并继续」。
      </template>
      <template v-else>
        目录已锁定。改标题/增删章请点「修改大纲」；关联要素可在文档窗口拓扑图操作。
      </template>
    </p>
    <p v-if="!hasDocName" class="card-hint name-warn">
      文档名还没定清楚。在对话里告诉我写什么、叫什么名字即可，定好后会显示在文档窗口。
    </p>
    <p v-else-if="docTitle" class="card-hint doc-name-line">
      文档名：<strong>{{ docTitle }}</strong>
      <button type="button" class="rename-link" :disabled="disabled || busy" @click="$emit('rename')">
        改名
      </button>
    </p>
    <p v-if="chapterCount" class="card-hint">已加载 {{ chapterCount }} 章，详情见文档窗口。</p>
    <p v-else-if="loading" class="card-hint">正在加载目录…</p>
    <p v-else class="card-hint">目录为空，请点「重新生成」或由助手重新起草。</p>
    <div class="confirm-actions">
      <template v-if="editable">
        <a-button class="chat-action-btn" :loading="busy" :disabled="disabled" @click="$emit('regenerate')" shape="round">
          重新生成
        </a-button>
        <a-button
 class="chat-action-btn"
 type="primary"
 :loading="busy"
 :disabled="disabled || !chapterCount"
 @click="$emit('confirm')"
 shape="round">
          确认大纲并继续
        </a-button>
        <a-button
 v-if="canCollectBlueprint"
 class="chat-action-btn"
 :loading="busy"
 :disabled="disabled || !chapterCount"
 @click="$emit('collect-blueprint')"
 shape="round">
          收录到蓝本库
        </a-button>
      </template>
      <template v-else>
        <a-button class="chat-action-btn" type="primary" :loading="busy" :disabled="disabled" @click="$emit('reopen')" shape="round">
          修改大纲
        </a-button>
        <a-button class="chat-action-btn" :loading="busy" :disabled="disabled" @click="$emit('regenerate')" shape="round">
          重新生成
        </a-button>
        <a-button
 v-if="canCollectBlueprint"
 class="chat-action-btn"
 :loading="busy"
 :disabled="disabled || !chapterCount"
 @click="$emit('collect-blueprint')"
 shape="round">
          收录到蓝本库
        </a-button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { request } from "../api";

const props = defineProps({
  title: { type: String, default: "" },
  taskId: { type: String, default: "" },
  editable: { type: Boolean, default: true },
  chapters: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  docTitle: { type: String, default: "" },
  docCategory: { type: String, default: "" },
  /** 已收录到蓝本库后为 false */
  canCollectBlueprint: { type: Boolean, default: true },
});

defineEmits(["confirm", "regenerate", "reopen", "collect-blueprint", "rename"]);

const taskData = ref(null);
const loading = ref(false);

const chapterCount = computed(() => {
  const fromTask = taskData.value?.outline?.chapters?.length || 0;
  if (fromTask) return fromTask;
  return props.chapters?.length || 0;
});

const hasDocName = computed(() => {
  const name = String(props.docTitle || "").trim();
  if (!name) return false;
  if (name === "对话写作任务" || name === "未命名" || name === "未命名文档") return false;
  return true;
});

async function loadTask() {
  const tid = String(props.taskId || "").trim();
  if (!tid) {
    taskData.value = null;
    return;
  }
  loading.value = true;
  try {
    taskData.value = await request(`/api/writing/tasks/${encodeURIComponent(tid)}`);
  } catch {
    taskData.value = null;
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.taskId,
  () => {
    loadTask();
  },
  { immediate: true }
);
</script>

<style scoped>
/* 子组件拿不到 ChatPage scoped 的 .card-title/.card-hint，对齐会话正文 13px */
.freeform-outline-card {
  font-size: 13px;
  line-height: 1.55;
  color: #334155;
}
.freeform-outline-card .card-title {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 650;
  color: #0f172a;
}
.freeform-outline-card .card-hint {
  margin: 0 0 6px;
  font-size: 13px;
  color: #64748b;
  line-height: 1.5;
}
.confirm-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.name-warn {
  color: #b45309;
}
.doc-name-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.rename-link {
  border: 0;
  background: transparent;
  color: #4338ca;
  font-size: 13px;
  cursor: pointer;
  padding: 0;
}
.rename-link:hover:not(:disabled) {
  text-decoration: underline;
}
.rename-link:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
