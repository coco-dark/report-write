/**
 * 库蓝本 id vs 写作副本 id（w*）判定。
 * 分析开跑时常把 w* 误写入 blueprint_id；文案与「同步到蓝本库」须共用同一套解析。
 */

export function isWritingCopyId(id) {
  return /^w/i.test(String(id || "").trim());
}

/** bp:w* 是脏登记键，统一成 task:w*；其它键原样返回 */
export function normalizeDocumentRegistryKey(key) {
  const k = String(key || "").trim();
  if (!k) return "";
  if (/^bp:/i.test(k)) {
    const raw = k.slice(3).trim();
    if (isWritingCopyId(raw)) return `task:${raw}`;
  }
  return k;
}

/** 文档窗下拉展示名：永不回退到 bp:/task: 原始 id */
export function documentWindowOptionLabel(doc) {
  const title = String(doc?.title || "").trim();
  if (title && !/^(task:|bp:)/i.test(title) && !isWritingCopyId(title)) {
    return title;
  }
  const kind = String(doc?.kindLabel || "").trim();
  if (kind) return kind;
  return "未命名文档";
}

/** 可作为「源蓝本库」条目的 id：非空且不是写作副本 w* */
export function isLibraryBlueprintId(id) {
  const s = String(id || "").trim();
  return Boolean(s && !isWritingCopyId(s));
}

/** 无蓝本·依据样本（收录前不入库）；勿用会话里上一份选用蓝本 id 冒充源 */
export function isFreeformSampleDocument(doc, session = {}) {
  // 已挂库 / 本任务已收录 → 不再当「未入库样本」
  if (isLibraryBlueprintId(doc?.blueprintId) || isLibraryBlueprintId(session.collectedBlueprintId)) {
    return false;
  }
  const mode = String(doc?.wizardMode || "").trim();
  if (mode === "freeform_sample" || mode === "create") return true;
  const label = String(doc?.kindLabel || "").trim();
  if (label.includes("依据样本")) return true;
  return false;
}

/**
 * 从文档项 + 会话上下文解析真正的库蓝本源 id。
 * @param {object|null|undefined} doc conversationDocuments 项
 * @param {object} [session]
 * @param {string} [session.selectedBlueprint]
 * @param {object} [session.liveWizard]
 * @param {string} [session.runtimeBlueprintId]
 * @param {string} [session.collectedBlueprintId]
 */
export function resolveLibraryBlueprintId(doc, session = {}) {
  const live = session.liveWizard || {};
  const collected = String(session.collectedBlueprintId || "").trim();
  // 依据样本未收录：只认文档自身挂接，禁止回落 selected/live/runtime（会串上一份）
  if (isFreeformSampleDocument(doc, session)) {
    const own = [doc?.blueprintId, collected]
      .map((x) => String(x || "").trim())
      .find(isLibraryBlueprintId);
    return own || "";
  }
  const ownId = String(doc?.blueprintId || "").trim();
  const kind = String(doc?.kind || "");
  // 无蓝本大纲/写作文档没有自己的库蓝本：只认本任务已收录，不回落会话里上一份选用蓝本
  if (
    !isLibraryBlueprintId(ownId) &&
    (kind === "outline" || kind === "writing" || kind === "task")
  ) {
    return isLibraryBlueprintId(collected) ? collected : "";
  }
  const candidates = [
    doc?.blueprintId,
    String(doc?.kind || "") === "blueprint" ? doc?.taskId : "",
    collected,
    session.selectedBlueprint,
    live.source_blueprint_id,
    live.blueprint_id,
    session.runtimeBlueprintId,
  ].map((x) => String(x || "").trim());
  return candidates.find(isLibraryBlueprintId) || "";
}

/**
 * 有写作副本 w* 且能解析到库蓝本源（或已收录）→ 应显示「同步到蓝本库」
 */
export function hasWritingCopyWithLibrarySource(doc, session = {}) {
  const wid = String(doc?.writingTaskId || "").trim();
  if (!isWritingCopyId(wid)) return false;
  if (resolveLibraryBlueprintId(doc, session)) return true;
  return Boolean(String(session.collectedBlueprintId || "").trim());
}

/** upsert 用：拒绝把 w* 落成 blueprintId，优先保留旧库蓝本 */
export function sanitizeLibraryBlueprintId(incoming, previous = "") {
  const next = String(incoming || "").trim();
  if (isLibraryBlueprintId(next)) return next;
  const prev = String(previous || "").trim();
  if (isLibraryBlueprintId(prev)) return prev;
  return "";
}
