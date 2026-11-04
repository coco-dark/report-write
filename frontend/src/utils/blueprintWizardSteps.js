/** 蓝本对话向导步序（与后端 wizard_state 一致；止于学习分析，无「完成」节点） */
export const WIZARD_STEP_ORDER = ["meta", "upload", "outline", "outline_confirm", "study"];

/** 对话气泡内可操作的向导步（与 backend chat_card_policy.WIZARD_STEPS_CHAT 一致） */
export const WIZARD_STEPS_CHAT = ["meta", "upload", "outline", "outline_confirm"];

const STEP_ALIASES = {
  upload_done: "outline",
  outline_done: "outline_confirm",
  study_done: "study",
  done: "study",
};

/**
 * 进入/恢复会话时清掉一次性自驱标记，避免刷新后自动重学/重提取。
 * （同页内由 live-step 新写入的 trigger 仍可驱动自动执行。）
 */
export function stripEphemeralWizardTriggers(wizard) {
  if (!wizard || typeof wizard !== "object") return wizard;
  const next = { ...wizard };
  delete next.trigger_chapter_study;
  delete next.chapter_study_keys;
  delete next.trigger_begin_study;
  delete next.study_stream_active;
  delete next.trigger_outline_extract;
  delete next.outline_extract_active;
  const pending = String(next.pending_wizard_command || "").trim();
  if (
    pending === "re_extract_outline" ||
    pending === "go_study" ||
    pending === "re_study_chapters"
  ) {
    delete next.pending_wizard_command;
  }
  return next;
}

/**
 * 历史向导卡落库快照：去掉进行中标记，避免旧蓝本卡被当前提取/学习状态「点亮」。
 */
export function freezeWizardCardPayload(payload) {
  if (!payload || typeof payload !== "object") return payload || {};
  const next = stripEphemeralWizardTriggers({ ...payload });
  delete next.trigger_outline_extract;
  delete next.outline_extract_active;
  delete next.trigger_chapter_study;
  delete next.chapter_study_keys;
  delete next.trigger_begin_study;
  delete next.study_stream_active;
  return next;
}

/** 取第一个「有内容」的向导对象；空 {} 视为缺失（JS 里 || 会误命中）。 */
export function pickNonEmptyWizard(...candidates) {
  for (const raw of candidates) {
    if (!raw || typeof raw !== "object") continue;
    if (!Object.keys(raw).length) continue;
    return raw;
  }
  return null;
}

/**
 * 仅当 live 与卡片属于同一蓝本（或都还在未建 id 的名称步）时才合并 live。
 */
export function liveWizardMatchesCard(cardPayload, liveWizard) {
  if (!liveWizard || typeof liveWizard !== "object") return false;
  const cardIds = [
    String(cardPayload?.blueprint_id || "").trim(),
    String(cardPayload?.working_task_id || "").trim(),
    String(cardPayload?.task_id || "").trim(),
  ].filter(Boolean);
  const liveIds = [
    String(liveWizard.blueprint_id || "").trim(),
    String(liveWizard.working_task_id || "").trim(),
    String(liveWizard.task_id || "").trim(),
  ].filter(Boolean);
  if (cardIds.length && liveIds.length) {
    return cardIds.some((id) => liveIds.includes(id));
  }
  if (!cardIds.length && !liveIds.length) return true;
  if (isFreshBlueprintWizardStart(cardPayload) && liveIds.length) return false;
  if (cardIds.length && !liveIds.length) return false;
  return false;
}

/** 过渡步（upload_done 等）映射为流水线 id */
export function normalizeWizardStep(step) {
  const s = String(step || "meta").trim();
  return STEP_ALIASES[s] || s;
}

export function wizardStepIndex(step) {
  const i = WIZARD_STEP_ORDER.indexOf(normalizeWizardStep(step));
  return i >= 0 ? i : -1;
}

/**
 * 服务端刚开的「创建蓝本」向导（无 id 的 meta/upload）：不得与旧蓝本 live 合并。
 */
export function isFreshBlueprintWizardStart(source) {
  if (!source || typeof source !== "object") return false;
  if (String(source.blueprint_id || "").trim()) return false;
  const step = normalizeWizardStep(source.step || "meta");
  if (wizardStepIndex(step) > wizardStepIndex("upload")) return false;
  const mode = String(source.mode || "").trim();
  return mode === "create" || step === "meta";
}

/**
 * 合并历史卡片 payload 与会话级 live 状态；live 不得把步数倒回卡片已到达的进度。
 * 空 blueprint_id 不得冲掉已有 id；已有 id 时严禁停在 / 回退到 meta；目录已出后不得因 leftover trigger 倒回 outline。
 * 新建向导（mode=create 且无 id）不得继承旧蓝本的 id / 目录。
 */
export function mergeWizardPayload(base = {}, live = {}) {
  const baseBid = String(base.blueprint_id || "").trim();
  const liveBid = String(live.blueprint_id || "").trim();
  const liveStepRaw = live?.step ? normalizeWizardStep(live.step) : "";
  // 新开创建向导：整段替换为 live，避免把旧蓝本目录/标题串到新任务上
  if (
    !liveBid &&
    baseBid &&
    isFreshBlueprintWizardStart({ ...live, step: liveStepRaw || live?.step || "meta" })
  ) {
    const freshChapters = Array.isArray(live.chapters) ? live.chapters : [];
    return {
      ...live,
      blueprint_id: "",
      step: liveStepRaw || "meta",
      chapters: freshChapters,
      chapter_count:
        Number(live.chapter_count || 0) || freshChapters.length || 0,
    };
  }
  const bid = liveBid || baseBid;
  const baseStep = normalizeWizardStep(base.step);
  const liveStep = liveStepRaw;
  const bi = wizardStepIndex(baseStep);
  const li = wizardStepIndex(liveStep);
  // live 显式带 pending（含空串）时以 live 为准，避免回退后被 base 残留钉住
  const liveHasPending = Object.prototype.hasOwnProperty.call(
    live || {},
    "pending_wizard_command"
  );
  const pendingCmd = String(
    liveHasPending
      ? live.pending_wizard_command || ""
      : live.pending_wizard_command || base.pending_wizard_command || ""
  ).trim();
  // 用户主动回退：允许 live 把步数倒回 upload / outline
  const reUpload = pendingCmd === "re_upload_sample";
  const confirmIdx = wizardStepIndex("outline_confirm");
  // 重新提取只认 pending（提取开始后 trigger 会被置 false；完成后 pending 清空）。
  // 禁止把 trigger 单独当成 reExtract，否则 softSync 会点燃死循环提取。
  const reExtract = pendingCmd === "re_extract_outline";
  const extractRunning = live.outline_extract_active === true;
  const livePastExtract = !reUpload && !reExtract && !extractRunning && li >= confirmIdx;
  let step = baseStep;
  if (reUpload) {
    step = "upload";
  } else if (reExtract || extractRunning) {
    step = "outline";
  } else if (li >= 0 && (bi < 0 || li >= bi)) {
    step = liveStep;
  } else if (bi >= 0) {
    step = baseStep;
  }
  // 蓝本已建好（有 id）后名称步已完成，禁止 UI / 同步把进度打回「填写名称」
  if (bid && wizardStepIndex(step) <= wizardStepIndex("meta")) {
    step = "upload";
  }

  // live 显式给了 chapters（含空数组）时以 live 为准，便于重新提取清空预览
  const chapters = Object.prototype.hasOwnProperty.call(live || {}, "chapters")
    ? Array.isArray(live.chapters)
      ? live.chapters
      : []
    : Array.isArray(base.chapters)
      ? base.chapters
      : [];
  const maxIdx = Math.max(
    bi >= 0 ? bi : -1,
    li >= 0 ? li : -1,
    wizardStepIndex(step)
  );
  const liveClearedExtract = live.trigger_outline_extract === false && !reExtract;
  // 客户端显式关掉提取触发时，不得被 base 上的 leftover 再次打开
  let pendingExtract = false;
  if (reUpload || liveClearedExtract) {
    pendingExtract = false;
  } else if (live.trigger_outline_extract === true) {
    pendingExtract = true;
  } else if (
    pendingCmd === "re_extract_outline" &&
    live.outline_extract_active !== true &&
    live.trigger_outline_extract !== false
  ) {
    // 已点重新提取、流尚未开始：保持 trigger，驱动 auto-extract
    pendingExtract = true;
  } else {
    pendingExtract = Boolean(base.trigger_outline_extract || live.trigger_outline_extract);
  }
  if (pendingExtract && !reExtract && !extractRunning && (maxIdx >= confirmIdx || chapters.length > 0)) {
    pendingExtract = false;
  }
  // 已到确认目录及之后，或已有目录章节：严禁因 leftover trigger 倒回「提取中」
  // （主动 re_upload / re_extract / 提取进行中除外）
  if (
    !reUpload &&
    !reExtract &&
    !extractRunning &&
    (maxIdx >= confirmIdx || chapters.length > 0) &&
    wizardStepIndex(step) < confirmIdx
  ) {
    step = "outline_confirm";
  }

  const mode = String(live.mode || base.mode || "").trim();
  // 选用已有蓝本：不得因提取残留把步数钉回 outline；也不允许回退到上传
  if (mode === "selected" && pendingExtract) {
    pendingExtract = false;
  }
  if (mode === "selected" && reUpload) {
    step = "outline_confirm";
  }
  let nextStep = reUpload
    ? "upload"
    : pendingExtract || reExtract || extractRunning
      ? "outline"
      : step;
  if (mode === "selected" && ["meta", "upload", "outline"].includes(nextStep)) {
    nextStep = "outline_confirm";
  }
  const merged = {
    ...base,
    ...live,
    ...(bid ? { blueprint_id: bid } : {}),
    ...(mode ? { mode } : {}),
    step: nextStep,
  };
  // 仅「待开跑」时保留 trigger；提取进行中/完成后不得反复点燃
  if (pendingExtract && !extractRunning) {
    merged.trigger_outline_extract = true;
  } else {
    delete merged.trigger_outline_extract;
  }
  // live 显式清空 pending 时丢掉；进行中则保留 pending
  if (reExtract) {
    merged.pending_wizard_command = "re_extract_outline";
  } else if (liveHasPending && !pendingCmd) {
    delete merged.pending_wizard_command;
  }
  // 目录已出 / 已到确认步：清掉「提取中」残留，避免切回会话又自动重跑提取
  if (!pendingExtract && !extractRunning && (maxIdx >= confirmIdx || chapters.length > 0)) {
    delete merged.outline_extract_active;
  }
  if (Object.prototype.hasOwnProperty.call(live || {}, "chapters")) {
    merged.chapters = chapters;
    if (Object.prototype.hasOwnProperty.call(live || {}, "chapter_count")) {
      merged.chapter_count = Number(live.chapter_count || 0) || chapters.length || 0;
    } else if (!chapters.length) {
      merged.chapter_count = 0;
    }
  }
  // 一次性自驱标记只认 live：禁止从历史卡 base 复活（刷新/重进会把旧 trigger 又点着）
  if (live.trigger_begin_study === true) {
    merged.trigger_begin_study = true;
  } else {
    delete merged.trigger_begin_study;
  }
  const liveStudyKeys = Array.isArray(live.trigger_chapter_study)
    ? live.trigger_chapter_study.map((k) => String(k || "").trim()).filter(Boolean)
    : [];
  const liveChapterKeys = Array.isArray(live.chapter_study_keys)
    ? live.chapter_study_keys.map((k) => String(k || "").trim()).filter(Boolean)
    : [];
  if (liveStudyKeys.length) {
    merged.trigger_chapter_study = liveStudyKeys;
  } else {
    delete merged.trigger_chapter_study;
  }
  if (liveChapterKeys.length) {
    merged.chapter_study_keys = liveChapterKeys;
  } else {
    delete merged.chapter_study_keys;
  }
  if (live.study_stream_active === true) {
    merged.study_stream_active = true;
  } else {
    delete merged.study_stream_active;
  }
  if (live.outline_extract_active === true || extractRunning) {
    merged.outline_extract_active = true;
  } else if (!pendingExtract) {
    delete merged.outline_extract_active;
  }
  if (chapters.length) {
    const total =
      Number(live.chapter_count || base.chapter_count || merged.chapter_count || 0) ||
      chapters.length;
    merged.chapter_count = total;
    merged.chapters = chapters.slice(0, 8);
  }
  return merged;
}

/**
 * 是否在会话里展示 blueprint_wizard（名称/上传/提取/确认；分析由文档窗承接）。
 * ctx: { isFreeformWritingSession, isBlueprintWizardStale, wizardEffectiveStep, liveWizardForCard }
 */
export function shouldShowBlueprintWizardInChat(card, msgIndex, cardIndex, ctx) {
  const snapStep = normalizeWizardStep(card?.payload?.step);
  const earlyCreate = WIZARD_STEPS_CHAT.includes(snapStep);
  const isFreeform = ctx?.isFreeformWritingSession?.() ?? false;
  if (isChatOperableSampleWizardPayload(card?.payload)) return true;
  if (isFreeform && !earlyCreate) return false;
  const stale = ctx?.isBlueprintWizardStale?.(msgIndex, cardIndex) ?? false;
  if (earlyCreate) {
    const bid = String(card?.payload?.blueprint_id || card?.payload?.working_task_id || "").trim();
    if (stale && snapStep === "meta" && !bid) return false;
    return true;
  }
  if (stale) return false;
  const step = ctx?.wizardEffectiveStep?.(card, { allowLive: true }) ?? snapStep;
  if (WIZARD_STEPS_CHAT.includes(step)) return true;
  const live =
    ctx?.liveWizardForCard?.(card, msgIndex, cardIndex) || card?.payload || {};
  if (Boolean(live.trigger_outline_extract) || Boolean(live.outline_extract_active)) return true;
  if (Boolean(live.study_stream_active) || Boolean(live.trigger_begin_study)) return true;
  const studyKeys = live.trigger_chapter_study || live.chapter_study_keys;
  return Array.isArray(studyKeys) && studyKeys.length > 0;
}

/** 对话内应保留的「依据样本/模版」早期向导卡（勿当成纯无蓝本吞掉） */
export function isChatOperableSampleWizardPayload(payload) {
  const p = payload && typeof payload === "object" ? payload : {};
  const step = normalizeWizardStep(p.step);
  if (!WIZARD_STEPS_CHAT.includes(step)) return false;
  const mode = String(p.mode || "").trim();
  if (mode === "selected") return false;
  if (mode === "create" || mode === "freeform_sample") return true;
  const bid = String(p.blueprint_id || p.working_task_id || "").trim();
  // ui-state 常清掉 mode；w* + 早期步仍视为样本向导
  return Boolean(bid && /^w/i.test(bid));
}

const WIZARD_CARD_TITLES = {
  meta: "依据模版编写 · 名称",
  upload: "依据样本 · 上传样板",
  outline: "依据样本 · 提取目录",
  outline_confirm: "依据样本 · 确认目录",
};

/**
 * done/softSync 无 cards 时，从 runtime 合成对话内 blueprint_wizard 卡（仅 meta/upload/outline/outline_confirm）。
 */
export function synthesizeChatWizardCardFromRuntime(runtime, replyHint = "") {
  const wf = runtime?.workflows?.blueprint_wizard || runtime?.blueprint_wizard || {};
  const wiz = pickNonEmptyWizard(wf) || (typeof wf === "object" && Object.keys(wf).length ? wf : null);
  if (!wiz) return null;
  let step = normalizeWizardStep(wiz.step || "meta");
  if (!WIZARD_STEPS_CHAT.includes(step)) return null;
  const mode = String(wiz.mode || "").trim();
  const planGoal = String(runtime?.session_goal?.goal || runtime?.plan?.goal || "").trim();
  const hint = String(replyHint || "").trim();
  const templateReply = /依据模版编写|依据样本/.test(hint);
  if (
    !templateReply &&
    mode !== "create" &&
    mode !== "freeform_sample" &&
    planGoal !== "template_wizard"
  ) {
    return null;
  }
  const bid = String(wiz.blueprint_id || wiz.working_task_id || "").trim();
  const payload = {
    ...wiz,
    step,
    mode: mode || "create",
    blueprint_id: bid,
  };
  return {
    type: "blueprint_wizard",
    title: WIZARD_CARD_TITLES[step] || "依据模版编写",
    payload: freezeWizardCardPayload(payload),
  };
}

export function ensureChatWizardCardsFromRuntime(existingCards, runtime, replyHint = "") {
  const cards = Array.isArray(existingCards) ? [...existingCards] : [];
  if (cards.some((c) => c?.type === "blueprint_wizard")) return cards;
  const synth = synthesizeChatWizardCardFromRuntime(runtime, replyHint);
  return synth ? [...cards, synth] : cards;
}
