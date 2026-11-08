<template>
  <Bubble
    class="chat-bubble"
    :class="role"
    :placement="placement"
    content=""
    variant="filled"
    shape="corner"
    :styles="styles"
    :root-class-name="`chat-bubble-root chat-bubble-${role}`"
  >
    <template #avatar>
      <a-avatar class="chat-bubble-avatar" :size="34" :style="avatarStyle">
        {{ role === "user" ? "我" : "AI" }}
      </a-avatar>
    </template>
    <template v-if="header" #header>
      <div class="bubble-doc-tag">{{ header }}</div>
    </template>
    <template #message>
      <div class="body rich">
        <span v-html="html" />
        <span v-if="streaming" class="cursor">▍</span>
      </div>
      <ul
        v-if="processSteps.length"
        class="process-log"
        :class="{ done: !streaming }"
      >
        <li v-for="(step, si) in processSteps" :key="si" :class="step.kind">
          <span class="dot" />
          <div class="step-body">
            <span>{{ step.text }}</span>
            <div
              v-if="step.html"
              class="draft-preview"
              v-html="step.html"
            />
            <pre
              v-if="step.debug"
              class="chain-debug-detail"
            >{{ step.debug }}</pre>
          </div>
        </li>
      </ul>
    </template>
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>
  </Bubble>
</template>

<script setup>
import { computed } from "vue";
import { Bubble } from "ant-design-x-vue";

const props = defineProps({
  role: { type: String, default: "assistant" },
  header: { type: String, default: "" },
  html: { type: String, default: "" },
  streaming: { type: Boolean, default: false },
  processSteps: { type: Array, default: () => [] },
});

const placement = computed(() => (props.role === "user" ? "end" : "start"));

const avatarStyle = computed(() =>
  props.role === "user"
    ? { background: "#e8f0fe", color: "#4e6ef2", fontWeight: 650, fontSize: "12px" }
    : { background: "#eef2ff", color: "#4e6ef2", fontWeight: 650, fontSize: "12px" }
);

const styles = computed(() => {
  // 不设统一 borderRadius，交给 shape="corner" 收尖朝向头像的一角
  if (props.role === "user") {
    return {
      content: {
        background: "#4e6ef2",
        color: "#fff",
        padding: "12px 14px",
      },
      header: { textAlign: "right", color: "rgba(255,255,255,0.72)" },
    };
  }
  return {
    content: {
      background: "#fff",
      border: "1px solid var(--ay-line, #e8ecf4)",
      padding: "12px 14px",
    },
  };
});
</script>

<style scoped>
.chat-bubble {
  width: 100%;
}
.chat-bubble :deep(.ant-bubble-content) {
  max-width: min(560px, 78vw);
}
.chat-bubble-avatar {
  border-radius: 10px !important;
}
.body {
  line-height: 1.55;
  font-size: 14px;
}
.bubble-doc-tag {
  font-size: 11px;
  line-height: 1.3;
  color: var(--ay-muted, #888);
  padding: 0 2px;
}
.cursor {
  display: inline-block;
  margin-left: 1px;
  color: var(--ay-blue, #4e6ef2);
  animation: blink 1s step-end infinite;
}
.chat-bubble.user .cursor {
  color: rgba(255, 255, 255, 0.85);
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
.process-log {
  list-style: none;
  margin: 8px 0 0;
  padding: 8px 10px;
  border-radius: 12px;
  border: 1px dashed #c5d0f5;
  background: #f5f7ff;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.process-log.done {
  border-color: #cbd5e1;
  background: #f8fafc;
  opacity: 0.9;
}
.process-log.done .dot {
  background: #94a3b8;
}
.process-log li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  color: #475569;
  line-height: 1.45;
}
.process-log .step-body {
  flex: 1;
  min-width: 0;
}
.process-log .dot {
  width: 6px;
  height: 6px;
  margin-top: 6px;
  border-radius: 50%;
  background: #4e6ef2;
  flex: 0 0 auto;
}
.process-log .error {
  color: #b91c1c;
}
.process-log .error .dot {
  background: #ef4444;
}
.process-log .chapter {
  color: #1d4ed8;
  font-weight: 650;
}
.process-log .chapter .dot {
  background: #3b82f6;
}
.process-log .step .dot {
  background: #64748b;
}
.process-log .chain {
  color: #0f766e;
  font-size: 12px;
}
.process-log .chain .dot {
  background: #14b8a6;
}
.process-log .draft {
  align-items: stretch;
  margin-top: 2px;
  padding: 8px 10px;
  border-radius: 10px;
  background: #fff7ed;
  border: 1px solid #fdba74;
  color: #9a3412;
  font-weight: 650;
}
.process-log .draft .dot {
  background: #f97316;
  margin-top: 8px;
}
.process-log .draft .step-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.draft-preview {
  font-weight: 400;
  color: #431407;
  font-size: 12px;
  line-height: 1.5;
  max-height: 240px;
  overflow: auto;
  background: #fff;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  padding: 8px 10px;
}
.draft-preview :deep(p) {
  margin: 0 0 6px;
}
.draft-preview :deep(p:last-child) {
  margin-bottom: 0;
}
.draft-preview :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}
.draft-preview :deep(th),
.draft-preview :deep(td) {
  border: 1px solid #fed7aa;
  padding: 3px 6px;
  text-align: left;
}
.chain-debug-detail {
  margin: 6px 0 0 14px;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  font-size: 11px;
  line-height: 1.35;
  white-space: pre-wrap;
  word-break: break-word;
  background: #f0fdfa;
  border: 1px solid #99f6e4;
  border-radius: 6px;
  color: #134e4a;
}
</style>
