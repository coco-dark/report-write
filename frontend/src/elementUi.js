/** 要素展示文案（对话 / 拓扑 / 关联弹窗共用） */

export function elementKindLabel(kind) {
  return (
    {
      kv: "键值",
      fixed: "固定",
      file: "文件",
      rag_search: "文件",
      file_slot: "引用槽位",
      llm_generate: "LLM",
      node_ref: "引用结果",
      image: "图片",
    }[kind] || kind || "要素"
  );
}

export function kvPairsFromElement(el) {
  const pairs = el?.kv_pairs;
  if (Array.isArray(pairs) && pairs.length) {
    return pairs.map((p) => ({
      key: String(p.key ?? "").trim(),
      value: String(p.value ?? "").trim(),
    }));
  }
  const name = String(el?.name ?? "").trim();
  const value = String(el?.value ?? "").trim();
  if (name || value) return [{ key: name || "键", value }];
  return [{ key: "", value: "" }];
}

export function normalizeKvElementDraft(draft) {
  if (!draft || draft.kind !== "kv") return draft;
  const rows = (draft.kv_pairs || [])
    .map((p) => ({ key: String(p.key || "").trim(), value: String(p.value || "").trim() }))
    .filter((p) => p.key);
  if (!rows.length && draft.kv_pairs?.length) {
    rows.push(...draft.kv_pairs);
  }
  if (rows.length) {
    draft.kv_pairs = rows;
    if (!draft.name?.trim()) draft.name = rows[0].key;
    if (rows.length === 1) draft.value = rows[0].value;
    else draft.value = rows.map((r) => `${r.key}：${r.value}`).join("\n");
  } else if (!draft.kv_pairs?.length) {
    draft.kv_pairs = [{ key: draft.name || "", value: draft.value || "" }];
  }
  return draft;
}

export function elementSummary(el) {
  if (!el) return "";
  const kind = el.kind || "";
  if (kind === "kv") {
    const pairs = kvPairsFromElement(el).filter((p) => p.key);
    if (pairs.length > 1) {
      return pairs.map((p) => `${p.key}→${p.value || "…"}`).slice(0, 3).join("；") + (pairs.length > 3 ? "…" : "");
    }
    const p = pairs[0];
    if (p?.value) return `值：${p.value.length > 40 ? `${p.value.slice(0, 40)}…` : p.value}`;
    if (p?.key) return `键：${p.key}`;
    return "（值待填写）";
  }
  if (kind === "file" || kind === "rag_search") {
    const files = Array.isArray(el.files) ? el.files : [];
    const parsed = files.filter((f) => String(f?.parse_status || "") === "parsed").length;
    const q = String(el.query || "").trim();
    if (!files.length) return "文件（待上传）";
    return [
      `${files.length} 个文件`,
      parsed < files.length ? `${files.length - parsed} 待解析` : "已解析",
      q ? `检索：${q}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (kind === "fixed") {
    const v = String(el.value ?? "").trim();
    return v ? `值：${v.length > 40 ? `${v.slice(0, 40)}…` : v}` : "（值待填写）";
  }
  if (kind === "llm_generate") {
    const p = String(el.prompt || "").trim();
    return p ? `提示：${p.slice(0, 48)}${p.length > 48 ? "…" : ""}` : "LLM（待配置）";
  }
  if (kind === "node_ref") {
    const key = String(el.ref_key || "").trim();
    return key ? `引用结果 · ${key}` : "引用结果（未选要素）";
  }
  if (kind === "image") {
    const src = String(el.image_source || "upload").trim() || "upload";
    if (String(el.image_path || "").trim()) {
      return src === "llm" ? "文生图 · 已生成" : "图片 · 已上传";
    }
    return src === "llm" ? "文生图（待生成）" : "图片（待上传）";
  }
  if (kind === "file_slot") {
    const slots = Array.isArray(el.file_slots) ? el.file_slots : [];
    if (!slots.length) return "文件插槽（待配置）";
    const names = slots
      .map((s) => String(s?.name || s?.query || `槽${s?.no || ""}`).trim())
      .filter(Boolean)
      .slice(0, 3);
    return names.length ? `插槽：${names.join("、")}${slots.length > 3 ? "…" : ""}` : `${slots.length} 个槽位`;
  }
  return "";
}

/** 与后端 element_needs_supplement 对齐：是否仍缺配置 */
export function elementNeedsSupplement(el) {
  if (!el || typeof el !== "object") return true;
  let kind = String(el.kind || "").trim();
  if (kind === "rag_search") kind = "file";
  if (kind === "kv") {
    const pairs = kvPairsFromElement(el).filter((p) => p.key);
    if (pairs.length) return !pairs.some((p) => String(p.value || "").trim());
    return !String(el.value || "").trim();
  }
  if (kind === "fixed") return !String(el.value ?? "").trim();
  if (kind === "file") {
    const files = Array.isArray(el.files) ? el.files : [];
    if (!files.length) return true;
    return !files.some((f) => String(f?.parse_status || "") === "parsed");
  }
  if (kind === "llm_generate") return !String(el.prompt || "").trim();
  if (kind === "node_ref") return !String(el.ref_key || "").trim();
  if (kind === "image") {
    const src = String(el.image_source || "upload").trim() || "upload";
    if (src === "llm") return !String(el.prompt || "").trim();
    return !String(el.image_path || "").trim();
  }
  if (kind === "file_slot") {
    const slots = Array.isArray(el.file_slots) ? el.file_slots : [];
    return !slots.length;
  }
  return !String(el.value ?? "").trim();
}

export function elementDraftUiKeysToStrip() {
  return ["knowledge_folder_picks", "knowledge_folder_unmatched", "ref_element_picks"];
}

export function stripElementDraftUiKeys(draft) {
  const copy = { ...draft };
  for (const key of elementDraftUiKeysToStrip()) {
    delete copy[key];
  }
  return copy;
}

export function elementDraftHasPendingPicks(draft) {
  if (!draft) return false;
  return (draft.ref_element_picks || []).some(
    (p) => (p.candidates || []).length > 0 && !String(p.selected_id || "").trim(),
  );
}

export function normalizeIdList(value) {
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

/** 与要素库「新建要素」一致的草稿结构 */
export function blankElementDraft(kind = "kv", { library = false } = {}) {
  const normalizedKind = kind === "rag_search" ? "file" : kind;
  const base = {
    name: "",
    kind: normalizedKind,
    value: "",
    query: "",
    prompt: "",
    kv_pairs: [{ key: "", value: "" }],
    knowledge_folder_ids: [],
    knowledge_scope: "both",
    ref_key: "",
    ref_kind: "element",
    ref_field: "result",
    material_files: [],
    files: [],
    file_slots: [],
    image_source: "upload",
    image_path: "",
    required: true,
  };
  if (!library) return base;
  return {
    ...base,
    id: "",
    category: "",
    tags: [],
    purpose: "",
    link_ids: [],
  };
}

export function defaultNewElementRow(kind = "kv") {
  return blankElementDraft(kind, { library: true });
}

export function cloneLibraryItemToTaskElement(lib) {
  const id = `el-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const kind = lib?.kind === "rag_search" ? "file" : lib?.kind || "kv";
  return {
    id,
    name: String(lib?.name || "").trim() || "未命名要素",
    kind,
    value: String(lib?.value || "").trim(),
    query: String(lib?.query || "").trim(),
    prompt: String(lib?.prompt || "").trim(),
    ref_key: lib?.ref_key || "",
    ref_kind: lib?.ref_kind || "element",
    ref_field: lib?.ref_field || "result",
    material_files: [...(lib?.material_files || [])],
    knowledge_folder_ids: [],
    files: Array.isArray(lib?.files) ? lib.files.map((f) => ({ ...f })) : [],
    file_slots: Array.isArray(lib?.file_slots) ? lib.file_slots.map((s) => ({ ...s })) : [],
    category: String(lib?.category || "").trim(),
    tags: Array.isArray(lib?.tags)
      ? lib.tags.map((t) => String(t || "").trim()).filter(Boolean)
      : [],
    purpose: String(lib?.purpose || "").trim(),
    image_source: String(lib?.image_source || "upload").trim() || "upload",
    image_path: String(lib?.image_path || "").trim(),
    result: "",
    required: lib?.required !== false,
    library_item_id: String(lib?.id || "").trim(),
    kv_pairs: kvPairsFromElement(lib).filter((p) => p.key || p.value),
  };
}

export function materializeNewElements(rows) {
  const out = [];
  for (const row of rows || []) {
    let draft = {
      ...row,
      kv_pairs: (row.kv_pairs || []).map((p) => ({ key: p.key, value: p.value })),
      knowledge_folder_ids: normalizeIdList(row.knowledge_folder_ids),
    };
    const kind = draft.kind || "kv";
    if (kind === "kv") {
      draft = normalizeKvElementDraft({ ...draft });
      if (!draft.kv_pairs?.some((p) => String(p.key || "").trim())) continue;
      if (!String(draft.name || "").trim()) draft.name = draft.kv_pairs[0].key;
    } else if (!String(draft.name || "").trim()) {
      continue;
    }
    if (kind === "node_ref") {
      draft.ref_kind = "element";
      draft.ref_field = "result";
      if (!String(draft.ref_key || "").trim()) continue;
    }
    const fileSlots = (Array.isArray(draft.file_slots) ? draft.file_slots : [])
      .map((slot) => ({
        no: Number(slot?.no) > 0 ? Number(slot.no) : 0,
        name: String(slot?.name || "").trim(),
        query: String(slot?.query || "").trim(),
      }))
      .filter((slot) => slot.no > 0);
    const id = `el-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    out.push({
      id,
      name: String(draft.name || "").trim() || "未命名要素",
      kind,
      value: String(draft.value || "").trim(),
      kv_pairs: [...(draft.kv_pairs || [])],
      query: String(draft.query || "").trim(),
      prompt: String(draft.prompt || "").trim(),
      ref_key: draft.ref_key || "",
      ref_kind: draft.ref_kind || "element",
      ref_field: draft.ref_field || "result",
      material_files: [...(draft.material_files || [])],
      knowledge_folder_ids: normalizeIdList(draft.knowledge_folder_ids),
      file_slots: fileSlots,
      files: Array.isArray(draft.files) ? draft.files.map((file) => ({ ...file })) : [],
      image_source: String(draft.image_source || "upload").trim() || "upload",
      image_path: String(draft.image_path || "").trim(),
      // 新建时已填的库元数据要带到任务要素，收入要素库卡片才能回显
      category: String(draft.category || "").trim(),
      tags: Array.isArray(draft.tags)
        ? draft.tags.map((t) => String(t || "").trim()).filter(Boolean)
        : [],
      purpose: String(draft.purpose || "").trim(),
      result: "",
      required: draft.required !== false,
    });
  }
  return out;
}
