/** 写作策略。兼容旧 compose 值。 */

export const COMPOSE_SOURCE_ONLY = "source_only";
export const COMPOSE_TEMPLATE_FILL = "template_fill";
export const COMPOSE_HYBRID = "hybrid";
export const COMPOSE_AUTO_GENERATE = "auto_generate";
export const COMPOSE_FIXED = "fixed";

export const COMPOSE_OPTIONS = [
  {
    value: COMPOSE_SOURCE_ONLY,
    label: "资料驱动 (SOURCE_ONLY)：有资料才写，无则整章转人工",
  },
  {
    value: COMPOSE_TEMPLATE_FILL,
    label: "模板填槽 (TEMPLATE_FILL)：固定话术为主，缺槽留空",
  },
  {
    value: COMPOSE_HYBRID,
    label: "混合 (HYBRID)：部分相关据实写，主题无关才转人工",
  },
  {
    value: COMPOSE_AUTO_GENERATE,
    label: "自动生成 (AUTO_GENERATE)：不依赖资料，按要点自动成文",
  },
  {
    value: COMPOSE_FIXED,
    label: "固定内容输出 (FIXED)：按原文直接输出，不改写",
  },
];

export function normalizeCompose(value, extras = {}) {
  const raw = String(value || "").trim().toLowerCase().replace(/-/g, "_");
  const presence = String(extras.presence || "").trim().toLowerCase();
  const fixed = Boolean(extras.fixed_output);
  if (["source_only", "sourceonly", "资料驱动"].includes(raw)) {
    return COMPOSE_SOURCE_ONLY;
  }
  if (
    [
      "auto_generate",
      "autogenerate",
      "auto",
      "free_write",
      "knowledge",
      "自动生成",
      "不依赖资料",
      "不依赖资料自动生成",
    ].includes(raw)
  ) {
    return COMPOSE_AUTO_GENERATE;
  }
  if (
    [
      "fixed",
      "verbatim",
      "copy",
      "fixed_output",
      "照抄",
      "固定",
      "固定内容",
      "固定正文",
      "固定内容输出",
    ].includes(raw)
  ) {
    return COMPOSE_FIXED;
  }
  if (
    [
      "template_fill",
      "templatefill",
      "template",
      "boilerplate",
      "slots",
      "模板填槽",
      "套话",
      "填槽",
    ].includes(raw)
  ) {
    return COMPOSE_TEMPLATE_FILL;
  }
  if (["hybrid", "generate", "混合", "按要点现写"].includes(raw)) {
    if (presence === "if_materials" || presence === "if-materials") {
      return COMPOSE_SOURCE_ONLY;
    }
    return COMPOSE_HYBRID;
  }
  if (!raw && fixed) return COMPOSE_FIXED;
  if (presence === "if_materials" || presence === "if-materials") {
    return COMPOSE_SOURCE_ONLY;
  }
  return COMPOSE_HYBRID;
}

export function presenceForCompose(compose) {
  return normalizeCompose(compose) === COMPOSE_SOURCE_ONLY ? "if_materials" : "always";
}

export function isTemplateFill(compose, extras = {}) {
  return normalizeCompose(compose, extras) === COMPOSE_TEMPLATE_FILL;
}

export function isFixedCompose(compose, extras = {}) {
  return normalizeCompose(compose, extras) === COMPOSE_FIXED;
}

export function usesSlotReference(compose, extras = {}) {
  const mode = normalizeCompose(compose, extras);
  return mode !== COMPOSE_AUTO_GENERATE && mode !== COMPOSE_FIXED;
}

export function composeLabel(value, extras = {}) {
  const mode = normalizeCompose(value, extras);
  const hit = COMPOSE_OPTIONS.find((item) => item.value === mode);
  return hit ? hit.label : mode;
}
