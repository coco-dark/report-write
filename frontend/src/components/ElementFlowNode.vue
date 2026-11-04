<template>
  <div class="element-node" :class="{ selected }">
    <Handle type="target" :position="Position.Left" class="handle" />
    <div class="inner">
      <span class="badge">要素</span>
      <div class="title" :title="data.title || data.name || '未命名要素'">
        {{ data.title || data.name || "未命名要素" }}
      </div>
      <div class="kind">{{ kindLabel }}</div>
    </div>
    <Handle type="source" :position="Position.Right" class="handle" />
  </div>
</template>

<script setup>
import { Handle, Position } from "@vue-flow/core";
import { computed } from "vue";

const props = defineProps({
  data: { type: Object, default: () => ({}) },
  selected: { type: Boolean, default: false },
});

const kindLabel = computed(() => {
  return (
    {
      kv: "键值",
      fixed: "固定值",
      file: "文件",
      rag_search: "文件",
      file_slot: "引用槽位",
      llm_generate: "LLM 生成",
      node_ref: "引用结果",
    }[props.data.kind] ||
    props.data.kind ||
    "要素"
  );
});
</script>

<style scoped>
.element-node {
  width: 96px;
  height: 96px;
  border-radius: 50%;
  border: 1.5px dashed #d97706;
  background: linear-gradient(180deg, #fffbeb 0%, #fef3c7 100%);
  box-shadow: inset 0 0 0 1px rgba(217, 119, 6, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
}
.element-node.selected {
  border-style: solid;
  border-color: #b45309;
  background: linear-gradient(180deg, #fde68a 0%, #fcd34d 100%);
  box-shadow: 0 0 0 2px rgba(180, 83, 9, 0.22);
}
.inner {
  width: 78%;
  text-align: center;
  overflow: hidden;
}
.badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 999px;
  background: #b45309;
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.title {
  margin-top: 4px;
  color: #78350f;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.25;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}
.kind {
  margin-top: 3px;
  color: #92400e;
  font-size: 10px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.handle {
  width: 8px;
  height: 8px;
  border: 1.5px solid #d97706;
  background: #fffbeb;
}
</style>
