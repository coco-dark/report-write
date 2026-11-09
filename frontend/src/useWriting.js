import { computed, reactive, ref, watch } from "vue";
import { ElMessage } from "./ui.js";

import { authHeaders, readStream, request } from "./api";
import { formatExtracts, lines, newTaskId, parseExtracts } from "./constants";

function blankChapter(key, title) {
  return {
    key,
    title: title || key,
    status: "pending",
    evidence: [],
    checks: [],
    human_note: "",
    draft: null,
    official_text: "",
  };
}

export function useWriting() {
  const taskId = ref(newTaskId());
  const bizType = ref("002");
  const forceStudy = ref(false);
  const templateFile = ref(null);
  const materialFiles = ref([]);
  const studied = ref([]);
  const selectedStudy = ref("");
  const busy = ref(false);
  const currentTask = ref(null);
  const timeline = ref([]);
  /** 依据文档学习进度：当前章节 / 已完成列表 */
  const studyProgress = ref({
    active: false,
    phase: "",
    current: "",
    index: 0,
    total: 0,
    items: [], // { key, title, sequence, status: running|done|pending }
  });

  /** 单章/多章运行进度：检索 → 写作 → 检查 */
  const runProgress = ref({
    active: false,
    phase: "",
    current: "",
    chapter: "",
    step: "",
    items: [], // { key, title, step, status, note }
  });

  const RUN_STEP_LABEL = { search: "检索", write: "写作", check: "检查" };

  function resetStudyProgress() {
    studyProgress.value = {
      active: false,
      phase: "",
      current: "",
      index: 0,
      total: 0,
      items: [],
    };
  }

  function resetRunProgress() {
    runProgress.value = {
      active: false,
      phase: "",
      current: "",
      chapter: "",
      step: "",
      items: [],
    };
  }

  function touchStudyProgress(event) {
    if (event?.type === "task") {
      studyProgress.value = { ...studyProgress.value, active: false, current: "" };
      return;
    }
    if (event?.type !== "step") return;
    const step = event.step;
    if (step !== "study" && step !== "outline") return;
    const result = event.result || {};
    const index = Number(result.index || 0);
    const total = Number(result.total || studyProgress.value.total || 0);
    const title = event.title || "";
    const key = event.chapter || `${step}-${index || title}`;
    const items = [...(studyProgress.value.items || [])];
    const existing = items.findIndex((item) => item.key === key);
    const row = {
      key,
      title,
      sequence: result.sequence || "",
      status: event.status === "done" ? "done" : "running",
      note: result.note || "",
    };
    if (existing >= 0) items[existing] = { ...items[existing], ...row };
    else items.push(row);
    if (event.status === "running") {
      for (const item of items) {
        if (item.key !== key && item.status === "running") item.status = "done";
      }
    }
    studyProgress.value = {
      active: true,
      phase: step === "outline" ? "提取目录" : "分析要点",
      current: title,
      index: index || studyProgress.value.index,
      total: total || studyProgress.value.total,
      items,
    };
  }

  function touchRunProgress(event) {
    if (event?.type === "task") {
      runProgress.value = { ...runProgress.value, active: false, current: "" };
      return;
    }
    if (event?.type !== "step") return;
    const step = event.step;
    if (!["search", "write", "check"].includes(step)) return;
    const title = event.title || event.chapter || "";
    const chapter = event.chapter || "";
    const key = `${chapter}:${step}`;
    const items = [...(runProgress.value.items || [])];
    const existing = items.findIndex((item) => item.key === key);
    const row = {
      key,
      title,
      chapter,
      step,
      stepLabel: RUN_STEP_LABEL[step] || step,
      status: event.status === "done" ? "done" : event.status === "skipped" ? "skipped" : "running",
      note: (event.result && event.result.note) || "",
    };
    if (existing >= 0) items[existing] = { ...items[existing], ...row };
    else items.push(row);
    if (event.status === "running") {
      for (const item of items) {
        if (item.key !== key && item.status === "running") item.status = "done";
      }
    }
    runProgress.value = {
      active: true,
      phase: RUN_STEP_LABEL[step] || step,
      current: title,
      chapter,
      step,
      items,
    };
  }
  const focus = ref(0);
  const exportText = ref("");
  const exportVisible = ref(false);

  const globals = reactive({ voice: "", layout: "", paragraph_format: "" });
  const chapterDrafts = reactive({});
  const comments = reactive({});
  const editedTexts = reactive({});
  const live = new Map();

  const entries = computed(() => {
    const task = currentTask.value;
    if (!task) return [];
    const briefChapters = task.brief?.chapters || [];
    const states = new Map((task.chapters || []).map((item) => [item.key, item]));
    const source = briefChapters.length ? briefChapters : task.chapters || [];
    return source.map((item) => {
      const state = states.get(item.key) || blankChapter(item.key, item.title);
      const overlay = live.get(item.key);
      return {
        requirement: item,
        state: overlay ? { ...state, ...overlay, key: item.key } : state,
      };
    });
  });

  const active = computed(() => entries.value[focus.value] || null);
  const confirmed = computed(() => Boolean(currentTask.value?.brief_confirmed));
  const outlineConfirmed = computed(() => Boolean(currentTask.value?.outline_confirmed));
  const outline = computed(() => currentTask.value?.outline || null);
  const outlineFocus = ref(0);

  const phase = computed(() => {
    if (!currentTask.value) return 0;
    if (!outlineConfirmed.value) return 1;
    if (!confirmed.value) return 2;
    if (currentTask.value.status === "completed") return 4;
    return 3;
  });

  function resetSession() {
    timeline.value = [];
    currentTask.value = null;
    focus.value = 0;
    outlineFocus.value = 0;
    live.clear();
    exportText.value = "";
    for (const key of Object.keys(chapterDrafts)) delete chapterDrafts[key];
    for (const key of Object.keys(comments)) delete comments[key];
    for (const key of Object.keys(editedTexts)) delete editedTexts[key];
    globals.voice = "";
    globals.layout = "";
    globals.paragraph_format = "";
  }

  function rememberDrafts(task) {
    const brief = task.brief || {};
    if (!globals.voice && !globals.layout && !globals.paragraph_format) {
      globals.voice = brief.voice || "";
      globals.layout = brief.layout || "";
      globals.paragraph_format = brief.paragraph_format || "";
    }
    for (const chapter of brief.chapters || []) {
      if (!chapterDrafts[chapter.key]) {
        chapterDrafts[chapter.key] = {
          sequence: chapter.sequence || "",
          describes: chapter.describes || "",
          extracts: formatExtracts(chapter.extracts),
          generate: chapter.generate || "",
          keywords: (chapter.keywords || []).join("\n"),
        };
      }
    }
  }

  function showTask(task, keepFocusKey = "") {
    currentTask.value = task;
    rememberDrafts(task);
    const list = entries.value;
    if (keepFocusKey) {
      const index = list.findIndex((item) => item.requirement.key === keepFocusKey);
      focus.value = index >= 0 ? index : 0;
    } else if (focus.value >= list.length) {
      focus.value = Math.max(list.length - 1, 0);
    }
  }

  function currentKey() {
    return entries.value[focus.value]?.requirement.key || "";
  }

  function rememberLive(event) {
    if (!event.chapter) return;
    const item = live.get(event.chapter) || blankChapter(event.chapter, event.title);
    item.title = event.title || item.title;
    const payload = event.result || {};
    if (event.step === "search") {
      item.status = event.status === "running" ? "searching" : item.status;
      if (event.status === "skipped") {
        item.status = "needs_human";
        item.human_note = payload.note || "本章跳过";
      }
      if (event.status === "done") {
        item.evidence = payload.cards || [];
        item.human_note = payload.note || "";
        item.status = payload.adopted ? "writing" : "needs_human";
      }
    }
    if (event.step === "write") {
      item.status = event.status === "running" ? "writing" : item.status;
      if (event.status === "done") {
        item.draft = { paragraphs: (payload.paragraphs || []).map((text) => ({ text })) };
        item.status = "checking";
      }
    }
    if (event.step === "check") {
      item.status = event.status === "running" ? "checking" : item.status;
      if (event.status === "done") {
        item.checks = [...(item.checks || []), { action: payload.action, reason: payload.reason || "" }];
        item.status = payload.action === "accept" ? "awaiting_confirm" : payload.action === "revise" ? "writing" : "needs_human";
        if (item.status === "needs_human") item.human_note = payload.reason || "";
      }
    }
    live.set(event.chapter, item);
  }

  function applyEvent(event) {
    if (event.type === "error") {
      studyProgress.value = { ...studyProgress.value, active: false };
      runProgress.value = { ...runProgress.value, active: false };
      throw new Error(event.message || event.detail || "执行失败");
    }
    touchStudyProgress(event);
    touchRunProgress(event);
    if (event.type === "task") {
      const task = event.task;
      if (!task || typeof task !== "object") return;
      const key = currentKey();
      live.clear();
      showTask(task, key);
      timeline.value = task.steps || timeline.value;
      studyProgress.value = { ...studyProgress.value, active: false, current: "" };
      runProgress.value = { ...runProgress.value, active: false, current: "" };
      const outlineChapters = task.outline && typeof task.outline === "object" ? task.outline.chapters : null;
      if (task.brief_confirmed) {
        ElMessage.success("这一章已写完，可以切换章节查看");
      } else if (task.brief) {
        ElMessage.success("分析结果已保存，请从第一章开始核对");
      } else if (Array.isArray(outlineChapters) && outlineChapters.length) {
        ElMessage.success("目录已生成，请核对后确认");
      }
      return;
    }
    const key = `${event.step}:${event.chapter || ""}`;
    const current = timeline.value.find((item) => item.key === key && item.status === "running");
    if (current) {
      current.status = event.status;
      current.result = event.result || {};
      current.title = event.title || current.title;
    } else {
      timeline.value.push({ ...event, key, result: event.result || {} });
    }
    rememberLive(event);
    if (event.chapter && currentTask.value) {
      const index = entries.value.findIndex((item) => item.requirement.key === event.chapter);
      if (index >= 0) focus.value = index;
      currentTask.value = { ...currentTask.value };
    }
  }

  async function guard(work) {
    busy.value = true;
    try {
      return await work();
    } catch (error) {
      ElMessage.error(error.message || "执行失败");
      return undefined;
    } finally {
      busy.value = false;
    }
  }

  function loadStudies() {
    return request(`/api/writing/studies?biz_type=${encodeURIComponent(bizType.value)}`)
      .then((rows) => {
        studied.value = rows || [];
        if (!studied.value.some((item) => studyKey(item) === selectedStudy.value)) {
          selectedStudy.value = "";
        }
      })
      .catch(() => {
        studied.value = [];
      });
  }

  function studyKey(item) {
    return `${item.sample_id}\t${item.sample_version}`;
  }

  function useStudy() {
    return guard(async () => {
      const chosen = studied.value.find((item) => studyKey(item) === selectedStudy.value);
      if (!chosen) throw new Error("请选择已经分析过的模版");
      const task = await request("/api/writing/tasks/use-study", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          task_id: taskId.value.trim(),
          biz_type: bizType.value,
          sample_id: chosen.sample_id,
          sample_version: chosen.sample_version,
        }),
      });
      resetSession();
      showTask(task);
      ElMessage.success("已载入分析结果，请核对后确认");
    });
  }

  watch(bizType, () => {
    loadStudies();
  });

  function startFresh() {
    taskId.value = newTaskId();
    templateFile.value = null;
    materialFiles.value = [];
    forceStudy.value = false;
    resetSession();
    ElMessage.success("已生成新的任务编号");
  }

  async function loadBlueprints() {
    return request("/api/writing/blueprints");
  }

  function openBlueprint(id) {
    return guard(async () => {
      if (!id) throw new Error("请选择蓝本");
      resetSession();
      templateFile.value = null;
      materialFiles.value = [];
      selectedStudy.value = "";
      taskId.value = id;
      const task = await request(`/api/writing/tasks/${encodeURIComponent(id)}`);
      showTask(task);
      await loadStudies();
    });
  }

  function createBlueprint(payload = {}) {
    return guard(async () => {
      const created = await request("/api/writing/blueprints", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          title: payload.title || "未命名蓝本",
          category: payload.category || "",
          tags: payload.tags || [],
          description: payload.description || "",
          biz_type: bizType.value || "002",
        }),
      });
      resetSession();
      templateFile.value = null;
      materialFiles.value = [];
      selectedStudy.value = "";
      taskId.value = created.id;
      showTask(created);
      await loadStudies();
      ElMessage.success("蓝本已创建");
      return created;
    });
  }

  function leaveBlueprint() {
    resetSession();
    templateFile.value = null;
    materialFiles.value = [];
    selectedStudy.value = "";
    taskId.value = "";
  }

  function analyze() {
    return generateOutline();
  }

  function generateOutline(options = {}) {
    return guard(async () => {
      if (!taskId.value.trim()) taskId.value = newTaskId();
      const useSample = Boolean(options.use_sample);
      if (!useSample && !templateFile.value) throw new Error("请上传 PDF 或 Word 文件");
      const body = new FormData();
      body.append("task_id", taskId.value.trim());
      body.append("biz_type", bizType.value);
      body.append("use_llm", options.use_llm === false ? "false" : "true");
      body.append("use_sample", useSample ? "true" : "false");
      if (!useSample) body.append("template", templateFile.value);
      const response = await fetch("/api/writing/tasks/outline/generate/stream", { method: "POST", body });
      await readStream(response, applyEvent);
      outlineFocus.value = 0;
    });
  }

  function uploadBlueprintSample(file) {
    return guard(async () => {
      if (!file) throw new Error("请选择样本文件");
      if (!taskId.value.trim()) throw new Error("请先打开蓝本");
      const body = new FormData();
      body.append("file", file);
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/sample`, {
        method: "POST",
        body,
      });
      showTask(updated);
      ElMessage.success(`样本已解析（${updated.sample_chars || 0} 字）`);
    });
  }

  function clearBlueprintSample() {
    return guard(async () => {
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/sample`, {
        method: "DELETE",
      });
      showTask(updated);
      ElMessage.success("已清除蓝本样本");
    });
  }

  async function saveOutlineNodes(chapters) {
    try {
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/outline`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ chapters, replace_tree: true }),
      });
      const key = currentTask.value?.outline?.chapters?.[outlineFocus.value]?.key;
      showTask(updated);
      if (key) {
        const index = (updated.outline?.chapters || []).findIndex((item) => item.key === key);
        if (index >= 0) outlineFocus.value = index;
      }
      return updated;
    } catch (error) {
      ElMessage.error(error.message || "保存目录失败");
      throw error;
    }
  }

  function importOutlineFile(file) {
    return guard(async () => {
      const body = new FormData();
      body.append("file", file);
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/outline/import`, {
        method: "POST",
        body,
      });
      showTask(updated);
      outlineFocus.value = 0;
      ElMessage.success("目录 JSON 已导入");
    });
  }

  function outlineFromText(payload) {
    return guard(async () => {
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/outline/from-text`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ ...payload, biz_type: bizType.value }),
      });
      showTask(updated);
      outlineFocus.value = 0;
      ElMessage.success("已根据目录文本生成结构");
    });
  }

  function confirmOutline() {
    return guard(async () => {
      const chapters = currentTask.value?.outline?.chapters || [];
      if (!chapters.length) throw new Error("请先生成目录");
      await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/outline`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ chapters }),
      });
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/outline/confirm`, {
        method: "POST",
      });
      showTask(updated);
      ElMessage.success("目录已确认，可以分析章节");
    });
  }

  function revokeOutline() {
    return guard(async () => {
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/outline/revoke`, {
        method: "POST",
      });
      showTask(updated);
      ElMessage.success("已撤回目录确认");
    });
  }

  async function copyOutline() {
    try {
      const data = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/outline/text`);
      await navigator.clipboard.writeText(data.text || "");
      ElMessage.success("目录文本已复制");
    } catch (error) {
      ElMessage.error(error.message || "复制失败");
    }
  }

  async function uploadSource() {
    if (!templateFile.value) throw new Error("请上传原文报告（PDF 或 Word）");
    if (!currentTask.value?.outline_confirmed) throw new Error("请先确认目录结构");
    const body = new FormData();
    body.append("biz_type", bizType.value);
    body.append("source", templateFile.value);
    const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/source`, {
      method: "POST",
      body,
    });
    showTask(updated);
    return updated;
  }

  function studyFromOutline() {
    return guard(async () => {
      if (!currentTask.value?.outline_confirmed) throw new Error("请先确认目录结构");
      if (!currentTask.value?.source_path) {
        await uploadSource();
      } else if (templateFile.value) {
        await uploadSource();
      }
      if (!currentTask.value?.source_path && !templateFile.value) {
        throw new Error("请上传原文报告，再按目录分析");
      }
      const response = await fetch(
        `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/from-outline/stream?force_study=${forceStudy.value ? "true" : "false"}`,
        { method: "POST" },
      );
      await readStream(response, applyEvent);
      await loadStudies();
    });
  }

  function addOutlineNode(index) {
    const chapters = [...(currentTask.value?.outline?.chapters || [])];
    const anchor = chapters[index] || { level: 1, sequence: "" };
    chapters.splice(index + 1, 0, {
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
    return saveOutlineNodes(chapters).then(() => {
      outlineFocus.value = index + 1;
    });
  }

  async function saveElementNodes(elements) {
    try {
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/elements`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ elements }),
      });
      showTask(updated);
    } catch (error) {
      ElMessage.error(error.message || "保存要素失败");
    }
  }

  function addElementNode(partial = {}) {
    const elements = [...(currentTask.value?.element_nodes || [])];
    elements.push({
      id: `el-${Date.now()}`,
      name: partial.name || "新要素",
      kind: partial.kind === "rag_search" ? "file" : partial.kind || "file",
      value: "",
      query: "",
      prompt: "",
      material_files: [],
      knowledge_folder_ids: [],
      result: "",
      required: true,
      ...partial,
    });
    return saveElementNodes(elements);
  }

  function removeElementNode(elementId) {
    const elements = (currentTask.value?.element_nodes || []).filter((item) => item.id !== elementId);
    const chapters = (currentTask.value?.outline?.chapters || []).map((item) => ({
      ...item,
      element_ids: (item.element_ids || []).filter((id) => id !== elementId),
    }));
    return saveElementNodes(elements).then(() => saveOutlineNodes(chapters));
  }

  function runChapter(chapterKey, includeChildren = false) {
    return guard(async () => {
      if (!currentTask.value?.outline_confirmed) throw new Error("请先确认目录结构");
      resetRunProgress();
      runProgress.value = {
        active: true,
        phase: "准备运行",
        current: chapterKey,
        chapter: chapterKey,
        step: "",
        items: [],
      };
      const uploads = materialFiles.value.filter((file) => file && file.name);
      if (uploads.length) {
        const body = new FormData();
        body.append("task_id", taskId.value.trim());
        body.append("biz_type", bizType.value);
        uploads.forEach((file) => body.append("files", file));
        await request("/api/writing/uploads", { method: "POST", body });
      }
      const response = await fetch(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/chapters/run/stream`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ chapter_key: chapterKey, include_children: includeChildren }),
      });
      await readStream(response, applyEvent);
      ElMessage.success(includeChildren ? "已运行该节点及子链路" : "已运行该章节");
    });
  }

  function saveSpecMeta(payload) {
    return guard(async () => {
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/spec`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });
      showTask(updated);
      ElMessage.success("规格信息已保存");
    });
  }

  function extractOutlineFromDocument(chapterKeys, { file = null, text = "", useSample = false } = {}) {
    return guard(async () => {
      if (!chapterKeys?.length) throw new Error("请先勾选章节");
      const hasFile = Boolean(file);
      const hasText = Boolean(String(text || "").trim());
      if (!useSample && !hasFile && !hasText) throw new Error("请粘贴文档、上传文件或选用蓝本样本");
      let response;
      if (hasFile) {
        const body = new FormData();
        body.append("chapter_keys", JSON.stringify(chapterKeys));
        body.append("template", file);
        response = await fetch(
          `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/extract-outline/stream`,
          { method: "POST", body }
        );
      } else {
        response = await fetch(
          `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/extract-outline/text/stream`,
          {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
              chapter_keys: chapterKeys,
              text: useSample ? "" : text,
              use_sample: Boolean(useSample),
            }),
          }
        );
      }
      await readStream(response, applyEvent);
      const mismatch = currentTask.value?.pending_outline_tool?.outline_mismatch;
      ElMessage.success(mismatch ? "已提取目录差异，请确认新章节" : "未发现新目录，可继续分析要点");
    });
  }

  function confirmOutlineExtract(drafts, { applyOutline = true, continueStudy = true } = {}) {
    return guard(async () => {
      // 确认目录后服务端按最新目录继续学要点（SSE）
      const response = await fetch(
        `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/confirm-outline/stream`,
        {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            drafts: drafts || [],
            apply_outline: applyOutline,
            continue_study: continueStudy,
          }),
        }
      );
      await readStream(response, applyEvent);
      if (applyOutline && (drafts || []).some((item) => item.accepted !== false)) {
        ElMessage.success(continueStudy ? "目录已写入，正在按最新目录分析要点" : "新目录已写入蓝本");
      } else if (continueStudy) {
        ElMessage.success("开始按当前目录分析要点");
      }
      return currentTask.value;
    });
  }

  function studyPointsFromDocument(chapterKeys, { file = null, text = "", useSample = false } = {}) {
    return guard(async () => {
      const keys = chapterKeys?.length ? chapterKeys : currentTask.value?.study_chapter_keys || [];
      if (!keys.length) throw new Error("没有可分析的章节");
      let response;
      if (file) {
        const body = new FormData();
        body.append("chapter_keys", JSON.stringify(keys));
        body.append("template", file);
        response = await fetch(
          `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/points/stream`,
          { method: "POST", body }
        );
      } else if (useSample) {
        response = await fetch(
          `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/points/text/stream`,
          {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ chapter_keys: keys, text: "", use_sample: true }),
          }
        );
      } else if (String(text || "").trim()) {
        response = await fetch(
          `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/points/text/stream`,
          {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ chapter_keys: keys, text }),
          }
        );
      } else {
        // 复用 extract 阶段已保存的资料
        response = await fetch(
          `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study/points/text/stream`,
          {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ chapter_keys: keys, text: "" }),
          }
        );
      }
      await readStream(response, applyEvent);
      ElMessage.success("写作要点分析完成，请确认");
    });
  }

  /** @deprecated 请用 extractOutlineFromDocument → studyPointsFromDocument */
  function autoStudyChapters(chapterKeys, opts = {}) {
    return extractOutlineFromDocument(chapterKeys, opts);
  }

  function confirmAutoStudy(drafts) {
    return guard(async () => {
      const list = drafts || [];
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/auto-study/confirm`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ drafts: list }),
      });
      showTask(updated);
      const left = (updated.pending_study || []).length;
      ElMessage.success(
        left ? `已确认 ${list.length} 章，还有 ${left} 章待确认` : `已确认并保存 ${list.length} 章分析结果`
      );
      return updated;
    });
  }

  function removeOutlineNode(index) {
    const chapters = [...(currentTask.value?.outline?.chapters || [])];
    if (chapters.length <= 1) {
      ElMessage.warning("至少保留一章");
      return Promise.resolve();
    }
    const key = chapters[index]?.key;
    if (!key) return Promise.resolve();
    return removeOutlineNodes([key]);
  }

  function removeOutlineNodes(keys) {
    const wanted = [...new Set((keys || []).map((item) => String(item || "").trim()).filter(Boolean))];
    if (!wanted.length) {
      ElMessage.warning("请先勾选要删除的章节");
      return Promise.resolve();
    }
    const chapters = [...(currentTask.value?.outline?.chapters || [])];
    if (!chapters.length) return Promise.resolve();
    const byKey = new Map(chapters.map((item) => [item.key, item]));
    const removeSet = new Set(wanted);
    // 父级被删时，未勾选的子孙一并去掉，避免孤儿节点
    for (const item of chapters) {
      let parent = (item.parent || "").trim();
      while (parent) {
        if (removeSet.has(parent)) {
          removeSet.add(item.key);
          break;
        }
        parent = (byKey.get(parent)?.parent || "").trim();
      }
    }
    const remaining = chapters.filter((item) => !removeSet.has(item.key));
    if (!remaining.length) {
      ElMessage.warning("至少保留一章，请减少勾选");
      return Promise.resolve();
    }
    if (remaining.length === chapters.length) {
      ElMessage.warning("没有可删除的章节");
      return Promise.resolve();
    }
    const focusKey = currentTask.value?.outline?.chapters?.[outlineFocus.value]?.key;
    return saveOutlineNodes(remaining).then(() => {
      const next = currentTask.value?.outline?.chapters || [];
      const keepIndex = next.findIndex((item) => item.key === focusKey);
      outlineFocus.value = keepIndex >= 0 ? keepIndex : 0;
      ElMessage.success(`已删除 ${chapters.length - remaining.length} 个章节`);
    });
  }

  function indentOutlineNode(index, delta) {
    const chapters = [...(currentTask.value?.outline?.chapters || [])].map((item) => ({ ...item }));
    const node = chapters[index];
    if (!node) return Promise.resolve();
    node.level = Math.max(1, Math.min(8, (node.level || 1) + delta));
    return saveOutlineNodes(chapters);
  }

  function confirmStudy() {
    return guard(async () => {
      const task = currentTask.value;
      if (!task?.brief) throw new Error("请先分析模版");
      const chapters = (task.brief?.chapters || []).map((chapter) => {
        const saved = chapterDrafts[chapter.key] || {};
        const generate = saved.generate ?? chapter.generate ?? "";
        const keywordText = saved.keywords ?? (chapter.keywords || []).join("\n");
        return {
          key: chapter.key,
          title: chapter.title,
          sequence: saved.sequence ?? chapter.sequence ?? "",
          describes: saved.describes ?? chapter.describes ?? "",
          extracts: parseExtracts(saved.extracts ?? formatExtracts(chapter.extracts)),
          generate,
          keywords: lines(keywordText),
          points: lines(generate),
        };
      });
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/study`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          voice: globals.voice,
          layout: globals.layout,
          paragraph_format: globals.paragraph_format,
          chapters,
        }),
      });
      showTask(updated, currentKey());
      ElMessage.success("分析结果已确认。可开始编写；资料请用要素「文件」或资料包槽位");
    });
  }

  function writeReport() {
    return guard(async () => {
      if (!currentTask.value?.brief_confirmed) throw new Error("请先确认分析结果");
      const uploads = materialFiles.value.filter((file) => file && file.name);
      if (uploads.length) {
        const body = new FormData();
        body.append("task_id", taskId.value.trim());
        body.append("biz_type", bizType.value);
        uploads.forEach((file) => body.append("files", file));
        const uploaded = await request("/api/writing/uploads", { method: "POST", body });
        applyEvent({
          type: "step",
          step: "upload",
          status: "done",
          title: `上传 ${bizType.value}`,
          result: { files: uploaded.files },
        });
      }
      const response = await fetch(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/write/stream`, {
        method: "POST",
      });
      await readStream(response, applyEvent);
    });
  }

  function reload() {
    return guard(async () => {
      const task = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}`);
      timeline.value = task.steps || [];
      showTask(task, currentKey());
    });
  }

  function rerun() {
    return guard(async () => {
      timeline.value = [];
      const response = await fetch(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/rerun/stream`, {
        method: "POST",
      });
      await readStream(response, applyEvent);
      ElMessage.success("已按当前要求重跑");
    });
  }

  function exportConfirmed() {
    return guard(async () => {
      const exported = await request(`/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/export`);
      exportText.value = Object.entries(exported.chapters || {})
        .map(([key, text]) => `【${key}】\n${text || "（未确认）"}`)
        .join("\n\n");
      exportVisible.value = true;
      ElMessage.success("已导出确认过的章节。未确认的章节为空");
    });
  }

  function confirmChapter(decision) {
    return guard(async () => {
      const entry = active.value;
      if (!entry) return;
      const key = entry.requirement.key;
      const draft = (entry.state.draft?.paragraphs || []).map((item) => item.text).join("\n\n");
      const text = editedTexts[key] ?? entry.state.official_text ?? draft;
      const task = await request(
        `/api/writing/tasks/${encodeURIComponent(taskId.value.trim())}/chapters/${encodeURIComponent(key)}/confirm`,
        {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            decision,
            text,
            comment: comments[key] || "",
          }),
        },
      );
      if (decision === "return") timeline.value = task.steps || timeline.value;
      live.delete(key);
      showTask(task, key);
      ElMessage.success("确认已保存");
    });
  }

  function selectChapter(index) {
    focus.value = index;
  }

  function draftOf(key) {
    if (!chapterDrafts[key]) {
      chapterDrafts[key] = { sequence: "", describes: "", extracts: "", generate: "", keywords: "" };
    }
    return chapterDrafts[key];
  }

  function commentOf(key) {
    if (comments[key] === undefined) comments[key] = "";
    return comments;
  }

  function textOf(key, fallback) {
    if (editedTexts[key] === undefined) editedTexts[key] = fallback || "";
    return editedTexts;
  }

  const visibleSteps = computed(() => {
    const key = active.value?.requirement.key;
    const matched = timeline.value.filter((item) => !item.chapter || item.chapter === key);
    return matched.length ? matched : timeline.value.slice(0, 1);
  });

  return {
    taskId,
    bizType,
    forceStudy,
    templateFile,
    materialFiles,
    studied,
    selectedStudy,
    busy,
    currentTask,
    timeline,
    studyProgress,
    resetStudyProgress,
    runProgress,
    resetRunProgress,
    focus,
    outlineFocus,
    exportText,
    exportVisible,
    globals,
    entries,
    active,
    confirmed,
    outlineConfirmed,
    outline,
    phase,
    visibleSteps,
    startFresh,
    loadBlueprints,
    openBlueprint,
    createBlueprint,
    leaveBlueprint,
    loadStudies,
    useStudy,
    analyze,
    generateOutline,
    uploadBlueprintSample,
    clearBlueprintSample,
    saveOutlineNodes,
    importOutlineFile,
    outlineFromText,
    confirmOutline,
    revokeOutline,
    copyOutline,
    studyFromOutline,
    uploadSource,
    addOutlineNode,
    removeOutlineNode,
    removeOutlineNodes,
    indentOutlineNode,
    saveElementNodes,
    addElementNode,
    removeElementNode,
    runChapter,
    saveSpecMeta,
    extractOutlineFromDocument,
    confirmOutlineExtract,
    studyPointsFromDocument,
    autoStudyChapters,
    confirmAutoStudy,
    confirmStudy,
    writeReport,
    reload,
    rerun,
    exportConfirmed,
    confirmChapter,
    selectChapter,
    draftOf,
    commentOf,
    textOf,
  };
}
