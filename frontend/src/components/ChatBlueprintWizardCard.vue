<template>
  <div class="bp-wizard" :class="{ archived: archived }">
    <ChatFlowPipeline
      :nodes="pipelineNodes"
      :current="pipelineStep"
      :running="!archived && outlineUiBusy"
      :snapshot-done="archived || studyPipelineComplete"
      :disabled="disabled || archived || isHistorical"
      @select-step="onPipelineSelect"
    />

    <div v-if="outlineUiBusy && !archived" class="wizard-running" role="status">
      <span class="running-dot" />
      <span>{{ runningLabel }}</span>
    </div>

    <div v-if="archived" class="wizard-pane wizard-archived">
      <p class="archived-text">{{ archivedSummary }}</p>
    </div>

    <div v-else-if="step === 'meta'" class="wizard-pane">
      <el-input
        v-model="title"
        placeholder="蓝本名称，例如：XX 项目安全预评价"
        :disabled="disabled || busy || metaSubmitLock"
        @keydown.enter.prevent="submitMeta"
      />
      <el-button
        class="chat-action-btn"
        type="primary"
        round
        :loading="busy"
        :disabled="disabled || busy || metaSubmitLock || !title.trim()"
        @click.prevent="submitMeta"
      >
        创建并下一步
      </el-button>
    </div>

    <div v-else-if="step === 'upload'" class="wizard-pane">
      <p class="hint">上传样板 Word 或 PDF，用于提取目录与分析要点。</p>
      <el-upload :show-file-list="false" :http-request="uploadSample" :disabled="disabled || busy">
        <el-button class="chat-action-btn" type="primary" round :loading="busy" :disabled="disabled">
          上传样板
        </el-button>
      </el-upload>
      <ul v-if="processLines.length" class="mini-log">
        <li v-for="(line, i) in processLines" :key="i">{{ line }}</li>
      </ul>
    </div>

    <div v-else-if="step === 'outline'" class="wizard-pane">
      <p class="hint">从样板提取章节目录（过程会显示在下方）。</p>
      <div class="wizard-outline-actions">
        <el-button
          v-if="!isSelectedMode"
          class="chat-action-btn"
          round
          :loading="busy"
          :disabled="disabled || busy || outlineUiBusy"
          @click="requestReUpload"
        >
          重新上传样板
        </el-button>
        <el-button
          class="chat-action-btn"
          type="primary"
          round
          :loading="outlineUiBusy"
          :disabled="disabled || outlineUiBusy || outlineAlreadyExtracted"
          @click="runOutline"
        >
          提取目录
        </el-button>
      </div>
      <ul v-if="processLines.length" class="mini-log">
        <li v-for="(line, i) in processLines" :key="i">{{ line }}</li>
      </ul>
      <p v-else-if="outlineUiBusy" class="hint tiny-hint">正在提取，进度会一条条显示在下方。</p>
    </div>

    <div v-else-if="step === 'outline_confirm'" class="wizard-pane">
      <p class="hint">{{ outlineConfirmHint }}</p>
      <p class="hint tiny-hint">
        想整份一起确认，点下面按钮就行；要是只想先认某几章，直接跟我说「确认第1到3章」也行。
      </p>
      <p v-if="!taskOutline?.chapters?.length && !isSelectedMode" class="hint">目录加载中或为空，可重新提取。</p>
      <p v-else-if="!taskOutline?.chapters?.length && isSelectedMode" class="hint">目录加载中或为空，请换一份已有目录的蓝本。</p>
      <div class="wizard-outline-actions">
        <el-button
          v-if="!isSelectedMode"
          class="chat-action-btn"
          round
          :loading="busy"
          :disabled="disabled || busy || outlineExtractMarked"
          @click="requestReUpload"
        >
          重新上传样板
        </el-button>
        <el-button
          v-if="!isSelectedMode"
          class="chat-action-btn"
          round
          :loading="busy"
          :disabled="disabled || busy || outlineExtractMarked"
          @click="requestReExtract"
        >
          重新提取目录
        </el-button>
        <el-button
          class="chat-action-btn"
          type="primary"
          round
          :loading="busy"
          :disabled="disabled || busy || outlineExtractMarked || !canConfirmOutline"
          @click="confirmOutline"
        >
          确认目录
        </el-button>
      </div>
      <ul v-if="processLines.length" class="mini-log">
        <li v-for="(line, i) in processLines" :key="i">{{ line }}</li>
      </ul>
    </div>

    <div v-else-if="step === 'study'" class="wizard-pane wizard-study-pane">
      <p class="hint">{{ studyHint }}</p>
      <p class="hint tiny-hint">
        可以点「开始分析全部」；对话里也可以说「分析第2章整章」「只要本章」或「分析第1到5章」。
        已分析过的章会保留，继续分析只跑未完成的。完整目录请在右侧文档窗口查看。
      </p>
      <p v-if="studyOrphaned" class="hint warn-hint">
        分析流可能因切换页面已中断，不会自动接着跑。请点「{{ studyButtonLabel }}」从未完成的章继续；已写入的要点会保留。
      </p>
      <div class="study-actions">
        <el-button
          v-if="studyMode === 'confirm_pending'"
          class="chat-action-btn"
          type="primary"
          round
          :loading="busy"
          :disabled="disabled || busy"
          @click="confirmPendingStudy"
        >
          确认分析结果并写入蓝本
        </el-button>
        <el-button
          v-else
          class="chat-action-btn"
          type="primary"
          round
          :loading="busy"
          :disabled="disabled || busy"
          @click="onStudyPrimaryClick"
        >
          {{ studyButtonLabel }}
        </el-button>
        <el-button
          v-if="canWriteAnalyzed"
          class="chat-action-btn"
          round
          plain
          :disabled="disabled || busy"
          @click="emitWriteAnalyzed"
        >
          先写已分析的章节
        </el-button>
        <el-button
          v-if="!isSelectedMode"
          class="chat-action-btn"
          round
          plain
          :disabled="disabled || busy"
          @click="requestReUpload"
        >
          重新上传样板
        </el-button>
        <el-button
          v-if="!isSelectedMode"
          class="chat-action-btn"
          round
          plain
          :disabled="disabled || busy"
          @click="requestReExtract"
        >
          重新提取目录
        </el-button>
        <el-button
          class="chat-action-btn"
          round
          plain
          :disabled="disabled || busy"
          @click="requestGoOutlineConfirm"
        >
          回到确认目录
        </el-button>
      </div>
      <ul v-if="activityLines.length" ref="activityLogEl" class="mini-log activity-log">
        <li v-for="(line, i) in activityLines" :key="`${i}-${line}`">{{ line }}</li>
      </ul>
      <p v-else-if="busy" class="hint tiny-hint">正在分析，进度会一条条显示在下方。</p>
    </div>

    <div v-else class="wizard-pane">
      <p class="hint">蓝本流程已完成，可继续选用章节开写；写作进度会一条条显示在下方。</p>
      <ul v-if="activityLines.length" ref="activityLogEl" class="mini-log activity-log">
        <li v-for="(line, i) in activityLines" :key="`${i}-${line}`">{{ line }}</li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { authHeaders, readStream, request } from "../api";
import ChatFlowPipeline from "./ChatFlowPipeline.vue";
import { mergeWizardPayload, normalizeWizardStep, wizardStepIndex } from "../utils/blueprintWizardSteps";

const STEP_DONE_LABEL = {
  meta: "已填写蓝本名称",
  upload: "已上传样板",
  outline: "已提取目录",
  outline_confirm: "已确认目录",
  study: "已完成分析要点",
};

const props = defineProps({
  card: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
  processLog: { type: Array, default: () => [] },
  /** 会话级蓝本向导真值（与 task 对齐），覆盖历史卡片里过期的 step */
  liveWizard: { type: Object, default: null },
  /** 可 await 的向导落库（保证「提取目录」等步骤写入对话） */
  persistAction: { type: Function, default: null },
});

const emit = defineEmits([
  "wizard-action",
  "process",
  "live-step",
  "study-synced",
  "write-analyzed",
  "study-locate",
]);

function emitStudyLocate(chapterKey) {
  const key = String(chapterKey || "").trim();
  if (!key || !blueprintId.value) return;
  emit("study-locate", { blueprint_id: blueprintId.value, chapter_key: key });
}

async function persistWizardAction(payload) {
  if (typeof props.persistAction === "function") {
    await props.persistAction(payload);
    return;
  }
  emit("wizard-action", payload);
}

function requestReUpload() {
  if (props.disabled || busy.value || isSelectedMode.value) return;
  const bid = blueprintId.value;
  persistWizardAction({
    wizard_command: "re_upload_sample",
    step: "upload",
    blueprint_id: bid,
    outline_confirmed: false,
  });
}

async function requestReExtract() {
  if (props.disabled || busy.value || isSelectedMode.value) return;
  const bid = blueprintId.value;
  if (!bid) {
    ElMessage.warning("请先上传样板后再提取目录");
    return;
  }
  const pastConfirm =
    Boolean(payload.value?.outline_confirmed) ||
    wizardStepIndex(step.value) >= wizardStepIndex("outline_confirm");
  if (pastConfirm) {
    try {
      await ElMessageBox.confirm(
        "目录已经确认过。重新提取会整本重写章节结构（可能导致章数变化），是否继续？",
        "重新提取目录",
        { type: "warning", confirmButtonText: "重新提取", cancelButtonText: "取消" }
      );
    } catch {
      return;
    }
  }
  // 清掉本地「已有目录」缓存，避免 reconcile / auto-extract 直接沿用上次结果
  outlineSyncedFromTask.value = "";
  autoExtractToken.value = "";
  bumpLiveStep("outline", {
    trigger_outline_extract: true,
    pending_wizard_command: "re_extract_outline",
    outline_extract_active: false,
    outline_confirmed: false,
    chapters: [],
    chapter_count: 0,
  });
  // 先开提取流。若先 await persist，softSync 会剥掉 trigger 并把卡标成 stale，
  // 导致 runOutline 被跳过，界面直接回显任务里旧目录（只见 GET doc-outline）。
  await runOutline({ force: true });
}

function requestGoOutlineConfirm() {
  if (props.disabled || busy.value) return;
  const bid = blueprintId.value;
  if (!bid) return;
  persistWizardAction({
    wizard_command: "go_outline_confirm",
    step: "outline_confirm",
    blueprint_id: bid,
  });
}

/** 点击已完成步进：主动回退到对应步骤 */
function onPipelineSelect(node) {
  if (props.disabled || archived.value || isHistorical.value) return;
  const id = String(node?.id || "").trim();
  if (!id) return;
  if (id === "meta") {
    ElMessage.info("名称可在右侧文档窗口改标题");
    return;
  }
  if (isSelectedMode.value) {
    if (id === "outline_confirm") {
      requestGoOutlineConfirm();
    }
    return;
  }
  if (id === "upload" || id === "upload_done") {
    requestReUpload();
    return;
  }
  if (id === "outline" || id === "outline_done") {
    requestReExtract();
    return;
  }
  if (id === "outline_confirm") {
    requestGoOutlineConfirm();
  }
}

const busy = ref(false);
const runningLabel = ref("处理中…");
const title = ref(props.card?.payload?.title || "");
const processLines = ref([]);
const studyItems = ref([]);
const studySelectedKeys = ref([]);
/** 同蓝本全局互斥，避免新旧向导卡各跑一遍提取 */
const outlineExtractInflightBids = new Set();

const DEFAULT_PIPELINE_STEPS = [
  { id: "meta", label: "填写名称" },
  { id: "upload", label: "上传样板" },
  { id: "outline", label: "提取目录" },
  { id: "outline_confirm", label: "确认目录" },
  { id: "study", label: "分析要点" },
];

const isHistorical = computed(() => props.liveWizard == null);

const payload = computed(() => {
  const base = props.card?.payload || {};
  // 历史卡（无 live）严禁合并会话进度，否则上一步会显示成当前步
  if (isHistorical.value) {
    return { ...base };
  }
  return mergeWizardPayload(base, props.liveWizard || {});
});
const step = computed(() => normalizeWizardStep(payload.value.step));
/** 流水线：历史卡只用落库快照步 */
const pipelineStep = computed(() =>
  isHistorical.value
    ? normalizeWizardStep(props.card?.payload?.step || step.value)
    : step.value
);
const blueprintId = computed(() => payload.value.blueprint_id || "");
/** 任务 API / 学习流落盘目标：选用即 clone 后优先工作副本 w* */
const operationalTaskId = computed(
  () =>
    String(payload.value.working_task_id || "").trim() ||
    String(payload.value.blueprint_id || "").trim()
);
/** 选用已有蓝本：无创建向导（名称/上传/提取） */
const isSelectedMode = computed(() => String(payload.value.mode || "").trim() === "selected");
const SELECTED_PIPELINE_STEPS = [
  { id: "outline_confirm", label: "确认目录" },
  { id: "study", label: "分析要点" },
];
const steps = computed(() => {
  const raw = Array.isArray(payload.value.steps) ? payload.value.steps : [];
  return raw.filter((row) => String(row?.id || "").trim() !== "done");
});
const pipelineNodes = computed(() => {
  if (isSelectedMode.value) {
    return steps.value.length ? steps.value : SELECTED_PIPELINE_STEPS;
  }
  return steps.value.length ? steps.value : DEFAULT_PIPELINE_STEPS;
});
const chapters = computed(() => {
  const list = payload.value?.chapters;
  return Array.isArray(list) ? list : [];
});
/** 提取中：busy 或会话标记，避免 remount / 落库后按钮仍可点 */
const outlineExtractMarked = computed(
  () =>
    Boolean(payload.value?.outline_extract_active) ||
    Boolean(payload.value?.trigger_outline_extract) ||
    (blueprintId.value && outlineExtractInflightBids.has(blueprintId.value))
);
const outlineUiBusy = computed(
  () => busy.value || (step.value === "outline" && outlineExtractMarked.value)
);
const outlineAlreadyExtracted = computed(() => {
  if (wizardStepIndex(step.value) > wizardStepIndex("outline")) return true;
  const n = Number(payload.value?.chapter_count || 0);
  if (n > 0 && !outlineExtractMarked.value) return true;
  return Array.isArray(chapters.value) && chapters.value.length > 0 && !outlineExtractMarked.value;
});
const studyMode = computed(() => String(payload.value.study_mode || "start").trim());
const studyProgress = computed(() => ({
  analyzed: Number(payload.value.study_analyzed ?? 0),
  unanalyzed: Number(payload.value.study_unanalyzed ?? 0),
  total: Number(payload.value.study_total ?? 0),
}));
const studyOrphaned = computed(
  () =>
    !busy.value &&
    !props.disabled &&
    (Boolean(payload.value?.study_stream_active) ||
      studyItems.value.some((row) => row.status === "running") ||
      logLooksLikeStudyRunning([...(props.processLog || []), ...processLines.value]))
);

const outlineConfirmHint = computed(() => {
  const n = Number(payload.value?.chapter_count || chapters.value?.length || taskOutline.value?.chapters?.length || 0);
  const countBit = n > 0 ? `一共 ${n} 章，` : "";
  return `目录已经提出来了，${countBit}你先在右侧文档窗口里瞅瞅树和拓扑图，有不对的改一改。核对满意后再点「确认目录」——确认和分析是分开的，这步只定目录，不会自动开跑分析。`;
});

const studyHint = computed(() => {
  const { analyzed, unanalyzed, total } = studyProgress.value;
  if (busy.value) {
    return "我正在帮你学各章写作要点，先别急着切页面；学到哪一章会一条条显示在下方。";
  }
  if (studyMode.value === "confirm_pending") {
    return "上次学出来的要点还在等你拍板，点下方按钮写入蓝本后就能继续或收工。";
  }
  if (studyMode.value === "continue" && total > 0) {
    return `已经分析了 ${analyzed}/${total} 章，还剩 ${unanalyzed} 章。可以点「继续分析」只分析未完成的，也可以「先写已分析的章节」开写；或跟我说「分析第×到×章」「帮我写2.3.1」。`;
  }
  if (studyMode.value === "complete" && total > 0) {
    return `各章要点都齐了（${analyzed}/${total}）。若想再刷一遍可以说「重新分析」，或点「重新分析」。`;
  }
  const n = total || Number(payload.value?.chapter_count || chapters.value?.length || 0);
  const countBit = n > 0 ? `目录里大约 ${n} 章，` : "";
  return `目录已经确认好了。${countBit}接下来分析写作要点：你可以点「开始分析」全部分析一遍，也可以在对话里指定范围，比如「分析第1到3章」「只分析概述」。`;
});

/** 学/写进度流水：只展示过程行，不铺目录树 */
const activityLines = computed(() =>
  (processLines.value || []).map((line) => String(line || "").trim()).filter(Boolean).slice(-60)
);
const activityLogEl = ref(null);

function looksLikeWritingProgressLine(line) {
  const text = String(line || "").trim();
  if (!text) return false;
  return (
    /^▶/.test(text) ||
    /开始写|写作已暂停/.test(text) ||
    /(?:检索|写作|检查)\s*·/.test(text) ||
    (/结束/.test(text) && /「/.test(text))
  );
}

function looksLikeOutlineProgressLine(line) {
  const text = String(line || "").trim();
  if (!text) return false;
  return (
    /提取目录|目录提取|正在从样板|继续提取|识别章节|生成大纲|目录已/.test(text) ||
    /^▶/.test(text) ||
    /^✓/.test(text)
  );
}

async function scrollActivityLogToBottom() {
  await nextTick();
  const el = activityLogEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}
const studyButtonLabel = computed(() => {
  if (studyMode.value === "continue") return "继续分析";
  if (studyMode.value === "complete") return "重新分析";
  return "开始分析全部";
});
const canWriteAnalyzed = computed(
  () =>
    !props.disabled &&
    Number(studyProgress.value.analyzed || 0) > 0 &&
    studyMode.value !== "confirm_pending"
);

function unanalyzedChapterKeysFromTask(task) {
  const chapters = task?.outline?.chapters || [];
  return chapters
    .filter((ch) => !ch.structure_only && !ch.config?.structure_only)
    .filter((ch) => !(ch.points || []).length)
    .map((ch) => String(ch.key || "").trim())
    .filter(Boolean);
}

function emitWriteAnalyzed() {
  if (!canWriteAnalyzed.value || !blueprintId.value) return;
  emit("write-analyzed", { blueprint_id: blueprintId.value });
}

async function onStudyPrimaryClick() {
  if (studyMode.value === "continue") {
    let pendingKeys = unanalyzedChapterKeysFromTask(taskData.value);
    if (!pendingKeys.length) {
      pendingKeys = studyItems.value
        .filter((row) => row.status !== "done")
        .map((row) => String(row.key || "").trim())
        .filter(Boolean);
    }
    // 优先按任务真值里未学章接着跑，避免 from-outline 整模板重学
    if (pendingKeys.length) {
      await runPartialStudy(pendingKeys);
      return;
    }
    // 任务尚未 hydrate：再拉一次再 partial
    await hydrateStudyFromTask({ force: true });
    pendingKeys = unanalyzedChapterKeysFromTask(taskData.value);
    if (pendingKeys.length) {
      await runPartialStudy(pendingKeys);
      return;
    }
  }
  await runStudy({ force: studyMode.value === "complete" });
}
const taskData = ref(null);
const outlineSaving = ref(false);
const outlineFocus = ref(0);
const taskOutline = computed(() => taskData.value?.outline || null);
/** 确认目录：任务目录、卡片预览或 chapter_count 任一就绪即可点，勿等 taskOutline 才解禁 */
const canConfirmOutline = computed(() => {
  if (taskOutline.value?.chapters?.length) return true;
  if (chapters.value?.length) return true;
  return Number(payload.value?.chapter_count || 0) > 0;
});

// 历史卡一律归档展示；勿依赖 busy（组件复用时 busy 可能残留，导致旧蓝本仍显示「提取中」）
const archived = computed(() => isHistorical.value);
const studyPipelineComplete = computed(
  () => !busy.value && String(payload.value.study_mode || "").trim() === "complete"
);

const archivedSummary = computed(() => {
  const name = (payload.value.title || title.value || "").trim();
  // 历史卡只用卡片自身 payload，展示落库时的步骤快照
  const frozenStep = normalizeWizardStep(props.card?.payload?.step || step.value);
  if (archived.value) {
    const bid = blueprintId.value ? `（${blueprintId.value}）` : "";
    const frozenTitle = String(props.card?.title || "").trim();
    const label =
      frozenTitle ||
      STEP_DONE_LABEL[frozenStep] ||
      "蓝本向导步骤";
    const detail =
      frozenStep === "meta" && name
        ? `：${name}`
        : frozenStep === "upload" && name
          ? `：${name}`
          : "";
    return `${label}${detail}${bid}。此为历史步骤快照，请向下滚动使用最新一张蓝本向导继续。`;
  }
  const base = STEP_DONE_LABEL[step.value] || "该步骤已完成";
  if (step.value === "meta" && name) return `${base}：${name}`;
  if (blueprintId.value && step.value !== "meta") {
    return `${base}（${blueprintId.value}）`;
  }
  return base;
});

/** 同蓝本短时去重，避免刷新页后历史卡/live 反复打全量 GET */
let hydrateTaskToken = "";
let hydrateTaskAt = 0;

watch(
  () => [
    step.value,
    blueprintId.value,
    props.disabled,
    isHistorical.value,
  ],
  async () => {
    // 历史快照卡禁止拉任务 / 推 live，否则 N 张卡 × 全量 GET 打爆后端
    if (props.disabled || isHistorical.value) return;
    if (
      blueprintId.value &&
      !busy.value &&
      wizardStepIndex(step.value) <= wizardStepIndex("outline")
    ) {
      await reconcileOutlineStepWithTask();
    }
    if ((step.value === "outline_confirm" || step.value === "study") && blueprintId.value) {
      // study 用 hydrate 即可（内部已 GET）；勿再叠一次 loadTaskForOutline
      if (step.value === "study") {
        await hydrateStudyFromTask();
      } else {
        await loadTaskForOutline();
      }
    }
  },
  { immediate: true }
);

function outlineChaptersOf(task) {
  const outline = task?.outline;
  if (!outline || typeof outline !== "object") return [];
  return Array.isArray(outline.chapters) ? outline.chapters : [];
}

function chapterPreviewFromTask(task) {
  // 全量写入 live/卡片，避免小窗只拿到截断的 80 章
  const list = outlineChaptersOf(task);
  return list.map((n) => ({
    key: n.key,
    sequence: n.sequence,
    title: n.title,
    // 拓扑图依赖 parent/level；丢掉后只能刷新全量任务才看得见
    parent: n.parent || "",
    parent_sequence: n.parent_sequence || "",
    level: n.level || 1,
    structure_only: Boolean(n.structure_only),
  }));
}

async function finishOutlineFromTask(task, { emitDone = false, chapterCount = 0 } = {}) {
  const bid = blueprintId.value;
  if (!bid || !task) return false;
  const chapters = chapterPreviewFromTask(task);
  if (!chapters.length) return false;
  outlineSyncedFromTask.value = bid;
  taskData.value = task;
  busy.value = false;
  runningLabel.value = "处理中…";
  const total = Number(chapterCount) || chapters.length;
  bumpLiveStep("outline_confirm", {
    chapters: chapters.slice(0, 8),
    chapter_count: total,
    trigger_outline_extract: false,
    outline_extract_active: false,
    pending_wizard_command: "",
  });
  if (emitDone) {
    // 带上章节预览，后端可跳过整本任务重载，避免「正在整理回复」卡住
    await persistWizardAction({
      step: "outline_done",
      blueprint_id: bid,
      chapter_count: total,
      pending_wizard_command: "",
    });
  }
  return true;
}

function isReExtractPending() {
  // 只认 pending：trigger 在开跑后会被置 false；若把 trigger 也算进去，
  // softSync 稍一回写就会再次 force 提取，形成死循环。
  return String(payload.value?.pending_wizard_command || "").trim() === "re_extract_outline";
}

async function reconcileOutlineStepWithTask() {
  if (!blueprintId.value || busy.value || props.disabled) return;
  if (wizardStepIndex(step.value) > wizardStepIndex("outline")) return;
  const bid = blueprintId.value;
  // 用户主动重新提取：禁止用任务里旧目录直接「完成」
  if (isReExtractPending() || outlineExtractInflightBids.has(bid)) {
    return;
  }
  if (
    outlineSyncedFromTask.value === bid &&
    !payload.value?.trigger_outline_extract &&
    !payload.value?.outline_extract_active
  ) {
    return;
  }
  try {
    const task = await request(`/api/writing/tasks/${encodeURIComponent(bid)}`);
    const count = task?.outline?.chapters?.length || 0;
    if (count > 0) {
      // 任务里已有目录时，即使 leftover trigger 仍在，也必须立刻离开「提取中」
      await finishOutlineFromTask(task, {
        emitDone:
          Boolean(payload.value?.trigger_outline_extract) ||
          Boolean(payload.value?.outline_extract_active) ||
          step.value === "outline",
      });
    }
  } catch {
    /* 任务尚未就绪 */
  }
}

watch(
  () => (Array.isArray(props.processLog) ? props.processLog.slice() : []),
  (lines) => {
    if (props.disabled || isHistorical.value) return;
    if (!Array.isArray(lines) || !lines.length) return;
    // 初次灌入会话已有过程日志（含提取目录进度）
    if (!processLines.value.length) {
      processLines.value = [...lines];
      return;
    }
    const lastParent = String(lines[lines.length - 1] || "").trim();
    const lastLocal = String(processLines.value[processLines.value.length - 1] || "").trim();
    if (!lastParent || lastParent === lastLocal) return;
    // 学习由本卡 pushProcess；提取/写作由 ChatPage 写入 processLog，追加可见进度行
    if (looksLikeWritingProgressLine(lastParent) || looksLikeOutlineProgressLine(lastParent)) {
      processLines.value = [...processLines.value, lastParent];
    }
  },
  { immediate: true }
);

watch(
  () => [outlineExtractMarked.value, step.value, props.disabled, isHistorical.value],
  () => {
    if (props.disabled || isHistorical.value) return;
    if (step.value !== "outline" || !outlineExtractMarked.value) return;
    if (!busy.value) {
      runningLabel.value = runningLabel.value || "正在从样板提取目录…";
      if (!/提取|目录/.test(String(runningLabel.value))) {
        runningLabel.value = "正在从样板提取目录…";
      }
    }
  },
  { immediate: true }
);

watch(
  () => activityLines.value.length,
  (n, prev) => {
    if (n > (prev || 0)) scrollActivityLogToBottom();
  }
);

const autoExtractToken = ref("");
const outlineSyncedFromTask = ref("");

function tryAutoOutlineExtract() {
  if (isHistorical.value || props.disabled) return;
  const reExtract = isReExtractPending();
  const extractActive = Boolean(payload.value?.outline_extract_active);
  const trigger = Boolean(payload.value?.trigger_outline_extract);
  // 已在提取中：只展示进度，勿再开新流（避免与 runOutline/softSync 叠成循环）
  if (extractActive && !trigger && !reExtract) return;
  if (busy.value || (blueprintId.value && outlineExtractInflightBids.has(blueprintId.value))) {
    return;
  }
  // 目录已确认：禁止 leftover trigger 自动重提（须用户点「重新提取」）
  if (
    !reExtract &&
    (Boolean(payload.value?.outline_confirmed) ||
      wizardStepIndex(step.value) >= wizardStepIndex("outline_confirm"))
  ) {
    return;
  }
  const wantExtract = reExtract || trigger;
  const token = `${props.card?.card_id || ""}:${wantExtract}:${reExtract}:${step.value}`;
  const chapterN = Array.isArray(payload.value?.chapters) ? payload.value.chapters.length : 0;
  if (wizardStepIndex(step.value) > wizardStepIndex("outline")) return;
  // 重新提取：仅当 pending 仍在且本地尚未同步完成本次结果时重跑
  if (outlineSyncedFromTask.value === blueprintId.value && !reExtract) return;
  if (
    step.value === "outline" &&
    blueprintId.value &&
    wantExtract &&
    (chapterN === 0 || reExtract) &&
    token !== autoExtractToken.value
  ) {
    autoExtractToken.value = token;
    if (reExtract) {
      outlineSyncedFromTask.value = "";
    }
    const resume = !reExtract && extractActive;
    bumpLiveStep("outline", {
      outline_extract_active: true,
      trigger_outline_extract: false,
      ...(reExtract ? { chapters: [], chapter_count: 0 } : {}),
    });
    runOutline({ resume, force: reExtract });
  }
}

watch(
  () => [
    step.value,
    blueprintId.value,
    payload.value?.trigger_outline_extract,
    payload.value?.outline_extract_active,
    props.card?.card_id,
    props.disabled,
    isHistorical.value,
  ],
  () => {
    if (isHistorical.value || props.disabled) return;
    tryAutoOutlineExtract();
  },
  { immediate: true }
);

watch(
  isHistorical,
  (hist) => {
    if (!hist) return;
    // 变为历史快照：清掉本卡进行中态，避免 10-29-01 仍闪「提取中」而当前在跑 10-29-03
    busy.value = false;
    runningLabel.value = "处理中…";
    processLines.value = [];
    studyItems.value = [];
  },
  { immediate: true }
);

const autoStudyKeysToken = ref("");

async function tryAutoChapterStudy() {
  if (isHistorical.value || props.disabled) return;
  const keys = payload.value?.trigger_chapter_study || payload.value?.chapter_study_keys;
  const token = `${props.card?.card_id || ""}:${JSON.stringify(keys || [])}`;
  if (
    step.value !== "study" ||
    !blueprintId.value ||
    !Array.isArray(keys) ||
    !keys.length ||
    token === autoStudyKeysToken.value ||
    busy.value ||
    props.disabled
  ) {
    return;
  }
  await hydrateStudyFromTask({ force: true });
  if (busy.value) return;
  // 任务尚未拉到：勿清 trigger，等下次 watch 再试
  if (!taskData.value?.outline?.chapters?.length) return;
  autoStudyKeysToken.value = token;
  const extra = studyExtraFromTask(taskData.value || {});
  if (extra.study_mode === "complete") {
    bumpLiveStep("study", {
      trigger_chapter_study: [],
      chapter_study_keys: [],
      trigger_begin_study: false,
    });
    return;
  }
  const remain = new Set(unanalyzedChapterKeysFromTask(taskData.value));
  // 指定章重学：即便已有要点也要跑；仅过滤不存在的 key
  const allKeys = new Set(
    (taskData.value?.outline?.chapters || []).map((ch) => String(ch?.key || "").trim()).filter(Boolean)
  );
  const requested = keys.map((k) => String(k || "").trim()).filter((k) => k && allKeys.has(k));
  // 若解析出的 key 都不在大纲里，保留 trigger 勿清空（可能大纲尚未对齐）
  if (!requested.length) return;
  const pending = requested.filter((k) => remain.has(k));
  // 全是已分析章 → 仍按请求重学（用户点了依据样本分析）
  await runPartialStudy(pending.length ? pending : requested);
}

watch(
  () => [
    step.value,
    blueprintId.value,
    payload.value?.trigger_chapter_study,
    props.card?.card_id,
    props.disabled,
  ],
  () => {
    tryAutoChapterStudy();
  },
  { immediate: true }
);

const autoBeginStudyToken = ref("");

async function tryAutoBeginStudy() {
  if (isHistorical.value || props.disabled) return;
  const token = `${props.card?.card_id || ""}:${payload.value?.trigger_begin_study || ""}`;
  const chapterKeys = payload.value?.trigger_chapter_study || payload.value?.chapter_study_keys;
  if (
    step.value !== "study" ||
    !blueprintId.value ||
    !payload.value?.trigger_begin_study ||
    token === autoBeginStudyToken.value ||
    busy.value ||
    props.disabled ||
    (Array.isArray(chapterKeys) && chapterKeys.length)
  ) {
    return;
  }
  await hydrateStudyFromTask({ force: true });
  if (busy.value) return;
  autoBeginStudyToken.value = token;
  const extra = studyExtraFromTask(taskData.value || {});
  const remain = unanalyzedChapterKeysFromTask(taskData.value);
  // 重进对话：只补未学章。已学完或任务尚未对齐前，都不要整本 from-outline 重跑
  if (extra.study_mode === "complete" || !remain.length) {
    bumpLiveStep("study", {
      trigger_begin_study: false,
      trigger_chapter_study: [],
      chapter_study_keys: [],
    });
    return;
  }
  await runPartialStudy(remain);
}

watch(
  () => [
    step.value,
    blueprintId.value,
    payload.value?.trigger_begin_study,
    props.card?.card_id,
    props.disabled,
  ],
  () => {
    tryAutoBeginStudy();
  },
  { immediate: true }
);

function studyExtraFromTask(task) {
  const chapters = task?.outline?.chapters || [];
  const writable = chapters.filter((ch) => !ch.structure_only && !ch.config?.structure_only);
  const analyzed = writable.filter((ch) => (ch.points || []).length > 0);
  const unanalyzed = writable.filter((ch) => !(ch.points || []).length);
  const pending = task?.pending_study || [];
  const total = writable.length || chapters.length;
  let study_mode = "start";
  if (pending.length) study_mode = "confirm_pending";
  else if (analyzed.length && unanalyzed.length) study_mode = "continue";
  else if (analyzed.length && !unanalyzed.length) study_mode = "complete";
  return {
    study_analyzed: analyzed.length,
    study_unanalyzed: unanalyzed.length,
    study_total: total,
    study_pending_confirm: pending.length > 0,
    study_mode,
  };
}

function logLooksLikeStudyRunning(lines) {
  const last = String((lines || []).filter(Boolean).slice(-1)[0] || "").trim();
  if (!last) return false;
  if (/完成\s*$/.test(last) && !/…/.test(last)) return false;
  return /…$|正在分析|正在学习|正在确认目录|分析要点|学习要点/.test(last);
}

function parseStudyItemsFromLog(lines) {
  const items = [];
  const byLabel = new Map();
  for (const raw of lines || []) {
    const line = String(raw || "").trim();
    if (!line) continue;
    let label = "";
    let status = "";
    const done = line.match(/^[✓✔]?\s*(.+?)完成\s*$/);
    const running = line.match(/^(.+?)…\s*$/);
    if (done) {
      label = (done[1] || "").trim();
      status = "done";
    } else if (running) {
      label = (running[1] || "").trim();
      status = "running";
    }
    if (!label) continue;
    byLabel.set(label, { key: label, label, status });
  }
  for (const row of byLabel.values()) items.push(row);
  return items;
}

function buildStudyItemsFromTask(task) {
  const chapters = task?.outline?.chapters || [];
  return chapters
    .filter((ch) => !ch.structure_only && !(ch.config?.structure_only))
    .map((ch) => {
      const label = `${ch.sequence || ""} ${ch.title || ""}`.trim() || ch.key || "";
      const hasPoints = (ch.points || []).length > 0;
      return { key: ch.key || label, label, status: hasPoints ? "done" : "pending" };
    });
}

function mergeStudyItemRows(fromTask, fromLog) {
  const map = new Map();
  for (const row of fromTask) map.set(row.key, { ...row });
  for (const row of fromLog) {
    const prev = map.get(row.key) || map.get(row.label);
    if (!prev) {
      map.set(row.key, row);
      continue;
    }
    if (row.status === "running") map.set(prev.key, { ...prev, status: "running" });
    else if (row.status === "done") map.set(prev.key, { ...prev, status: "done" });
  }
  return [...map.values()];
}

function emitLiveStudyFromTask(task, extra = {}) {
  if (!blueprintId.value || !task || props.disabled || isHistorical.value) return;
  const studyExtra = studyExtraFromTask(task);
  const live = props.liveWizard || {};
  const unchanged =
    Number(live.study_analyzed ?? -1) === Number(studyExtra.study_analyzed) &&
    Number(live.study_unanalyzed ?? -1) === Number(studyExtra.study_unanalyzed) &&
    Number(live.study_total ?? -1) === Number(studyExtra.study_total) &&
    String(live.study_mode || "") === String(studyExtra.study_mode || "") &&
    Boolean(live.study_pending_confirm) === Boolean(studyExtra.study_pending_confirm) &&
    !extra?.force;
  if (unchanged && !Object.keys(extra || {}).some((k) => k !== "force")) return;
  emit("live-step", {
    blueprint_id: blueprintId.value,
    step: step.value,
    study_stream_active: false,
    ...studyExtra,
    ...extra,
  });
}

function setStudyStreamActive(active) {
  if (!blueprintId.value) return;
  emit("live-step", {
    blueprint_id: blueprintId.value,
    step: step.value,
    study_stream_active: Boolean(active),
  });
}

async function hydrateStudyFromTask({ force = false } = {}) {
  if (!operationalTaskId.value || props.disabled || isHistorical.value) return;
  const token = `${operationalTaskId.value}:${step.value}`;
  const now = Date.now();
  if (!force && hydrateTaskToken === token && now - hydrateTaskAt < 8000) {
    return;
  }
  hydrateTaskToken = token;
  hydrateTaskAt = now;
  try {
    const task = await request(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}`);
    taskData.value = task;
    const pending = task?.pending_study || [];
    if (pending.length) {
      studyItems.value = pending.slice(0, 80).map((row) => ({
        key: row.chapter_key || row.key || "",
        label: `${row.sequence || ""} ${row.title || row.chapter_key || ""}`.trim(),
        status: "done",
      }));
    } else {
      const logs = [...(props.processLog || []), ...processLines.value];
      studyItems.value = mergeStudyItemRows(buildStudyItemsFromTask(task), parseStudyItemsFromLog(logs));
    }
    emitLiveStudyFromTask(task);
  } catch {
    /* 忽略 */
  }
}

async function flushPendingStudyToOutline() {
  if (!operationalTaskId.value) return null;
  await request(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/auto-study/confirm`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ drafts: [] }),
  });
  const task = await request(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}`);
  taskData.value = task;
  emitLiveStudyFromTask(task, { force: true });
  return task;
}

async function confirmPendingStudy() {
  if (!blueprintId.value) return;
  busy.value = true;
  runningLabel.value = "正在确认分析结果…";
  try {
    const task = await flushPendingStudyToOutline();
    ElMessage.success("分析结果已写入蓝本");
    const extra = studyExtraFromTask(task || {});
    const pending = (task?.pending_study || []).length;
    if (!pending && extra.study_mode === "complete") {
      bumpLiveStep("study", { study_mode: "complete" });
      emit("wizard-action", { step: "study_done", blueprint_id: blueprintId.value });
    } else {
      bumpLiveStep("study");
    }
    emit("study-synced", { blueprint_id: blueprintId.value });
  } catch (error) {
    ElMessage.error(error.message || "确认失败");
  } finally {
    busy.value = false;
  }
}

async function loadTaskForOutline() {
  if (!blueprintId.value || props.disabled || isHistorical.value) {
    if (!blueprintId.value) taskData.value = null;
    return;
  }
  // 先用卡片/live 预览垫上，避免确认按钮因 taskOutline 空而一直灰掉
  if (!taskData.value?.outline?.chapters?.length && chapters.value?.length) {
    taskData.value = {
      id: blueprintId.value,
      outline_confirmed: Boolean(payload.value?.outline_confirmed),
      outline: { chapters: chapters.value.map((ch) => ({ ...ch })) },
    };
  }
  const token = `${blueprintId.value}:outline`;
  const now = Date.now();
  const haveFull =
    (taskData.value?.outline?.chapters || []).length >=
    Number(payload.value?.chapter_count || 0);
  if (hydrateTaskToken === token && now - hydrateTaskAt < 8000 && haveFull && taskData.value) {
    return;
  }
  hydrateTaskToken = token;
  hydrateTaskAt = now;
  try {
    // 优先轻量目录，避免刷新页时每张卡打 400KB 全量任务
    const slim = await request(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/doc-outline`);
    if (slim?.chapters?.length) {
      taskData.value = {
        id: slim.id,
        outline_confirmed: Boolean(slim.outline_confirmed),
        outline: { chapters: slim.chapters },
        element_nodes: slim.element_nodes || [],
      };
      const total = Number(slim.chapter_count || slim.chapters.length || 0);
      const already =
        Number(payload.value?.chapter_count || 0) === total &&
        wizardStepIndex(step.value) >= wizardStepIndex("outline_confirm");
      if (total > 0 && !already) {
        bumpLiveStep(step.value || "outline_confirm", {
          chapters: chapterPreviewFromTask(taskData.value).slice(0, 8),
          chapter_count: total,
        });
      }
      return;
    }
    taskData.value = await request(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}`);
    const total = taskData.value?.outline?.chapters?.length || 0;
    const already =
      Number(payload.value?.chapter_count || 0) === total &&
      wizardStepIndex(step.value) >= wizardStepIndex("outline_confirm");
    if (total > 0 && !already) {
      bumpLiveStep(step.value || "outline_confirm", {
        chapters: chapterPreviewFromTask(taskData.value).slice(0, 8),
        chapter_count: total,
      });
    }
  } catch {
    /* 保留预览垫底，勿清空导致按钮不可点 */
  }
}

async function saveOutlineChapters(chapters) {
  if (!blueprintId.value) return;
  outlineSaving.value = true;
  try {
    const updated = await request(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/outline`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ chapters }),
    });
    taskData.value = updated;
    if ((updated?.outline?.chapters || []).length) {
      bumpLiveStep("outline_confirm");
    }
    return updated;
  } catch (error) {
    ElMessage.error(error.message || "保存目录失败");
    throw error;
  } finally {
    outlineSaving.value = false;
  }
}

function addOutlineNode(index) {
  const list = [...(taskData.value?.outline?.chapters || [])];
  const anchor = list[index] || { level: 1, sequence: "" };
  list.splice(index + 1, 0, {
    key: `ch-new-${Date.now()}`,
    sequence: "",
    title: "新章节",
    level: anchor.level || 1,
    parent: "",
    parent_sequence: "",
    points: [],
    keywords: [],
    describes: "",
    aux_prompt: "",
    material_files: [],
    knowledge_folder_ids: [],
    element_ids: [],
    elements: [],
  });
  return saveOutlineChapters(list).then(() => {
    outlineFocus.value = index + 1;
  });
}

function removeOutlineNode(index) {
  const list = [...(taskData.value?.outline?.chapters || [])];
  if (list.length <= 1) {
    ElMessage.warning("至少保留一章");
    return Promise.resolve();
  }
  list.splice(index, 1);
  return saveOutlineChapters(list).then(() => {
    outlineFocus.value = Math.min(outlineFocus.value, list.length - 1);
  });
}

function indentOutlineNode(index, delta) {
  const list = [...(taskData.value?.outline?.chapters || [])].map((item) => ({ ...item }));
  const node = list[index];
  if (!node) return Promise.resolve();
  node.level = Math.max(1, Math.min(8, (node.level || 1) + delta));
  return saveOutlineChapters(list);
}

async function saveElementNodes(elements) {
  if (!blueprintId.value) return;
  try {
    const updated = await request(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/elements`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ elements }),
    });
    taskData.value = updated;
  } catch (error) {
    ElMessage.error(error.message || "保存要素失败");
  }
}

function removeElementNode(elementId) {
  const elements = (taskData.value?.element_nodes || []).filter((item) => item.id !== elementId);
  const chapters = (taskData.value?.outline?.chapters || []).map((item) => ({
    ...item,
    element_ids: (item.element_ids || []).filter((id) => id !== elementId),
  }));
  return saveElementNodes(elements).then(() => saveOutlineChapters(chapters));
}

function pushProcess(msg) {
  if (!msg) return;
  processLines.value.push(msg);
  emit("process", msg);
}

function trackStreamEvent(event) {
  if (event.type === "process" && event.message) {
    runningLabel.value = event.message;
    pushProcess(event.message);
  }
  // 范围学习：每章落盘进度 → 定位该章并软刷新目录状态
  if (event.type === "study_chapter_progress" && event.chapter) {
    emitStudyLocate(event.chapter);
    emit("live-step", {
      blueprint_id: blueprintId.value,
      step: step.value,
      study_stream_active: true,
      study_analyzed: Number(event.study_analyzed || 0),
      study_unanalyzed: Number(event.study_unanalyzed || 0),
    });
    emit("study-synced", { blueprint_id: blueprintId.value, chapter_key: event.chapter });
    return;
  }
  if (event.type === "step") {
    const result = event.result && typeof event.result === "object" ? event.result : {};
    const idx = Number(result.index || event.index || 0) || 0;
    const total = Number(result.total || event.total || 0) || 0;
    const seq = String(result.sequence || "").trim();
    const title = String(event.title || "").trim();
    const label = [seq, title].filter(Boolean).join(" ") || title || event.chapter || event.step || "步骤";
    const outlineStep = String(event.step || "").trim() === "outline" || step.value === "outline";
    if (event.status === "running") {
      const line = outlineStep
        ? total > 0
          ? `▶ (${idx}/${total}) ${label}…`
          : `▶ ${label}…`
        : total > 0
          ? `▶ (${idx}/${total}) 正在分析「${label}」…`
          : `▶ 正在分析「${label}」…`;
      runningLabel.value = outlineStep
        ? total > 0
          ? `正在提取目录 ${idx}/${total} · ${label}`
          : `${label}…`
        : total > 0
          ? `正在分析 ${idx}/${total} · ${label}`
          : `${label}…`;
      pushProcess(line);
      // 开始分析本章 → 立刻定位到该章配置
      if (event.chapter) emitStudyLocate(event.chapter);
    }
    if (event.status === "done") {
      const note = String(result.note || "").trim();
      const line =
        event.chapter
          ? total > 0
            ? `✓ (${idx}/${total}) 「${label}」${note ? ` · ${note}` : "完成"}`
            : `✓ 「${label}」${note ? ` · ${note}` : "完成"}`
          : `${label}完成`;
      pushProcess(line);
      // 本章分析完成仍停在该章，等下一章 running 再切
      if (event.chapter) emitStudyLocate(event.chapter);
    }
    trackStudyStep(event);
  }
}

/** 同步锁：成功后不释放，避免回车+点击或请求结束后的二次触发再打一条「创建蓝本」 */
const metaSubmitLock = ref(false);

async function submitMeta() {
  const name = title.value.trim();
  if (!name) {
    ElMessage.warning("请先填写蓝本名称");
    return;
  }
  if (metaSubmitLock.value || busy.value || props.disabled) return;
  if (blueprintId.value && step.value !== "meta") {
    ElMessage.info("该蓝本已创建，请在最下方最新向导卡继续上传或提取目录");
    return;
  }
  metaSubmitLock.value = true;
  busy.value = true;
  runningLabel.value = "正在创建蓝本…";
  let ok = false;
  try {
    await persistWizardAction({
      step: "meta",
      title: name,
      blueprint_id: blueprintId.value || "",
    });
    ok = true;
  } catch (error) {
    ElMessage.error(error?.message || "创建蓝本失败");
  } finally {
    busy.value = false;
    // 成功则保持锁定；失败才允许重试
    if (!ok) metaSubmitLock.value = false;
  }
}

async function uploadSample(option) {
  if (!blueprintId.value) return;
  busy.value = true;
  processLines.value = [];
  runningLabel.value = "正在上传并解析样板…";
  pushProcess(runningLabel.value);
  try {
    const body = new FormData();
    body.append("file", option.file);
    const response = await fetch(`/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/sample`, {
      method: "POST",
      headers: authHeaders(),
      body,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || response.statusText);
    }
    let task = null;
    try {
      task = await response.json();
    } catch {
      task = null;
    }
    const name = task?.sample_name || option.file?.name || "样板";
    const chars = Number(task?.sample_chars || 0);
    pushProcess(chars ? `✓ 已上传「${name}」，解析约 ${chars} 字` : `✓ 已上传「${name}」`);
    ElMessage.success(chars ? `样板已上传，约 ${chars} 字` : "样板已上传");
    bumpLiveStep("outline", { outline_extract_active: false });
    // 上传必须立刻落一条「完成上传样板」对话，否则会话看起来停在上传卡
    await persistWizardAction({ step: "upload_done", blueprint_id: blueprintId.value });
    option.onSuccess?.();
  } catch (error) {
    ElMessage.error(error.message || "上传失败");
    option.onError?.(error);
  } finally {
    busy.value = false;
  }
}

function bumpLiveStep(nextStep, extra = {}) {
  if (!blueprintId.value || !nextStep || props.disabled || isHistorical.value) return;
  const live = props.liveWizard || {};
  if (
    String(live.step || "") === String(nextStep) &&
    String(live.blueprint_id || "") === String(blueprintId.value) &&
    Number(live.chapter_count || 0) === Number(extra.chapter_count || live.chapter_count || 0) &&
    extra.trigger_begin_study == null &&
    extra.study_stream_active == null &&
    !Array.isArray(extra.trigger_chapter_study) &&
    extra.outline_extract_active == null
  ) {
    return;
  }
  const patch = { blueprint_id: blueprintId.value, step: nextStep, ...extra };
  if (nextStep !== "outline" && nextStep !== "meta") {
    patch.trigger_outline_extract = false;
    patch.outline_extract_active = false;
    patch.trigger_chapter_study = [];
    // 提取完成后必须清掉「重新提取」挂起命令，否则 merge 会把步数钉回 outline
    if (wizardStepIndex(nextStep) >= wizardStepIndex("outline_confirm")) {
      patch.pending_wizard_command = "";
    }
  }
  // 进入学习/完成即视为目录已确认，文档窗据此对齐，避免一边学习一边「待确认」
  if (wizardStepIndex(nextStep) >= wizardStepIndex("study")) {
    patch.outline_confirmed = true;
  }
  if (wizardStepIndex(nextStep) >= wizardStepIndex("outline_confirm")) {
    const fromTask = chapterPreviewFromTask(taskData.value);
    const total =
      Number(patch.chapter_count || payload.value?.chapter_count || fromTask.length || 0) ||
      fromTask.length;
    if (total) patch.chapter_count = total;
    if (!Array.isArray(patch.chapters) && fromTask.length) {
      patch.chapters = fromTask.slice(0, 8);
    } else if (Array.isArray(patch.chapters) && patch.chapters.length > 8) {
      patch.chapters = patch.chapters.slice(0, 8);
    }
  }
  emit("live-step", patch);
}

async function runOutline({ resume = false, force = false } = {}) {
  if (!blueprintId.value) {
    ElMessage.warning("蓝本尚未创建完成，请先填写名称并上传样板");
    return;
  }
  if (props.disabled) {
    ElMessage.info("请在最下方最新的蓝本向导卡片里操作");
    return;
  }
  if (busy.value) return;
  const forceRe = force || isReExtractPending();
  if (outlineAlreadyExtracted.value && !resume && !forceRe) {
    ElMessage.info("目录已提取，请在「确认目录」步骤核对，或点「重新提取目录」");
    return;
  }
  if (outlineExtractInflightBids.has(blueprintId.value)) return;
  outlineExtractInflightBids.add(blueprintId.value);
  busy.value = true;
  if (!resume || forceRe) {
    processLines.value = [];
    outlineSyncedFromTask.value = "";
  } else if (Array.isArray(props.processLog) && props.processLog.length && !processLines.value.length) {
    processLines.value = [...props.processLog];
  }
  runningLabel.value = forceRe
    ? "正在重新提取目录…"
    : resume
      ? "正在继续提取目录…"
      : "正在从样板提取目录…";
  pushProcess(runningLabel.value);
  // 持久化「提取中」；重新提取时先清空章节预览，避免 UI/reconcile 误判已完成
  bumpLiveStep("outline", {
    outline_extract_active: true,
    trigger_outline_extract: false,
    ...(forceRe
      ? {
          pending_wizard_command: "re_extract_outline",
          chapters: [],
          chapter_count: 0,
          outline_confirmed: false,
        }
      : {}),
  });
  if (!resume || forceRe) {
    try {
      // 提取进行中只同步 active/pending，不要再带 wizard_command：
      // 否则每次都会插「重新提取」回复卡 + softSync，叠出多路 extract 流。
      await persistWizardAction({
        step: "outline",
        blueprint_id: blueprintId.value,
        outline_extract_active: true,
        ...(forceRe
          ? {
              pending_wizard_command: "re_extract_outline",
              chapters: [],
              chapter_count: 0,
              outline_confirmed: false,
            }
          : {}),
      });
    } catch {
      /* 提取仍继续，结束时再落 outline_done */
    }
  }
  try {
    const response = await fetch(
      `/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/study/extract-outline/text/stream`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          chapter_keys: [],
          text: "",
          use_sample: true,
          use_llm: true,
        }),
      }
    );
    let chapterCount = 0;
    await readStream(response, (event) => {
      trackStreamEvent(event);
      const extracted = outlineChaptersOf(event.task);
      if (event.type === "task" && extracted.length) {
        chapterCount = extracted.length;
        taskData.value = event.task;
        const head = extracted.slice(0, 4);
        const preview = head
          .map((c) => `${c.sequence || ""} ${c.title || ""}`.trim())
          .filter(Boolean)
          .join("；");
        pushProcess(`✓ 目录提取完成，共 ${chapterCount} 章${preview ? `（如：${preview}…）` : ""}`);
        // 流式预览章节；正式步数与对话以 outline_done 落库为准
        if (chapterCount > 0) {
          outlineSyncedFromTask.value = blueprintId.value;
        }
      }
      if (event.type === "error") throw new Error(event.detail || "提取失败");
    });
    if (!chapterCount) {
      throw new Error("未能从样板识别到章节目录，请换一份带目录的 Word/PDF 重试");
    }
    ElMessage.success(`目录已提取，共 ${chapterCount} 章，请确认目录`);
    // 先用流式 task 推进到确认目录，避免 GET task / 落库卡住时 UI 一直停在「提取中」
    const streamed = taskData.value;
    const previewChapters = chapterPreviewFromTask(streamed || { outline: { chapters: [] } });
    bumpLiveStep("outline_confirm", {
      chapters: previewChapters,
      trigger_outline_extract: false,
      outline_extract_active: false,
      pending_wizard_command: "",
    });
    // 先占住 inflight，等 outline_done 落库完成再放开，避免 softSync 期间又开新流
    busy.value = false;
    runningLabel.value = "处理中…";
    try {
      const ok = await finishOutlineFromTask(streamed || { outline: { chapters: [] } }, {
        emitDone: true,
        chapterCount,
      });
      if (!ok) {
        await persistWizardAction({
          step: "outline_done",
          blueprint_id: blueprintId.value,
          chapter_count: chapterCount,
          pending_wizard_command: "",
        });
      }
    } catch (persistError) {
      ElMessage.warning(persistError?.message || "目录已提取，同步对话失败，可继续确认目录");
    }
    loadTaskForOutline().catch(() => {});
  } catch (error) {
    ElMessage.error(error.message || "提取目录失败");
    // 失败也保留 active，方便切回后重试续跑
    bumpLiveStep("outline", {
      outline_extract_active: true,
      trigger_outline_extract: false,
    });
  } finally {
    outlineExtractInflightBids.delete(blueprintId.value);
    busy.value = false;
    runningLabel.value = "处理中…";
  }
}

async function confirmOutline() {
  if (!blueprintId.value || props.disabled) return;
  if (outlineExtractInflightBids.has(blueprintId.value)) {
    ElMessage.info("目录还在提取，稍后再确认");
    return;
  }
  busy.value = true;
  processLines.value = [];
  studyItems.value = [];
  runningLabel.value = "正在确认目录…";
  pushProcess(runningLabel.value);
  try {
    // 只确认目录，不自动开学习（学习由用户点按钮或对话指定范围）
    const response = await fetch(
      `/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/study/confirm-outline/stream`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          drafts: [],
          apply_outline: true,
          continue_study: false,
        }),
      }
    );
    await readStream(response, (event) => {
      trackStreamEvent(event);
      if (event.type === "task" && event.task) {
        taskData.value = event.task;
      }
      if (event.type === "error") throw new Error(event.detail || "确认失败");
    });
    ElMessage.success("目录已确认，下一步再决定怎么学");
    pushProcess("✓ 目录已确认。分析要点是下一步，你可以全部分析，也可以指定章节。");
    bumpLiveStep("study", {
      outline_confirmed: true,
      trigger_begin_study: false,
      trigger_chapter_study: [],
      chapter_study_keys: [],
      study_stream_active: false,
    });
    // 落库推到 study：避免会话停在 outline_confirm，后续分析又被当成「确认目录」
    await persistWizardAction({
      step: "study",
      blueprint_id: blueprintId.value,
      outline_confirmed: true,
      trigger_begin_study: false,
    });
    await hydrateStudyFromTask();
  } catch (error) {
    ElMessage.error(error.message || "确认目录失败");
  } finally {
    busy.value = false;
    runningLabel.value = "处理中…";
  }
}

function trackStudyStep(event) {
  if (event.type !== "step") return;
  const key = `${event.chapter || ""}-${event.step || ""}`;
  const label = [event.title, event.chapter].filter(Boolean).join(" · ") || event.step || "步骤";
  const status = event.status || "running";
  const idx = studyItems.value.findIndex((x) => x.key === key);
  const row = { key, label, status };
  if (idx >= 0) studyItems.value[idx] = row;
  else studyItems.value.push(row);
}

async function finishStudyAfterStream() {
  await hydrateStudyFromTask({ force: true });
  if ((taskData.value?.pending_study || []).length) {
    await flushPendingStudyToOutline();
  }
  const extra = studyExtraFromTask(taskData.value || {});
  if (extra.study_mode === "complete") {
    bumpLiveStep("study", { study_mode: "complete" });
    emit("wizard-action", { step: "study_done", blueprint_id: blueprintId.value });
  } else {
    bumpLiveStep("study");
  }
  emit("study-synced", { blueprint_id: blueprintId.value });
}

async function runPartialStudy(chapterKeys) {
  if (!operationalTaskId.value || busy.value || !chapterKeys?.length) return;
  busy.value = true;
  // 开跑前清掉一次性触发，避免刷新/重进对话再次自动重学
  bumpLiveStep("study", {
    trigger_chapter_study: [],
    chapter_study_keys: [],
    trigger_begin_study: false,
    study_stream_active: true,
  });
  emitStudyLocate(chapterKeys[0]);
  processLines.value = [];
  studyItems.value = [];
  runningLabel.value = "正在接着分析未完成的章节…";
  pushProcess(runningLabel.value);
  try {
    const response = await fetch(
      `/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/study/points/text/stream`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          chapter_keys: chapterKeys,
          text: "",
          use_sample: true,
        }),
      }
    );
    await readStream(response, (event) => {
      trackStreamEvent(event);
      if (event.type === "task" && event.task) {
        taskData.value = event.task;
      }
      if (event.type === "error") throw new Error(event.detail || "分析失败");
    });
    ElMessage.success("已继续分析未完成的章节");
    await finishStudyAfterStream();
  } catch (error) {
    ElMessage.error(error.message || "分析失败");
  } finally {
    busy.value = false;
    setStudyStreamActive(false);
    await hydrateStudyFromTask({ force: true });
  }
}

function writableChapterKeysFromTask(task) {
  const chapters = task?.outline?.chapters || [];
  return chapters
    .filter((ch) => !ch.structure_only && !ch.config?.structure_only)
    .map((ch) => String(ch.key || "").trim())
    .filter(Boolean);
}

async function runStudy({ force = false } = {}) {
  if (!operationalTaskId.value || busy.value) return;
  // 与「继续分析」同一条样本+逐章模型链路，勿走 from-outline
  if (!taskData.value?.outline?.chapters?.length) {
    await hydrateStudyFromTask({ force: true });
  }
  let keys = force
    ? writableChapterKeysFromTask(taskData.value)
    : unanalyzedChapterKeysFromTask(taskData.value);
  if (!keys.length) {
    keys = writableChapterKeysFromTask(taskData.value);
  }
  if (!keys.length) {
    ElMessage.warning("当前没有可分析的章节");
    return;
  }
  busy.value = true;
  bumpLiveStep("study", {
    trigger_begin_study: false,
    trigger_chapter_study: [],
    chapter_study_keys: [],
    study_stream_active: true,
  });
  emitStudyLocate(keys[0]);
  if (!studyOrphaned.value) {
    processLines.value = [];
    studyItems.value = [];
  } else {
    studyItems.value = studyItems.value.map((row) =>
      row.status === "running" ? { ...row, status: "pending" } : row
    );
  }
  runningLabel.value = force ? "正在重新分析各章要点…" : "正在分析各章要点…";
  pushProcess(runningLabel.value);
  try {
    const response = await fetch(
      `/api/writing/tasks/${encodeURIComponent(operationalTaskId.value)}/study/points/text/stream`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          chapter_keys: keys,
          text: "",
          use_sample: true,
        }),
      }
    );
    await readStream(response, (event) => {
      trackStreamEvent(event);
      if (event.type === "task" && event.task) {
        taskData.value = event.task;
      }
      if (event.type === "error") throw new Error(event.detail || "分析失败");
    });
    await finishStudyAfterStream();
  } catch (error) {
    ElMessage.error(error.message || "分析失败");
  } finally {
    busy.value = false;
    setStudyStreamActive(false);
    await hydrateStudyFromTask({ force: true });
  }
}
</script>

<style scoped>
.bp-wizard {
  margin-top: 8px;
}
.study-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.bp-wizard.archived {
  opacity: 0.92;
}
.wizard-running {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eff6ff;
  color: #1e40af;
  font-size: 13px;
}
.running-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #3b82f6;
  animation: run-blink 1s ease-in-out infinite;
}
@keyframes run-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
.wizard-archived .archived-text {
  margin: 0;
  font-size: 13px;
  color: #047857;
}
.warn-hint {
  color: #b45309;
  background: #fffbeb;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid #fde68a;
}
.wizard-outline-pane .hint strong {
  font-weight: 600;
  color: #334155;
}
.wizard-outline-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 4px;
}
.wizard-outline-host {
  margin: 0 0 12px;
  min-height: 520px;
  border: 1px solid var(--ay-line);
  border-radius: 12px;
  overflow: hidden;
  background: #f8fafc;
  padding: 10px;
  display: flex;
  flex-direction: column;
}
.wizard-outline-host :deep(.canvas-card) {
  border: none;
  box-shadow: none;
}
.wizard-outline-host :deep(.outline-shell.treeAbove.canvasOnly) {
  flex: 1 1 auto;
  min-height: 0;
}
.wizard-outline-host :deep(.outline-shell.treeAbove.canvasOnly .canvas) {
  flex: 1 1 auto;
  height: 340px;
  min-height: 300px;
}
.wizard-outline-host :deep(.outline-shell.canvasOnly .canvas .vue-flow) {
  width: 100%;
  height: 100%;
}
.wizard-study-pane .wizard-study-picker {
  margin: 0 0 12px;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid var(--ay-line, #e2e8f0);
  background: #fff;
}
.wizard-study-pane .tiny-hint {
  font-size: 12px;
  margin-bottom: 8px;
}
.wizard-study-topology {
  margin-bottom: 12px;
}
.wizard-pane .hint {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--ay-muted);
}
.wizard-pane .chat-action-btn {
  margin-top: 4px;
}
.outline-list {
  max-height: 200px;
  overflow: auto;
  margin: 0 0 10px;
  padding-left: 18px;
  font-size: 13px;
}
.mini-log {
  margin: 10px 0 0;
  padding-left: 16px;
  font-size: 12px;
  color: var(--ay-muted);
  max-height: 160px;
  overflow: auto;
}
.activity-log {
  max-height: 220px;
  padding: 8px 10px 8px 18px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #eef2f7;
  line-height: 1.55;
}
.activity-log li {
  word-break: break-word;
}
.study-progress {
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  max-height: 200px;
  overflow: auto;
  font-size: 12px;
}
.study-progress li {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid #f1f5f9;
}
.study-progress li.done {
  color: #047857;
}
.study-progress li.running {
  color: #1e40af;
}
.study-progress .st {
  width: 1em;
  flex-shrink: 0;
}
</style>
