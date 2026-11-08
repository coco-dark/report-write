<template>
  <div class="outline-shell" :class="{ canvasOnly, treeAbove }">
    <div v-if="!canvasOnly" class="card-head">
      <div>
        <span class="card-title">章节拓扑</span>
        <p class="hint head-hint">在图上点击章节节点弹窗编辑。</p>
      </div>
    </div>

    <div v-if="treeAbove && nodes.length" class="outline-tree-card">
      <div class="outline-tree-head">
        <span class="card-title">章节目录</span>
        <span class="hint">树形结构 · 点击节点在下方拓扑中定位</span>
      </div>
      <UiTree
 class="outline-chapter-tree"
 :data="chapterTreeData"
 node-key="key"
 default-expand-all
 highlight-current
 :expand-on-click-node="false"
 :current-node-key="currentChapterKey"
 @node-click="onChapterTreeClick"
 >
        <template #default="{ data }">
          <span class="tree-seq">{{ data.sequence || "—" }}</span>
          <span class="tree-title">{{ data.title || data.key }}</span>
          <a-button
 v-if="confirmed"
 size="small"
 class="tree-link-btn"
 @click.stop="openElementLinkForKey(data.key)"
 type="link">
            关联要素{{ (data.element_ids || []).length ? `（${data.element_ids.length}）` : "" }}
          </a-button>
        </template>
      </UiTree>
    </div>

    <div v-if="nodes.length" class="canvas-card">
      <div class="canvas-toolbar">
        <a-button size="small" :disabled="confirmed || busy" @click="addNode">新增章节</a-button>
        <a-button size="small" :disabled="busy" @click="startNewElement">新增要素</a-button>
        <a-button size="small" :disabled="!nodes.length" @click="relayout">重新排版</a-button>
        <a-button size="small" :disabled="busy || !nodes.length" @click="$emit('copy')" type="text">复制目录</a-button>
        <span class="hint">共 {{ nodes.length }} 章 · {{ elementNodes.length }} 要素 · 章节连到要素建立关联</span>
      </div>
      <div class="canvas">
        <VueFlow
          id="outline-flow"
          v-model:nodes="flowNodes"
          v-model:edges="flowEdges"
          :node-types="nodeTypes"
          :default-viewport="{ zoom: 0.85, x: 40, y: 40 }"
          :min-zoom="0.3"
          :max-zoom="1.6"
          fit-view-on-init
          @pane-ready="onFlowPaneReady"
          @node-click="onNodeClick"
          @node-double-click="onNodeClick"
          @connect="onConnect"
        >
          <Background pattern-color="#dcdfe6" :gap="18" />
          <Controls />
          <!-- 大目录下 MiniMap 本身很重，超过 80 章时关掉 -->
          <MiniMap v-if="nodes.length <= 80" pannable zoomable />
        </VueFlow>
      </div>
    </div>

    <a-empty v-else description="生成目录大纲后，这里显示可交互拓扑图" :image-style="{ height: 72 }"  />

    <a-modal
 v-model:open="dialogVisible"
 :title="dialogTitle"
 width="760px"
 class="node-dialog"
 @afterClose="$emit('dialog-closed')"
 destroy-on-close centered>
      <a-form v-if="draft" layout="vertical">
        <a-tabs v-model:activeKey="chapterTab" class="chapter-tabs">
          <a-tab-pane tab="运行结果" key="run">
            <div v-if="chapterRunState" class="run-result">
              <div class="run-result-banner">
                <strong>本章运行结果</strong>
                <a-tag size="small" :color="runStatusType">{{ runStatusLabel }}</a-tag>
                <span v-if="adoptedEvidence.length">采纳证据 {{ adoptedEvidence.length }}</span>
              </div>
              <p v-if="chapterRunState.human_note" class="hint field-hint">说明：{{ chapterRunState.human_note }}</p>
              <div v-if="runHtml" class="html-draft" v-html="runHtml" />
              <a-empty v-else description="尚未生成正文（可能检索未采纳证据，或尚未运行完成）" :image-style="{ height: 64 }"  />
              <details v-if="(chapterRunState.evidence || []).length" class="evidence-box">
                <summary>证据卡片（{{ chapterRunState.evidence.length }}）</summary>
                <ul>
                  <li v-for="card in chapterRunState.evidence" :key="card.id">
                    <a-tag size="small" :color="card.adopted ? 'success' : 'default'">
                      {{ card.adopted ? "采纳" : "未采纳" }}
                    </a-tag>
                    <span class="ev-src">{{ card.source_file || "—" }}</span>
                    <p class="ev-excerpt">{{ card.excerpt }}</p>
                  </li>
                </ul>
              </details>
            </div>
            <a-empty v-else description="本章还没有运行记录，点下方「运行本章」开始" :image-style="{ height: 64 }"  />
          </a-tab-pane>

          <a-tab-pane tab="分析结果" key="result">
            <div class="study-result-banner">
              <strong>本章分析结果</strong>
              <span>
                简述 {{ (draft.describes || "").trim() ? "有" : "无" }} ·
                要点 {{ pointsList.length }} ·
                要素 {{ (draft.element_ids || []).length }} ·
                版式块 {{ (draft.content_blocks || []).length }}
              </span>
            </div>
            <a-form-item label="本章简述">
              <a-textarea
                v-model:value="draft.describes"
                :rows="3"
                placeholder="尚未生成简述；可在「重新解析」粘贴原文生成"
              />
            </a-form-item>
            <a-form-item label="写作要点">
              <p class="hint field-hint">一行一条，可直接改写。</p>
              <ol v-if="pointsList.length" class="points-view">
                <li v-for="(line, index) in pointsList" :key="index">{{ line }}</li>
              </ol>
              <a-empty v-else description="暂无写作要点" :image-style="{ height: 56 }"  />
              <a-textarea
                v-model:value="pointsText"
                :rows="6"
                class="points-edit"
                placeholder="编辑要点：一行一条"
              />
            </a-form-item>
            <a-form-item label="版式块（文字 / 表格 / 图片占位）">
              <ul v-if="(draft.content_blocks || []).length" class="block-list">
                <li v-for="(block, index) in draft.content_blocks" :key="`${block.kind}-${index}`">
                  <strong>{{ blockKindLabel(block.kind) }}</strong>
                  <span v-if="block.kind === 'table'">
                    {{ block.rows || "?" }}×{{ block.cols || "?" }}
                    <template v-if="(block.merges || []).length"> · 合并 {{ block.merges.length }} 处</template>
                  </span>
                  <span class="block-note">{{ block.note || "" }}</span>
                  <pre v-if="block.kind === 'table' && (block.cells_sample || []).length" class="table-sample">{{
                    formatTableSample(block)
                  }}</pre>
                </li>
              </ul>
              <a-empty v-else description="暂无版式块（表格结构等）" :image-style="{ height: 56 }"  />
            </a-form-item>
            <a-form-item :label="draft.fixed_output ? '固定正文' : '参考正文'">
              <p class="hint field-hint">
                {{
                  draft.fixed_output
                    ? "开启固定内容后，写作将跳过检索，由大模型按此原文输出（勿改写事实）。"
                    : "高权重参考写法：描述方式、写作特点与格式；事实仍以检索证据为准，勿照抄参考里的专名/数据。"
                }}
              </p>
              <ChapterHtmlEditor
                v-model="draft.reference_text"
                :placeholder="draft.fixed_output ? '粘贴本章固定原文（将按原文输出）' : '粘贴本章参考正文（样板原文节选，可从 Word 复制）'"
                :min-height="160"
                :max-height="360"
              />
            </a-form-item>
          </a-tab-pane>

          <a-tab-pane tab="节点配置" key="config">
            <a-row :gutter="12">
              <a-col :span="8">
                <a-form-item label="章节序号">
                  <a-input v-model:value="draft.sequence" placeholder="如 1.2" :disabled="confirmed" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="层级">
                  <a-input-number v-model:value="draft.level" :min="1" :max="8" style="width: 100%"  />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="上级节点">
                  <a-select v-model:value="draft.parent" allow-clear placeholder="无（顶级）" style="width: 100%">
                    <a-select-option
                      v-for="item in parentOptions"
                      :key="item.key"
                      :value="item.key">{{ `${item.sequence || '—'} ${item.title}` }}</a-select-option>
                  </a-select>
                </a-form-item>
              </a-col>
            </a-row>
            <a-form-item label="标题">
              <a-input v-model:value="draft.title" />
            </a-form-item>
            <a-form-item label="占位符 key">
              <p class="hint field-hint">模版占位用；由章节名中译英生成，可改。勿与其它章节重复。</p>
              <a-input v-model:value="draft.key" placeholder="如 substation_site" />
            </a-form-item>
            <a-form-item label="仅目录节点">
              <a-switch
 v-model:checked="draft.structure_only"
 checked-children="开"
 inchecked-children="关"
 @change="onStructureOnlyChange"
  />
              <p class="hint field-hint">
                开启后本节点只作目录标题，不用解析、不检索、不写正文；正文由子节展开。
              </p>
            </a-form-item>
            <a-form-item v-if="!draft.structure_only" label="固定内容输出">
              <a-switch v-model:checked="draft.fixed_output" checked-children="开" inchecked-children="关"  />
              <p class="hint field-hint">
                开启后跳过资料检索，按「分析结果」里的固定正文调用大模型原样输出。
              </p>
            </a-form-item>
            <a-form-item v-if="!draft.structure_only && !draft.fixed_output" label="辅助提示词">
              <p class="hint field-hint">
                硬约束，优先于要点/参考正文；只写写法与体裁，不要写工程名、站名、投资额等项目元数据。
                例：只输出表格内容，不要文字叙述。
              </p>
              <a-textarea
                v-model:value="draft.aux_prompt"
                :rows="3"
                placeholder="如：只输出表格，不要导语、结语或其它文字（勿填项目实例）"
              />
            </a-form-item>
            <div class="elements-block">
              <div class="paste-head">
                <span class="paste-title">关联要素</span>
                <a-button size="small" type="primary" @click="openElementLinkFromDraft">
                  从列表管理关联
                </a-button>
              </div>
              <p class="hint field-hint">
                要素内容在拓扑「新增要素」或要素节点里配置；本章只勾选要启用的要素。也可在图上把章节连到要素。
              </p>
              <ul v-if="draftLinkedElements.length" class="linked-preview">
                <li v-for="el in draftLinkedElements" :key="el.id">
                  <a-tag size="small">{{ elementKindLabel(el.kind) }}</a-tag>
                  <span>{{ el.name || el.id }}</span>
                </li>
              </ul>
              <a-empty v-else description="未关联要素" :image-style="{ height: 48 }"  />
            </div>
          </a-tab-pane>

          <a-tab-pane tab="重新解析" key="parse">
            <div v-if="draft.structure_only" class="parse-box">
              <a-empty description="目录节点无需解析，正文由子节展开" :image-style="{ height: 64 }"  />
            </div>
            <div v-else class="parse-box">
              <div class="paste-head"><span class="paste-title">粘贴原文 · 生成简述、写法要点与表格结构</span></div>
              <a-textarea
                v-model:value="sampleText"
                :rows="8"
                placeholder="粘贴该章原文（可含表格描述）；模型提炼写法与表格结构"
              />
              <div class="paste-actions">
                <a-button
 type="primary"
 :loading="parsing"
 :disabled="!sampleText.trim() || parsing || busy"
 @click="parsePoints"
 >
                  用 LLM 解析
                </a-button>
              </div>
            </div>
          </a-tab-pane>
        </a-tabs>
      </a-form>
      <template #footer>
        <a-button @click="dialogVisible = false">取消</a-button>
        <a-button
 :loading="busy"
 :disabled="busy"
 title="仅运行当前章节"
 @click="runCurrent(false)"
 type="default" class="btn-success">
          运行本章
        </a-button>
        <a-button
 :loading="busy"
 :disabled="busy"
 title="运行本章及所有子章节"
 @click="runCurrent(true)"
 type="link">
          含子章
        </a-button>
        <a-button v-if="!confirmed" @click="removeCurrent" danger>删除节点</a-button>
        <a-button type="primary" :loading="saving" :disabled="saving || busy" @click="saveDialog">保存</a-button>
      </template>
    </a-modal>

    <ChapterElementAssociateDialog
      v-model="elementLinkVisible"
      :chapter-label="elementLinkChapterLabel"
      :all-elements="elementNodes"
      :linked-ids="elementLinkIds"
      :task-id="taskId"
      :allow-create="!confirmed"
      :pack-slot-options="packSlotOptions"
      :robot-id="robotId"
      :configure-hint="
        confirmed
          ? '目录已锁定：可关联已有要素；新建要素请点「修改大纲」或工具栏「新增要素」。'
          : '新建的行会进入任务要素库；详细配置可点拓扑上的要素节点。'
      "
      @confirm="onElementLinkConfirm"
    />

    <a-modal
 v-model:open="elementDialogVisible"
 :title="elementDialogIsNew ? '新增要素' : '编辑要素节点'"
 width="760px"
 class="element-node-dialog"
 @afterClose="onElementDialogClosed"
 destroy-on-close centered>
      <a-form v-if="elementDraft" layout="vertical">
        <ElementConfigFields
          v-model="elementDraft"
          :ref-target-options="refTargetOptions"
          :pack-slot-options="packSlotOptions"
          :task-id="taskId"
          :element-id="elementDraft?.id"
        />
      </a-form>
      <template #footer>
        <a-button @click="elementDialogVisible = false">取消</a-button>
        <a-button v-if="!elementDialogIsNew" @click="removeElementDraft" danger>删除</a-button>
        <a-button type="primary" @click="saveElementDraft">保存</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import { useVueFlow, VueFlow } from "@vue-flow/core";
import { MiniMap } from "@vue-flow/minimap";
import { computed, markRaw, nextTick, ref, watch } from "vue";
import { ElMessage } from "../ui.js";

import { request } from "../api";
import { STATUS_LABELS } from "../constants";
import ChapterHtmlEditor from "./ChapterHtmlEditor.vue";
import ChapterElementAssociateDialog from "./ChapterElementAssociateDialog.vue";
import ElementConfigFields from "./ElementConfigFields.vue";
import ChapterFlowNode from "./ChapterFlowNode.vue";
import ElementFlowNode from "./ElementFlowNode.vue";
import {
  blankElementDraft,
  elementKindLabel,
  kvPairsFromElement,
  normalizeKvElementDraft,
} from "../elementUi";

import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import "@vue-flow/controls/dist/style.css";
import "@vue-flow/minimap/dist/style.css";

const NODE_W = 180;
const NODE_H = 88;
const ELEMENT_SIZE = 96;
const GAP_X = 80;
const GAP_Y = 28;

const props = defineProps({
  outline: { type: Object, default: null },
  confirmed: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  focus: { type: Number, default: 0 },
  taskId: { type: String, default: "" },
  canvasOnly: { type: Boolean, default: false },
  /** 对话向导等场景：上方章节目录树 + 下方拓扑图 */
  treeAbove: { type: Boolean, default: false },
  locateKey: { type: String, default: "" },
  openKey: { type: String, default: "" },
  openElementId: { type: String, default: "" },
  elementNodes: { type: Array, default: () => [] },
  materialNames: { type: Array, default: () => [] },
  canRun: { type: Boolean, default: false },
  chapterStates: { type: Array, default: () => [] },
  preferTab: { type: String, default: "" },
  persistChapters: { type: Function, default: null },
  packSlotOptions: { type: Array, default: () => [] },
  robotId: { type: String, default: "" },
  /** 父级 v-show 保活时：重新显示后补一次 fitView（避免 0 尺寸画布） */
  visible: { type: Boolean, default: true },
});

const saving = ref(false);
const packSlotOptionsLocal = ref([]);
const packSlotOptions = computed(() =>
  (props.packSlotOptions || []).length
    ? props.packSlotOptions
    : packSlotOptionsLocal.value
);

async function refreshPackSlotOptions() {
  const tid = String(props.taskId || "").trim();
  if (!tid) {
    packSlotOptionsLocal.value = [];
    return;
  }
  try {
    const task = await request(`/api/writing/tasks/${encodeURIComponent(tid)}`);
    const pid = String(task?.materials_pack_id || "").trim();
    if (!pid) {
      packSlotOptionsLocal.value = [];
      return;
    }
    const pack = await request(`/api/writing/materials-packs/${encodeURIComponent(pid)}`);
    const byNo = new Map();
    for (const s of pack?.slot_schema || []) {
      const no = Number(s?.no) > 0 ? Number(s.no) : 0;
      if (no < 1) continue;
      byNo.set(no, { no, name: String(s?.name || "").trim() });
    }
    for (const f of pack?.slots_fill || []) {
      const no = Number(f?.slot_no) > 0 ? Number(f.slot_no) : 0;
      if (no < 1) continue;
      if (!byNo.has(no)) byNo.set(no, { no, name: "" });
    }
    packSlotOptionsLocal.value = [...byNo.values()].sort((a, b) => a.no - b.no);
  } catch {
    packSlotOptionsLocal.value = [];
  }
}

watch(
  () => props.taskId,
  () => {
    refreshPackSlotOptions();
  },
  { immediate: true }
);
const { fitView } = useVueFlow({ id: "outline-flow" });
let locateTimer = 0;
let fitViewTimer = 0;

const emit = defineEmits([
  "confirm",
  "revoke",
  "copy",
  "import",
  "from-text",
  "select",
  "change",
  "add",
  "remove",
  "indent",
  "dialog-closed",
  "add-element",
  "change-elements",
  "remove-element",
  "run-chapter",
]);

const sampleText = ref("");
const parsing = ref(false);
const dialogVisible = ref(false);
const draft = ref(null);
const pointsText = ref("");
const chapterTab = ref("result");
const elementDialogVisible = ref(false);
const elementDraft = ref(null);
const elementDialogIsNew = computed(() => {
  const id = elementDraft.value?.id;
  if (!id) return true;
  return !props.elementNodes.some((item) => item.id === id);
});
const elementLinkVisible = ref(false);
const elementLinkIds = ref([]);
const elementLinkChapterKey = ref("");
const elementLinkChapterLabel = ref("");
const elementLinkStandalone = ref(false);
const flowNodes = ref([]);
const flowEdges = ref([]);
const nodeTypes = {
  chapter: markRaw(ChapterFlowNode),
  element: markRaw(ElementFlowNode),
};

const nodes = computed(() => props.outline?.chapters || []);

const chapterTreeData = computed(() => buildChapterTree(nodes.value));

const currentChapterKey = computed(() => nodes.value[props.focus]?.key || "");

function buildChapterTree(chapters) {
  const list = (chapters || []).map((item) => ({ ...item, children: [] }));
  const byKey = new Map(list.map((item) => [item.key, item]));
  const roots = [];
  for (const item of list) {
    if (item.parent && byKey.has(item.parent)) {
      byKey.get(item.parent).children.push(item);
    } else {
      roots.push(item);
    }
  }
  return roots;
}

function onChapterTreeClick(data) {
  const index = nodes.value.findIndex((item) => item.key === data.key);
  if (index < 0) return;
  emit("select", index);
  locateNode(data.key);
}
const hasStudyResult = computed(() => {
  if (!draft.value) return false;
  return Boolean(
    (draft.value.describes || "").trim() ||
      (draft.value.points || []).length ||
      (draft.value.keywords || []).length ||
      (draft.value.content_blocks || []).length
  );
});
const dialogTitle = computed(() => {
  if (!draft.value) return "编辑章节";
  const name = `${draft.value.sequence || ""} ${draft.value.title || ""}`.trim();
  if (chapterTab.value === "run") return `运行结果 · ${name}`;
  if (chapterTab.value === "result" || hasStudyResult.value) return `分析结果 · ${name}`;
  return `编辑：${name}`;
});
const pointsList = computed(() =>
  pointsText.value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
);
const chapterRunState = computed(() => {
  const key = draft.value?.key;
  if (!key) return null;
  return (props.chapterStates || []).find((item) => item.key === key) || null;
});
const adoptedEvidence = computed(() =>
  (chapterRunState.value?.evidence || []).filter((card) => card.adopted)
);
const runHtml = computed(() => {
  const state = chapterRunState.value;
  if (!state) return "";
  let html = "";
  if ((state.official_text || "").trim()) html = state.official_text;
  else {
    const paragraphs = state.draft?.paragraphs || [];
    if (!paragraphs.length) return "";
    html = paragraphs.map((item) => item.text || "").filter(Boolean).join("\n");
  }
  // 展示时隐藏证据编号标记
  return String(html).replace(/\[ev-\d+\]/g, "").replace(/[ \t]{2,}/g, " ");
});
const runStatusLabel = computed(() => {
  const status = chapterRunState.value?.status || "";
  return STATUS_LABELS[status] || status || "未知";
});
const runStatusType = computed(() => {
  const status = chapterRunState.value?.status || "";
  if (status === "confirmed" || status === "awaiting_confirm") return "success";
  if (status === "needs_human" || status === "skipped") return "warning";
  if (status === "searching" || status === "writing" || status === "checking") return "processing";
  return "default";
});
const hasRunResult = computed(() => Boolean(runHtml.value || (chapterRunState.value?.evidence || []).length));
const parentOptions = computed(() => nodes.value.filter((item) => item.key !== draft.value?.key));
const draftLinkedElements = computed(() => {
  const ids = draft.value?.element_ids || [];
  return ids.map((id) => props.elementNodes.find((item) => item.id === id)).filter(Boolean);
});
const refTargetOptions = computed(() => {
  if (!elementDraft.value) return [];
  return props.elementNodes
    .filter((item) => item.id !== elementDraft.value.id)
    .map((item) => ({
      value: item.id,
      label: `${item.name || item.id}（${elementKindLabel(item.kind)}）`,
    }));
});

/** 结构签名：仅 key/父子/标题/关联变化时重排版；写作 status 刷新不整图重建（大目录卡顿主因） */
function outlineFlowSignature(outline, elementNodes) {
  const chapters = outline?.chapters || [];
  const chSig = chapters
    .map(
      (c) =>
        `${c?.key || ""}>${c?.parent || ""}:${c?.title || ""}#${(c?.element_ids || []).join(",")}`
    )
    .join("|");
  const elSig = (elementNodes || [])
    .map((e) => `${e?.id || ""}:${e?.name || ""}:${e?.kind || ""}`)
    .join("|");
  return `${chapters.length}:${chSig}||${elSig}`;
}

watch(
  () => outlineFlowSignature(props.outline, props.elementNodes),
  () => syncFlow(),
  { immediate: true },
);

watch(
  () => props.visible,
  (vis, prev) => {
    if (!vis || vis === prev) return;
    // 仅触发容器尺寸刷新；不要每次切回都 fitView（237 章会明显卡一下）
    nextTick(() => {
      try {
        window.dispatchEvent(new Event("resize"));
      } catch {
        /* ignore */
      }
    });
  }
);

watch(
  () => props.locateKey,
  (key) => {
    if (!key) return;
    locateNode(key);
  },
);

watch(
  () => props.focus,
  (index, prev) => {
    if (index === prev) return;
    locateNode(nodes.value[index]?.key);
  },
);

watch(
  () => props.openKey,
  (key) => {
    if (!key) return;
    const node = nodes.value.find((item) => item.key === key);
    if (node) {
      locateNode(node.key);
      openDialog(node);
    }
  },
);

watch(
  () => props.openElementId,
  (id) => {
    if (!id) return;
    const node = props.elementNodes.find((item) => item.id === id);
    if (node) openElementDialog(node);
  },
);

async function persistChapterList(chapters) {
  const serialized = chapters.map((item) => serializeChapter(item));
  if (typeof props.persistChapters === "function") {
    await props.persistChapters(serialized);
  } else if (props.taskId) {
    await request(`/api/writing/tasks/${encodeURIComponent(props.taskId)}/outline`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapters: serialized, replace_tree: true }),
    });
    emit("change", serialized);
  } else {
    emit("change", serialized);
  }
}

function scheduleFitView() {
  window.clearTimeout(fitViewTimer);
  fitViewTimer = window.setTimeout(() => {
    nextTick(() => {
      if (!flowNodes.value.length) return;
      try {
        const large = flowNodes.value.length > 80;
        fitView({
          padding: 0.2,
          duration: large ? 0 : 200,
          maxZoom: large ? 0.9 : 1.2,
        });
      } catch {
        /* flow not mounted yet */
      }
    });
  }, 60);
}

function onFlowPaneReady() {
  scheduleFitView();
}

function locateNode(nodeId) {
  const id = (nodeId || "").trim();
  if (!id) return;
  window.clearTimeout(locateTimer);
  locateTimer = window.setTimeout(() => {
    nextTick(() => {
      fitView({
        nodes: [id],
        padding: 0.42,
        duration: 320,
        maxZoom: 1.15,
        minZoom: 0.45,
      });
    });
  }, 40);
}

function relayout() {
  syncFlow();
  nextTick(() => {
    fitView({ padding: 0.18, duration: 280, maxZoom: 1.2 });
  });
  ElMessage.success("已按树结构重新排版");
}

function syncFlow() {
  const chapters = nodes.value;
  const positions = layoutPositions(chapters);
  const chapterNodes = chapters.map((item, index) => ({
    id: item.key,
    type: "chapter",
    position: positions.get(item.key) || { x: 40, y: 40 + index * (NODE_H + GAP_Y) },
    data: {
      sequence: item.sequence || "",
      title: item.title || "",
      key: item.key || "",
      pointCount: (item.points || []).length,
      keywordCount: (item.keywords || []).length,
      folderCount: 0,
      blockCount: (item.content_blocks || []).length,
      fixedOutput: Boolean(item.fixed_output),
      hasStudy:
        Boolean((item.describes || "").trim()) ||
        (item.points || []).length > 0 ||
        (item.keywords || []).length > 0 ||
        (item.content_blocks || []).length > 0 ||
        Boolean((item.reference_text || "").trim()),
      hasRun: Boolean(
        (() => {
          const state = (props.chapterStates || []).find((row) => row.key === item.key);
          return (
            state &&
            ((state.official_text || "").trim() ||
              (state.draft?.paragraphs || []).length ||
              (state.evidence || []).length)
          );
        })()
      ),
      elementCount: (item.element_ids || []).length,
      role: "extract",
    },
    selected: index === props.focus,
  }));
  const maxChapterX = Math.max(0, ...[...positions.values()].map((item) => item.x));
  const elementFlow = props.elementNodes.map((item, index) => ({
    id: item.id,
    type: "element",
    position: { x: maxChapterX + NODE_W + GAP_X + 40, y: 24 + index * (ELEMENT_SIZE + GAP_Y) },
    data: {
      title: item.name || item.id,
      name: item.name,
      kind: item.kind,
      query: item.query,
      value: item.value,
      role: "element",
    },
  }));
  flowNodes.value = [...chapterNodes, ...elementFlow];
  const treeEdges = chapters
    .filter((item) => item.parent)
    .map((item) => ({
      id: `tree:${item.parent}->${item.key}`,
      source: item.parent,
      target: item.key,
      type: "smoothstep",
      animated: false,
    }));
  const linkEdges = [];
  for (const chapter of chapters) {
    for (const elementId of chapter.element_ids || []) {
      linkEdges.push({
        id: `link:${chapter.key}->${elementId}`,
        source: chapter.key,
        target: elementId,
        type: "smoothstep",
        animated: true,
        style: { stroke: "#d97706" },
      });
    }
  }
  flowEdges.value = [...treeEdges, ...linkEdges];
  if (chapters.length) scheduleFitView();
}

function layoutPositions(chapters) {
  const byKey = new Map(chapters.map((item) => [item.key, item]));
  const children = new Map();
  const roots = [];
  for (const item of chapters) {
    if (item.parent && byKey.has(item.parent)) {
      if (!children.has(item.parent)) children.set(item.parent, []);
      children.get(item.parent).push(item.key);
    } else {
      roots.push(item.key);
    }
  }
  const depthOf = new Map();
  const orderAtDepth = new Map();
  const walk = (key, depth) => {
    depthOf.set(key, depth);
    if (!orderAtDepth.has(depth)) orderAtDepth.set(depth, []);
    orderAtDepth.get(depth).push(key);
    for (const child of children.get(key) || []) walk(child, depth + 1);
  };
  for (const root of roots) walk(root, 0);
  for (const item of chapters) {
    if (!depthOf.has(item.key)) walk(item.key, 0);
  }
  const rowOf = new Map();
  for (const [, keys] of orderAtDepth.entries()) {
    keys.forEach((key, row) => rowOf.set(key, row));
  }
  const result = new Map();
  for (const item of chapters) {
    const depth = depthOf.get(item.key) || 0;
    const row = rowOf.get(item.key) || 0;
    result.set(item.key, {
      x: 24 + depth * (NODE_W + GAP_X),
      y: 24 + row * (NODE_H + GAP_Y),
    });
  }
  return result;
}

function onNodeClick({ node }) {
  if (node.type === "element") {
    const found = props.elementNodes.find((item) => item.id === node.id);
    if (!found) return;
    if (props.confirmed) {
      ElMessage.info("目录已锁定：要改要素内容请点「修改大纲」，或在此为章节「关联要素」。");
      return;
    }
    openElementDialog(found);
    return;
  }
  const index = nodes.value.findIndex((item) => item.key === node.id);
  if (index < 0) return;
  emit("select", index);
  if (props.confirmed) {
    openElementLinkForKey(node.id);
    return;
  }
  openDialog(nodes.value[index]);
}

function onConnect(connection) {
  if (connection.source === connection.target) return;
  const chapterKeys = new Set(nodes.value.map((item) => item.key));
  const elementIds = new Set(props.elementNodes.map((item) => item.id));
  const sourceIsChapter = chapterKeys.has(connection.source);
  const targetIsChapter = chapterKeys.has(connection.target);
  const sourceIsElement = elementIds.has(connection.source);
  const targetIsElement = elementIds.has(connection.target);

  if (sourceIsChapter && targetIsElement) {
    linkChapterElement(connection.source, connection.target);
    return;
  }
  if (sourceIsElement && targetIsChapter) {
    linkChapterElement(connection.target, connection.source);
    return;
  }
  if (props.confirmed) return;
  if (!(sourceIsChapter && targetIsChapter)) return;
  const chapters = nodes.value.map((item) => ({
    ...item,
    points: [...(item.points || [])],
    keywords: [...(item.keywords || [])],
    content_blocks: [...(item.content_blocks || [])],
    material_files: [...(item.material_files || [])],
    knowledge_folder_ids: [...(item.knowledge_folder_ids || [])],
    element_ids: [...(item.element_ids || [])],
  }));
  const target = chapters.find((item) => item.key === connection.target);
  if (!target) return;
  target.parent = connection.source;
  const parent = chapters.find((item) => item.key === connection.source);
  if (parent) {
    target.level = Math.min(8, (parent.level || 1) + 1);
    target.parent_sequence = parent.sequence || "";
  }
  emit("change", chapters);
}

function chapterLabelByKey(key) {
  const node = nodes.value.find((item) => item.key === key);
  if (!node) return key || "";
  return `${node.sequence || ""} ${node.title || node.key}`.trim();
}

function openElementLinkForKey(chapterKey) {
  const node = nodes.value.find((item) => item.key === chapterKey);
  if (!node) return;
  elementLinkChapterKey.value = chapterKey;
  elementLinkChapterLabel.value = chapterLabelByKey(chapterKey);
  elementLinkIds.value = [...(node.element_ids || [])];
  elementLinkStandalone.value = true;
  elementLinkVisible.value = true;
}

function openElementLinkFromDraft() {
  if (!draft.value?.key) return;
  elementLinkChapterKey.value = draft.value.key;
  elementLinkChapterLabel.value = chapterLabelByKey(draft.value.key);
  elementLinkIds.value = [...(draft.value.element_ids || [])];
  elementLinkStandalone.value = false;
  elementLinkVisible.value = true;
}

async function onElementLinkConfirm({ linkedIds, newElements }) {
  if (newElements?.length) {
    const merged = [...props.elementNodes];
    for (const el of newElements) {
      if (!merged.some((item) => item.id === el.id)) merged.push(el);
    }
    emit("change-elements", merged);
  }
  const chapterKey = elementLinkChapterKey.value;
  if (!chapterKey) return;

  if (!elementLinkStandalone.value && draft.value?.key === chapterKey) {
    draft.value.element_ids = [...linkedIds];
    elementLinkVisible.value = false;
    return;
  }

  const chapters = nodes.value.map((item) => ({
    ...item,
    points: [...(item.points || [])],
    keywords: [...(item.keywords || [])],
    content_blocks: [...(item.content_blocks || [])],
    material_files: [...(item.material_files || [])],
    knowledge_folder_ids: [...(item.knowledge_folder_ids || [])],
    element_ids: [...(item.element_ids || [])],
  }));
  const chapter = chapters.find((item) => item.key === chapterKey);
  if (!chapter) return;
  chapter.element_ids = [...linkedIds];
  try {
    await persistChapterList(chapters);
    if (props.confirmed) ElMessage.success("本章要素关联已保存");
  } catch {
    return;
  }
  if (draft.value?.key === chapterKey) {
    draft.value.element_ids = [...linkedIds];
  }
}

function linkChapterElement(chapterKey, elementId) {
  const chapters = nodes.value.map((item) => ({
    ...item,
    points: [...(item.points || [])],
    keywords: [...(item.keywords || [])],
    content_blocks: [...(item.content_blocks || [])],
    material_files: [...(item.material_files || [])],
    knowledge_folder_ids: [...(item.knowledge_folder_ids || [])],
    element_ids: [...(item.element_ids || [])],
  }));
  const chapter = chapters.find((item) => item.key === chapterKey);
  if (!chapter) return;
  if (!chapter.element_ids.includes(elementId)) chapter.element_ids.push(elementId);
  persistChapterList(chapters).catch(() => {});
}

function openDialog(node) {
  draft.value = {
    ...node,
    key: node.key || "",
    points: [...(node.points || [])],
    keywords: [...(node.keywords || [])],
    content_blocks: [...(node.content_blocks || [])],
    reference_text: node.reference_text || "",
    structure_only: Boolean(node.structure_only),
    search_terms_separate: Boolean(node.search_terms_separate),
    fixed_output: Boolean(node.fixed_output),
    aux_prompt: node.aux_prompt || "",
    material_files: [...(node.material_files || [])],
    knowledge_folder_ids: [...(node.knowledge_folder_ids || [])],
    element_ids: [...(node.element_ids || [])],
  };
  pointsText.value = (node.points || []).join("\n");
  sampleText.value = "";
  const prefer = (props.preferTab || "").trim();
  const state = (props.chapterStates || []).find((item) => item.key === node.key);
  const ran = Boolean(
    state &&
      ((state.official_text || "").trim() ||
        (state.draft?.paragraphs || []).length ||
        (state.evidence || []).length)
  );
  const studied =
    Boolean((node.describes || "").trim()) ||
    (node.points || []).length > 0 ||
    (node.keywords || []).length > 0 ||
    (node.content_blocks || []).length > 0;
  if (prefer === "run" || prefer === "result" || prefer === "config" || prefer === "parse") {
    chapterTab.value = prefer;
  } else if (ran) {
    chapterTab.value = "run";
  } else if (studied) {
    chapterTab.value = "result";
  } else {
    chapterTab.value = "config";
  }
  dialogVisible.value = true;
}

function blockKindLabel(kind) {
  return { text: "文字", table: "表格", image: "图片占位" }[kind] || kind || "文字";
}

function formatTableSample(block) {
  const rows = block.cells_sample || [];
  return rows.map((row) => (Array.isArray(row) ? row.join(" | ") : String(row))).join("\n");
}

function openElementDialog(node) {
  elementDraft.value = {
    ...node,
    material_files: [...(node.material_files || [])],
    knowledge_folder_ids: [...(node.knowledge_folder_ids || [])],
    knowledge_scope: node.knowledge_scope || "both",
    kv_pairs: kvPairsFromElement(node).map((p) => ({ ...p })),
    ref_key: node.ref_key || "",
    ref_kind: node.kind === "node_ref" ? "element" : node.ref_kind || "chapter",
    ref_field: node.kind === "node_ref" ? "result" : node.ref_field || (node.ref_kind === "element" ? "result" : "points"),
  };
  elementDialogVisible.value = true;
}

/** 先弹窗配置，保存后再写入任务要素 / 拓扑（取消不落库、图上不先出现节点） */
function startNewElement() {
  if (props.busy) return;
  const draft = blankElementDraft("kv", { library: false });
  delete draft.id;
  openElementDialog(draft);
}

function onElementDialogClosed() {
  elementDraft.value = null;
  emit("dialog-closed");
}

defineExpose({ startNewElement, openElementDialog });

function normalizeIdList(value) {
  const list = Array.isArray(value) ? value : value != null && value !== "" ? [value] : [];
  return list
    .map((item) => {
      if (item == null) return "";
      if (typeof item === "string" || typeof item === "number") return String(item).trim();
      if (typeof item === "object") return String(item.id || item.value || "").trim();
      return "";
    })
    .filter(Boolean);
}

function serializeChapter(item) {
  return {
    key: item.key || "",
    sequence: item.sequence || "",
    title: item.title || "",
    level: Number(item.level || 1),
    parent: item.parent || "",
    parent_sequence: item.parent_sequence || "",
    points: [...(item.points || [])],
    keywords: [...(item.keywords || [])].map((x) => String(x).trim()).filter(Boolean),
    describes: (item.describes || "").trim(),
    content_blocks: [...(item.content_blocks || [])],
    reference_text: (item.reference_text || "").trim(),
    aux_prompt: (item.aux_prompt || "").trim(),
    material_files: [...(item.material_files || [])],
    knowledge_folder_ids: normalizeIdList(item.knowledge_folder_ids),
    element_ids: normalizeIdList(item.element_ids),
    elements: [...(item.elements || [])],
    structure_only: Boolean(item.structure_only),
    search_terms_separate: Boolean(item.search_terms_separate),
    fixed_output: Boolean(item.fixed_output),
  };
}

async function saveDialog() {
  if (!draft.value || saving.value) return;
  const oldKey = draft.value.key;
  const nextKey = (draft.value.key || "").trim();
  if (!nextKey) {
    ElMessage.warning("占位符 key 不能为空");
    return;
  }
  if (nodes.value.some((item) => item.key !== oldKey && item.key === nextKey)) {
    ElMessage.warning("占位符 key 与其它章节重复");
    return;
  }
  const referenceText = (draft.value.reference_text || "").trim();
  const structureOnly = Boolean(draft.value.structure_only);
  const fixedOutput = Boolean(draft.value.fixed_output) && !structureOnly;
  if (fixedOutput && !referenceText) {
    ElMessage.warning("已开启固定内容，请先在「分析结果」填写固定正文");
    return;
  }
  const chapters = nodes.value.map((item) => serializeChapter(item));
  const index = chapters.findIndex((item) => item.key === oldKey);
  if (index < 0) return;
  for (const item of chapters) {
    if (item.parent === oldKey) item.parent = nextKey;
  }
  const points = structureOnly
    ? []
    : pointsText.value
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
  let describes = (draft.value.describes || "").trim();
  if (structureOnly && !describes) {
    describes = `目录节点「${draft.value.title || nextKey}」，正文由子节展开，本章不单独撰写。`;
  }
  chapters[index] = serializeChapter({
    ...draft.value,
    key: nextKey,
    points,
    keywords: structureOnly ? [] : [...(draft.value.keywords || [])],
    describes,
    content_blocks: structureOnly ? [] : [...(draft.value.content_blocks || [])],
    reference_text: structureOnly ? "" : referenceText,
    structure_only: structureOnly,
    search_terms_separate: structureOnly ? false : Boolean(draft.value.search_terms_separate),
    fixed_output: fixedOutput,
    knowledge_folder_ids: draft.value.knowledge_folder_ids,
    element_ids: draft.value.element_ids,
  });
  saving.value = true;
  try {
    if (typeof props.persistChapters === "function") {
      await props.persistChapters(chapters);
    } else if (props.taskId) {
      await request(`/api/writing/tasks/${encodeURIComponent(props.taskId)}/outline`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chapters }),
      });
      emit("change", chapters);
    } else {
      emit("change", chapters);
    }
    dialogVisible.value = false;
    ElMessage.success("节点配置已保存");
  } catch (error) {
    // persistChapters 已提示错误时避免重复；直接请求路径在此提示
    if (typeof props.persistChapters !== "function") {
      ElMessage.error(error.message || "保存失败");
    }
  } finally {
    saving.value = false;
  }
}

function saveElementDraft() {
  if (!elementDraft.value) return;
  let saved = { ...elementDraft.value };
  if (saved.kind === "kv") {
    saved = normalizeKvElementDraft(saved);
    if (!saved.kv_pairs?.some((p) => p.key?.trim())) {
      ElMessage.warning("请至少填写一行键值");
      return;
    }
  }
  if (saved.kind === "node_ref") {
    saved.ref_kind = "element";
    saved.ref_field = "result";
    if (!String(saved.ref_key || "").trim()) {
      ElMessage.warning("请选择要引用结果的要素");
      return;
    }
  }
  if (!(saved.name || "").trim()) {
    if (saved.kind === "kv" && saved.kv_pairs?.some((p) => p.key?.trim())) {
      saved.name = saved.kv_pairs.find((p) => p.key?.trim())?.key || "新要素";
    } else {
      ElMessage.warning("请填写要素名称");
      return;
    }
  }
  const isNew = elementDialogIsNew.value;
  if (isNew || !saved.id) {
    saved.id = `el-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  }
  const elements = props.elementNodes.map((item) => (item.id === saved.id ? saved : { ...item }));
  if (!elements.some((item) => item.id === saved.id)) {
    elements.push(saved);
  }
  emit("change-elements", elements);
  elementDialogVisible.value = false;
  ElMessage.success(isNew ? "要素已新增" : "要素已保存");
}

function removeElementDraft() {
  if (!elementDraft.value) return;
  const id = elementDraft.value.id;
  elementDialogVisible.value = false;
  if (id && props.elementNodes.some((item) => item.id === id)) {
    emit("remove-element", id);
  }
}

function removeCurrent() {
  if (!draft.value) return;
  const index = nodes.value.findIndex((item) => item.key === draft.value.key);
  dialogVisible.value = false;
  if (index >= 0) emit("remove", index);
}

function addNode() {
  emit("add", props.focus);
}

function runCurrent(includeChildren = false) {
  if (!draft.value?.key) return;
  emit("run-chapter", { key: draft.value.key, includeChildren: Boolean(includeChildren) });
}

function onStructureOnlyChange(on) {
  if (!draft.value || !on) return;
  draft.value.fixed_output = false;
  draft.value.keywords = [];
  draft.value.content_blocks = [];
  draft.value.reference_text = "";
  pointsText.value = "";
  if (!(draft.value.describes || "").trim()) {
    draft.value.describes = `目录节点「${draft.value.title || draft.value.key || ""}」，正文由子节展开，本章不单独撰写。`;
  }
}

async function parsePoints() {
  if (!draft.value || !sampleText.value.trim()) return;
  if (draft.value.structure_only) {
    ElMessage.info("目录节点无需解析");
    return;
  }
  if (!props.taskId) {
    ElMessage.warning("请先生成任务目录");
    return;
  }
  parsing.value = true;
  try {
    const data = await request(`/api/writing/tasks/${encodeURIComponent(props.taskId)}/outline/parse-points`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: sampleText.value,
        title: draft.value.title || "",
        sequence: draft.value.sequence || "",
      }),
    });
    const points = data.points || [];
    const keywords = data.keywords || [];
    const describes = (data.describes || "").trim();
    const contentBlocks = data.content_blocks || [];
    const referenceText = (data.reference_text || "").trim();
    const structureOnly = Boolean(data.structure_only);
    if (
      !structureOnly &&
      !points.length &&
      !keywords.length &&
      !describes &&
      !contentBlocks.length &&
      !referenceText
    ) {
      ElMessage.warning("没有解析出简述或写法要点");
      return;
    }
    if (draft.value) {
      draft.value.structure_only = structureOnly;
      if (structureOnly) {
        onStructureOnlyChange(true);
        if (describes) draft.value.describes = describes;
        chapterTab.value = "config";
        ElMessage.success("已识别为目录节点，无需写作要点");
        return;
      }
      if (points.length) pointsText.value = points.join("\n");
      if (describes) draft.value.describes = describes;
      if (keywords.length) {
        // 解析结果覆盖检索词（V2 风格：可跨项目复用），避免旧专名残留
        draft.value.keywords = [...keywords];
      }
      if (contentBlocks.length) draft.value.content_blocks = contentBlocks;
      if (referenceText) draft.value.reference_text = referenceText;
    }
    chapterTab.value = "result";
    ElMessage.success(
      [
        describes ? "已生成简述" : "",
        points.length ? `${points.length} 条写法要点` : "",
        keywords.length ? `${keywords.length} 个检索词` : "",
        contentBlocks.length ? `${contentBlocks.length} 个版式块` : "",
        referenceText ? "已填参考正文" : "",
      ]
        .filter(Boolean)
        .join(" · ") || "解析完成"
    );
  } catch (error) {
    ElMessage.error(error.message || "解析失败");
  } finally {
    parsing.value = false;
  }
}
</script>

<style scoped>
.outline-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}
.outline-shell.canvasOnly {
  flex: 1 1 auto;
  min-height: 0;
  height: 100%;
}
.outline-shell.treeAbove {
  gap: 10px;
}
.outline-tree-card {
  flex-shrink: 0;
  border: 1px solid var(--ay-line, var(--ay-line));
  border-radius: var(--ay-radius-lg, 12px);
  background: #fff;
  padding: 10px 12px 8px;
  max-height: 220px;
  overflow: auto;
}
.outline-tree-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 12px;
  margin-bottom: 8px;
}
.outline-tree-head .card-title {
  font-weight: 650;
  font-size: 14px;
}
.outline-chapter-tree {
  
  background: transparent;
}
.outline-chapter-tree .tree-seq {
  display: inline-block;
  min-width: 2.2em;
  margin-right: 6px;
  color: var(--ay-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.outline-chapter-tree .tree-title {
  font-size: 13px;
  color: var(--ay-ink);
}
.outline-shell.treeAbove.canvasOnly .canvas-card {
  flex: 1 1 auto;
  min-height: 300px;
}
.outline-shell.treeAbove.canvasOnly .canvas {
  min-height: 300px;
  height: 340px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.card-title {
  font-weight: 650;
  font-size: 16px;
}
.head-hint,
.hint,
.field-hint {
  margin: 4px 0 0;
  color: var(--ay-muted);
  font-size: 13px;
  line-height: 1.5;
}
.field-hint {
  margin-bottom: 6px;
}
.chapter-tabs :deep(.ant-tabs-nav) {
  margin-bottom: 12px;
}
.study-result-banner,
.run-result-banner {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: baseline;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #166534;
  font-size: 13px;
}
.run-result-banner {
  background: #eff6ff;
  border-color: #bfdbfe;
  color: #1e40af;
}
.study-result-banner strong,
.run-result-banner strong {
  font-weight: 650;
}
.html-draft {
  line-height: 1.55;
  font-size: 13px;
  color: #0f172a;
  max-height: 420px;
  overflow: auto;
  padding: 12px;
  border: 1px solid var(--ay-line);
  border-radius: 8px;
  background: #fff;
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
.evidence-box {
  margin-top: 12px;
  font-size: 13px;
}
.evidence-box ul {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 200px;
  overflow: auto;
}
.evidence-box li {
  padding: 8px 10px;
  border: 1px solid var(--ay-line);
  border-radius: 8px;
  background: #fafbfc;
}
.ev-src {
  margin-left: 8px;
  color: var(--ay-muted);
  font-size: 12px;
}
.ev-excerpt {
  margin: 6px 0 0;
  color: #334155;
  line-height: 1.45;
  white-space: pre-wrap;
}
.points-view {
  margin: 0 0 10px;
  padding: 10px 10px 10px 28px;
  max-height: 280px;
  overflow: auto;
  border: 1px solid var(--ay-line);
  border-radius: 8px;
  background: #fafbfc;
  font-size: 13px;
  line-height: 1.55;
  color: #0f172a;
}
.points-view li + li {
  margin-top: 6px;
}
.points-edit {
  margin-top: 4px;
}
.table-sample {
  flex: 1 1 100%;
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: 6px;
  background: #f8fafc;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-all;
}
.block-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.block-list li {
  padding: 8px 10px;
  border: 1px solid var(--ay-line);
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.45;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: baseline;
}
.block-note {
  color: var(--ay-muted);
  flex: 1 1 100%;
}
.tree-link-btn {
  margin-left: auto;
  font-size: 12px;
}
.linked-preview {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.linked-preview li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.tools {
  border: 1px solid var(--ay-line);
  border-radius: 10px;
  overflow: hidden;
}
.paste-box,
.parse-box {
  padding: 4px 0 0;
}
.parse-box,
.elements-block {
  margin-top: 8px;
  padding: 12px;
  border: 1px dashed var(--ay-line);
  border-radius: 10px;
  background: #fafbfc;
}
.elements-block {
  border-style: solid;
}
.paste-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.paste-title {
  font-weight: 650;
  font-size: 13px;
}
.paste-actions,
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.hidden {
  display: none;
}
.canvas-card {
  border: 1px solid var(--ay-line, var(--ay-line));
  border-radius: var(--ay-radius-lg, 12px);
  overflow: hidden;
  background: #fff;
  box-shadow: 0 2px 10px rgba(31, 35, 41, 0.03);
}
.outline-shell.canvasOnly .canvas-card {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.canvas-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid var(--ay-line, var(--ay-line));
  flex-shrink: 0;
  background: #fbfcfe;
}
.canvas {
  height: 560px;
}
.outline-shell.canvasOnly .canvas {
  flex: 1 1 auto;
  min-height: 280px;
  height: 360px;
}
.outline-shell.canvasOnly .canvas .vue-flow {
  width: 100%;
  height: 100%;
}
</style>

<style>
/* 要素弹窗需盖在 Vue Flow 之上 */
.element-node-dialog {
  z-index: 4000 !important;
}
.element-card {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid var(--ay-line);
  border-radius: 8px;
  background: #fff;
}
.element-ops {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mt8 {
  margin-top: 8px;
}
@media (max-width: 900px) {
  .confirm-bar,
  .card-head {
    flex-direction: column;
    align-items: stretch;
  }
  .canvas {
    height: 420px;
  }
}
</style>
