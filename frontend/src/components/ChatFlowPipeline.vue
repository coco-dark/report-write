<template>
  <div class="flow-pipeline" role="list" aria-label="流程进度">
    <template v-for="(node, index) in nodes" :key="node.id">
      <div
        class="flow-node"
        role="listitem"
        :class="{
          active: !snapshotDone && node.id === current,
          done: index < currentIndex || (snapshotDone && index <= currentIndex),
          pending: snapshotDone ? index > currentIndex : index > currentIndex,
          running: !snapshotDone && running && node.id === current,
          clickable: canSelect(index),
        }"
        :tabindex="canSelect(index) ? 0 : -1"
        :aria-disabled="canSelect(index) ? undefined : 'true'"
        :title="canSelect(index) ? `回到「${node.label}」` : undefined"
        @click="onSelect(index, node)"
        @keydown.enter.prevent="onSelect(index, node)"
        @keydown.space.prevent="onSelect(index, node)"
      >
        <span class="flow-dot">{{
          index < currentIndex || (snapshotDone && index <= currentIndex)
            ? "✓"
            : !snapshotDone && running && node.id === current
              ? "…"
              : index + 1
        }}</span>
        <span class="flow-label">{{ node.label }}</span>
      </div>
      <span
        v-if="index < nodes.length - 1"
        class="flow-line"
        :class="{ done: index < currentIndex || (snapshotDone && index < currentIndex) }"
      />
    </template>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  current: { type: String, default: "" },
  running: { type: Boolean, default: false },
  /** 历史步骤快照：当前步也显示为已完成 ✓，不再闪「进行中」 */
  snapshotDone: { type: Boolean, default: false },
  /** 禁用点击回退（例如历史卡） */
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(["select-step"]);

const currentIndex = computed(() => {
  const i = props.nodes.findIndex((n) => n.id === props.current);
  return i >= 0 ? i : 0;
});

/** 仅允许点当前步之前的已完成节点（不可跳未来步） */
function canSelect(index) {
  if (props.disabled || props.snapshotDone) return false;
  return index < currentIndex.value;
}

function onSelect(index, node) {
  if (!canSelect(index) || !node) return;
  emit("select-step", { id: String(node.id || "").trim(), label: node.label, index });
}
</script>

<style scoped>
.flow-pipeline {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 0;
  margin: 0 0 14px;
  padding: 10px 8px;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
  border: 1px solid var(--ay-line, #e2e8f0);
}
.flow-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 52px;
  max-width: 72px;
  text-align: center;
}
.flow-node.clickable {
  cursor: pointer;
}
.flow-node.clickable:hover .flow-dot {
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.28);
}
.flow-node.clickable:hover .flow-label {
  color: #047857;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.flow-dot {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: #e2e8f0;
  color: #64748b;
}
.flow-node.active .flow-dot {
  background: #4e6ef2;
  color: #fff;
  box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.25);
}
.flow-node.running .flow-dot {
  animation: flow-pulse 1.2s ease-in-out infinite;
}
@keyframes flow-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.25);
  }
  50% {
    box-shadow: 0 0 0 6px rgba(78, 110, 242, 0.12);
  }
}
.flow-node.done .flow-dot {
  background: #10b981;
  color: #fff;
}
.flow-label {
  font-size: 10px;
  line-height: 1.25;
  color: #64748b;
}
.flow-node.active .flow-label {
  color: #1e40af;
  font-weight: 600;
}
.flow-node.done .flow-label {
  color: #047857;
}
.flow-line {
  flex: 1 1 12px;
  min-width: 12px;
  max-width: 28px;
  height: 2px;
  margin-top: 13px;
  background: #cbd5e1;
  border-radius: 1px;
}
.flow-line.done {
  background: #34d399;
}
</style>
