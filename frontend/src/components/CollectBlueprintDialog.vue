<template>
  <a-modal
 :open="modelValue"
 :title="dialogTitle"
 width="480px"
 :mask-closable="false"
 @update:open="(open) => !open && onCancel()"
 @cancel="onCancel"
 destroy-on-close centered>
    <p v-if="duplicateHint" class="dup-hint">{{ duplicateHint }}</p>
    <a-form layout="vertical" @submit.prevent>
      <a-form-item label="蓝本名称" required>
        <a-input
          v-model:value="form.title"
          maxlength="80"
          show-count
          placeholder="如：安全预评价正文蓝本"
          @keyup.enter="onConfirm"
        />
      </a-form-item>
      <a-form-item label="分类" required>
        <a-input v-model:value="form.category" maxlength="40" placeholder="如：安全评价" />
      </a-form-item>
      <a-form-item label="标签" required>
        <a-select
 ref="tagsSelectRef"
 v-model:value="form.tags"
 placeholder="至少填一个；输入后回车，或直接点收录"
 style="width: 100%"
 mode="tags" show-search>
          <a-select-option v-for="t in knownTags" :key="t" :value="t">{{ t }}</a-select-option>
        </a-select>
      </a-form-item>
    </a-form>
    <template #footer>
      <a-button @click="onCancel">取消</a-button>
      <a-button type="primary" :loading="busy" @click="onConfirm">收录</a-button>
    </template>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { ElMessage } from "../ui.js";

import { authHeaders, request } from "../api";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  suggestedTitle: { type: String, default: "" },
  suggestedCategory: { type: String, default: "" },
  suggestedTags: { type: Array, default: () => [] },
  knownTags: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "confirm", "cancel"]);

const form = reactive({
  title: "",
  category: "",
  tags: [],
});
const tagsSelectRef = ref(null);
const duplicateHint = ref("");
const checking = ref(false);

/** 输入了但还没回车确认的标签文字 */
function pendingTagFromSelect() {
  const inst = tagsSelectRef.value;
  if (!inst) return "";
  const raw =
    inst.inputValue ??
    inst.states?.inputValue ??
    inst.query ??
    inst?.$refs?.input?.value ??
    "";
  return String(raw || "").trim();
}

const dialogTitle = computed(() =>
  duplicateHint.value ? "名称重复，请换一个名字" : "收录到蓝本库"
);

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    form.title = String(props.suggestedTitle || "").trim();
    form.category = String(props.suggestedCategory || "").trim();
    form.tags = (props.suggestedTags || [])
      .map((t) => String(t || "").trim())
      .filter(Boolean)
      .filter((t) => t.toLowerCase() !== "from_freeform");
    duplicateHint.value = "";
  }
);

async function checkTitleDuplicate(title) {
  const tip = String(title || "").trim();
  if (!tip) return false;
  try {
    checking.value = true;
    const data = await request(
      `/api/writing/blueprints/check-title?title=${encodeURIComponent(tip)}`,
      { headers: authHeaders() }
    );
    if (data?.exists) {
      duplicateHint.value = `蓝本库已有同名「${data.items?.[0]?.title || tip}」，请填写新的名称。`;
      return true;
    }
    duplicateHint.value = "";
    return false;
  } catch {
    return false;
  } finally {
    checking.value = false;
  }
}

async function onConfirm() {
  const title = String(form.title || "").trim();
  const category = String(form.category || "").trim();
  let tags = (form.tags || []).map((t) => String(t || "").trim()).filter(Boolean);
  // 用户输入后直接点「收录」、未回车时，把输入框残值收成标签
  const pending = pendingTagFromSelect();
  if (pending && !tags.includes(pending)) {
    tags = [...tags, pending];
    form.tags = tags;
  }
  if (!title) {
    ElMessage.warning("请填写蓝本名称");
    return;
  }
  if (!category) {
    ElMessage.warning("请填写蓝本分类");
    return;
  }
  if (!tags.length) {
    ElMessage.warning("请至少填写一个蓝本标签（输入后回车，或直接点收录）");
    return;
  }
  if (await checkTitleDuplicate(title)) {
    return;
  }
  emit("confirm", { title, category, tags });
}

function onCancel() {
  emit("update:modelValue", false);
  emit("cancel");
}
</script>

<style scoped>
.dup-hint {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff7e6;
  color: #ad6800;
  font-size: 13px;
  line-height: 1.45;
}
</style>
