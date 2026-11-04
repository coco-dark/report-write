<template>
  <div class="chapter-node" :class="{ selected }">
    <Handle type="target" :position="Position.Left" class="handle" />
    <div class="badge">章节</div>
    <div class="seq">{{ data.sequence || "—" }}</div>
    <div class="title">{{ data.title }}</div>
    <div class="key" v-if="data.key">{{ data.key }}</div>
    <div class="meta">
      <span v-if="data.fixedOutput" class="fixed">固定输出</span>
      <span v-if="data.hasRun" class="written">已运行 · 点开看正文</span>
      <span v-else-if="data.hasStudy" class="studied">已分析 · 点开查看</span>
      <span v-if="data.pointCount">{{ data.pointCount }} 要点</span>
      <span v-if="!data.fixedOutput && data.keywordCount">{{ data.keywordCount }} 检索词</span>
      <span v-if="data.blockCount">{{ data.blockCount }} 版式</span>
      <span v-if="data.elementCount">{{ data.elementCount }} 要素</span>
    </div>
    <Handle type="source" :position="Position.Right" class="handle" />
  </div>
</template>

<script setup>
import { Handle, Position } from "@vue-flow/core";

defineProps({
  data: { type: Object, default: () => ({}) },
  selected: { type: Boolean, default: false },
});
</script>

<style scoped>
.chapter-node {
  position: relative;
  min-width: 168px;
  max-width: 210px;
  padding: 12px 14px 12px 16px;
  border: 1.5px solid #c5ccd8;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.chapter-node::before {
  content: "";
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: #3b82f6;
}
.chapter-node.selected {
  border-color: #2563eb;
  background: #eff6ff;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.18);
}
.badge {
  display: inline-block;
  margin-bottom: 4px;
  padding: 1px 6px;
  border-radius: 3px;
  background: #e2e8f0;
  color: #475569;
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.02em;
}
.chapter-node.selected .badge {
  background: #bfdbfe;
  color: #1d4ed8;
}
.seq {
  color: #64748b;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.title {
  margin-top: 2px;
  color: #0f172a;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.35;
  word-break: break-all;
}
.key {
  margin-top: 4px;
  color: #64748b;
  font-size: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
  color: #16a34a;
  font-size: 11px;
}
.studied {
  color: #15803d;
  font-weight: 650;
}
.written {
  color: #1d4ed8;
  font-weight: 650;
}
.fixed {
  color: #b45309;
  font-weight: 650;
}
.handle {
  width: 8px;
  height: 8px;
  border: 1.5px solid #64748b;
  background: #fff;
}
</style>
