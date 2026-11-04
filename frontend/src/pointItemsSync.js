/** 写作要点 points ↔ point_items 双向同步（与后端 point_items_sync 对齐） */

const SUPPLEMENT_TODO_RE = /（待补充[：:][^）)]*）|（待填写）|\{\{配置要素\}\}/;
const SUPPLEMENT_SLOT_RE = /（待补充[：:]([^）)]*)）|（待填写）|\{\{配置要素\}\}/;
const ELEMENT_ID_TOKEN_RE = /^el-[a-z0-9-]+$/i;

export function isElementIdToken(value) {
  return ELEMENT_ID_TOKEN_RE.test(String(value || "").trim());
}

export function humanLabel(...candidates) {
  for (const raw of candidates) {
    const text = String(raw || "").trim();
    if (text && !isElementIdToken(text)) return text;
  }
  return "";
}

export function parseSlotFromText(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const m = raw.match(SUPPLEMENT_SLOT_RE);
  if (m && m[1]) return String(m[1]).trim();
  return "";
}

export function textNeedsSupplement(text) {
  return SUPPLEMENT_TODO_RE.test(String(text || ""));
}

export function pointsFromPointItems(items) {
  const out = [];
  for (const row of items || []) {
    if (typeof row === "string") {
      const line = row.trim();
      if (line) out.push(line);
      continue;
    }
    if (!row || typeof row !== "object") continue;
    const line = String(row.text || row.label || "").trim();
    if (line) out.push(line);
  }
  return out;
}

export function syncPointItemsFromPoints(points, prevItems) {
  const prev = (prevItems || []).filter((x) => x && typeof x === "object");
  const lines = (points || []).map((p) => String(p || "").trim()).filter(Boolean);
  const out = [];
  lines.forEach((line, i) => {
    const prevRow = prev[i] || {};
    const id = String(prevRow.id || prevRow.key || `p${i}`).trim() || `p${i}`;
    let slot = humanLabel(prevRow.slot, prevRow.element_name);
    if (!slot) slot = parseSlotFromText(line);
    const needs = textNeedsSupplement(line) || Boolean(prevRow.needs_supplement);
    const row = { id, text: line };
    if (slot) row.slot = slot;
    const eid = String(prevRow.element_id || "").trim();
    if (eid) {
      row.element_id = eid;
      const ename = humanLabel(prevRow.element_name, slot);
      if (ename) row.element_name = ename;
    } else if (needs) {
      row.needs_supplement = true;
    }
    out.push(row);
  });
  return out;
}

/** 展示用：优先 point_items 的 text，否则回落 points 数组 */
export function pointsTextFromChapter(node, ch, cfg) {
  return pointsFromPointItems(pointItemsFromChapter(node, ch, cfg)).join("\n");
}

export function normalizePointItemRow(row, index = 0) {
  if (typeof row === "string") {
    const text = row.trim();
    if (!text) return null;
    const slot = parseSlotFromText(text);
    const out = { id: `p${index}`, text };
    if (slot) out.slot = slot;
    if (textNeedsSupplement(text)) out.needs_supplement = true;
    return out;
  }
  if (!row || typeof row !== "object") return null;
  const text = String(row.text || row.label || "").trim();
  const id = String(row.id || row.key || `p${index}`).trim() || `p${index}`;
  const out = { id, text };
  const slot = humanLabel(row.slot, row.element_name) || parseSlotFromText(text);
  if (slot) out.slot = slot;
  if (textNeedsSupplement(text) || row.needs_supplement) out.needs_supplement = true;
  const eid = String(row.element_id || "").trim();
  if (eid) {
    out.element_id = eid;
    out.needs_supplement = false;
    const ename = humanLabel(row.element_name, slot);
    if (ename) out.element_name = ename;
  }
  return out;
}

export function pointItemsRichnessScore(items) {
  if (!Array.isArray(items) || !items.length) return 0;
  let score = items.length;
  for (const row of items) {
    if (String(row?.element_id || "").trim()) score += 10;
    if (String(row?.element_name || "").trim()) score += 2;
  }
  return score;
}

export function pickRicherPointItems(...candidates) {
  let best = [];
  let bestScore = 0;
  for (const raw of candidates) {
    if (!Array.isArray(raw) || !raw.length) continue;
    const score = pointItemsRichnessScore(raw);
    if (score > bestScore) {
      bestScore = score;
      best = raw;
    }
  }
  if (!best.length) return [];
  return best.map((r, i) => normalizePointItemRow(r, i)).filter(Boolean);
}

/** 大纲/配置真源已有 point_items 时只用真源，避免进度卡旧列表（条数更多）盖回删除结果 */
export function pickPointItemsAuthoritative(outlineCandidates, ...fallbackCandidates) {
  const outline = (outlineCandidates || []).filter((a) => Array.isArray(a));
  if (outline.length) {
    return pickRicherPointItems(...outline);
  }
  return pickRicherPointItems(...fallbackCandidates);
}

/** 章节配置表格：优先 point_items，否则由 points 生成 */
export function pointItemsFromChapter(node, ch, cfg) {
  const outlineSources = [
    node?.point_items,
    ch?.point_items,
    cfg?.point_items,
    node?.config?.point_items,
    ch?.config?.point_items,
  ];
  if (outlineSources.some((a) => Array.isArray(a))) {
    return pickPointItemsAuthoritative(outlineSources);
  }
  const pick = (a, b) => (Array.isArray(a) && a.length ? a : Array.isArray(b) ? b : []);
  const points = pick(node?.points, pick(ch?.points, cfg?.points));
  const lines = (points || []).map((p) => String(p || "").trim()).filter(Boolean);
  return syncPointItemsFromPoints(lines, []);
}

export function pointItemsSignature(items) {
  return JSON.stringify(
    (items || []).map((row) => ({
      id: String(row?.id || ""),
      text: String(row?.text || ""),
      slot: String(row?.slot || ""),
      element_id: String(row?.element_id || ""),
      needs: Boolean(row?.needs_supplement),
    }))
  );
}

/** 关联要素后：按待补项名 / 本次新增 id / 指定行，写入 point_items.element_id */
export function linkPointItemsToElements(items, elements, options = {}) {
  const list = (items || [])
    .filter((row) => row && typeof row === "object")
    .map((row) => ({ ...row }));
  const elementIds = (options.elementIds || [])
    .map((id) => String(id || "").trim())
    .filter(Boolean);
  const newIds = (options.newElementIds || [])
    .map((id) => String(id || "").trim())
    .filter(Boolean);
  const byId = new Map();
  for (const el of elements || []) {
    const id = String(el?.id || "").trim();
    if (id) byId.set(id, el);
  }
  const pointLink = options.pointLink || null;
  const linkIndex =
    pointLink != null && Number(pointLink.index) >= 0 ? Number(pointLink.index) : -1;
  const linkId = String(pointLink?.id || "").trim();
  const assignedNew = new Set();

  const idsUsedByOtherRows = (rowIndex) => {
    const used = new Set();
    list.forEach((r, j) => {
      if (j === rowIndex) return;
      const id = String(r?.element_id || "").trim();
      if (id) used.add(id);
    });
    return used;
  };

  const out = list.map((row, i) => {
    const next = { ...row };
    let eid = String(next.element_id || "").trim();
    const isTargetRow =
      (linkIndex >= 0 && linkIndex === i) ||
      (linkId && String(next.id || "") === linkId);
    if (!eid && elementIds.length) {
      if (isTargetRow && (newIds.length || linkIndex >= 0 || linkId)) {
        if (newIds.length) {
          eid = newIds.find((id) => !assignedNew.has(id)) || newIds[0];
          if (eid) assignedNew.add(eid);
        } else {
          const used = idsUsedByOtherRows(i);
          eid = elementIds.find((id) => !used.has(id)) || "";
        }
      }
      if (!eid) {
        const slot = humanLabel(next.slot, next.element_name);
        if (slot) {
          for (const id of elementIds) {
            const el = byId.get(id);
            if (!el) continue;
            const name = humanLabel(el.name);
            if (!name) continue;
            if (name === slot || name.includes(slot) || slot.includes(name)) {
              eid = id;
              break;
            }
          }
        }
      }
    }
    if (eid && elementIds.includes(eid)) {
      const el = byId.get(eid);
      next.element_id = eid;
      const ename = humanLabel(el?.name, next.element_name, next.slot);
      if (ename) next.element_name = ename;
      else delete next.element_name;
      next.needs_supplement = false;
    }
    return next;
  });

  const usedIds = new Set(
    out.map((r) => String(r?.element_id || "").trim()).filter(Boolean)
  );
  const orphanIds = elementIds.filter((id) => !usedIds.has(id));
  if (!orphanIds.length) return out;

  const patched = out.map((row) => ({ ...row }));
  for (const eid of orphanIds) {
    const idx = patched.findIndex((row) => {
      if (String(row?.element_id || "").trim()) return false;
      if (row?.needs_supplement) return true;
      const slot = String(row?.slot || "").trim();
      const text = String(row?.text || "");
      return Boolean(slot) || textNeedsSupplement(text);
    });
    if (idx < 0) break;
    const row = patched[idx];
    const el = byId.get(eid);
    row.element_id = eid;
    const ename = humanLabel(el?.name, row.element_name, row.slot);
    if (ename) row.element_name = ename;
    else delete row.element_name;
    row.needs_supplement = false;
  }
  return patched;
}
