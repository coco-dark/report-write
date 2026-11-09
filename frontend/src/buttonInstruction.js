/** 按钮点击 → 对话里的一句指令（与后端 _user_action_label 对齐）。 */

function usableChapterPart(value) {
  const t = String(value || "").trim();
  if (!t || t === "本章" || t === "当前章节") return "";
  return t;
}

export function chapterBracket(payload = {}, fallback = "") {
  const seq = usableChapterPart(payload.sequence);
  const title = usableChapterPart(payload.chapter_title) || usableChapterPart(payload.title);
  let inner = [seq, title].filter(Boolean).join(" ").trim();
  if (!inner) inner = usableChapterPart(fallback);
  return inner ? `[${inner}]` : "";
}

export function actionInstruction(action, payload = {}, chapterLabel = "") {
  const key = String(action || "").trim();
  const tag = chapterBracket(payload, chapterLabel);
  const chap = tag || (chapterLabel ? `[${chapterLabel}]` : "");
  const slot = String(payload.pointSlot || payload.slot || payload.slot_label || "").trim();

  if (key === "retry_chapter") {
    const mode = String(payload._write_mode || payload.write_mode || "").trim();
    if (mode === "rewrite") return chap ? `重写章节${chap}` : "重写本章";
    return chap ? `开始写作章节${chap}` : "开始写作本章";
  }
  if (key === "retry_search_chapter") {
    return chap ? `重试检索章节${chap}` : "重试本章检索";
  }
  if (key === "confirm_chapter") {
    return chap ? `确认章节${chap}` : "确认本章";
  }
  if (key === "configure_chapter") {
    return chap ? `保存章节配置${chap}` : "保存章节配置";
  }
  if (key === "get_chapter_info") {
    return chap ? `查看章节信息${chap}` : "查看章节信息";
  }
  if (key === "generate_chapter_describes") {
    return chap ? `生成章节说明${chap}` : "生成章节说明";
  }
  if (key === "generate_chapter_boilerplate") {
    return chap ? `生成套话正文${chap}` : "生成套话正文";
  }
  if (key === "associate_elements") {
    const scope = String(payload.associateScope || "").trim();
    if (scope === "point" || slot || payload.pointItemId != null || payload.pointIndex != null) {
      if (slot) return chap ? `关联要点要素${chap}「${slot}」` : `关联要点要素「${slot}」`;
      return chap ? `关联要点要素${chap}` : "关联要点要素";
    }
    return chap ? `关联章节要素${chap}` : "关联章节要素";
  }
  if (key === "add_chapter_element") {
    const name = String(payload.name || "").trim();
    if (name && chap) return `添加要素「${name}」到章节${chap}`;
    return chap ? `添加要素到章节${chap}` : "添加章节要素";
  }
  if (key === "start_writing") {
    const keys = payload.chapter_keys || payload.write_chapter_keys || payload.chapterKeys || [];
    const n = Array.isArray(keys) ? keys.length : 0;
    const pace = String(payload.writing_pace || "").trim().toLowerCase();
    if (chap && n <= 1) return `开始写作章节${chap}`;
    if (n > 1) return `开始写作（${n}章）`;
    if (["batch", "all", "full"].includes(pace)) return "开始写作（全部写完再确认）";
    if (["step", "chapter", "one"].includes(pace)) return "开始写作（写一章确认一章）";
    return "开始写作";
  }
  if (key === "write_analyzed_only") {
    const keys = payload.chapter_keys || payload.write_chapter_keys || payload.chapterKeys || [];
    const n = Array.isArray(keys) ? keys.length : 0;
    if (chap && n <= 1) return `先写已分析的章节${chap}`;
    if (n > 1) return `先写已分析的章节（${n}章）`;
    return "先写已分析的章节";
  }
  if (key === "select_write_chapters") {
    const keys = payload.chapter_keys || payload.write_chapter_keys || [];
    const n = Array.isArray(keys) ? keys.length : 0;
    return n ? `确认要写的章节（${n}章）` : "确认要写的章节";
  }
  if (key === "pause_writing") return "暂停写作";
  if (key === "confirm_all_chapters") {
    const rows = payload.chapters;
    const n = Array.isArray(rows) ? rows.length : 0;
    return n ? `确认全部章节（${n}章）` : "确认全部章节";
  }
  if (key === "generate_freeform_points") {
    const keys = payload.chapter_keys || payload.chapterKeys || [];
    const n = Array.isArray(keys) ? keys.length : 0;
    if (chap && n <= 1) return `生成章节${chap}的写作要点`;
    if (n > 1) return `生成写作要点（${n}章）`;
    return "生成写作要点";
  }
  if (key === "skip_freeform_points") return "跳过写作要点，直接开写";
  if (key === "check_writing_supplements") {
    return chap ? `重新检查写作要点待补充项${chap}` : "重新检查写作要点待补充项";
  }
  if (key === "skip_writing_supplement_check") {
    const keys = payload.chapter_keys || [];
    const n = Array.isArray(keys) ? keys.length : 0;
    if (chap && n <= 1) return `先这样开写章节${chap}`;
    if (n > 1) return `先这样开写（${n}章）`;
    return "先这样开写";
  }
  if (key === "dismiss_writing_supplement") {
    if (slot && chap) return `忽略待补充项${chap}要点「${slot}」`;
    if (slot) return `忽略待补充项「${slot}」`;
    return chap ? `忽略待补充项${chap}` : "忽略待补充项";
  }
  if (key === "submit_outline") {
    if (payload.regenerate) return "重新生成章节目录";
    if (payload.reopen_edit) return "重新编辑目录";
    if (payload.confirm) return "确认大纲";
    return "确认大纲";
  }
  if (key === "select_blueprint") {
    const title = String(payload.title || payload.blueprint_title || "").trim();
    return title ? `选用蓝本「${title}」` : "选用蓝本";
  }
  if (key === "skip_blueprint") return "跳过蓝本，自动生成大纲";
  if (key === "begin_blueprint_wizard") return "依据模版编写";
  if (key === "list_blueprints") return "查看可用蓝本";
  if (key === "open_blueprint_library") return "查看蓝本库";
  if (key === "open_element_library") return "查看要素库";
  if (key === "save_freeform_as_blueprint") return "收录到蓝本库";
  if (key === "sync_document_to_blueprint_library") return "同步到蓝本库";
  if (key === "usage_guide") return "查看使用说明";
  if (key === "materials_pack_skip") return "不用资料包";
  if (key === "materials_pack_use") {
    const pack = String(payload.pack_name || payload.name || "").trim();
    return pack ? `使用资料包「${pack}」` : "换绑资料包";
  }
  if (key === "materials_pack_create") {
    const pack = String(payload.pack_name || payload.name || "").trim();
    return pack ? `新建资料包「${pack}」` : "新建资料包";
  }
  if (key === "materials_pack_list") return "查看资料包";
  if (key === "materials_pack_view") return "查看资料包配置";
  if (key === "materials_pack_unbind") return "解绑资料包";
  if (key === "materials_pack_confirm_library") {
    const pack = String(payload.pack_name || payload.name || "").trim();
    return pack ? `资料包入库「${pack}」` : "确认资料包入库";
  }
  if (key === "materials_pack_add_slot") return "添加资料包槽位";
  if (key === "usage_guide") return "查看使用说明";
  if (key === "collect_to_element_library") return "收入要素库";
  if (key === "open_knowledge") return "查看资料库";
  if (key === "resolve_intent_clarify") {
    const label = String(payload.option_label || payload.option_id || "").trim();
    return label || "确认选项";
  }
  return "";
}
