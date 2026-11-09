<template>
  <a-timeline v-if="steps.length">
    <a-timeline-item
 v-for="(item, index) in steps"
 :key="`${item.key || item.step}-${index}`"
 :label="statusText(item.status)"
 >
      <div class="step-title">
        <strong>{{ stepName(item.step) }}</strong>
        <span v-if="item.title">{{ item.title }}</span>
        <a-tag v-if="item.chapter" size="small">{{ item.chapter }}</a-tag>
      </div>
      <div class="step-body">
        <template v-if="item.step === 'upload'">
          <p v-if="item.result?.template">模版原文 {{ item.result.template }}</p>
          <ul>
            <li v-for="name in item.result?.files || []" :key="name">{{ name }}</li>
          </ul>
        </template>
        <template v-else-if="item.step === 'study' && item.status === 'done'">
          <p>
            {{ item.result?.from_cache ? "沿用已保存的分析结果" : "分析结果已保存" }}，共
            {{ (item.result?.chapters || []).length }} 章。请按序号逐章核对。
          </p>
          <ol class="ordered">
            <li v-for="chapter in item.result?.chapters || []" :key="chapter.key">
              {{ chapter.sequence || "未编号" }} {{ chapter.title }}
            </li>
          </ol>
        </template>
        <template v-else-if="item.step === 'search' && item.result?.cards">
          <p>采纳 {{ item.result.adopted }} 条。{{ item.result.note || "" }}</p>
          <ul>
            <li v-for="(card, cardIndex) in item.result.cards" :key="cardIndex" class="clip">
              {{ card.adopted ? "采纳" : "未采纳" }} {{ card.source_file }} {{ card.excerpt }}
            </li>
          </ul>
        </template>
        <div
          v-else-if="item.step === 'write' && item.result?.paragraphs"
          class="draft html-draft"
          v-html="hideEvidenceMarks((item.result.paragraphs || []).join('\n'))"
        />
        <p v-else-if="item.step === 'check' && item.status === 'done'">
          {{ label(item.result?.action) }} {{ item.result?.reason || "" }}
        </p>
        <p v-else-if="item.result?.note">{{ item.result.note }}</p>
      </div>
    </a-timeline-item>
  </a-timeline>
</template>

<script setup>
import { STEP_NAMES, STEP_STATUS, STATUS_LABELS } from "../constants";

defineProps({
  steps: { type: Array, default: () => [] },
});

function stepName(step) {
  return STEP_NAMES[step] || step;
}

function statusText(status) {
  return STEP_STATUS[status] || STATUS_LABELS[status] || status;
}

function label(value) {
  return STATUS_LABELS[value] || value || "";
}

function hideEvidenceMarks(html) {
  return String(html || "")
    .replace(/\[ev-\d+\]/g, "")
    .replace(/[ \t]{2,}/g, " ");
}
</script>

<style scoped>
.step-title {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.step-body {
  color: var(--ay-ink);
  font-size: 13px;
}
.draft {
  white-space: pre-wrap;
}
.html-draft {
  white-space: normal;
  line-height: 1.55;
}
.html-draft :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  font-size: 12px;
}
.html-draft :deep(th),
.html-draft :deep(td) {
  border: 1px solid var(--ay-line);
  padding: 4px 8px;
}
.html-draft :deep(.image-placeholder) {
  margin: 8px 0;
  padding: 12px;
  border: 1px dashed var(--ay-line);
  color: var(--ay-muted);
  text-align: center;
}
.clip {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.ordered {
  margin: 6px 0;
  padding-left: 18px;
}
</style>
