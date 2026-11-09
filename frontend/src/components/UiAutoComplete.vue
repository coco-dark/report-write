<template>
  <a-auto-complete
    v-model:value="inner"
    :options="options"
    :allow-clear="clearable"
    :placeholder="placeholder"
    :disabled="disabled"
    :size="size"
    :style="style"
    :popup-class-name="popperClass || popupClassName"
    @search="onSearch"
    @select="onSelect"
    @change="onChange"
  />
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  fetchSuggestions: { type: Function, default: null },
  clearable: { type: Boolean, default: false },
  placeholder: { type: String, default: "" },
  disabled: { type: Boolean, default: false },
  size: { type: String, default: "middle" },
  style: { type: [String, Object], default: "" },
  popperClass: { type: String, default: "" },
  popupClassName: { type: String, default: "" },
});

const emit = defineEmits(["update:modelValue", "select", "change"]);

const inner = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v == null ? "" : String(v)),
});

const options = ref([]);

function onSearch(query) {
  const fetch = props.fetchSuggestions;
  if (typeof fetch !== "function") return;
  fetch(query || "", (list) => {
    options.value = (list || []).map((item) => {
      if (typeof item === "string") return { value: item };
      const value = item.value ?? item.label ?? "";
      return { value, label: item.label || value };
    });
  });
}

function onSelect(value) {
  emit("select", value);
  emit("change", value);
}

function onChange(value) {
  emit("change", value);
}

watch(
  () => props.modelValue,
  (v) => {
    if (v && !options.value.length) onSearch(v);
  }
);
</script>
