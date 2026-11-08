<template>
  <a-tree
    class="ui-tree"
    :tree-data="normalizedData"
    :field-names="fieldNames"
    :checkable="showCheckbox"
    :block-node="true"
    :check-strictly="true"
    :selectable="true"
    :selected-keys="selectedKeys"
    :checked-keys="checkedKeys"
    :expanded-keys="expandedKeys"
    :auto-expand-parent="true"
    @select="onSelect"
    @check="onCheck"
    @expand="onExpand"
  >
    <template v-if="$slots.default" #title="node">
      <slot :data="node" :node="node" />
    </template>
  </a-tree>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  data: { type: Array, default: () => [] },
  nodeKey: { type: String, default: "key" },
  props: { type: Object, default: () => ({}) },
  showCheckbox: { type: Boolean, default: false },
  defaultExpandAll: { type: Boolean, default: false },
  defaultExpandedKeys: { type: Array, default: () => [] },
  expandOnClickNode: { type: Boolean, default: false },
  checkOnClickNode: { type: Boolean, default: false },
  highlightCurrent: { type: Boolean, default: false },
  currentNodeKey: { type: [String, Number], default: undefined },
  indent: { type: Number, default: 18 },
});

const emit = defineEmits(["check", "node-click", "current-change"]);

const childrenField = computed(() => props.props?.children || "children");
const labelField = computed(() => props.props?.label || "label");
const keyField = computed(() => props.nodeKey || "key");
const disabledField = computed(() => props.props?.disabled || "disabled");

const fieldNames = computed(() => ({
  title: labelField.value,
  key: keyField.value,
  children: childrenField.value,
}));

const checkedKeys = ref([]);
const selectedKeys = ref([]);
const expandedKeys = ref([]);

const normalizedData = computed(() => {
  const childKey = childrenField.value;
  const disKey = disabledField.value;
  const map = (nodes) =>
    (nodes || []).map((node) => {
      const children = map(node?.[childKey] || []);
      const disabled = Boolean(node?.[disKey]);
      return {
        ...node,
        [childKey]: children,
        disableCheckbox: disabled,
      };
    });
  return map(props.data);
});

function walk(nodes, visit) {
  for (const node of nodes || []) {
    visit(node);
    walk(node?.[childrenField.value] || [], visit);
  }
}

function allKeys() {
  const keys = [];
  walk(normalizedData.value, (node) => {
    const k = node?.[keyField.value];
    if (k != null && k !== "") keys.push(k);
  });
  return keys;
}

function findNode(key) {
  let hit = null;
  walk(normalizedData.value, (node) => {
    if (String(node?.[keyField.value]) === String(key)) hit = node;
  });
  return hit;
}

watch(
  () => [normalizedData.value, props.defaultExpandAll, props.defaultExpandedKeys],
  () => {
    if (props.defaultExpandAll) expandedKeys.value = allKeys();
    else if (props.defaultExpandedKeys?.length) expandedKeys.value = [...props.defaultExpandedKeys];
  },
  { immediate: true, deep: true }
);

watch(
  () => props.currentNodeKey,
  (key) => {
    selectedKeys.value = key != null && key !== "" ? [key] : [];
  },
  { immediate: true }
);

function unwrapChecked(arg) {
  if (Array.isArray(arg)) return arg;
  if (arg && Array.isArray(arg.checked)) return arg.checked;
  return [];
}

function onCheck(keys) {
  checkedKeys.value = unwrapChecked(keys).map((k) => k);
  emit("check", null, { checkedKeys: checkedKeys.value });
}

function onSelect(keys, info) {
  selectedKeys.value = keys || [];
  const data = info?.node?.dataRef || info?.node || findNode(keys?.[0]);
  if (data) {
    emit("node-click", data, data);
    emit("current-change", data);
  }
}

function onExpand(keys) {
  expandedKeys.value = keys || [];
}

function getCheckedKeys() {
  return [...checkedKeys.value];
}

function setCheckedKeys(keys) {
  checkedKeys.value = [...(keys || [])];
}

function setCurrentKey(key) {
  selectedKeys.value = key != null && key !== "" ? [key] : [];
}

defineExpose({ getCheckedKeys, setCheckedKeys, setCurrentKey });
</script>
