<template>
  <div class="chat-page">
    <div
      v-if="globalLoading"
      class="global-loading-mask"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div class="global-loading-box">
        <span class="global-loading-spinner" aria-hidden="true" />
        <span class="global-loading-text">{{ globalLoadingText }}</span>
      </div>
    </div>
    <aside class="history-rail" aria-label="对话历史">
      <button type="button" class="new-chat" :disabled="globalLoading" @click="startNewChat">
        新对话
      </button>
      <div class="history-head">历史对话</div>
      <div v-if="historyLoading" class="history-empty">加载中…</div>
      <div v-else-if="!historyItems.length" class="history-empty">暂无历史</div>
      <div
        v-for="item in historyItems"
        :key="item.id"
        class="history-item"
        :class="{ active: item.id === sessionId, loading: openingSessionId === item.id }"
      >
        <button
          type="button"
          class="history-item-main"
          :disabled="sending || globalLoading"
          @click="openHistory(item.id)"
        >
          <strong>{{ item.title || "新对话" }}</strong>
          <span>{{
            openingSessionId === item.id
              ? "加载中…"
              : item.preview || item.updated_at || ""
          }}</span>
        </button>
        <button
          type="button"
          class="history-item-del"
          title="删除对话"
          :disabled="sending || globalLoading"
          @click.stop="deleteHistory(item)"
        >
          ×
        </button>
      </div>
    </aside>

    <div class="chat-main">
    <div ref="chatStageEl" class="chat-stage">
      <div v-if="messages.length" class="thread">
        <ChatMessageBubble
          v-for="(item, index) in messages"
          :key="index"
          class="bubble"
          :role="item.role"
          :header="messageDocTag(item)"
          :html="toDisplayHtml(friendlyMessageText(item))"
          :streaming="!!item.streaming"
          :process-steps="bubbleProcessSteps(item)"
        >
          <template v-if="item.cards?.length" #footer>
            <div class="cards">
              <template v-for="(card, ci) in item.cards" :key="ci">
                <div v-if="card.type === 'blueprint_list'" class="card bp-list" :class="{ used: isCardUsed(index, ci) }">
                  <div class="card-title">{{ card.title || "请选择蓝本" }}</div>
                  <p class="card-hint">{{ card.payload?.hint || "点选下方蓝本即可继续" }}</p>

                  <div v-if="card.payload?.layout === 'table'" class="bp-table-wrap">
                    <table class="bp-table">
                      <thead>
                        <tr>
                          <th>名称</th>
                          <th>用途说明</th>
                          <th>分类</th>
                          <th>章节</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="bp in card.payload?.items || []"
                          :key="bp.id"
                          :class="{ picked: pickedBlueprintId === bp.id }"
                        >
                          <td>
                            <strong>{{ bp.title || bp.id }}</strong>
                          </td>
                          <td class="desc">{{ bp.description || "—" }}</td>
                          <td>{{ bp.category || "—" }}</td>
                          <td>{{ bp.chapter_count || 0 }}</td>
                          <td>
                            <a-button
 class="chat-action-btn chat-action-btn--compact"
 type="primary"
 :disabled="sending || isCardUsed(index, ci)"
 @click="pickBlueprint(bp, index, ci)"
 shape="round">
                              选用
                            </a-button>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>

                  <template v-else>
                    <button
                      v-for="bp in card.payload?.items || []"
                      :key="bp.id"
                      type="button"
                      class="bp-item"
                      :class="{ active: selectedBlueprint === bp.id, picked: pickedBlueprintId === bp.id }"
                      :disabled="sending || isCardUsed(index, ci)"
                      @click="pickBlueprint(bp, index, ci)"
                    >
                      <div class="bp-main">
                        <strong>{{ bp.title || bp.id }}</strong>
                        <em v-if="bp.description" class="bp-desc">{{ bp.description }}</em>
                        <span>
                          {{ bp.category || "未分类" }} · {{ bp.chapter_count || 0 }} 章
                          {{ bp.outline_confirmed ? " · 目录已确认" : "" }}
                        </span>
                      </div>
                      <span class="bp-cta">选用</span>
                    </button>
                  </template>
                </div>

                <!-- 渲染模版 / Word 样式引导卡不再展示；蓝本等其它 resource_choice 仍保留 -->
                <template
                  v-else-if="
                    card.type === 'resource_choice' &&
                    ['render', 'word_style', 'render_template'].includes(String(card.payload?.resource || ''))
                  "
                />
                <div
                  v-else-if="card.type === 'resource_choice'"
                  class="card"
                  :class="{ used: isCardUsed(index, ci) }"
                >
                  <div class="card-title">{{ card.title }}</div>
                  <p class="card-hint">{{ card.payload?.message }}</p>
                  <div class="confirm-actions gate-actions">
                    <a-button
 class="chat-action-btn"
 type="primary"
 :disabled="sending || isCardUsed(index, ci)"
 @click="pickResourceChoice(card, true, index, ci)"
 shape="round">
                      {{ card.payload?.yes_label || "是" }}
                    </a-button>
                    <a-button
 class="chat-action-btn"
 :disabled="sending || isCardUsed(index, ci)"
 @click="pickResourceChoice(card, false, index, ci)"
 shape="round">
                      {{ card.payload?.no_label || "否" }}
                    </a-button>
                  </div>
                </div>

                <template v-else-if="card.type === 'knowledge_empty'" />

                <!-- 填名称/上传/提取/确认目录：会话内可操作；分析进度改由文档小窗承接 -->
                <div
                  v-else-if="
                    card.type === 'blueprint_wizard' &&
                    wizardCardBelongsToFocus(card, item) &&
                    shouldShowWizardCardInChat(card, index, ci)
                  "
                  class="card blueprint-wizard-card"
                  :class="{ used: isBlueprintWizardStale(index, ci) }"
                >
                  <div class="card-title">{{ wizardCardHeadline(card, index, ci) }}</div>
                  <ChatBlueprintWizardCard
                    :key="`bpw-${index}-${ci}-${card.payload?.blueprint_id || ''}-${normalizeWizardStep(card.payload?.step)}-${isBlueprintWizardStale(index, ci) ? 'H' : 'L'}`"
                    :card="wizardCardForRender(card, index, ci)"
                    :live-wizard="liveWizardForCard(card, index, ci)"
                    :disabled="isBlueprintWizardStale(index, ci)"
                    :process-log="wizardProcessLogForCard(card, index, ci)"
                    :persist-action="(p) => onBlueprintWizard(p, index, ci)"
                    @wizard-action="(p) => onBlueprintWizard(p, index, ci)"
                    @live-step="onWizardLiveStep"
                    @process="onBlueprintWizardProcess"
                    @study-synced="onWizardStudySynced"
                    @write-analyzed="onWizardWriteAnalyzed"
                    @study-locate="onWizardStudyLocate"
                  />
                </div>
                <!-- 其它文档的向导卡：多文档时勿按当前线路改写/露出，避免串台 -->
                <template
                  v-else-if="
                    card.type === 'blueprint_wizard' &&
                    !wizardCardBelongsToFocus(card, item)
                  "
                />
                <div
                  v-else-if="
                    card.type === 'blueprint_wizard' &&
                    isFreeformWritingSession() &&
                    wizardCardBelongsToFocus(card, item) &&
                    !hasActiveEntryOnboardingClarify(item.cards) &&
                    !isChatOperableSampleWizardPayload(card.payload) &&
                    !['create', 'freeform_sample'].includes(
                      String(card.payload?.mode || '').trim()
                    ) &&
                    !['meta', 'upload', 'outline', 'outline_confirm'].includes(
                      normalizeWizardStep(card.payload?.step)
                    )
                  "
                  class="card blueprint-wizard-card used"
                >
                  <div class="card-title">已改为无蓝本写作</div>
                  <p class="card-hint">本会话不再走「上传样板 / 分析要点」。请用下方大纲确认与写作要点卡片继续。</p>
                </div>
                <!-- 分析/完成：不渲染，进度与操作在文档小窗 -->
                <template v-else-if="card.type === 'blueprint_wizard'" />

                <div v-else-if="card.type === 'template_wizard'" class="card">
                  <div class="card-title">{{ card.title }}</div>
                  <ChatTemplateWizardCard
                    :card="card"
                    :robot-id="robotId"
                    :live-wizard="liveTemplateWizard"
                    :disabled="sending || isCardUsed(index, ci)"
                    @saved="(p) => onTemplateWizard(p, index, ci)"
                    @live-step="onTemplateLiveStep"
                    @export-word="() => doExportWord()"
                  />
                </div>

                <div
                  v-else-if="card.type === 'intent_clarify'"
                  class="card intent-clarify-card"
                  :class="{ used: isCardUsed(index, ci) }"
                >
                  <p class="hint">{{ card.payload?.question || card.title || "请帮我确认一下" }}</p>
                  <div class="blueprint-path-actions">
                    <a-button
 v-for="opt in card.payload?.options || []"
 :key="opt.id || opt.label"
 class="chat-action-btn"
 type="primary"
 :disabled="sending || isCardUsed(index, ci)"
 @click="resolveIntentClarify(card, opt, index, ci)"
 shape="round">
                      {{ opt.label }}
                    </a-button>
                  </div>
                </div>

                <div
                  v-else-if="card.type === 'study_source_paste'"
                  class="card study-source-paste-card"
                  :class="{ used: isCardUsed(index, ci) }"
                >
                  <div class="card-title">{{ card.title || "根据原文内容分析" }}</div>
                  <p class="hint">
                    请把「{{ card.payload?.chapter_label || "本章" }}」的原文粘贴到下方，再开始分析。
                  </p>
                  <ChapterHtmlEditor
                    class="study-source-rich-editor"
                    :model-value="studySourcePasteDrafts[`${index}-${ci}`] || ''"
                    placeholder="在此粘贴本章原文（从 Word / 网页复制即可）…"
                    :min-height="140"
                    :max-height="280"
                    :readonly="isCardUsed(index, ci)"
                    @update:model-value="(v) => onStudySourcePasteHtml(`${index}-${ci}`, v)"
                  />
                  <div class="blueprint-path-actions">
                    <a-button
 class="chat-action-btn"
 type="primary"
 :disabled="sending || isCardUsed(index, ci) || !studySourcePasteText(`${index}-${ci}`)"
 @click="submitStudySourcePaste(card, index, ci)"
 shape="round">
                      开始分析
                    </a-button>
                  </div>
                </div>

                <div
                  v-else-if="card.type === 'skip_blueprint'"
                  class="card skip-blueprint-card"
                  :class="{ used: isCardUsed(index, ci) }"
                >
                  <div class="blueprint-path-actions">
                    <a-button
 class="chat-action-btn"
 type="primary"
 :disabled="sending || isCardUsed(index, ci)"
 @click="pickCreateBlueprint(index, ci)"
 shape="round">
                      依据模版编写
                    </a-button>
                    <a-button
 class="chat-action-btn"
 :disabled="sending || isCardUsed(index, ci)"
 @click="pickSkip(index, ci)"
 shape="round">
                      无蓝本 · 自动大纲
                    </a-button>
                  </div>
                </div>

                <!-- handoff 改由文档小窗承接，见下方统一隐藏 -->

                <div v-else-if="card.type === 'nav_action'" class="card nav-action-card">
                  <div class="card-title">{{ card.title || navActionLabel(card) }}</div>
                  <p v-if="card.payload?.hint" class="gate-hint">{{ card.payload.hint }}</p>
                  <template v-if="navBrowseSections(card).length">
                    <div
                      v-for="sec in navBrowseSections(card)"
                      :key="sec.key || sec.title"
                      class="nav-browse-section"
                    >
                      <div class="nav-browse-section-title">
                        {{ sec.title }}
                        <span class="nav-browse-section-count">{{ (sec.items || []).length }}</span>
                      </div>
                      <p v-if="!(sec.items || []).length" class="gate-hint nav-browse-empty">
                        {{ sec.key === "task" ? "本任务没有未入库要素" : "暂无" }}
                      </p>
                      <ul v-else class="nav-browse-list">
                        <li
                          v-for="row in (sec.items || []).slice(0, 16)"
                          :key="`${sec.key}-${row.id || row.name}`"
                        >
                          <span class="nav-browse-kind">{{ navBrowseKindLabel(row) }}</span>
                          <span class="nav-browse-name">{{ row.name || row.id }}</span>
                          <span v-if="row.category" class="nav-browse-meta">{{ row.category }}</span>
                        </li>
                      </ul>
                    </div>
                  </template>
                  <ul
                    v-else-if="Array.isArray(card.payload?.items) && card.payload.items.length"
                    class="nav-browse-list"
                  >
                    <li v-for="row in card.payload.items.slice(0, 24)" :key="row.id || row.name">
                      <span class="nav-browse-kind">{{ navBrowseKindLabel(row) }}</span>
                      <span class="nav-browse-name">{{ row.name || row.id }}</span>
                      <span v-if="row.category" class="nav-browse-meta">{{ row.category }}</span>
                      <span v-else-if="row.source === 'task'" class="nav-browse-meta">本任务</span>
                      <span v-else-if="row.source === 'library'" class="nav-browse-meta">个人库</span>
                    </li>
                  </ul>
                  <p
                    v-else-if="card.payload?.empty_hint"
                    class="gate-hint nav-browse-empty"
                  >
                    {{ card.payload.empty_hint }}
                  </p>
                  <p
                    v-if="Number(card.payload?.total || 0) > 24"
                    class="gate-hint"
                  >
                    仅展示前 24 条，共 {{ card.payload.total }} 条；打开完整页可查看全部。
                  </p>
                  <a-button class="chat-action-btn" type="primary" @click="goNavAction(card)" shape="round">
                    {{
                      card.payload?.target === "materials" || card.payload?.target === "knowledge"
                        ? "打开资料库"
                        : card.payload?.target === "blueprints" || card.payload?.target === "blueprint"
                          ? "打开蓝本库"
                          : "打开要素库"
                    }}
                  </a-button>
                </div>

                <div
                  v-else-if="card.type === 'associate_elements'"
                  class="card"
                  :class="{ used: isCardUsed(index, ci) }"
                >
                  <div class="card-title">{{ card.title || "关联要素" }}</div>
                  <p v-if="card.payload?.chapter_label" class="gate-hint">
                    章节：{{ card.payload.chapter_label }}
                  </p>
                  <p v-if="card.payload?.auto_open" class="gate-hint">
                    {{ isCardUsed(index, ci) ? "已打开关联面板" : "正在打开关联面板…" }}
                  </p>
                  <a-button
 class="chat-action-btn"
 :type="card.payload?.auto_open ? 'default' : 'primary'"
 @click="onAssociateElementsCard(card, index, ci)"
 shape="round">
                    {{ card.payload?.auto_open ? "再次打开" : "关联要素" }}
                  </a-button>
                </div>

                <template v-else-if="card.type === 'associate_element_knowledge'" />

                <!-- 章节目录/选章/handoff 由文档小窗承接；旧卡不渲染 -->
                <template
                  v-else-if="
                    card.type === 'chapter_picker' ||
                    card.type === 'analysis_gate' ||
                    card.type === 'handoff'
                  "
                />

                <div
                  v-else-if="card.type === 'doc_window_hint'"
                  class="card"
                  :class="{ used: isCardUsed(index, ci) }"
                >
                  <div class="card-title">{{ card.title || "在文档窗口继续" }}</div>
                  <p v-if="card.payload?.hint" class="gate-hint">{{ card.payload.hint }}</p>
                  <a-button
 class="chat-action-btn"
 type="primary"
 @click="onDocWindowHintCard(card, index, ci)"
 shape="round">
                    打开文档窗口
                  </a-button>
                </div>

                <div v-else-if="card.type === 'write_analyzed_only'" class="card">
                  <a-button
 class="chat-action-btn"
 type="primary"
 :loading="sending"
 :disabled="isCardUsed(index, ci)"
 @click="confirmWriteAnalyzedOnly(card, index, ci)"
 shape="round">
                    {{ card.title || "选择已分析章节" }}
                  </a-button>
                </div>

                <div v-else-if="card.type === 'freeform_points_help'" class="card freeform-points-help-card">
                  <div class="card-title">{{ card.title || "需要协助准备写作要点吗？" }}</div>
                  <p class="gate-hint">
                    {{ card.payload?.hint || "可以让我按目录自动起草各章写法要点，也可以自己补充或先跳过。" }}
                  </p>
                  <div class="confirm-actions gate-actions">
                    <a-button
 class="chat-action-btn"
 type="primary"
 :loading="sending"
 :disabled="isCardUsed(index, ci)"
 @click="
 sendAction('generate_freeform_points', card.payload || {})
 "
 shape="round">
                      帮我生成写作要点
                    </a-button>
                    <a-button
 class="chat-action-btn"
 :loading="sending"
 :disabled="isCardUsed(index, ci)"
 @click="
 sendAction('skip_freeform_points', card.payload || {})
 "
 shape="round">
                      先跳过，直接开写
                    </a-button>
                  </div>
                  <p class="gate-hint soft">
                    熟悉的话也可以在输入框粘贴各章写法说明，我会记入对应章节。
                  </p>
                </div>

                <div
                  v-else-if="card.type === 'save_blueprint_offer'"
                  class="card save-bp-offer-card save-bp-offer-card--legacy"
                >
                  <p class="gate-hint soft">
                    {{ card.payload?.hint || "可把当前目录与写作要点保存为新蓝本。" }}
                    请先确认大纲，再在大纲卡片或导出栏点「收录到蓝本库」。
                  </p>
                </div>

                <ChatElementConfigureCard
                  v-else-if="card.type === 'element_configure_pending'"
                  :card="card"
                  :session-id="sessionId"
                  :task-elements="elementsForTask(taskIdForCard(card))"
                  :suppress-auto-open="suppressElementConfigureAutoOpen"
                  @done="onElementConfigureSaved"
                  @saved="onElementConfigureSaved"
                />

                <!-- 待收录要素改到文档窗口角标，对话流不再展示此卡 -->
                <template v-else-if="card.type === 'element_library_pending'" />

                <!-- 书写方式已取消：勾选章节后直接依次写作 -->
                <template v-else-if="card.type === 'writing_pace'" />

                <div v-else-if="card.type === 'materials_pack_gate'" class="card materials-pack-card">
                  <div class="card-title">{{ card.title || "选择资料包" }}</div>
                  <p class="card-hint">
                    资料库是放资料包的地方。可选用资料库中已有包，或新建（默认待入库，稍后确认入库）。
                  </p>
                  <p v-if="(card.payload?.slot_schema || []).length" class="card-hint">
                    蓝本槽位：
                    {{
                      (card.payload.slot_schema || [])
                        .map((s) => `${s.no}.${s.name || s.query || "未命名"}`)
                        .join("、")
                    }}
                  </p>
                  <div class="materials-pack-actions">
                    <a-button
 class="chat-action-btn"
 :loading="sending"
 @click="
 sendAction('materials_pack_skip', {
 task_id: card.payload?.task_id,
 blueprint_id: card.payload?.blueprint_id,
 })
 "
 shape="round">
                      不用资料包
                    </a-button>
                    <a-button
 class="chat-action-btn"
 type="primary"
 :loading="sending"
 :disabled="!(card.payload?.library_packs || []).length"
 @click="
 sendAction('materials_pack_use', {
 task_id: card.payload?.task_id,
 blueprint_id: card.payload?.blueprint_id,
 })
 "
 shape="round">
                      使用已有
                    </a-button>
                    <a-button
 class="chat-action-btn"
 type="primary"
 :loading="sending"
 @click="createMaterialsPackFromCard(card)"
 shape="round">
                      新建资料包
                    </a-button>
                  </div>
                  <div
                    v-if="(card.payload?.pending_packs || []).length"
                    class="materials-pack-pending"
                  >
                    <p class="card-hint">待入库：</p>
                    <a-button
 v-for="p in card.payload.pending_packs"
 :key="p.id"
 size="small"
 :loading="sending"
 @click="
 sendAction('materials_pack_confirm_library', {
 pack_id: p.id,
 name: p.name,
 pack_name: p.name,
 task_id: card.payload?.task_id,
 })
 "
 shape="round">
                      入库「{{ p.name }}」
                    </a-button>
                  </div>
                </div>

                <div v-else-if="card.type === 'materials_pack_pick'" class="card materials-pack-card">
                  <div class="card-title">{{ card.title || "选用资料包" }}</div>
                  <div class="materials-pack-actions">
                    <a-button
 v-for="p in card.payload?.packs || []"
 :key="p.id"
 class="chat-action-btn"
 type="primary"
 :loading="sending"
 @click="
 sendAction('materials_pack_use', {
 task_id: card.payload?.task_id,
 pack_id: p.id,
 name: p.name,
 pack_name: p.name,
 })
 "
 shape="round">
                      {{ p.name || p.id }}
                    </a-button>
                  </div>
                </div>

                <div v-else-if="card.type === 'materials_pack_list'" class="card materials-pack-card">
                  <div class="card-title">{{ card.title || "全部资料包" }}</div>
                  <p v-if="card.payload?.bound_pack_id" class="card-hint">
                    当前文档已绑定：{{ card.payload.bound_pack_id }}
                  </p>
                  <div class="materials-pack-actions">
                    <a-button
 v-for="p in [...(card.payload?.library_packs || []), ...(card.payload?.pending_packs || [])]"
 :key="p.id"
 class="chat-action-btn"
 :type="p.id === card.payload?.bound_pack_id ? 'primary' : 'default'"
 :loading="sending"
 @click="
 sendAction('materials_pack_use', {
 task_id: card.payload?.task_id,
 pack_id: p.id,
 name: p.name,
 pack_name: p.name,
 })
 "
 shape="round">
                      {{ p.name || p.id }}{{ p.in_library === false ? "（待入库）" : "" }}
                      · {{ p.slot_count || 0 }} 槽
                    </a-button>
                  </div>
                  <div class="materials-pack-actions" style="margin-top: 8px">
                    <a-button
 class="chat-action-btn"
 :loading="sending"
 @click="createMaterialsPackFromCard(card)"
 shape="round">
                      新建资料包
                    </a-button>
                    <a-button
 class="chat-action-btn"
 :loading="sending"
 @click="sendAction('materials_pack_view', { task_id: card.payload?.task_id })"
 shape="round">
                      查看当前配置
                    </a-button>
                  </div>
                </div>

                <div v-else-if="card.type === 'materials_pack_bound'" class="card materials-pack-card">
                  <div class="card-title">{{ card.title || "资料包" }}</div>
                  <p class="card-hint">
                    {{
                      card.payload?.task_id
                        ? `已绑定「${card.payload?.pack?.name || card.payload?.pack_id}」。可在右侧文档窗按槽位上传文件。`
                        : `已预建「${card.payload?.pack?.name || card.payload?.pack_id}」（开写时自动绑定）。请在右侧文档窗按槽位上传文件。`
                    }}
                  </p>
                  <ul
                    v-if="(card.payload?.slots || card.payload?.slot_schema || []).length"
                    class="materials-pack-slot-list"
                  >
                    <li
                      v-for="s in card.payload.slots || card.payload.slot_schema"
                      :key="s.no"
                    >
                      槽{{ s.no }} · {{ s.name || "未命名" }}
                      <span class="muted">
                        ·
                        {{
                          s.parse_status === "parsed"
                            ? "已解析"
                            : s.parse_status === "error"
                              ? "解析失败"
                              : s.file_name
                                ? "待解析"
                                : "未上传"
                        }}
                      </span>
                    </li>
                  </ul>
                  <div class="materials-pack-actions">
                    <a-button
 class="chat-action-btn"
 :loading="sending"
 @click="
 sendAction('materials_pack_add_slot', {
 task_id: card.payload?.task_id,
 pack_id: card.payload?.pack_id,
 })
 "
 shape="round">
                      添加槽位
                    </a-button>
                    <a-button
 class="chat-action-btn"
 :loading="sending"
 @click="
 sendAction('materials_pack_list', { task_id: card.payload?.task_id })
 "
 shape="round">
                      全部资料包
                    </a-button>
                    <a-button
 class="chat-action-btn"
 :loading="sending"
 @click="
 sendAction('materials_pack_use', { task_id: card.payload?.task_id })
 "
 shape="round">
                      换绑
                    </a-button>
                    <a-button
 class="chat-action-btn"
 type="primary"
 :loading="sending"
                      @click="sendAction('start_writing', card.payload || {})"
 shape="round">
                      继续写作
                    </a-button>
                  </div>
                </div>

                <div v-else-if="card.type === 'start_writing'" class="card">
                  <a-button
 class="chat-action-btn"
 type="primary"
 :loading="sending"
 @click="sendAction('start_writing', card.payload || {})"
 shape="round">
                    {{ card.title || "开始写作" }}
                  </a-button>
                </div>

                <div
                  v-else-if="card.type === 'writing_supplement_check'"
                  class="card writing-supplement-card"
                  :class="{ used: isCardUsed(index, ci) }"
                >
                  <p class="hint">{{ writingSupplementHint(card) }}</p>
                  <ul
                    v-if="visibleWritingSupplementItems(card).length"
                    class="supplement-gap-list"
                  >
                    <li
                      v-for="item in visibleWritingSupplementItems(card)"
                      :key="item.id || item.display_line"
                      class="supplement-gap-row"
                      :class="{ clickable: !card.payload?.readonly && !isCardUsed(index, ci) }"
                      role="button"
                      :tabindex="card.payload?.readonly || isCardUsed(index, ci) ? -1 : 0"
                      @click="onSupplementGapRowClick(card, item, index, ci, $event)"
                      @keydown.enter.prevent="openWritingSupplementItem(card, item)"
                    >
                      <span
                        v-if="supplementItemKindLabel(item)"
                        class="supplement-gap-kind"
                        :title="supplementItemKindLabel(item)"
                      >{{ supplementItemKindLabel(item) }}</span>
                      <span class="supplement-gap-line">{{
                        supplementGapDisplayLine(item, card.payload?.task_id)
                      }}</span>
                      <span v-if="item.chapter_label" class="supplement-gap-ch">{{
                        item.chapter_label
                      }}</span>
                      <div v-if="!card.payload?.readonly" class="supplement-gap-actions">
                        <a-button
 size="small"
 type="primary"
 :disabled="sending || isCardUsed(index, ci)"
 @click.stop="openWritingSupplementItem(card, item)"
 shape="round">
                          去配置
                        </a-button>
                        <a-button
 size="small"
 :disabled="sending || isCardUsed(index, ci)"
 @click="dismissWritingSupplement(card, item, index, ci)"
 shape="round">
                          忽略
                        </a-button>
                      </div>
                      <a-button
 v-else
 size="small"
 type="primary"
 :disabled="sending"
 @click="openWritingSupplementItem(card, item)"
 shape="round">
                        去配置
                      </a-button>
                    </li>
                  </ul>
                  <div v-if="!card.payload?.readonly" class="blueprint-path-actions">
                    <a-button
 class="chat-action-btn"
 type="primary"
 :loading="sending"
 :disabled="isCardUsed(index, ci)"
 @click="skipWritingSupplementCheck(card, index, ci)"
 shape="round">
                      先这样开写
                    </a-button>
                    <a-button
 class="chat-action-btn"
 :disabled="sending || isCardUsed(index, ci)"
 @click="recheckWritingSupplements(card)"
 shape="round">
                      重新检查
                    </a-button>
                  </div>
                </div>

                <ChatFreeformOutlineCard
                  v-else-if="card.type === 'outline_preview'"
                  :title="card.title"
                  :task-id="card.payload?.task_id"
                  :editable="!!card.payload?.editable"
                  :chapters="card.payload?.chapters || []"
                  :doc-title="card.payload?.doc_title || ''"
                  :doc-category="card.payload?.doc_category || ''"
                  :busy="sending"
                  :disabled="sending"
                  :can-collect-blueprint="showOutlineCollectBlueprint"
                  @confirm="confirmFreeformOutline(card)"
                  @regenerate="regenerateFreeformOutline(card)"
                  @reopen="reopenOutlineEdit(card)"
                  @collect-blueprint="collectFreeformToBlueprint(card)"
                  @rename="renameFreeformDocument(card)"
                />

                <!-- 写作进度改进行气泡 process-log（虚线框），进度卡仅作数据载体 -->
                <template v-else-if="card.type === 'progress'" />

                <!-- 对话内「渲染并下载 Word」卡不再展示；导出走输入区上方 -->
                <template v-else-if="card.type === 'export'" />
              </template>
            </div>
          </template>
        </ChatMessageBubble>
      </div>
      <div v-if="showOpeningSuggests" class="opening-suggests">
        <p class="opening-suggests-hint">不知道从哪开始？点一句试试：</p>
        <div class="welcome-actions">
          <div class="suggests">
            <button
              v-for="item in welcomeSuggests"
              :key="item"
              type="button"
              class="suggest"
              :disabled="sending || usageGuideLoading"
              @click="applySuggest(item)"
            >
              {{ item }}
            </button>
          </div>
          <button
            type="button"
            class="suggest suggest-guide"
            :disabled="sending || usageGuideLoading"
            @click="showUsageGuide"
          >
            {{ usageGuideLoading ? "正在整理使用说明…" : "使用说明" }}
          </button>
        </div>
      </div>
    </div>

    <div class="composer-wrap">
      <div v-if="robotSummary || phaseLabel" class="robot-chip">
        <span v-if="robotSummary">{{ robotSummary }}</span>
        <span v-if="phaseLabel" class="phase">{{ phaseLabel }}</span>
      </div>
      <div v-if="taskId" class="composer-export" aria-label="导出">
        <div class="composer-export-main">
          <span class="composer-export-label">导出</span>
          <a-select
 v-model:value="exportTemplateId"
 allow-clear
 placeholder="渲染模版（可选）"
 size="small"
 style="width: 160px"
 show-search>
            <a-select-option
              v-for="item in exportRenderTemplates"
              :key="item.id"
              :value="item.id">{{ item.name || item.id }}</a-select-option>
          </a-select>
          <a-select
 v-model:value="exportWordStyleId"
 placeholder="Word 样式"
 size="small"
 style="width: 160px"
 show-search>
            <a-select-option
              v-for="item in exportWordStyles"
              :key="item.id"
              :value="item.id">{{ item.name || item.id }}</a-select-option>
          </a-select>
          <label class="export-draft-toggle composer-export-only">
            <a-switch v-model:checked="exportConfirmedOnly" size="small"  />
            <span>仅已确认</span>
          </label>
          <a-button
 v-if="taskId"
 class="chat-action-btn"
 type="primary"
 size="small"
 :loading="exportBusy"
 @click="doExportWord()"
 shape="round">
            下载 Word
          </a-button>
          <a-button
 v-if="taskId"
 class="chat-action-btn"
 size="small"
 :loading="exportPdfBusy"
 @click="doExportPdf()"
 shape="round">
            下载 PDF
          </a-button>
        </div>
      </div>
      <div class="composer">
        <div class="composer-top">
          <div class="composer-tools">
            <a-select
 v-model:value="robotId"
 allow-clear
 placeholder="选择机器人（可选）"
 style="width: 220px"
 @change="onRobotChange"
 show-search>
              <a-select-option v-for="item in robots" :key="item.id" :value="item.id">{{ item.name }}</a-select-option>
            </a-select>
            <a-button
 size="small"
 :loading="sending"
 @click="openBlueprintLibraryFromComposer"
 shape="round">
              查看蓝本
            </a-button>
            <a-button
 size="small"
 type="primary"
 :class="{ 'is-doc-win-on': docWindowOpen }"
 :disabled="docWindowClosing || docWindowBusy"
 @click="toggleDocWindow"
 shape="round">
              文档窗口
            </a-button>
          </div>
          <div class="fmt-bar">
            <button type="button" class="fmt" title="加粗" @mousedown.prevent="fmt('bold')"><b>B</b></button>
            <button type="button" class="fmt" title="斜体" @mousedown.prevent="fmt('italic')"><i>I</i></button>
            <button type="button" class="fmt" title="下划线" @mousedown.prevent="fmt('underline')"><u>U</u></button>
            <button type="button" class="fmt" title="无序列表" @mousedown.prevent="fmt('insertUnorderedList')">• 列表</button>
            <button type="button" class="fmt" title="有序列表" @mousedown.prevent="fmt('insertOrderedList')">1. 列表</button>
            <button type="button" class="fmt" title="清除格式" @mousedown.prevent="fmt('removeFormat')">清除</button>
          </div>
        </div>
        <div
          v-if="composerLocked"
          class="composer-lock-bar"
          role="status"
        >
          <span>{{ docWindowOpsLockLabel || (sending ? "正在处理…" : "写作/分析进行中…") }}</span>
          <button type="button" class="composer-unlock" @click="onManualUnlockOps">
            解除锁定
          </button>
        </div>
        <div
          ref="editorEl"
          class="rich-editor"
          :class="{ 'is-locked': composerLocked }"
          :contenteditable="composerLocked ? 'false' : 'true'"
          :data-placeholder="composerLocked ? '写作/分析进行中，请稍候…' : composerPlaceholder"
          @input="onEditorInput"
          @paste="onEditorPaste"
          @keydown="onEditorKeydown"
        />
        <div class="composer-bar">
          <div class="composer-hints">
            <span v-if="suggestedNextLabel" class="hint suggested-next">下一步：{{ suggestedNextLabel }}</span>
            <span class="hint">{{ statusHint }}</span>
            <span v-if="chainDebugDev" class="intent-audit-dev">{{ chainDebugDev }}</span>
          </div>
          <a-button
 type="primary"
 :loading="sending"
 :disabled="composerLocked || !draftPlain.trim()"
 @click="send"
 shape="round">
            发送
          </a-button>
        </div>
      </div>
    </div>
    </div>

    <a-modal
 v-model:open="searchBranchDialog.visible"
 class="search-branch-dialog"
 width="min(420px, 92vw)"
 :mask="false"
 
 :mask-closable="false"
 :title="searchBranchDialog.title ? `试检检索 · ${searchBranchDialog.title}` : '试检检索'"
 @afterClose="resetSearchBranchDialog"
 destroy-on-close centered>
      <div class="search-branch-body">
        <div v-if="searchBranchDialog.loading" class="search-branch-loading">
          <span class="search-branch-spinner" aria-hidden="true" />
          <p class="search-branch-hint">正在检索，请稍候…</p>
        </div>
        <template v-else>
          <p class="search-branch-summary">
            采纳 <strong>{{ searchBranchDialog.adopted }}</strong> 条证据
            <span v-if="searchBranchDialog.note"> · {{ searchBranchDialog.note }}</span>
          </p>
          <p v-if="!searchBranchDialog.cards.length" class="search-branch-empty">没有检索到证据卡片，可调整关键词后再试。</p>
          <ul v-else class="search-branch-list">
            <li v-for="(ev, ei) in searchBranchDialog.cards" :key="ei" :class="{ adopted: ev.adopted }">
              <div class="search-branch-meta">
                <a-tag v-if="ev.adopted" size="small" color="success">采纳</a-tag>
                <a-tag v-else size="small">未采纳</a-tag>
                <span v-if="ev.point" class="search-branch-point">{{ ev.point }}</span>
                <span v-if="ev.source_file" class="search-branch-src">{{ ev.source_file }}</span>
              </div>
              <div class="search-branch-excerpt">{{ ev.excerpt || ev.quote || "（无摘录）" }}</div>
            </li>
          </ul>
          <p class="search-branch-foot">此为旁支试检，证据已写入任务；写作请用「保存并重试写作」。</p>
        </template>
        <ul v-if="searchBranchDialog.loading && searchBranchDialog.progress.length" class="search-branch-progress">
          <li v-for="(line, pi) in searchBranchDialog.progress" :key="pi">{{ line }}</li>
        </ul>
      </div>
      <template #footer>
        <a-button @click="searchBranchDialog.visible = false" shape="round">关闭</a-button>
      </template>
    </a-modal>

    <ChapterElementAssociateDialog
      v-model="chapterElementAssociate.visible"
      :chapter-label="chapterElementAssociate.label"
      :associate-scope="chapterElementAssociate.associateScope"
      :all-elements="elementsForTask(chapterElementAssociate.taskId)"
      :linked-ids="chapterElementAssociate.linkedIds"
      :task-id="chapterElementAssociate.taskId"
      :robot-id="robotId"
      :pack-slot-options="associatePackSlots"
      :seed-create-draft="chapterElementAssociate.seedCreateDraft"
      configure-hint="待补项关联时会自动生成要素草稿，确认保存后即写入本章；要改已有要素可在要素库编辑。"
      @confirm="onChapterElementAssociateConfirm"
    />
    <CollectBlueprintDialog
      v-model="collectBlueprintDialog.visible"
      :suggested-title="collectBlueprintDialog.title"
      :suggested-category="collectBlueprintDialog.category"
      :suggested-tags="collectBlueprintDialog.tags"
      :busy="saveBlueprintBusy"
      @confirm="onCollectBlueprintConfirm"
      @cancel="onCollectBlueprintCancel"
    />
    <aside
      v-if="docWindowOpen"
      class="chat-doc-dock"
      :style="{ width: `${docDockWidth}px` }"
      aria-label="文档窗口侧栏"
    >
      <div
        class="chat-doc-split"
        title="拖动调整文档窗口宽度"
        @mousedown.prevent="startDocDockResize"
      />
      <ChatDocumentWindow
        docked
        :documents="conversationDocuments"
        :active-id="docWindowActiveId"
        :busy="docWindowBusy"
        :busy-label="docWindowBusyLabel"
        :ops-locked="docWindowOpsLocked || sending || pointsDraftInflight"
        :ops-lock-label="docWindowOpsLockLabel || (sending ? '正在处理…' : '')"
        :force-closing="docWindowClosing"
        :outline-reload-token="docOutlineReloadToken"
        :outline-force-reload-token="docOutlineForceReloadToken"
        :preview-reload-token="docPreviewReloadToken"
        :draft-text="docWindowDraftText"
        :locate="docWindowLocate"
        :writing-pace="latestWritingPace"
        :is-freeform="isFreeformWritingSession()"
        :can-save-as-blueprint="showDocSaveAsBlueprint"
        :can-sync-to-blueprint-library="showDocSyncToBlueprintLibrary"
      :session-id="sessionId"
      :robot-id="robotId"
      :element-library-pending="elementLibraryPendingItems"
        :pending-materials-pack-id="docWindowPendingMaterialsPackId"
        @close="onDocWindowClose"
        @sync-request="onDocWindowSyncRequest"
        @update:active-id="onDocWindowActiveId"
        @focus-chapter="onDocWindowFocus"
        @config-saved="onDocWindowConfigSaved"
        @elements-saved="onDocWindowElementsSaved"
        @project-folders-saved="onDocWindowProjectFoldersSaved"
        @update:draft-text="onDocWindowDraftText"
        @write-action="onDocWindowWriteAction"
        @wizard-action="onDocWindowWizardAction"
        @library-pending-changed="onLibraryPendingChanged"
        @rename-document="onDocWindowRenameDocument"
        @materials-pack-pending="onDocWindowMaterialsPackPending"
      />
    </aside>
    <a-modal
 v-model:open="supplementGapDialogVisible"
 title="补充配置要素"
 width="460px"
 class="supplement-gap-dialog"
 destroy-on-close centered>
      <p v-if="supplementGapPick?.item" class="supplement-gap-dialog-line">
        {{
          supplementGapDisplayLine(supplementGapPick.item, supplementGapPick.card?.payload?.task_id) ||
            supplementGapPick.item.slot_label ||
            "待补充项"
        }}
      </p>
      <p v-if="supplementGapPick?.item?.chapter_label" class="supplement-gap-dialog-ch">
        {{ supplementGapPick.item.chapter_label }}
      </p>
      <p class="supplement-gap-dialog-hint">可关联已有要素，或为待补项新建；也可到文档窗「写作准备」里改要点。</p>
      <template #footer>
        <a-button @click="supplementGapDialogVisible = false">取消</a-button>
        <a-button @click="supplementOpenDocContent">到文档窗</a-button>
        <a-button type="primary" @click="supplementAssociateFromGap">关联已有要素</a-button>
        <a-button type="primary" @click="supplementCreateElementFromGap">新建要素</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, inject, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "../ui.js";

import {
  authHeaders,
  docOutlineUrl,
  getUserId,
  isAbortError,
  isTransientNetworkError,
  readStream,
  request,
} from "../api";
import {
  mergeWizardPayload,
  normalizeWizardStep,
  stripEphemeralWizardTriggers,
  freezeWizardCardPayload,
  liveWizardMatchesCard,
  isFreshBlueprintWizardStart,
  pickNonEmptyWizard,
  wizardStepIndex,
  WIZARD_STEPS_CHAT,
  shouldShowBlueprintWizardInChat,
  ensureChatWizardCardsFromRuntime,
  synthesizeChatWizardCardFromRuntime,
  isChatOperableSampleWizardPayload,
} from "../utils/blueprintWizardSteps";
import {
  hasWritingCopyWithLibrarySource,
  isFreeformSampleDocument,
  isLibraryBlueprintId,
  isWritingCopyId,
  normalizeDocumentRegistryKey,
  resolveLibraryBlueprintId,
  sanitizeLibraryBlueprintId,
} from "../utils/documentBlueprintIdentity";
import { ASSISTANT_NAME, BRAND_NAME } from "../brand";
import { actionInstruction, chapterBracket } from "../buttonInstruction";
import { normalizeCompose, presenceForCompose } from "../composeStrategy";
const isDev = import.meta.env.DEV;
const brandInitial = BRAND_NAME.charAt(0);
const composerPlaceholder = `发给${ASSISTANT_NAME}…（Enter 换行，Ctrl+Enter 发送；可粘贴 Word / 富文本）`;
import ChatBlueprintWizardCard from "../components/ChatBlueprintWizardCard.vue";
import ChatDocumentWindow from "../components/ChatDocumentWindow.vue";
import ChatElementConfigureCard from "../components/ChatElementConfigureCard.vue";
import ChatFreeformOutlineCard from "../components/ChatFreeformOutlineCard.vue";
import ChatMessageBubble from "../components/ChatMessageBubble.vue";
import ChatTemplateWizardCard from "../components/ChatTemplateWizardCard.vue";
import ChapterElementAssociateDialog from "../components/ChapterElementAssociateDialog.vue";
import ChapterHtmlEditor from "../components/ChapterHtmlEditor.vue";
import ChapterPickerTree from "../components/ChapterPickerTree.vue";
import CollectBlueprintDialog from "../components/CollectBlueprintDialog.vue";
import {
  elementKindLabel,
  elementSummary,
  elementNeedsSupplement,
  mergeElementNodes,
} from "../elementUi";
import {
  pointsTextFromChapter,
  syncPointItemsFromPoints,
  linkPointItemsToElements,
  pointsFromPointItems,
  textNeedsSupplement,
  humanLabel,
  studySupplementProcessMessage,
} from "../pointItemsSync";
import {
  clipboardToHtml,
  escapeHtml,
  htmlToPlain,
  insertHtmlAtCursor,
  sanitizeHtml,
  toDisplayHtml,
} from "../chatHtml";

const STEP_LABELS = {
  upload: "上传",
  outline: "目录",
  study: "分析",
  search: "检索",
  write: "写作",
  check: "检查",
};

const WRITE_PIPELINE_STEPS = new Set(["search", "write", "check"]);
/** 会话过程条里属于「写作过程」的行（改由小窗承接） */
const WRITE_PROCESS_CHAT_RE =
  /^(▶\s|✓\s|　摘要：)|(?:检索|写作|检查)\s*·|正在写|开始写「|重试章节|本章写完|要素解析/;

/**
 * 写作中实时步骤：taskId → chapterKey → { status, status_label, steps, is_running }
 * 合入 conversationDocuments，供小窗「写作过程」即时刷新。
 */
const liveWritingOverlay = ref({});

const SEARCH_BRANCH_NOISE_RE = /正在想怎么帮你|稍等，我捋一下|正在重试章节|写作\s*·|检查\s*·/;

function emptySearchBranchDialog() {
  return {
    visible: false,
    loading: false,
    title: "",
    chapterKey: "",
    adopted: 0,
    note: "",
    cards: [],
    progress: [],
  };
}

const searchBranchDialog = ref(emptySearchBranchDialog());
const globalLoading = ref(false);
const globalLoadingText = ref("加载中…");
/** 侧栏正在打开的历史会话 id（切换中高亮） */
const openingSessionId = ref("");
const docWindowOpen = ref(false);
const docWindowActiveId = ref("");
const docWindowAutoOpenedKey = ref("");
const docWindowClosing = ref(false);
/** 仅驱动小窗内遮罩，不盖整页 */
const docWindowBusy = ref(false);
const docWindowBusyLabel = ref("");
/** 分析中：只锁章节操作，不加遮罩 */
const docWindowOpsLocked = ref(false);
const docWindowOpsLockLabel = ref("");
/** 无蓝本确认大纲 / 生成要点：LLM 分批推理中，勿因过程条含「要点」就解锁 */
const pointsDraftInflight = ref(false);
/** 右侧并排停靠宽度，与会话同屏不遮挡 */
function defaultDocDockWidth() {
  if (typeof window === "undefined") return 560;
  return Math.min(640, Math.max(440, Math.floor(window.innerWidth * 0.42)));
}
const docDockWidth = ref(defaultDocDockWidth());
let docDockResize = null;

function clampDocDockWidth(raw) {
  const max = Math.max(420, window.innerWidth - 320 - 240);
  return Math.min(max, Math.max(400, Math.round(raw)));
}

function startDocDockResize(event) {
  if (event.button !== 0) return;
  docDockResize = { x: event.clientX, w: docDockWidth.value };
  window.addEventListener("mousemove", onDocDockResizeMove);
  window.addEventListener("mouseup", stopDocDockResize);
}

function onDocDockResizeMove(event) {
  if (!docDockResize) return;
  // 分隔条在左侧：向左拖加宽文档窗
  const next = docDockResize.w + (docDockResize.x - event.clientX);
  docDockWidth.value = clampDocDockWidth(next);
}

function stopDocDockResize() {
  docDockResize = null;
  window.removeEventListener("mousemove", onDocDockResizeMove);
  window.removeEventListener("mouseup", stopDocDockResize);
}
/** 仅在本会话检测到新蓝本/目录提取或新增要素配置时才自动弹文档窗；进入历史会话不弹 */
const docWindowAutoOpenArmed = ref(false);
/** 恢复/切换历史会话时短暂为 true，避免用已有状态误武装自动开窗 */
const sessionHydrating = ref(false);
/** 进入已有对话时抑制要素配置弹窗；本轮新增要素配置仍可自动弹出 */
const suppressElementConfigureAutoOpen = ref(false);
/** 学习计数等软刷新 */
const docOutlineReloadToken = ref(0);
/** 配置/分析/写作操作完成后强制 fresh 重拉 */
const docOutlineForceReloadToken = ref(0);
/** 写完一章后强制重拉全文预览，避免仍显示上一稿 */
const docPreviewReloadToken = ref(0);
/** 文档窗 PUT 落盘后递增，经 ui-state 同步以失效服务端 turn_context */
const clientConfigRevision = ref(0);

function bumpClientConfigRevision() {
  clientConfigRevision.value += 1;
  schedulePersistUiState(true);
}

function bumpDocOutlineReload({ force = false } = {}) {
  if (force) docOutlineForceReloadToken.value += 1;
  else docOutlineReloadToken.value += 1;
}

function writingTaskIdForTask(taskId) {
  const tid = String(taskId || "").trim();
  if (!tid) return "";
  const docs = conversationDocuments.value || [];
  const hit =
    docs.find((d) => String(d.writingTaskId || "") === tid) ||
    docs.find((d) => String(d.taskId || "") === tid);
  return String(hit?.writingTaskId || "").trim();
}

function bumpDraftPreviewReload() {
  docPreviewReloadToken.value += 1;
}
/** 文档窗定位驱动：token 变化时窗口 watch 并选中章节/切 Tab */
const docWindowLocate = ref({
  token: 0,
  taskId: "",
  chapterKey: "",
  tab: "config",
  mainView: "",
  reason: "",
  elementId: "",
  associateKnowledge: false,
});
let docWindowLocateSeq = 0;

const supplementGapDialogVisible = ref(false);
const supplementGapPick = ref(null);

const latestWritingPace = computed(() => {
  const progress = latestProgressCards().find((c) => c?.type === "progress");
  return String(progress?.payload?.writing_pace || "step").trim() || "step";
});

/**
 * DocWindowDriver：按意图开窗并定位章节/Tab。
 * reason: write_running | write_awaiting_confirm | write_chapter_done | write_batch_advance | …
 */
function driveDocWindow(
  reason,
  {
    taskId: driveTaskId = "",
    blueprintId = "",
    chapterKey = "",
    tab = "",
    mainView = "",
    elementId = "",
    associateKnowledge = false,
    elementNodes = null,
    elementIds = null,
    newElementIds = null,
    pointItems = null,
    point_items = null,
    pointItemLink = null,
    points = null,
    keywords = null,
    describes = "",
    draftHtml = "",
    draftTitle = "",
    sessionEpoch: driveEpoch = null,
    sessionId: driveSessionId = "",
  } = {}
) {
  if (sessionHydrating.value) return;
  // 旧会话残留的分析定位不得打开/切换新会话文档窗
  if (driveEpoch != null && !isCurrentSessionContext(driveEpoch, driveSessionId)) return;
  const tid = String(driveTaskId || blueprintId || "").trim();
  const key = String(chapterKey || "").trim();
  const why = String(reason || "").trim();
  if (!tid) return;
  // 分析定位：文档必须属于当前会话文档列表，禁止凭裸 taskId 拉旧蓝本
  if (why === "analyze") {
    const docs = conversationDocuments.value || [];
    const owned = docs.some(
      (d) =>
        String(d.taskId || "") === tid ||
        String(d.blueprintId || "") === tid ||
        String(d.writingTaskId || "") === tid ||
        String(d.id || "") === `bp:${tid}` ||
        String(d.id || "") === `task:${tid}`
    );
    if (!owned) return;
  }

  let nextTab = String(tab || "").trim();
  let nextMainView = String(mainView || "").trim();
  if (!nextTab) {
    if (
      why === "write_awaiting_confirm" ||
      why === "write_chapter_done" ||
      why === "write_running" ||
      why === "write_batch_advance"
    ) {
      // 写作进度在会话卡展示；文档窗定位到写作准备 / 全文预览即可
      nextTab = "content";
      if (!nextMainView) nextMainView = "preview";
    } else if (why === "analyze") nextTab = key ? "config" : "topology";
    else if (why === "select_write" || why === "open_doc_window") nextTab = key ? "config" : "topology";
    else nextTab = "config";
  }
  if (nextTab === "draft" || nextTab === "preview" || nextTab === "process" || nextTab === "text" || nextTab === "progress") {
    if (nextTab === "preview" && !nextMainView) nextMainView = "preview";
    nextTab = "content";
    if (!nextMainView) nextMainView = "preview";
  }

  armDocWindowAutoOpen();
  const docs = conversationDocuments.value || [];
  // 当前会话尚无文档登记：禁止凭裸 taskId 开窗（避免拉到已删会话蓝本）
  if (!docs.length) return;
  const sessionBp = String(
    selectedBlueprint.value || liveBlueprintWizard.value?.blueprint_id || blueprintId || ""
  ).trim();
  // 写作副本 tid 优先于会话蓝本 bp: 槽，避免分析/保存时把用户从 A 抢到 B
  // 找不到目标时禁止落到「随便一篇」——同会话多文档也必须按 task 隔离
  const hit =
    docs.find((d) => String(d.writingTaskId || "") === tid) ||
    docs.find((d) => String(d.taskId || "") === tid) ||
    docs.find((d) => String(d.id || "") === `task:${tid}`) ||
    (sessionBp && docs.find((d) => String(d.id || "") === `bp:${sessionBp}`)) ||
    docs.find((d) => String(d.blueprintId || "") === tid) ||
    docs.find((d) => String(d.id || "") === `bp:${tid}`) ||
    docs.find((d) => String(d.id || "").includes(tid));
  if (!hit?.id) return;
  // 用户已停在匹配同一写作副本的文档项时勿改 activeId（防 bp:/task: 来回跳）
  const curId = String(docWindowActiveId.value || "").trim();
  const curDoc = curId ? docs.find((d) => d.id === curId) : null;
  const curWid = String(curDoc?.writingTaskId || curDoc?.taskId || "").trim();
  const locateSameDocOnly = [
    "analyze",
    "write_running",
    "write_chapter_done",
    "write_batch_advance",
    "write_awaiting_confirm",
  ].includes(why);
  if (locateSameDocOnly && curId) {
    const pinned = normalizeDocumentRegistryKey(streamFocusPinnedKey);
    const showing =
      (curWid && curWid === tid) ||
      curId === hit.id ||
      curId === `task:${tid}` ||
      curId === `bp:${tid}`;
    const pinOk =
      !pinned ||
      pinned === hit.id ||
      pinned === `task:${tid}` ||
      pinned === `bp:${tid}`;
    // 已切到另一份文档：后台分析/写作不得把小窗抢回来
    if (!showing || !pinOk) {
      if (showing && (why === "write_chapter_done" || why === "write_awaiting_confirm")) {
        bumpDraftPreviewReload();
      }
      return;
    }
  }
  if (!(curId && curWid && curWid === tid && curDoc)) {
    docWindowActiveId.value = hit.id;
  }
  const autoKey = `${sessionId.value || "nosession"}:drive:${tid}:${why}`;
  docWindowAutoOpenedKey.value = autoKey;
  docWindowOpen.value = true;
  const locateTid = String(hit?.writingTaskId || hit?.taskId || tid).trim();
  if (key) {
    docWindowFocusChapter.value = { taskId: locateTid, key };
    expandOnlyChapter(locateTid, key, latestProgressCards(), {
      refreshText:
        why === "write_awaiting_confirm" ||
        why === "write_chapter_done" ||
        why === "write_running" ||
        why === "write_batch_advance",
    });
  }
  const openKnowledge =
    Boolean(associateKnowledge) || why === "associate_element_knowledge";
  docWindowLocateSeq += 1;
  docWindowLocate.value = {
    token: docWindowLocateSeq,
    taskId: locateTid,
    chapterKey: key,
    tab: nextTab,
    mainView: nextMainView,
    reason: why,
    elementId: String(elementId || "").trim(),
    associateKnowledge: openKnowledge,
    ...(Array.isArray(elementNodes) ? { elementNodes } : {}),
    ...(Array.isArray(elementIds) ? { elementIds } : {}),
    ...(Array.isArray(newElementIds) ? { newElementIds } : {}),
    ...(Array.isArray(pointItems) ? { pointItems } : {}),
    ...(Array.isArray(point_items) ? { point_items } : {}),
    ...(Array.isArray(point_items) && !Array.isArray(pointItems)
      ? { pointItems: point_items }
      : {}),
    ...(pointItemLink && typeof pointItemLink === "object" ? { pointItemLink } : {}),
    ...(Array.isArray(points) ? { points } : {}),
    ...(Array.isArray(keywords) ? { keywords } : {}),
    ...(describes ? { describes: String(describes) } : {}),
    ...(String(draftHtml || "").trim() ? { draftHtml: String(draftHtml) } : {}),
    ...(String(draftTitle || "").trim() ? { draftTitle: String(draftTitle) } : {}),
  };
}

async function onDocWindowSyncRequest() {
  docWindowBusy.value = true;
  docWindowBusyLabel.value = "正在同步文档窗口…";
  try {
    await persistUiStateForced();
    ElMessage.success("已同步到数据库");
  } catch (error) {
    ElMessage.error(error?.message || "同步失败");
  } finally {
    docWindowBusy.value = false;
    docWindowBusyLabel.value = "";
  }
}

async function onDocWindowClose() {
  if (docWindowClosing.value) return;
  docWindowClosing.value = true;
  docWindowBusy.value = true;
  docWindowBusyLabel.value = "正在关闭文档窗口…";
  try {
    await persistUiStateForced();
  } catch {
    /* 关窗仍继续 */
  } finally {
    docWindowOpen.value = false;
    docWindowClosing.value = false;
    docWindowBusy.value = false;
    docWindowBusyLabel.value = "";
  }
}

/** 从文档项对齐会话 taskId / selectedBlueprint（与下拉切换一致） */
function alignSessionFocusFromDoc(doc) {
  if (!doc) return;
  const bid = String(doc.blueprintId || "").trim();
  const tid = String(doc.writingTaskId || doc.taskId || "").trim();
  // 依据样本 / 无蓝本未挂库：清掉上一份选用蓝本，否则顶栏会误显「同步」藏「收录」
  if (isFreeformSampleDocument(doc) || (String(doc.kind || "") !== "blueprint" && !bid)) {
    selectedBlueprint.value = "";
  } else if (bid && !/^w/i.test(bid)) {
    selectedBlueprint.value = bid;
  }
  if (String(doc.kind || "") === "blueprint") {
    taskId.value = String(doc.writingTaskId || "").trim();
  } else if (tid) {
    taskId.value = tid;
  }
}

/** 本轮流式已切过焦点（focus_document）；softSync 勿用旧 activeBefore / 脏 serverFocus 打回 */
let streamFocusPinnedKey = "";
/** 正在跑页面级分析的写作任务 id；切到其它文档时勿把分析锁带到新窗口 */
let pageStudyOwnerTaskId = "";
/** 程序切焦点（流式/ syncDocWindow）时勿再 POST announce */
let programmaticFocusSyncDepth = 0;
/** 本轮写作流水线已开跑且尚未 run_done；避免 done/finally 过早解锁小窗 */
let writingStreamActive = false;
/** 本章刚写出的完整 HTML，写完立刻铺进全文预览 */
const liveWriteHtmlByChapter = new Map();

function wizardDocKeyFromPayload(payload, fallback = "") {
  const p = payload && typeof payload === "object" ? payload : {};
  const id = String(p.working_task_id || p.blueprint_id || p.task_id || "").trim();
  if (id) return /^task:|^bp:/.test(id) ? id : `task:${id}`;
  return String(fallback || "").trim();
}

function rememberLiveWizardForDoc(docId, wizard) {
  const key = normalizeDocumentRegistryKey(String(docId || "").trim());
  if (!key) return;
  const next = { ...(liveWizardsByDoc.value || {}) };
  if (wizard && typeof wizard === "object" && Object.keys(wizard).length) {
    next[key] = { ...wizard };
    const tid = String(wizard.working_task_id || wizard.blueprint_id || "").trim();
    if (tid) next[`task:${tid.replace(/^(task:|bp:)/, "")}`] = { ...wizard };
  } else {
    delete next[key];
  }
  liveWizardsByDoc.value = next;
}

function stashLiveWizardForCurrentDoc() {
  const key = String(docWindowActiveId.value || currentFocusDocumentId() || "").trim();
  if (key && liveBlueprintWizard.value) {
    rememberLiveWizardForDoc(key, liveBlueprintWizard.value);
  }
}

function storedLiveWizardForDoc(doc) {
  const key = String(doc?.id || "").trim();
  const tid = String(doc?.writingTaskId || doc?.taskId || "").trim();
  const map = liveWizardsByDoc.value || {};
  return (
    (key && map[key]) ||
    (tid && (map[`task:${tid}`] || map[tid])) ||
    null
  );
}

/** 口语/服务端切焦点后，对齐文档小窗下拉 + 会话 taskId */
function syncDocWindowToFocusKey(focusKey, { stashCurrent = true } = {}) {
  const key = normalizeDocumentRegistryKey(String(focusKey || "").trim());
  if (!key) return;
  if (stashCurrent) stashLiveWizardForCurrentDoc();
  const docs = conversationDocuments.value || [];
  let hit = docs.find((d) => d.id === key);
  if (!hit && key.startsWith("bp:")) {
    const bid = key.slice(3);
    hit =
      docs.find((d) => String(d.blueprintId || d.taskId || "") === bid) ||
      (isWritingCopyId(bid)
        ? docs.find(
            (d) =>
              d.id === `task:${bid}` ||
              String(d.taskId || "") === bid ||
              String(d.writingTaskId || "") === bid
          )
        : null);
  }
  if (!hit && key.startsWith("task:")) {
    const tid = key.slice(5);
    hit = docs.find((d) => String(d.taskId || "") === tid || String(d.writingTaskId || "") === tid);
  }
  if (hit?.id) {
    programmaticFocusSyncDepth += 1;
    try {
      if (docWindowActiveId.value !== hit.id) {
        docWindowActiveId.value = hit.id;
      }
    } finally {
      nextTick(() => {
        programmaticFocusSyncDepth = Math.max(0, programmaticFocusSyncDepth - 1);
      });
    }
    alignSessionFocusFromDoc(hit);
    // 新开无蓝本文档：清掉上一份蓝本向导 live，避免小窗仍显示旧目录
    realignLiveWizardToActiveDoc(hit);
  } else {
    // 登记表尚未跟上：先挂 activeId，并按 key 推 taskId，避免仍落在旧文档
    programmaticFocusSyncDepth += 1;
    try {
      docWindowActiveId.value = key;
    } finally {
      nextTick(() => {
        programmaticFocusSyncDepth = Math.max(0, programmaticFocusSyncDepth - 1);
      });
    }
    if (key.startsWith("task:")) {
      const tid = key.slice(5);
      if (tid) taskId.value = tid;
      selectedBlueprint.value = "";
      // 仅当 live 指向其它任务时才清；新建样本上传卡依赖本轮 live 向导
      const live = liveBlueprintWizard.value || {};
      const liveWork = String(live.working_task_id || live.blueprint_id || "").trim();
      if (liveWork && liveWork !== tid) {
        liveBlueprintWizard.value = null;
      }
    } else if (key.startsWith("bp:")) {
      const bid = key.slice(3);
      if (bid && !isWritingCopyId(bid)) selectedBlueprint.value = bid;
    } else if (key.startsWith("doc:")) {
      /* 隔离槽：等 docs 进表后再 align */
    }
  }
  if (sessionRuntime.value && typeof sessionRuntime.value === "object") {
    sessionRuntime.value = { ...sessionRuntime.value, focus_document_key: key };
  } else {
    sessionRuntime.value = { focus_document_key: key };
  }
  if (!docWindowOpen.value) {
    docWindowOpen.value = true;
  }
  // 写作进行中：禁止 force 刷大纲，否则会把章节定位打回第一章
  const writeLocked =
    writingStreamActive ||
    /写作|书写/.test(String(docWindowOpsLockLabel.value || ""));
  if (writeLocked) {
    const runKey = String(activeRunChapterKey.value || "").trim();
    const runTid = String(taskId.value || hit?.writingTaskId || hit?.taskId || "").trim();
    if (runTid && runKey) {
      driveDocWindow("write_running", {
        taskId: runTid,
        chapterKey: runKey,
        mainView: "chapter",
        tab: "content",
      });
    }
    syncStudyLockToActiveDoc();
    return;
  }
  bumpDocOutlineReload({ force: true });
  syncStudyLockToActiveDoc();
}

/** 文档下拉切换：先改 activeId（下拉名称立刻变），后台落盘；目录由小窗 identity watch 拉取，勿再 force 双拉 */
async function onDocWindowActiveId(nextId) {
  const id = String(nextId || "").trim();
  if (!id || id === docWindowActiveId.value) {
    if (id) docWindowActiveId.value = id;
    return;
  }
  // 写作/保存进行中禁止切走；分析中允许切到另一份（分析在后台继续）
  if (isDocWindowWriteLockActive() || docWindowBusy.value) {
    ElMessage.warning(
      docWindowOpsLockLabel.value
        ? `${docWindowOpsLockLabel.value}，请稍后再切换文档`
        : "当前文档操作进行中，请稍后再切换"
    );
    return;
  }
  docWindowActiveId.value = id;
  // 立刻写 runtime 焦点，不等 ui-state 回包（sendAction/document_key 单一真相）
  if (sessionRuntime.value && typeof sessionRuntime.value === "object") {
    sessionRuntime.value = { ...sessionRuntime.value, focus_document_key: id };
  } else {
    sessionRuntime.value = { focus_document_key: id };
  }
  // 对齐会话焦点指针，避免后续无 payload 的聊天动作落到旧任务
  const doc = (conversationDocuments.value || []).find((d) => d.id === id);
  alignSessionFocusFromDoc(doc);
  realignLiveWizardToActiveDoc(doc);
  const fromProgram = programmaticFocusSyncDepth > 0;
  try {
    await syncFocusDocumentWithServer(doc, { announce: !fromProgram });
    if (!fromProgram) {
      void persistUiStateForced().catch(() => {});
    }
  } catch {
    /* 网络失败时仍靠 ui-state 落焦点 */
  }
  if (!fromProgram) {
    scrollChatToFocusThread();
  }
  syncStudyLockToActiveDoc();
}

/** 切文档 tab 时勿沿用上一份的蓝本向导 live 状态 */
function realignLiveWizardToActiveDoc(doc) {
  if (!doc) {
    liveBlueprintWizard.value = null;
    return;
  }
  const stored = storedLiveWizardForDoc(doc);
  if (stored && Object.keys(stored).length) {
    liveBlueprintWizard.value = { ...stored };
    return;
  }
  // 依据样本支线：保留/对齐本任务向导（上传→提取→确认→分析），不要当纯无蓝本清掉
  if (isFreeformSampleDocument(doc) || isFreeformSampleWizardMode({ mode: doc.wizardMode })) {
    const sampleTid = String(doc.writingTaskId || doc.taskId || "").trim();
    const live = liveBlueprintWizard.value || {};
    const liveWork = String(live.working_task_id || live.blueprint_id || "").trim();
    if (sampleTid && liveWork && liveWork !== sampleTid) {
      liveBlueprintWizard.value = null;
      syncLiveWizardFromLatestCard();
    }
    return;
  }
  const kind = String(doc.kind || "").trim();
  const libBp = String(doc.blueprintId || "").trim();
  const pathHint = String(doc.writingPath || doc.entryPath || "").trim();
  if (pathHint === "template") {
    syncLiveWizardFromLatestCard();
    return;
  }
  const freeformDoc =
    pathHint === "freeform" ||
    kind === "outline" ||
    kind === "writing" ||
    (!libBp && /^w/i.test(String(doc.writingTaskId || doc.taskId || "")));
  if (freeformDoc && pathHint !== "template") {
    liveBlueprintWizard.value = null;
    return;
  }
  const liveBid = String(liveBlueprintWizard.value?.blueprint_id || "").trim();
  const docBp = libBp || (kind === "blueprint" ? String(doc.taskId || "").trim() : "");
  if (liveBid && docBp && liveBid !== docBp) {
    liveBlueprintWizard.value = null;
    syncLiveWizardFromLatestCard();
  }
}

async function toggleDocWindow() {
  if (docWindowClosing.value || docWindowBusy.value) return;
  if (docWindowOpen.value) {
    await onDocWindowClose();
    return;
  }
  if (!docWindowActiveId.value && conversationDocuments.value.length) {
    const target = pickDocWindowTarget();
    if (target?.id) docWindowActiveId.value = target.id;
  }
  // 打开只挂载小窗；首轮读数遮罩在小窗内部
  docWindowOpen.value = true;
}

/** 关窗/手动同步：绕过 sending 节流，尽量把 chapter_edits 落库 */
async function persistUiStateForced() {
  if (!sessionId.value) return;
  const payload = buildUiStatePayload();
  // 焦点只走 /focus-document；这里带上会把进行中的「切换到另一份」打回旧文档
  delete payload.focus_document_key;
  delete payload.focus_task_id;
  delete payload.focus_blueprint_id;
  if (payload.freeform_patch && typeof payload.freeform_patch === "object") {
    delete payload.freeform_patch.focus_document_key;
  }
  await request(`/api/writing/chat/sessions/${encodeURIComponent(sessionId.value)}/ui-state`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  lastUiStateFingerprint = uiStateFingerprintOf(payload);
  lastUiStateAt = Date.now();
}

let studyLockWatchdog = null;
let studyLastProgressAt = 0;

function clearStudyLockWatchdog() {
  if (studyLockWatchdog) {
    clearInterval(studyLockWatchdog);
    studyLockWatchdog = null;
  }
}

function noteStudyProgressHeartbeat() {
  studyLastProgressAt = Date.now();
}

function armStudyLockWatchdog() {
  // 已在看守中则不重置心跳，避免「反复上锁」把超时永远推后
  if (studyLockWatchdog) return;
  studyLastProgressAt = Date.now();
  studyLockWatchdog = setInterval(() => {
    if (!docWindowOpsLocked.value && !pageStudyInflight) {
      clearStudyLockWatchdog();
      return;
    }
    const label = String(docWindowOpsLockLabel.value || "");
    const looksStudy =
      /分析|学习|要点/.test(label) || pageStudyInflight || pointsDraftInflight.value;
    if (!looksStudy) return;
    // 2 分钟无任何进度事件 → 视为状态不同步，自动解锁，避免永久锁死
    if (Date.now() - studyLastProgressAt > 120_000) {
      releaseStudyOpsLock({
        notify: true,
        reason: "分析长时间无进度，已自动解除锁定，可继续操作",
      });
    }
  }, 5000);
}

/** 清掉分析残留锁：study_stream_active / trigger / opsLock / 卡住的 sending */
function releaseStudyOpsLock({ notify = false, reason = "" } = {}) {
  pageStudyInflight = false;
  pageStudyKickArmed = false;
  pageStudyToken = "";
  pointsDraftInflight.value = false;
  clearStudyLockWatchdog();
  const live = liveBlueprintWizard.value;
  if (live && typeof live === "object") {
    const next = { ...live };
    delete next.study_stream_active;
    delete next.trigger_begin_study;
    delete next.trigger_chapter_study;
    delete next.chapter_study_keys;
    liveBlueprintWizard.value = next;
  }
  setDocWindowOpsLock(false);
  pageStudyOwnerTaskId = "";
  if (sending.value) {
    try {
      streamAbortController?.abort();
    } catch {
      /* ignore */
    }
    sending.value = false;
  }
  if (notify) {
    ElMessage.warning(reason || "已解除分析锁定，可继续操作");
  }
}

function onManualUnlockOps() {
  releaseStudyOpsLock({
    notify: true,
    reason: "已手动解除锁定。若分析其实还在跑，可到文档窗再点「继续分析」。",
  });
}

function setDocWindowOpsLock(on, label = "") {
  if (on) {
    docWindowOpsLocked.value = true;
    docWindowOpsLockLabel.value = String(label || "").trim() || "请稍候…";
    if (/分析|学习|要点/.test(docWindowOpsLockLabel.value)) {
      armStudyLockWatchdog();
    }
    return;
  }
  docWindowOpsLocked.value = false;
  docWindowOpsLockLabel.value = "";
  clearStudyLockWatchdog();
}

function isDocWindowWriteLockActive() {
  return (
    writingStreamActive ||
    /写作|书写/.test(String(docWindowOpsLockLabel.value || ""))
  );
}

function windowIsShowingTask(tid) {
  const want = String(tid || "").trim();
  if (!want) return false;
  const pinned = normalizeDocumentRegistryKey(streamFocusPinnedKey);
  const curId = normalizeDocumentRegistryKey(String(docWindowActiveId.value || "").trim());
  const focusId = pinned || curId;
  if (!focusId) return false;
  if (focusId === `task:${want}` || focusId === `bp:${want}` || focusId === want) return true;
  const hit = (conversationDocuments.value || []).find((d) => d.id === focusId);
  const hid = String(hit?.writingTaskId || hit?.taskId || "").trim();
  return Boolean(hid && hid === want);
}

function syncStudyLockToActiveDoc() {
  if (isDocWindowWriteLockActive()) return;
  if (!pageStudyInflight) return;
  const owner = String(pageStudyOwnerTaskId || "").trim();
  if (owner && windowIsShowingTask(owner)) {
    noteStudyProgressHeartbeat();
    setDocWindowOpsLock(true, docWindowOpsLockLabel.value || "正在分析章节…");
    return;
  }
  setDocWindowOpsLock(false);
}

function isDocWindowStudyLockLabel(label = "") {
  return /分析|学习|要点|原文|解析/.test(String(label || ""));
}

function setDocWindowStudyBusy(on, label = "") {
  if (!on) {
    const cur = String(docWindowOpsLockLabel.value || "");
    // 仅当当前确实是写作锁时才跳过；分析锁必须能收掉（勿被残留 writingStreamActive 永久挡住）
    if (/写作|书写/.test(cur) && !isDocWindowStudyLockLabel(cur)) return;
    setDocWindowOpsLock(false);
    return;
  }
  const owner = String(pageStudyOwnerTaskId || "").trim();
  // 正在看另一份文档：分析继续跑，但不要锁死当前窗口
  if (owner && String(docWindowActiveId.value || "").trim() && !windowIsShowingTask(owner)) {
    return;
  }
  // 心跳只在真实进度事件里刷新，这里上锁不重置超时
  setDocWindowOpsLock(true, label || "正在依据样本分析章节…");
}

function setDocWindowWriteBusy(on, label = "") {
  setDocWindowOpsLock(on, label || "正在写作…");
}

function onWizardStudySynced(payload = {}) {
  // 中途：driveDocWindow(analyze) 已补本章要点，勿全量拉目录
  const midStream = Boolean(payload?.chapter_key);
  if (midStream) {
    noteStudyProgressHeartbeat();
    return;
  }
  bumpDocOutlineReload({ force: true });
  if (!pageStudyInflight && !pointsDraftInflight.value) {
    setDocWindowStudyBusy(false);
  }
}

/** 分析流推进到某章时，文档小窗定位到该章（范围学习：运行一章定位一章） */
function onWizardStudyLocate(payload = {}) {
  const bid = String(payload?.blueprint_id || liveBlueprintWizard.value?.blueprint_id || "").trim();
  const key = String(payload?.chapter_key || payload?.chapterKey || "").trim();
  if (!bid || !key) return;
  noteStudyProgressHeartbeat();
  armDocWindowAutoOpen();
  const workTid = String(
    payload?.working_task_id ||
      payload?.task_id ||
      liveBlueprintWizard.value?.working_task_id ||
      ""
  ).trim();
  driveDocWindow("analyze", {
    blueprintId: bid,
    taskId: (workTid && /^w/i.test(workTid) ? workTid : "") || bid,
    chapterKey: key,
    tab: "config",
  });
}

function isUniqueTurnCard(cards, ci) {
  const type = String(cards?.[ci]?.type || "");
  if (type !== "writing_pace" && type !== "handoff") return true;
  return (cards || []).findIndex((c) => String(c?.type || "") === type) === ci;
}

async function onWizardWriteAnalyzed(payload = {}) {
  const bid = String(payload?.blueprint_id || liveBlueprintWizard.value?.blueprint_id || "").trim();
  if (!bid || sending.value) return;
  await sendAction("write_analyzed_only", { blueprint_id: bid });
}
/** 防止目录未同步完就弹窗；客户会以为解析不全 */
const docWindowOpening = ref(false);

const STATUS_LABELS = {
  pending: "未开写",
  searching: "检索中",
  writing: "写作中",
  checking: "检查中",
  awaiting_confirm: "已写作",
  confirmed: "已写作",
  needs_human: "已写作",
  skipped: "已跳过",
  studying: "待开写",
  running: "进行中",
  completed: "已完成",
  done: "完成",
  accept: "通过",
  revise: "退回修改",
  approve: "通过",
  edit: "改字后通过",
  return: "退回",
  skip: "跳过",
  error: "失败",
};

function statusLabel(value) {
  const key = String(value || "").trim();
  return STATUS_LABELS[key] || key || "未知";
}

/** 按钮操作 → 对话里展示的口语文案（兼容旧会话里的「[操作] xxx」） */
const ACTION_DISPLAY_LABELS = {
  select_blueprint: "选用蓝本",
  skip_blueprint: "无蓝本 · 自动生成大纲",
  begin_blueprint_wizard: "无蓝本 · 依据模版编写",
  list_blueprints: "看看有哪些蓝本",
  open_blueprint_library: "查看蓝本库",
  write_analyzed_only: "先写已分析的章节",
  select_write_chapters: "确认要写的章节",
  start_writing: "开始写作",
  check_writing_supplements: "重新检查待补充",
  skip_writing_supplement_check: "先这样开写",
  dismiss_writing_supplement: "忽略待补充项",
  resume: "从蓝本页回来继续",
  confirm_chapter: "确认本章",
  confirm_all_chapters: "统一确认章节",
  configure_chapter: "保存章节配置",
  add_chapter_element: "添加章节要素",
  associate_elements: "关联章节要素",
  open_element_library: "查看要素",
  retry_chapter: "保存并开始写作",
  retry_search_chapter: "仅重试检索",
  handoff: "去完善蓝本",
  pause_writing: "暂停写作",
  resource_choice: "资源设置",
  blueprint_wizard: "蓝本创建向导",
  template_wizard: "模版配置",
  submit_outline: "确认大纲",
  save_freeform_as_blueprint: "收录到蓝本库",
  sync_document_to_blueprint_library: "同步到蓝本库",
  generate_freeform_points: "帮我生成写作要点",
  generate_chapter_boilerplate: "帮我生成套话正文",
  skip_freeform_points: "先不配要点，直接开写",
  resolve_intent_clarify: "确认意图选项",
  materials_pack_skip: "不用资料包",
  materials_pack_use: "换绑资料包",
  materials_pack_create: "新建资料包",
  materials_pack_confirm_library: "确认资料包入库",
  materials_pack_list: "查看全部资料包",
  materials_pack_view: "查看资料包配置",
  materials_pack_unbind: "解绑资料包",
  materials_pack_add_slot: "添加资料包槽位",
  materials_pack_remove_slot: "删除资料包槽位",
  materials_pack_update_slot: "修改资料包槽位",
  materials_pack_rename: "重命名资料包",
  usage_guide: "使用说明",
};

const WIZARD_STEP_USER_LABELS = {
  meta: "填写蓝本名称",
  upload: "上传样板文件",
  upload_done: "完成上传样板",
  outline: "提取章节目录",
  outline_done: "完成目录提取",
  outline_confirm: "确认章节目录",
  study: "开始分析要点",
  study_done: "完成分析要点",
  done: "完成蓝本向导",
};

const WIZARD_COMMAND_USER_LABELS = {
  re_extract_outline: "重新提取目录",
  re_upload_sample: "重新上传样板",
  go_outline_confirm: "进入确认目录",
  go_study: "进入分析要点",
  re_study_chapters: "重新分析章节",
};

function blueprintWizardActionLabel(payload = {}) {
  const command = String(payload.wizard_command || "").trim();
  if (command === "re_study_chapters") {
    const method = String(payload.study_method || "").trim();
    const keys = payload.chapter_keys || [];
    const n = Array.isArray(keys) ? keys.filter(Boolean).length : 0;
    const tag = chapterBracket(payload);
    const scope = n > 1 ? `（${n}章）` : tag;
    if (method === "source") return scope ? `根据原文分析章节${scope}` : "根据原文分析本章";
    if (method === "auto") return scope ? `自动分析章节${scope}` : "自动分析本章";
    if (method === "sample") return scope ? `依据样本分析章节${scope}` : "依据样本分析本章";
    return scope ? `分析章节${scope}` : "重新分析本章";
  }
  if (command && WIZARD_COMMAND_USER_LABELS[command]) {
    return WIZARD_COMMAND_USER_LABELS[command];
  }
  const step = String(payload.step || "").trim();
  const title = String(payload.title || "").trim();
  // 确认目录会推进到 study 步，但未真正开跑——勿显示成「开始分析要点」
  if (
    step === "study" &&
    payload.outline_confirmed === true &&
    payload.trigger_begin_study !== true &&
    !command
  ) {
    return "确认章节目录";
  }
  if (step === "meta" && title) {
    return `创建蓝本「${title}」`;
  }
  if (step && WIZARD_STEP_USER_LABELS[step]) {
    return WIZARD_STEP_USER_LABELS[step];
  }
  return ACTION_DISPLAY_LABELS.blueprint_wizard;
}

/** 无蓝本·依据样本（原「创建蓝本」向导，不入库；收录才进库） */
const FREEFORM_SAMPLE_STEP_CARD_HEADLINE = {
  meta: "依据样本 · 填写名称",
  upload: "依据样本 · 上传样板",
  upload_done: "依据样本 · 提取目录",
  outline: "依据样本 · 提取目录",
  outline_done: "依据样本 · 确认目录",
  outline_confirm: "依据样本 · 确认目录",
  study: "依据样本 · 分析要点",
  study_done: "依据样本 · 分析要点",
  done: "依据样本 · 分析要点",
};
/** @deprecated 文案已迁到 FREEFORM_SAMPLE；保留别名避免旧引用 */
const WIZARD_STEP_CARD_HEADLINE = FREEFORM_SAMPLE_STEP_CARD_HEADLINE;

const SELECTED_WIZARD_STEP_CARD_HEADLINE = {
  outline_done: "选用蓝本 · 确认目录",
  outline_confirm: "选用蓝本 · 确认目录",
  study: "选用蓝本 · 分析要点",
  study_done: "选用蓝本 · 分析要点",
  done: "选用蓝本 · 分析要点",
};

function isFreeformSampleWizardMode(payloadOrWizard) {
  const mode = String(payloadOrWizard?.mode || "").trim();
  // create = 旧称，现与 freeform_sample 同为「无蓝本·依据样本、收录前不入库」
  return mode === "freeform_sample" || mode === "create";
}

function wizardStepCardHeadline(step, payload = {}) {
  const mode = String(payload?.mode || "").trim();
  if (mode === "selected") {
    return SELECTED_WIZARD_STEP_CARD_HEADLINE[step] || "选用蓝本";
  }
  return FREEFORM_SAMPLE_STEP_CARD_HEADLINE[step] || "";
}

/** 最新卡且蓝本 id 与 live 一致时才注入 live；历史卡 / 其它蓝本卡一律隔离 */
function liveWizardForCard(card, msgIndex, cardIndex) {
  if (isBlueprintWizardStale(msgIndex, cardIndex)) return null;
  const payload = card?.payload || {};
  const step = normalizeWizardStep(payload.step);
  const cardKey = wizardDocKeyFromPayload(payload);
  const perDoc = cardKey ? (liveWizardsByDoc.value || {})[cardKey] : null;
  let live = perDoc || null;
  if (
    !live &&
    wizardCardBelongsToFocus(card, messages.value?.[msgIndex]) &&
    liveBlueprintWizard.value &&
    liveWizardMatchesCard(payload, liveBlueprintWizard.value)
  ) {
    live = liveBlueprintWizard.value;
  }
  const early = ["meta", "upload", "outline", "outline_confirm"].includes(step);
  if (live && early) {
    const liveStep = normalizeWizardStep(live.step);
    const liveExtracting =
      Boolean(live.outline_extract_active) ||
      Boolean(live.trigger_outline_extract) ||
      String(live.pending_wizard_command || "").trim() === "re_extract_outline";
    if (
      !liveExtracting &&
      (Boolean(live.outline_confirmed) ||
        wizardStepIndex(liveStep) >= wizardStepIndex("outline_confirm")) &&
      ["meta", "upload", "outline"].includes(step)
    ) {
      live = null;
    }
  }
  if (live && liveWizardMatchesCard(payload, live)) return live;
  if (early) {
    const snap = { ...payload };
    if (
      wizardCardBelongsToFocus(card, messages.value?.[msgIndex]) &&
      (isFreshBlueprintWizardStart(snap) ||
        !liveBlueprintWizard.value ||
        !liveWizardMatchesCard(payload, liveBlueprintWizard.value))
    ) {
      liveBlueprintWizard.value = { ...snap };
      const storeKey = cardKey || currentFocusDocumentId();
      if (storeKey) rememberLiveWizardForDoc(storeKey, liveBlueprintWizard.value);
    }
    return snap;
  }
  return live;
}

/** 历史卡渲染前冻结快照，去掉提取中/学习中等瞬时标记 */
function wizardCardForRender(card, msgIndex, cardIndex) {
  if (!card || !isBlueprintWizardStale(msgIndex, cardIndex)) return card;
  const frozen = freezeWizardCardPayload(card.payload || {});
  return { ...card, payload: frozen };
}

function wizardEffectiveStep(card, { allowLive = true } = {}) {
  if (!allowLive) {
    return normalizeWizardStep(card?.payload?.step);
  }
  if (!liveWizardMatchesCard(card?.payload || {}, liveBlueprintWizard.value)) {
    return normalizeWizardStep(card?.payload?.step);
  }
  const merged = mergeWizardPayload(card?.payload || {}, liveBlueprintWizard.value || {});
  return normalizeWizardStep(merged.step);
}

function syncLiveWizardFromLatestCard() {
  const latest = findLatestBlueprintWizard();
  if (!latest) return;
  const payload = messages.value[latest.msgIndex]?.cards?.[latest.cardIndex]?.payload || {};
  const bid = String(
    payload.blueprint_id || payload.working_task_id || payload.task_id || ""
  ).trim();
  if (!bid) return;
  const live = liveBlueprintWizard.value || {};
  const merged = mergeWizardPayload(payload, live);
  liveBlueprintWizard.value = {
    ...live,
    ...merged,
    blueprint_id: String(payload.blueprint_id || merged.blueprint_id || bid).trim(),
    working_task_id: String(
      payload.working_task_id || merged.working_task_id || bid
    ).trim(),
    step: merged.step,
  };
}

/** 对齐会话 live 向导状态；不改写消息里的向导卡快照（每步对话必须保留）。 */
function reconcileLocalWizardCardsFromSession(session) {
  const serverW = blueprintWizardStateFromSession(session);
  const live = { ...(serverW || {}), ...(liveBlueprintWizard.value || {}) };
  const latest = findLatestBlueprintWizard();
  if (!latest) {
    if (serverW && typeof serverW === "object") {
      liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, serverW);
    }
    return;
  }
  const msg = messages.value[latest.msgIndex];
  const card = msg?.cards?.[latest.cardIndex];
  if (!card || card.type !== "blueprint_wizard") return;
  const merged = mergeWizardPayload(card.payload || {}, live);
  const step = normalizeWizardStep(merged.step);
  const bid = String(merged.blueprint_id || live.blueprint_id || "").trim();
  if (bid || step !== "meta") {
    liveBlueprintWizard.value = {
      ...live,
      ...merged,
      step,
      ...(bid ? { blueprint_id: bid } : {}),
    };
  }
}

function wizardCardHeadline(card, msgIndex, cardIndex) {
  const stale =
    msgIndex != null && cardIndex != null && isBlueprintWizardStale(msgIndex, cardIndex);
  // 历史卡 / 其它蓝本卡必须冻结在落库快照，不能被当前 live 盖成「提取目录」
  const allowLive = !stale && liveWizardMatchesCard(card?.payload || {}, liveBlueprintWizard.value);
  const step = wizardEffectiveStep(card, { allowLive });
  const payload = allowLive
    ? mergeWizardPayload(card?.payload || {}, liveBlueprintWizard.value || {})
    : card?.payload || {};
  const selectedHeadline = wizardStepCardHeadline(step, payload);
  if (selectedHeadline) {
    return selectedHeadline;
  }
  const title = String(card?.title || "").trim();
  if (String(payload?.mode || "").trim() === "selected") {
    return title && !title.includes("创建蓝本") ? title : "选用蓝本";
  }
  return title && title !== "蓝本创建步骤" ? title : "在对话中创建蓝本";
}

/** 英文 slug / 与 key 相同的一串标识符，勿作为用户可见章名（Markdown 还会把下划线变斜体） */
function isInternalChapterSlug(text, chapterKey = "") {
  const t = String(text || "").trim();
  const k = String(chapterKey || "").trim();
  if (!t) return true;
  if (k && t.toLowerCase() === k.toLowerCase()) return true;
  if (/[\u4e00-\u9fff]/.test(t)) return false;
  return /^[A-Za-z][A-Za-z0-9_-]*$/.test(t);
}

function isPlaceholderChapterName(value) {
  const t = String(value || "").trim();
  return !t || t === "本章" || t === "当前章节";
}

/** 会话里展示用章名：序号 + 中文标题；找不到时用「本章」，绝不直接露 chapter_key */
function chapterUserLabel(taskId, chapterKey, hints = {}) {
  let key = String(chapterKey || hints.chapter_key || hints.focus_chapter_key || "").trim();
  let seq = String(hints.sequence || "").trim();
  let title = String(hints.title || "").trim();
  if (isPlaceholderChapterName(title) || isPlaceholderChapterName(hints.chapter_title)) {
    if (isPlaceholderChapterName(title)) title = "";
  } else if (!title) {
    title = String(hints.chapter_title || "").trim();
  }
  if (isPlaceholderChapterName(title) || isInternalChapterSlug(title, key)) title = "";

  const absorb = (ch) => {
    if (!ch || (key && String(ch?.key || "").trim() !== key)) return;
    if (!seq) seq = String(ch.sequence || ch.config?.sequence || "").trim();
    const t = String(ch.title || ch.config?.title || "").trim();
    if (t && !isPlaceholderChapterName(t) && !isInternalChapterSlug(t, key)) {
      title = title || t;
    }
    const lab = String(ch.chapter_label || ch.label || "").trim();
    if ((!seq || !title) && lab && !isPlaceholderChapterName(lab)) {
      const m = lab.match(/^(\d+(?:\.\d+)*)\s+(.+)$/);
      if (m) {
        seq = seq || m[1];
        title = title || m[2];
      } else if (!title && !isInternalChapterSlug(lab, key)) {
        title = lab;
      }
    }
  };

  const tid = String(taskId || hints.task_id || "").trim();
  if (!key) {
    const prog = latestProgressCards().find((c) => c?.type === "progress");
    const chapters = prog?.payload?.chapters || [];
    const focus =
      chapters.find((c) => c?.is_running || c?.is_next) ||
      chapters.find((c) => String(c?.key || "").trim()) ||
      null;
    key = String(
      hints.focus_chapter_key || prog?.payload?.focus_chapter_key || focus?.key || ""
    ).trim();
    if (focus) absorb(focus);
  }
  if (tid && key) {
    const edit = chapterEdits.value[chapterEditKey(tid, key)];
    if (edit) {
      if (!seq) seq = String(edit.sequence || "").trim();
      const et = String(edit.title || "").trim();
      if (et && !isPlaceholderChapterName(et) && !isInternalChapterSlug(et, key)) {
        title = title || et;
      }
    }
  }
  for (const card of latestProgressCards()) {
    const ptid = String(card?.payload?.task_id || "").trim();
    if (tid && ptid && ptid !== tid) continue;
    for (const ch of card?.payload?.chapters || []) absorb(ch);
  }
  for (const doc of conversationDocuments.value || []) {
    if (tid) {
      const dt = String(doc.writingTaskId || doc.taskId || "").trim();
      if (dt && dt !== tid) continue;
    }
    for (const ch of doc.chapters || []) absorb(ch);
  }
  if ((!seq || !title) && !tid) {
    for (const doc of conversationDocuments.value || []) {
      for (const ch of doc.chapters || []) absorb(ch);
    }
  }
  const label = [seq, title].filter(Boolean).join(" ").trim();
  return label || "本章";
}

function polishUserChatText(text) {
  const raw = String(text || "");
  const m = raw.match(/^(保存并重写|保存并开始写作|重写章节|开始写作章节)[「\[]([^」\]]+)[」\]]$/);
  if (!m) return raw;
  const [, prefix, quoted] = m;
  const prog = latestProgressCards().find((c) => c?.type === "progress");
  const tid = String(prog?.payload?.task_id || taskId.value || "").trim();
  const quotedKey = isPlaceholderChapterName(quoted) ? "" : quoted;
  const needResolve = isPlaceholderChapterName(quoted) || isInternalChapterSlug(quoted, quoted);
  const label = needResolve
    ? chapterUserLabel(tid, quotedKey, { task_id: tid, chapter_key: quotedKey })
    : quoted;
  const shown = isPlaceholderChapterName(label) ? quoted : label;
  if (isPlaceholderChapterName(shown) && !needResolve) return raw;
  if (prefix === "保存并重写" || prefix === "重写章节") return `重写章节[${shown}]`;
  if (prefix === "保存并开始写作" || prefix === "开始写作章节") return `开始写作章节[${shown}]`;
  return `${prefix}[${shown}]`;
}

function actionDisplayLabel(action, payload = {}) {
  const key = String(action || "").trim();
  if (key === "blueprint_wizard") {
    return blueprintWizardActionLabel(payload);
  }
  const chap = chapterUserLabel(
    payload.task_id,
    payload.chapter_key || payload.focus_chapter_key,
    payload
  );
  const instruction = actionInstruction(
    key,
    payload,
    isPlaceholderChapterName(chap) ? "" : chap
  );
  if (instruction) return instruction;
  return ACTION_DISPLAY_LABELS[key] || "继续";
}

const CHAPTER_SCOPED_ACTIONS = new Set([
  "retry_chapter",
  "retry_search_chapter",
  "confirm_chapter",
  "configure_chapter",
  "get_chapter_info",
  "generate_chapter_describes",
  "generate_chapter_boilerplate",
  "associate_elements",
  "add_chapter_element",
  "start_writing",
  "generate_freeform_points",
  "skip_writing_supplement_check",
  "check_writing_supplements",
  "dismiss_writing_supplement",
  "write_analyzed_only",
  "select_write_chapters",
  "confirm_all_chapters",
  "blueprint_wizard",
]);

function enrichActionPayload(action, raw = {}) {
  const payload = { ...(raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {}) };
  const tid = String(payload.task_id || payload.taskId || taskId.value || "").trim();
  if (tid) payload.task_id = tid;
  const nested = payload.chapter && typeof payload.chapter === "object" ? payload.chapter : null;
  if (nested) {
    if (!String(payload.chapter_key || "").trim()) {
      payload.chapter_key = String(nested.key || "").trim();
    }
    if (!String(payload.sequence || "").trim()) {
      payload.sequence = String(nested.sequence || "").trim();
    }
    if (isPlaceholderChapterName(payload.title) && nested.title) {
      payload.title = String(nested.title || "").trim();
    }
  }
  const keys = (payload.chapter_keys || payload.write_chapter_keys || payload.chapterKeys || [])
    .map((k) => String(k || "").trim())
    .filter(Boolean);
  if (keys.length) payload.chapter_keys = keys;
  let chapterKey = String(
    payload.chapter_key ||
      payload.chapterKey ||
      payload.focus_chapter_key ||
      payload.locate_chapter_key ||
      ""
  ).trim();
  if (!chapterKey && keys.length === 1) chapterKey = keys[0];
  if (chapterKey) payload.chapter_key = chapterKey;
  if (CHAPTER_SCOPED_ACTIONS.has(String(action || "").trim())) {
    const resolved = chapterUserLabel(tid, chapterKey, payload);
    const seqNow = String(payload.sequence || "").trim();
    let titleNow = String(payload.title || "").trim();
    if (isPlaceholderChapterName(titleNow) || isInternalChapterSlug(titleNow, chapterKey)) {
      titleNow = "";
    }
    if (!isPlaceholderChapterName(resolved)) {
      const m = String(resolved).match(/^(\d+(?:\.\d+)*)\s+(.+)$/);
      if (!seqNow && m) payload.sequence = m[1];
      if (!titleNow) {
        payload.title = m ? m[2] : resolved;
      } else {
        payload.title = titleNow;
      }
      payload.chapter_title = payload.title;
    }
    const lab = String(payload.chapter_label || payload.study_message || "").trim();
    if (lab && (!payload.sequence || isPlaceholderChapterName(payload.title))) {
      const m = lab.match(/^(\d+(?:\.\d+)*)\s+(.+)$/);
      if (m) {
        if (!payload.sequence) payload.sequence = m[1];
        if (isPlaceholderChapterName(payload.title)) {
          payload.title = m[2];
          payload.chapter_title = m[2];
        }
      }
    }
    const seqs = payload.chapter_sequences;
    if (Array.isArray(seqs) && seqs[0] && !String(payload.sequence || "").trim()) {
      payload.sequence = String(seqs[0]).trim();
    }
  }
  const packName = String(payload.pack_name || payload.name || "").trim();
  if (packName && String(action || "").startsWith("materials_pack_")) {
    payload.pack_name = packName;
  }
  return payload;
}

function friendlyMessageText(item) {
  const raw = String(item?.text || "");
  if (item?.role === "user") {
    return polishUserChatText(raw);
  }
  if (item?.streaming && !raw.trim()) {
    return "正在整理回复，请稍候…";
  }
  const op = raw.match(/^\[操作\]\s*(\S+)/);
  if (op) return actionDisplayLabel(op[1]);
  return (
    raw
      .replace(/总控\s*Agent/gi, "写作助手")
      .replace(/对话总控/g, "写作助手")
      .replace(/\bAgent\b/gi, "助手") || raw
  );
}

const TECH_PROCESS_RE =
  /\[链路\]|\[调试\]|phase=|path=|task=|conf=|guard:|rule:|intent=|freeform_|→\s*freeform|上下文·|对话尾·|工具·|LLM·|ReAct·|意图·|无蓝本续写|分类建议|内部分类|confirm_chapter|conf=/i;

function friendlyProcessText(message, { done = false } = {}) {
  let raw = String(message || "");
  raw =
    raw
      .replace(/正在联系写作助手思考下一步[^]*/gi, "正在联系写作助手思考下一步…")
      .replace(/写作助手正在决定调用哪些步骤/gi, "正在安排具体步骤…")
      .replace(/收到，我先理解你的需求[^]*/gi, "收到，我先理解你的需求…")
      .replace(/总控\s*Agent\s*思考中[….。\.]*/gi, "稍等，我捋一下…")
      .replace(/总控\s*Agent/gi, "写作助手")
      .replace(/对话总控/g, "写作助手")
      .replace(/总控这边出错了/g, "这边出了点问题")
      .replace(/总控/g, "写作助手")
      .replace(/\bAgent\b/gi, "助手")
      .replace(/对话模式·.*/g, "正在安排下一步…")
      .replace(/接着把你的稿子搭起来/g, "正在整理文档结构…")
      .replace(/正在理解你的意思[….。]*/g, "正在理解你的意思…")
      .replace(/兜底·.*/g, "正在处理…") || raw;
  if (done) raw = processMessageAsDone(raw);
  return raw;
}

/** 回合结束后把「正在…」收成完成态，避免过程条看起来一直卡住 */
function processMessageAsDone(message) {
  const raw = String(message || "").trim();
  if (!raw) return raw;
  const ellip = /(?:…|\.{2,})$/;
  // 分析进度条以 ▶ 开头，须单独收束，否则会一直显示「正在分析」
  const studyRunning = raw.match(
    /^▶\s*(?:\((\d+)\/(\d+)\)\s*)?正在分析[「"]([^」"]+)[」"]/
  );
  if (studyRunning) {
    const idx = studyRunning[1];
    const total = studyRunning[2];
    const label = String(studyRunning[3] || "").trim() || "章节";
    if (idx && total) return `✓ (${idx}/${total}) 「${label}」已分析`;
    return `✓ 「${label}」已分析`;
  }
  const studyProg = raw.match(/^▶\s*分析进度\s+(\d+)\/(\d+)\s*·\s*(.+)$/);
  if (studyProg) {
    return `✓ 分析进度 ${studyProg[1]}/${studyProg[2]} · ${String(studyProg[3] || "").trim()}`;
  }
  if (/^▶\s*正在分析/.test(raw)) {
    return raw.replace(/^▶\s*/, "✓ ").replace(/正在分析/, "已分析").replace(ellip, "");
  }
  const mapped = [
    [/^正在起草各章写作要点(?:…|\.{2,})?$/, "各章写作要点已起草"],
    [/^正在生成写作要点(?:…|\.{2,})?$/, "写作要点已生成"],
    [/^大纲已确认，正在自动生成各章写作要点(?:…|\.{2,})?$/, "大纲已确认，各章写作要点已生成"],
    [/^正在处理章节目录(?:…|\.{2,})?$/, "章节目录已处理"],
    [/^正在处理蓝本向导(?:…|\.{2,})?$/, "✓ 已提交分析任务"],
    [/^正在对指定章节重新分析要点(?:…|\.{2,})?$/, "已开始重新分析要点"],
    [/^正在处理你的请求(?:…|\.{2,})?$/, "请求已处理"],
    [/^正在安排具体步骤(?:…|\.{2,})?$/, "步骤已安排"],
    [/^正在联系写作助手思考下一步(?:…|\.{2,})?$/, "已联系写作助手"],
  ];
  for (const [re, to] of mapped) {
    if (re.test(raw)) return to;
  }
  const batchProg = raw.match(/^正在起草第\s*(\d+)\s*\/\s*(\d+)\s*批章节要点/);
  if (batchProg) {
    const cur = Number(batchProg[1]) || 0;
    const tot = Number(batchProg[2]) || 0;
    if (tot > 0 && cur < tot) return raw;
    if (tot > 0 && cur >= tot) return `第 ${cur}/${tot} 批章节要点已起草`;
  }
  if (/^正在按目录为\s*\d+\s*个可写章节起草写作要点/.test(raw)) {
    return raw;
  }
  if (/^正在/.test(raw) || /正在自动生成/.test(raw)) {
    return raw
      .replace(/^大纲已确认，正在/, "大纲已确认，已")
      .replace(/^正在(.+?)(?:…|\.{2,})$/, (_, body) => {
        const t = String(body || "")
          .replace(ellip, "")
          .trim();
        if (!t) return "已完成";
        if (/完成|已起草|已生成|已处理|已确认/.test(t)) return t;
        return `${t}完成`;
      });
  }
  return raw;
}

function isStudyRunningProcessMessage(message) {
  const msg = String(message || "").trim();
  return /^▶/.test(msg) && /正在分析|分析进度/.test(msg);
}

/** 清掉/收束分析中的 ▶ 过程行，避免完成后仍显示「正在分析」 */
function settleStudyRunningSteps(msgRef, { label = "", all = false } = {}) {
  if (!msgRef?.steps?.length) return;
  const needle = String(label || "").trim();
  const next = [];
  for (const row of msgRef.steps) {
    const msg = String(row?.message || "");
    if (!isStudyRunningProcessMessage(msg)) {
      next.push(row);
      continue;
    }
    if (all || !needle || msg.includes(needle)) {
      // 有明确完成行时直接丢掉进行中行；否则收成 ✓
      if (all || needle) continue;
      next.push({ ...row, message: processMessageAsDone(msg) });
      continue;
    }
    next.push(row);
  }
  msgRef.steps = next;
}

function finalizeAssistantProcessSteps(msgRef) {
  if (!msgRef?.steps?.length) return;
  const mapped = msgRef.steps.map((row) => {
    const msg = String(row?.message || "");
    if (!msg) return row;
    if (
      !/^正在/.test(msg) &&
      !/正在自动生成/.test(msg) &&
      !isStudyRunningProcessMessage(msg)
    ) {
      return row;
    }
    return { ...row, message: processMessageAsDone(msg) };
  });
  // 后端完成态 + 前端收束可能各写一条相同文案，合并连续重复
  const deduped = [];
  for (const row of mapped) {
    const prev = deduped[deduped.length - 1];
    if (
      prev &&
      String(prev.message || "") === String(row.message || "") &&
      String(prev.kind || "") === String(row.kind || "")
    ) {
      continue;
    }
    deduped.push(row);
  }
  msgRef.steps = deduped;
}

function pointsDraftBatchProgress(steps) {
  const batchRe = /第\s*(\d+)\s*\/\s*(\d+)\s*批/;
  let maxBatch = 0;
  let totalBatches = 0;
  for (const row of steps || []) {
    const m = String(row?.message || "").match(batchRe);
    if (!m) continue;
    maxBatch = Math.max(maxBatch, Number(m[1]) || 0);
    totalBatches = Math.max(totalBatches, Number(m[2]) || 0);
  }
  return {
    maxBatch,
    totalBatches,
    incomplete: totalBatches > 0 && maxBatch < totalBatches,
  };
}

function isPointsDraftProgressMessage(message) {
  const t = String(message || "");
  return /起草.*要点|写作要点|按目录为.*可写章节|要点草案|章节目录/.test(t);
}

function isPointsDraftWorkComplete(msgRef, runtime) {
  const reply = String(msgRef?.text || "").trim();
  if (
    /已按目录为\s*\d+\s*个可写章节起草|已为选中的\s*\d+\s*个章节起草|已起草了要点|各章写作要点已起草/.test(
      reply
    )
  ) {
    return true;
  }
  const steps = msgRef?.steps || [];
  const joined = steps.map((s) => String(s?.message || "")).join("\n");
  if (/要点草案已返回/.test(joined) && !pointsDraftBatchProgress(steps).incomplete) {
    return true;
  }
  const ff = runtime?.workflows?.freeform || runtime?.freeform || {};
  const step = String(ff.points_step || "").toLowerCase();
  if (step === "generated" || step === "skipped") return true;
  return false;
}

function beginPointsDraftLock(label = "正在起草写作要点…") {
  pointsDraftInflight.value = true;
  setDocWindowStudyBusy(true, label);
  noteStudyProgressHeartbeat();
}

function releasePointsDraftLockIfComplete(msgRef, runtime, { notifyIncomplete = false } = {}) {
  if (!pointsDraftInflight.value && !/要点|确认大纲/.test(String(docWindowOpsLockLabel.value || ""))) {
    return;
  }
  const steps = msgRef?.steps || [];
  const { incomplete } = pointsDraftBatchProgress(steps);
  if (isPointsDraftWorkComplete(msgRef, runtime)) {
    pointsDraftInflight.value = false;
    if (!pageStudyInflight && !liveBlueprintWizard.value?.study_stream_active) {
      setDocWindowStudyBusy(false);
    }
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (incomplete && notifyIncomplete) {
    ElMessage.warning("写作要点可能尚未全部生成完，请稍候或再点「生成要点」。");
    pointsDraftInflight.value = true;
    const keepLabel =
      String(docWindowOpsLockLabel.value || "").trim() || "正在起草写作要点…";
    setDocWindowStudyBusy(true, keepLabel);
  }
}

function statusTone(value) {
  const key = String(value || "").trim();
  if (key === "confirmed" || key === "awaiting_confirm" || key === "needs_human") return "ok";
  if (key === "skipped" || key === "error") return "warn";
  if (key === "searching" || key === "writing" || key === "checking" || key === "running") return "run";
  return "";
}

function chapterEditKey(taskId, chapterKey) {
  return `${taskId || ""}:${chapterKey || ""}`;
}

function chapterTitle(ch) {
  const seq = String(ch?.sequence || "").trim();
  const title = String(ch?.title || ch?.key || "未命名章节").trim();
  return seq ? `${seq} ${title}` : title;
}

function evidenceBlockTitle(ch) {
  const n = Number(ch?.evidence_count || (ch?.evidence || []).length || 0);
  const list = ch?.evidence || [];
  const elementLike = list.some(
    (ev) => String(ev?.source_file || "") === "要素" || String(ev?.id || "").startsWith("el-")
  );
  return elementLike ? `要素资料 · ${n}` : `证据 · ${n}`;
}

/** 进度卡：本章获取到的要素（会话窗口统一展示） */
function chapterObtainedElements(ch) {
  const rows = [];
  const seen = new Set();
  const push = (raw, scope = "chapter") => {
    if (!raw || typeof raw !== "object") return;
    let eid = String(raw.id || "").trim();
    if (eid.startsWith("el-el-")) eid = eid.slice(3);
    const name = String(raw.name || raw.point || eid || "要素").trim() || "要素";
    const key = eid || `name:${name}`;
    if (seen.has(key)) return;
    seen.add(key);
    const excerpt = String(raw.value || raw.excerpt || raw.result || "").trim();
    rows.push({
      id: eid || key,
      name,
      kind: String(raw.kind || raw.location || "").trim(),
      query: String(raw.query || "").trim(),
      excerpt: excerpt.slice(0, 480),
      ok: Boolean(raw.ok ?? excerpt),
      scope: scope === "point" ? "point" : "chapter",
    });
  };
  for (const row of ch?.element_results || []) push(row, "chapter");
  for (const ev of ch?.evidence || []) {
    push(
      {
        id: String(ev?.id || "").replace(/^el-/, "") || ev?.id,
        name: ev?.point || ev?.name,
        kind: ev?.location || ev?.kind,
        query: ev?.query,
        value: ev?.excerpt || ev?.value,
        ok: Boolean(String(ev?.excerpt || ev?.value || "").trim()),
      },
      "chapter"
    );
  }
  return rows.slice(0, 12);
}

function chapterObtainedOkCount(ch) {
  return chapterObtainedElements(ch).filter((row) => row.ok).length;
}

function chapterObtainedSummary(ch) {
  const list = chapterObtainedElements(ch);
  if (!list.length) return "";
  const ok = list.filter((row) => row.ok).length;
  return `获取要素 ${ok}/${list.length}`;
}

function chapterCheckActionLabel(action) {
  const key = String(action || "").trim();
  return (
    {
      accept: "通过",
      revise: "退回重写",
      needs_human: "转人工",
    }[key] || ""
  );
}

/** 折叠行短提示：重写 / 评分 / 要素 */
function chapterFoldHint(ch) {
  if (!ch || ch.structure_only) return "";
  const parts = [];
  const rev = Number(ch.revision_count || 0);
  const maxRev = Number(ch.max_revisions || 5);
  if (rev > 0) parts.push(`重写 ${rev}/${maxRev}`);
  const score = Number(ch.check_score || 0);
  const pass = Number(ch.write_score_pass || 70);
  if (score > 0) parts.push(`评分 ${score}/${pass}`);
  const els = chapterObtainedSummary(ch);
  if (els) parts.push(els);
  return parts.join(" · ");
}

function chapterRunPoints(ch) {
  const cfg = ch?.config || {};
  const raw = Array.isArray(cfg.points) ? cfg.points : Array.isArray(ch?.points) ? ch.points : [];
  return raw.map((p) => String(p || "").trim()).filter(Boolean);
}

function chapterRunDescribes(ch) {
  return String(ch?.config?.describes || ch?.describes || "").trim();
}

function chapterRunAux(ch) {
  return String(ch?.config?.aux_prompt || ch?.aux_prompt || "").trim();
}

/** 进度卡顶部：重试次数（加大）+ 评分 */
function chapterScoreBanner(ch) {
  if (!ch || ch.structure_only) return null;
  const rev = Number(ch.revision_count || 0);
  const maxRev = Number(ch.max_revisions || 5);
  const score = Number(ch.check_score || 0);
  const pass = Number(ch.write_score_pass || 70);
  const act = chapterCheckActionLabel(ch.check_action);
  const hasCheck = score > 0 || act || (ch.checks || []).length;
  if (!rev && !hasCheck) return null;
  const retry =
    rev > 0
      ? `重试 ${rev}/${maxRev}`
      : hasCheck
        ? `重试 0/${maxRev}`
        : "";
  const scoreText = score > 0
    ? `评分 ${score} / 阈值 ${pass}${score < pass ? " · 未达" : " · 达线"}`
    : "";
  return { retry, score: scoreText };
}

/** 进度展开：图2 风格要点列表（写作要点 / 重写 / 评分 / 过程） */
function chapterProgressLog(ch) {
  if (!ch || ch.structure_only) return [];
  const rows = [];
  const title = String(ch.title || ch.key || "本章").trim();
  const st = String(ch.status_label || ch.status || "").trim();
  const points = chapterRunPoints(ch);
  const describes = chapterRunDescribes(ch);
  const aux = chapterRunAux(ch);
  const rev = Number(ch.revision_count || 0);
  const maxRev = Number(ch.max_revisions || 5);
  const score = Number(ch.check_score || 0);
  const pass = Number(ch.write_score_pass || 70);
  const act = chapterCheckActionLabel(ch.check_action);
  const els = chapterObtainedElements(ch);

  const headBits = ["写作"];
  if (st) headBits.push(st);
  if (points.length) headBits.push(`${title} 按 ${points.length} 条写作要点撰写`);
  else headBits.push(title);
  rows.push({ text: headBits.join(" · ") });

  if (points.length) {
    rows.push({
      kind: "points",
      text: `写作要点 · ${points.length} 条`,
      items: points.map((p, i) => `${i + 1}. ${p}`),
    });
  }
  if (describes) {
    rows.push({
      text: `章节说明：${describes.length > 220 ? `${describes.slice(0, 220)}…` : describes}`,
      mute: true,
    });
  }
  if (aux) {
    rows.push({
      text: `补充提示：${aux.length > 160 ? `${aux.slice(0, 160)}…` : aux}`,
      mute: true,
    });
  }

  if (els.length) {
    const ok = els.filter((e) => e.ok).length;
    rows.push({ text: `获取要素 ${ok}/${els.length}` });
    for (const ev of els.slice(0, 6)) {
      const name = String(ev.name || "要素").trim();
      const bit = ev.ok
        ? String(ev.excerpt || ev.value || "已取到").trim().slice(0, 100)
        : "未取到";
      rows.push({ text: `${name}：${bit}`, nest: true, mute: true });
    }
  }

  if (rev > 0 || score > 0 || act || (ch.checks || []).length) {
    const bits = [];
    if (rev > 0) bits.push(`已重试 ${rev}/${maxRev} 次`);
    else bits.push(`未自动重试（上限 ${maxRev}）`);
    if (score > 0) bits.push(`评分 ${score}（阈值 ${pass}${score < pass ? " · 未达" : " · 达线"}）`);
    if (act) bits.push(`检查${act}`);
    rows.push({ text: bits.join(" · "), emph: true });
    const reason = String(ch.check_reason || "").trim();
    if (reason) rows.push({ text: reason, nest: true, mute: true });
  }

  for (const step of ch.steps || []) {
    const label = String(step.step_label || step.step || "步骤").trim();
    const sst = String(step.status_label || step.status || "").trim();
    const note = String(step.note || "").trim();
    rows.push({
      text: [label, sst, note].filter(Boolean).join(" · "),
      mute: true,
    });
  }

  if (!ch.is_running && (ch.has_draft || st.includes("已") || score > 0 || rev > 0)) {
    rows.push({
      text: `「${title}」写作完成`,
      emph: true,
    });
  }
  return rows;
}

/** 重试/开写后用服务端新草稿覆盖本地编辑区（用户未手工改过时） */
function adoptServerDraft(taskId, ch, { force = false } = {}) {
  if (!taskId || !ch?.key) return;
  const key = chapterEditKey(taskId, ch.key);
  const edit = chapterEdits.value[key];
  const raw = ch?.draft_text || ch?.draft_preview || "";
  const html = toDisplayHtml(raw) || raw;
  if (!html.trim()) return;
  if (!edit) {
    ensureChapterEdit(taskId, ch, { refreshText: true });
    return;
  }
  if (force || !edit._touched) {
    edit.text = html;
    edit._touched = false;
    chapterEdits.value = { ...chapterEdits.value, [key]: { ...edit } };
  }
}

function countByStatus(chapters, status) {
  return (chapters || []).filter((item) => item?.status === status).length;
}

function progressStat(payload, status) {
  const stats = payload?.stats;
  if (stats && stats[status] != null) return Number(stats[status]) || 0;
  return countByStatus(payload?.chapters, status);
}

function isChapterOpen(taskId, chapterKey) {
  return Boolean(openChapters.value[chapterEditKey(taskId, chapterKey)]);
}

function toggleChapter(taskId, ch) {
  const key = chapterEditKey(taskId, ch?.key);
  const open = !openChapters.value[key];
  openChapters.value = { ...openChapters.value, [key]: open };
  if (open) ensureChapterEdit(taskId, ch, { refreshText: true });
}

function canConfirmChapter(ch) {
  if (!ch) return false;
  if (ch.structure_only || ch.config?.structure_only) return false;
  if (ch.has_draft) return true;
  return ["awaiting_confirm", "needs_human"].includes(String(ch.status || ""));
}

function chapterNotStarted(ch) {
  if (!ch?.editable || ch?.structure_only || ch?.config?.structure_only) return false;
  if (ch?.has_draft || (ch?.steps || []).length) return false;
  return String(ch?.status || "") === "pending";
}

function isLatestProgressMessage(msgIndex) {
  const list = messages.value || [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const cards = list[i]?.cards || [];
    if (cards.some((c) => c?.type === "progress")) return i === msgIndex;
  }
  return false;
}

function focusEditableChapter(card) {
  const chapters = card?.payload?.chapters || [];
  const focus = String(card?.payload?.focus_chapter_key || "");
  if (focus) {
    const hit = chapters.find((ch) => ch?.key === focus);
    if (hit) return hit;
  }
  const running = chapters.find((ch) => ch?.is_running);
  if (running) return running;
  const needsReview = chapters.filter((ch) => {
    const st = String(ch?.status || "");
    return (st === "awaiting_confirm" || st === "needs_human") && canConfirmChapter(ch);
  });
  if (needsReview.length) return needsReview[needsReview.length - 1];
  return (
    chapters.find((ch) => ch?.editable && canConfirmChapter(ch)) ||
    chapters.find((ch) => ch?.editable) ||
    null
  );
}

const REDUNDANT_PROCESS_RE =
  /(本章写完了|确认后我再写下章|整理确认面板|进度在这儿|各章都跑过一轮|总控\s*Agent|对话总控|安排具体步骤|重试本章写作|决定调用哪些步骤|先理解你的需求|联系写作助手|步骤已安排)/i;

function isControlChromeProcess(message) {
  return REDUNDANT_PROCESS_RE.test(String(message || ""));
}

function formatChainDebugDetail(step) {
  try {
    const d = step?.detail && typeof step.detail === "object" ? step.detail : {};
    return JSON.stringify(d, null, 2);
  } catch {
    return String(step?.message || "");
  }
}

function visibleProcessSteps(item) {
  const steps = item?.steps || [];
  if (!steps.length) return [];
  return steps.filter((s) => {
    if (s?._writeProgress) return true;
    const msg = String(s?.message || "");
    if (TECH_PROCESS_RE.test(msg)) return false;
    const isOutlineProgress = isOutlineExtractProgressMessage(msg);
    const isStudyProgress = isStudyProgressMessage(msg);
    // 写作过程行（全角缩进 / ▶✓ / 检索·写作·检查）进虚线框
    if (
      WRITE_PROCESS_CHAT_RE.test(msg) ||
      /^[　]/.test(msg) ||
      /写作要点|章节说明|补充提示|重写完成|写作完成|写作结果/.test(msg)
    ) {
      return !TECH_PROCESS_RE.test(msg);
    }
    if ((s?.kind === "step" || s?.kind === "chapter") && !isStudyProgress && !isOutlineProgress) {
      return false;
    }
    if (s?.kind === "chain") return import.meta.env.DEV;
    if (isControlChromeProcess(msg)) return false;
    return !REDUNDANT_PROCESS_RE.test(msg);
  });
}

/** 写作过程追加进气泡虚线框（边跑边出，不整段重刷） */
function appendWriteProgress(msgRef, message, kind = "process", extra = null) {
  if (!msgRef || !message) return;
  pushProcess(msgRef, String(message).trim(), kind, {
    _writeProgress: true,
    ...(extra && typeof extra === "object" ? extra : {}),
  });
  // 替换数组，确保气泡过程条立刻重绘
  msgRef.steps = [...(msgRef.steps || [])];
}

function findProgressChapterRow(msgRef, chapterKey) {
  const key = String(chapterKey || "").trim();
  if (!key || !msgRef) return null;
  const progress = (msgRef.cards || []).find((c) => c?.type === "progress");
  const chapters = progress ? progressChaptersWithLive(progress) : [];
  return chapters.find((c) => String(c?.key || "") === key) || null;
}

/** 本章开写时输出一次要点/说明（同一章不重复） */
function pushChapterWriteContext(msgRef, ch, { title = "" } = {}) {
  if (!msgRef || !ch) return;
  const key = String(ch.key || "").trim();
  if (!key) return;
  if (!msgRef._writeCtxKeys) msgRef._writeCtxKeys = new Set();
  if (msgRef._writeCtxKeys.has(key)) return;
  msgRef._writeCtxKeys.add(key);
  const name = String(title || ch.title || ch.key || "本章").trim();
  const points = chapterRunPoints(ch);
  const head = points.length
    ? `写作 · 进行中 · ${name} 按 ${points.length} 条写作要点撰写`
    : `写作 · 进行中 · ${name}`;
  appendWriteProgress(msgRef, head);
  if (points.length) {
    appendWriteProgress(msgRef, `写作要点 · ${points.length} 条`);
    points.forEach((p, i) => appendWriteProgress(msgRef, `${i + 1}. ${p}`));
  }
  const describes = chapterRunDescribes(ch);
  if (describes) {
    appendWriteProgress(
      msgRef,
      `章节说明：${describes.length > 220 ? `${describes.slice(0, 220)}…` : describes}`
    );
  }
  const aux = chapterRunAux(ch);
  if (aux) {
    appendWriteProgress(
      msgRef,
      `补充提示：${aux.length > 160 ? `${aux.slice(0, 160)}…` : aux}`
    );
  }
}

function pushChapterWriteDone(msgRef, ch, { title = "", statusLabel = "", rewrite = false, preview = "" } = {}) {
  if (!msgRef) return;
  const name = String(title || ch?.title || ch?.key || "本章").trim();
  const banner = ch ? chapterScoreBanner(ch) : null;
  if (banner?.retry) {
    appendWriteProgress(
      msgRef,
      banner.score ? `${banner.retry} · ${banner.score}` : banner.retry
    );
  } else if (banner?.score) {
    appendWriteProgress(msgRef, banner.score);
  }
  const raw = String(preview || ch?.draft_preview || ch?.draft_text || "").trim();
  if (raw) {
    appendWriteProgress(msgRef, `写作结果 · 「${name}」`, "draft", {
      html: toDisplayHtml(raw) || raw,
    });
  }
  const done = statusLabel ? `「${name}」写作完成 · ${statusLabel}` : `「${name}」写作完成`;
  appendWriteProgress(msgRef, done);
}

/** 仅会话回放：把历史进度卡压成过程行（实时写作勿用，会整段蹦出） */
function syncWritingProgressToBubble(msgRef) {
  if (!msgRef) return;
  const progress = (msgRef.cards || []).find((c) => c?.type === "progress");
  const chapters = progress ? progressChaptersWithLive(progress) : [];
  const kept = (msgRef.steps || []).filter((s) => !s?._writeProgress);
  if (!chapters.length) {
    msgRef.steps = kept;
    return;
  }
  // 已有增量写作过程则不覆盖
  if ((msgRef.steps || []).some((s) => s?._writeProgress)) return;
  const lines = [];
  for (const ch of chapters) {
    const title = String(ch.title || ch.key || "本章").trim();
    const points = chapterRunPoints(ch);
    lines.push(
      points.length
        ? `写作 · 已写作 · ${title} 按 ${points.length} 条写作要点撰写`
        : `写作 · 已写作 · ${title}`
    );
    if (points.length) {
      lines.push(`写作要点 · ${points.length} 条`);
      points.forEach((p, i) => lines.push(`${i + 1}. ${p}`));
    }
    const banner = chapterScoreBanner(ch);
    if (banner?.retry) {
      lines.push(banner.score ? `${banner.retry} · ${banner.score}` : banner.retry);
    }
    const preview = String(ch.draft_preview || ch.draft_text || "").trim();
    if (preview) {
      lines.push({
        message: `写作结果 · 「${title}」`,
        kind: "draft",
        html: toDisplayHtml(preview) || preview,
      });
    }
    lines.push(`「${title}」写作完成`);
  }
  msgRef.steps = [
    ...kept,
    ...lines.map((row) =>
      typeof row === "string"
        ? { message: row, kind: "process", _writeProgress: true }
        : { kind: "process", _writeProgress: true, ...row }
    ),
  ];
}

function bubbleProcessSteps(item) {
  const streaming = !!item?.streaming;
  if ((item?.cards || []).some((c) => c?.type === "writing_supplement_check")) {
    return [];
  }
  if (/待补充|还空着|还差一处|跳过检查|配置要素还是空/.test(String(item?.text || ""))) {
    return [];
  }
  const steps = visibleProcessSteps(item);
  const studySettled = steps.some((s) =>
    /章节分析已完成|写作要点分析完成|分析失败/.test(String(s?.message || ""))
  );
  return steps.map((step) => {
    const msg = String(step?.message || "");
    const runningStudy = isStudyRunningProcessMessage(msg);
    // 分析 ▶ 行：进行中保持原样；整轮已有完成标记或回合结束再收束
    const applyDone = runningStudy
      ? studySettled
      : !streaming;
    return {
      kind: step.kind,
      text: friendlyProcessText(msg, { done: applyDone }),
      html: step.kind === "draft" ? String(step.html || "") : "",
      debug:
        isDev && step.kind === "chain" && step.detail && Object.keys(step.detail).length
          ? formatChainDebugDetail(step)
          : "",
    };
  });
}

function writingStepStatusLabel(status) {
  return (
    { running: "进行中", done: "完成", skipped: "跳过", error: "失败" }[String(status || "")] ||
    String(status || "")
  );
}

function chapterStatusFromWriteStep(step, status) {
  const st = String(status || "");
  const sp = String(step || "");
  // 写完检查通过后直接视为已写作（不再落待确认/待人工）
  if (st === "error") return "needs_human";
  if (sp === "search") return st === "done" || st === "skipped" ? "writing" : "searching";
  if (sp === "write") return st === "done" || st === "skipped" ? "checking" : "writing";
  if (sp === "check") return st === "done" || st === "skipped" ? "confirmed" : "checking";
  return "writing";
}

function isWritingPipelineEvent(event) {
  const step = String(event?.step || "").trim().toLowerCase();
  if (WRITE_PIPELINE_STEPS.has(step)) return true;
  if (event?.chapter && /search|write|check|检索|写作|检查/i.test(step || String(event?.status || ""))) {
    return true;
  }
  return false;
}

function isWritingProcessMessage(message) {
  return WRITE_PROCESS_CHAT_RE.test(String(message || ""));
}

function isOutlineExtractProgressMessage(message) {
  return /提取目录|目录提取|正在从样板|继续提取|识别章节|生成大纲|目录已|章节目录|正在提取目录|正在重新提取|解析文档|目录结构|第二轮|第一轮/.test(
    String(message || "")
  );
}

function isOutlineExtractActive(live = liveBlueprintWizard.value || {}) {
  return (
    Boolean(live.outline_extract_active) ||
    Boolean(live.trigger_outline_extract) ||
    String(live.pending_wizard_command || "").trim() === "re_extract_outline" ||
    normalizeWizardStep(live.step) === "outline"
  );
}

function isStudyStreamActive(live = liveBlueprintWizard.value || {}) {
  return (
    Boolean(live.study_stream_active) ||
    Boolean(live.trigger_begin_study) ||
    (Array.isArray(live.trigger_chapter_study) && live.trigger_chapter_study.length > 0) ||
    (Array.isArray(live.chapter_study_keys) && live.chapter_study_keys.length > 0) ||
    pageStudyInflight
  );
}

function isStudyProgressMessage(message) {
  return /分析|学习|要点|样本|蓝本向导|正在分析|分析进度|开始依据样本|已接单|待补充/.test(
    String(message || "")
  );
}

/** 分析/提取过程写入哪条助手消息：对齐蓝本 id、写作副本 id、当前文档焦点 */
function resolveProgressAssistantMessage(blueprintId = "", taskId = "") {
  const ids = new Set(
    [blueprintId, taskId, pageStudyOwnerBlueprintId, liveBlueprintWizard.value?.blueprint_id]
      .map((x) => String(x || "").trim())
      .filter(Boolean)
  );
  const live = liveBlueprintWizard.value || {};
  [live.working_task_id, live.source_blueprint_id].forEach((x) => {
    const s = String(x || "").trim();
    if (s) ids.add(s);
  });
  const focusKey = _docKeyStrip(currentFocusDocumentId());
  const list = messages.value || [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const msg = list[i];
    if (msg?.role !== "assistant") continue;
    const msgKey = _docKeyStrip(msg.document_key || msg.document_id || "");
    if (focusKey && msgKey && msgKey === focusKey) return msg;
    const cards = msg.cards || [];
    const hit = cards.some((c) => {
      if (c?.type !== "blueprint_wizard") return false;
      const p = c.payload || {};
      const cardIds = [p.blueprint_id, p.working_task_id, p.task_id]
        .map((x) => String(x || "").trim())
        .filter(Boolean);
      return cardIds.some((id) => ids.has(id));
    });
    if (hit) return msg;
  }
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i]?.role === "assistant") return list[i];
  }
  return null;
}

function pushStudyProgressLine(line, kind = "process", opts = {}) {
  const {
    epoch = null,
    sid = "",
    blueprintId = "",
    taskId = "",
    invokeHook = true,
  } = opts;
  const text = String(line || "").trim();
  if (!text) return;
  if (epoch != null && !isCurrentSessionContext(epoch, sid)) return;
  const msgRef = resolveProgressAssistantMessage(blueprintId, taskId);
  if (msgRef && !blueprintId && !taskId) {
    const cards = msgRef.cards || [];
    const hasWizard = cards.some((c) => c?.type === "blueprint_wizard");
    if (!hasWizard && /小墨|写作搭档|智能文档/.test(String(msgRef.text || ""))) {
      return;
    }
  }
  if (msgRef) {
    const steps = msgRef.steps || [];
    for (const row of steps) {
      if (row && /正在处理蓝本向导/.test(String(row.message || ""))) {
        row.kind = "process";
        row.message = "✓ 已提交分析任务";
      }
    }
    pushProcess(msgRef, text, kind === "step" ? "process" : kind);
  }
  if (isCurrentSessionContext(epoch ?? sessionEpoch, sid)) {
    blueprintWizardLog.value = [...blueprintWizardLog.value, text].slice(-40);
    if (invokeHook) onBlueprintWizardProcess(text, { skipPush: true });
    else scheduleScrollChatToBottom();
  }
}

function studyProgressLineFromChapterEvent(event = {}) {
  const key = String(event.chapter || "").trim();
  const label = studyChapterLabel(event);
  const analyzed = Number(event.study_analyzed || 0);
  const unanalyzed = Number(event.study_unanalyzed || 0);
  const total = Number(event.total || analyzed + unanalyzed || 0);
  if (total > 0) {
    return `▶ 分析进度 ${analyzed}/${total} · ${label || key || "章节"}`;
  }
  return `▶ 正在分析「${label || key || "章节"}」…`;
}

function pushStudySupplementProgressFromEvent(event = {}, opts = {}) {
  const items = Array.isArray(event.point_items) ? event.point_items : [];
  const fromPoints = !items.length && Array.isArray(event.points) ? event.points : [];
  const synthetic = fromPoints.map((text, i) => ({ id: `p${i}`, text: String(text || "") }));
  const line = studySupplementProcessMessage(items.length ? items : synthetic);
  if (!line) return;
  pushStudyProgressLine(line, "process", opts);
}

/** 提取进行中：即使卡被标 stale，仍把过程日志灌进对应蓝本向导卡 */
function wizardProcessLogForCard(card, msgIndex, cardIndex) {
  if (liveWizardForCard(card, msgIndex, cardIndex)) return blueprintWizardLog.value;
  const live = liveBlueprintWizard.value || {};
  const payload = card?.payload || {};
  if (isOutlineExtractActive(live) && liveWizardMatchesCard(payload, live)) {
    return blueprintWizardLog.value;
  }
  if (isStudyStreamActive(live) && liveWizardMatchesCard(payload, live)) {
    return blueprintWizardLog.value;
  }
  return [];
}

function resetLiveWritingChapter(taskId, chapterKey, { title = "" } = {}) {
  const tid = String(taskId || "").trim();
  const key = String(chapterKey || "").trim();
  if (!tid || !key) return;
  const next = { ...(liveWritingOverlay.value || {}) };
  const taskMap = { ...(next[tid] || {}) };
  taskMap[key] = {
    status: "writing",
    status_label: "写作中",
    steps: [],
    is_running: true,
    title: String(title || ""),
  };
  next[tid] = taskMap;
  liveWritingOverlay.value = next;
}

function upsertLiveWritingStep(taskId, chapterKey, event) {
  const tid = String(taskId || "").trim();
  const key = String(chapterKey || "").trim();
  if (!tid || !key) return;
  const step = String(event?.step || "").trim();
  const status = String(event?.status || "").trim();
  const note = String(event?.result?.note || event?.result?.reason || event?.result?.message || "")
    .trim()
    .slice(0, 240);
  const step_label = STEP_LABELS[step] || step || "步骤";
  const status_label = writingStepStatusLabel(status);
  const next = { ...(liveWritingOverlay.value || {}) };
  const taskMap = { ...(next[tid] || {}) };
  const prev = taskMap[key] || { steps: [], status: "writing", status_label: "写作中", is_running: true };
  const steps = Array.isArray(prev.steps) ? [...prev.steps] : [];
  const prevRow = steps.find((s) => String(s.step || "") === step) || {};
  const notes = Array.isArray(prevRow.notes) ? [...prevRow.notes] : [];
  if (note && notes[notes.length - 1] !== note) notes.push(note);
  const row = {
    step,
    step_label,
    status,
    status_label,
    note,
    notes: notes.slice(-24),
  };
  const idx = steps.findIndex((s) => String(s.step || "") === step);
  if (idx >= 0) steps[idx] = { ...steps[idx], ...row };
  else steps.push(row);
  const chStatus = chapterStatusFromWriteStep(step, status);
  const cards = Array.isArray(event?.result?.cards) ? event.result.cards : [];
  const evidenceFromCards = cards
    .filter((c) => c && typeof c === "object")
    .map((c, i) => ({
      id: String(c.id || `el-${i}`),
      point: String(c.point || c.source_file || "要素").trim() || "要素",
      source_file: String(c.source_file || "要素"),
      location: String(c.location || ""),
      query: String(c.query || ""),
      excerpt: String(c.excerpt || "").slice(0, 180),
      adopted: c.adopted !== false,
    }))
    .filter((c) => c.excerpt || c.point);
  const rawResults = Array.isArray(event?.result?.element_results)
    ? event.result.element_results
    : [];
  const elementResults = rawResults
    .filter((row) => row && typeof row === "object")
    .map((row, i) => ({
      id: String(row.id || `el-${i}`),
      name: String(row.name || row.id || "要素").trim() || "要素",
      kind: String(row.kind || ""),
      query: String(row.query || "").trim().slice(0, 120),
      value: String(row.value || "").trim().slice(0, 240),
      ok: Boolean(row.ok ?? String(row.value || "").trim()),
      slot_missing: Array.isArray(row.slot_missing) ? row.slot_missing.slice(0, 4) : [],
    }));
  const result = event?.result && typeof event.result === "object" ? event.result : {};
  const checkPatch = {};
  if (step === "check" && (status === "done" || status === "skipped")) {
    const score = Number(result.score || 0);
    const revRaw = Number(result.revision_count || 0);
    const action = String(result.action || "").trim();
    // 服务端已在 emit 前完成 revise +1，此处直接用 revision_count
    const revision_count = revRaw;
    checkPatch.revision_count = revision_count;
    checkPatch.max_revisions = Number(result.max_revisions || prev.max_revisions || 5);
    checkPatch.write_score_pass = Number(result.write_score_pass || prev.write_score_pass || 70);
    checkPatch.check_action = action;
    checkPatch.check_score = score;
    checkPatch.check_below_threshold = Boolean(
      result.below_threshold ?? (score > 0 && score < checkPatch.write_score_pass)
    );
    checkPatch.check_reason = String(result.reason || "").trim().slice(0, 160);
    checkPatch.rewritten = revision_count > 0;
    if (score > 0 || action) {
      const checks = Array.isArray(prev.checks) ? [...prev.checks] : [];
      checks.push({
        action,
        reason: checkPatch.check_reason,
        score,
        below_threshold: checkPatch.check_below_threshold,
      });
      checkPatch.checks = checks.slice(-3);
    }
  }
  taskMap[key] = {
    ...prev,
    steps,
    status: chStatus,
    status_label: statusLabel(chStatus) || chStatus,
    is_running: ["searching", "writing", "checking", "running"].includes(chStatus),
    ...checkPatch,
    ...(evidenceFromCards.length ? { evidence: evidenceFromCards } : {}),
    ...(elementResults.length ? { element_results: elementResults } : {}),
  };
  next[tid] = taskMap;
  liveWritingOverlay.value = next;
}

/** 仅「正在写 / 已写过」的章进进度展示（有要点未写也不出） */
function chapterWasWrittenOrWriting(ch) {
  if (!ch || ch.structure_only) return false;
  if (ch.is_running) return true;
  const st = String(ch.status || "").trim();
  if (["searching", "writing", "checking", "running"].includes(st)) return true;
  if (ch.has_draft || ch.draft_text || ch.draft_preview) return true;
  if (Number(ch.revision_count || 0) > 0 || Number(ch.check_score || 0) > 0) return true;
  if ((ch.steps || []).length || (ch.checks || []).length) return true;
  if (["confirmed", "awaiting_confirm", "needs_human"].includes(st)) {
    // 无正文/无步骤的确认态不当作本轮已写（避免整本大纲灌进进度）
    return Boolean(
      ch.has_draft ||
        ch.draft_text ||
        ch.draft_preview ||
        (ch.steps || []).length ||
        (ch.checks || []).length ||
        Number(ch.revision_count || 0) > 0
    );
  }
  return false;
}

/** 进度卡章节合入 live；只保留本轮已写/在写章 */
function progressChaptersWithLive(card) {
  const chapters = Array.isArray(card?.payload?.chapters) ? card.payload.chapters : [];
  const tid = String(card?.payload?.task_id || "").trim();
  const liveMap = tid ? liveWritingOverlay.value?.[tid] : null;
  const merged = chapters.map((ch) => {
    const live = liveMap?.[String(ch?.key || "")];
    if (!live) return ch;
    return {
      ...ch,
      status: live.status || ch.status,
      status_label: live.status_label || ch.status_label,
      steps: live.steps?.length ? live.steps : ch.steps,
      evidence: live.evidence?.length ? live.evidence : ch.evidence,
      evidence_count: live.evidence?.length || ch.evidence_count || (ch.evidence || []).length,
      element_results: live.element_results?.length
        ? live.element_results
        : ch.element_results,
      is_running: live.is_running != null ? Boolean(live.is_running) : ch.is_running,
      revision_count:
        live.revision_count != null ? live.revision_count : ch.revision_count,
      max_revisions: live.max_revisions != null ? live.max_revisions : ch.max_revisions,
      write_score_pass:
        live.write_score_pass != null ? live.write_score_pass : ch.write_score_pass,
      rewritten: live.rewritten != null ? live.rewritten : ch.rewritten,
      check_action: live.check_action || ch.check_action,
      check_score: live.check_score != null ? live.check_score : ch.check_score,
      check_below_threshold:
        live.check_below_threshold != null
          ? live.check_below_threshold
          : ch.check_below_threshold,
      check_reason: live.check_reason || ch.check_reason,
      checks: live.checks?.length ? live.checks : ch.checks,
      has_draft: Boolean(live.has_draft || ch.has_draft),
    };
  });
  // live 里正在跑的章即使 payload 尚未带 has_draft 也要露出
  const liveKeys = new Set(Object.keys(liveMap || {}));
  return merged.filter((ch) => {
    if (!ch || ch.structure_only) return false;
    if (liveKeys.has(String(ch.key || "")) && liveMap[String(ch.key || "")]?.is_running) {
      return true;
    }
    return chapterWasWrittenOrWriting(ch);
  });
}

function clearLiveWritingChapter(taskId, chapterKey) {
  const tid = String(taskId || "").trim();
  const key = String(chapterKey || "").trim();
  if (!tid || !key) return;
  const next = { ...(liveWritingOverlay.value || {}) };
  const taskMap = { ...(next[tid] || {}) };
  if (!(key in taskMap)) return;
  delete taskMap[key];
  if (Object.keys(taskMap).length) next[tid] = taskMap;
  else delete next[tid];
  liveWritingOverlay.value = next;
}

function applyLiveWritingOverlayToDocs(docs) {
  const overlay = liveWritingOverlay.value || {};
  if (!overlay || !Object.keys(overlay).length) return docs;
  const sessionWritingTid = String(taskId.value || "").trim();
  const focusKey = normalizeDocumentRegistryKey(
    String(sessionRuntime.value?.focus_document_key || docWindowActiveId.value || "").trim()
  );
  return (docs || []).map((doc) => {
    const writingTid = String(doc?.writingTaskId || "").trim();
    const tid = String(doc?.taskId || "").trim();
    const docId = String(doc?.id || "").trim();
    const isFocusDoc =
      (focusKey && (docId === focusKey || writingTid === focusKey.replace(/^task:/, ""))) ||
      (sessionWritingTid &&
        (writingTid === sessionWritingTid || tid === sessionWritingTid));
    // 一份文档一份写作副本：优先本副本 overlay；焦点蓝本行在 writingTaskId 尚未盖上时，
    // 允许用会话当前写作 tid 套 live 态（仅焦点文档，防串台）
    const candidates = writingTid
      ? [writingTid]
      : String(doc?.kind || "") === "blueprint"
        ? isFocusDoc && isWritingCopyId(sessionWritingTid)
          ? [sessionWritingTid]
          : []
        : [tid].filter(Boolean);
    let liveMap = null;
    for (const id of candidates) {
      if (overlay[id]) {
        liveMap = overlay[id];
        break;
      }
    }
    if (!liveMap) return doc;
    const chapters = (doc.chapters || []).map((ch) => {
      const live = liveMap[String(ch?.key || "")];
      if (!live) return ch;
      return {
        ...ch,
        status: live.status || ch.status,
        status_label: live.status_label || ch.status_label,
        steps: live.steps?.length ? live.steps : ch.steps,
        evidence: live.evidence?.length ? live.evidence : ch.evidence,
        evidence_count: live.evidence?.length || ch.evidence_count || (ch.evidence || []).length,
        element_results: live.element_results?.length
          ? live.element_results
          : ch.element_results,
        is_running: Boolean(live.is_running),
        revision_count:
          live.revision_count != null ? live.revision_count : ch.revision_count,
        max_revisions: live.max_revisions != null ? live.max_revisions : ch.max_revisions,
        write_score_pass:
          live.write_score_pass != null ? live.write_score_pass : ch.write_score_pass,
        rewritten: live.rewritten != null ? live.rewritten : ch.rewritten,
        check_action: live.check_action || ch.check_action,
        check_score: live.check_score != null ? live.check_score : ch.check_score,
        check_below_threshold:
          live.check_below_threshold != null
            ? live.check_below_threshold
            : ch.check_below_threshold,
        check_reason: live.check_reason || ch.check_reason,
        checks: live.checks?.length ? live.checks : ch.checks,
      };
    });
    return { ...doc, chapters };
  });
}

function normalizeDocWindowChapter(ch) {
  const cfg = ch?.config && typeof ch.config === "object" ? ch.config : {};
  const points = Array.isArray(ch?.points) && ch.points.length
    ? [...ch.points]
    : Array.isArray(cfg.points)
      ? [...cfg.points]
      : [];
  const keywords = Array.isArray(ch?.keywords) && ch.keywords.length
    ? [...ch.keywords]
    : Array.isArray(cfg.keywords)
      ? [...cfg.keywords]
      : [];
  return {
    key: ch?.key || "",
    sequence: ch?.sequence || "",
    title: ch?.title || "",
    parent: ch?.parent || "",
    parent_sequence: ch?.parent_sequence || cfg.parent_sequence || "",
    level: Number(ch?.level || cfg.level || 0) || undefined,
    status: ch?.status || "",
    status_label: ch?.status_label || "",
    steps: ch?.steps || [],
    checks: ch?.checks || [],
    evidence: ch?.evidence || [],
    evidence_count: ch?.evidence_count || (ch?.evidence || []).length,
    uncovered_points: ch?.uncovered_points || [],
    human_note: ch?.human_note || "",
    is_running: Boolean(ch?.is_running),
    editable: Boolean(ch?.editable),
    has_draft: Boolean(ch?.has_draft || ch?.draft_text || ch?.draft_preview),
    draft_text: ch?.draft_text || ch?.draft_preview || "",
    structure_only: Boolean(ch?.structure_only || cfg.structure_only),
    is_next: Boolean(ch?.is_next),
    revision_count: Number(ch?.revision_count || 0),
    max_revisions: Number(ch?.max_revisions || 5),
    write_score_pass: Number(ch?.write_score_pass || 70),
    rewritten: Boolean(ch?.rewritten),
    check_action: ch?.check_action || "",
    check_score: Number(ch?.check_score || 0),
    check_below_threshold: Boolean(ch?.check_below_threshold),
    check_reason: ch?.check_reason || "",
    element_results: Array.isArray(ch?.element_results) ? ch.element_results : [],
    // 顶层也带 points，避免文档窗合并时只看见空的 config
    points,
    keywords,
    describes: ch?.describes || cfg.describes || "",
    aux_prompt: ch?.aux_prompt || cfg.aux_prompt || "",
    reference_text: ch?.reference_text || cfg.reference_text || "",
    element_ids: Array.isArray(ch?.element_ids)
      ? [...ch.element_ids]
      : Array.isArray(cfg.element_ids)
        ? [...cfg.element_ids]
        : [],
    config: {
      points,
      keywords,
      describes: cfg.describes || ch?.describes || "",
      aux_prompt: cfg.aux_prompt || ch?.aux_prompt || "",
      reference_text: cfg.reference_text || ch?.reference_text || "",
      fixed_output: Boolean(cfg.fixed_output ?? ch?.fixed_output),
      compose: normalizeCompose(cfg.compose || ch?.compose, { fixed_output: cfg.fixed_output || ch?.fixed_output, presence: cfg.presence || ch?.presence }),
      presence: presenceForCompose(normalizeCompose(cfg.compose || ch?.compose, { fixed_output: cfg.fixed_output || ch?.fixed_output, presence: cfg.presence || ch?.presence })),
      structure_only: Boolean(cfg.structure_only || ch?.structure_only),
      element_ids: Array.isArray(cfg.element_ids)
        ? [...cfg.element_ids]
        : Array.isArray(ch?.element_ids)
          ? [...ch.element_ids]
          : [],
    },
  };
}

function latestProgressCards() {
  const list = messages.value || [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const cards = list[i]?.cards || [];
    if (cards.some((c) => c?.type === "progress")) return cards;
  }
  return [];
}

function onDocWindowFocus(payload) {
  const tid = String(payload?.taskId || "").trim();
  const key = String(payload?.chapterKey || "").trim();
  if (!tid || !key) return;
  docWindowFocusChapter.value = { taskId: tid, key };
  const wantPreviewRefresh =
    String(payload?.mainView || "") === "preview" ||
    payload?.tab === "preview" ||
    payload?.tab === "draft";
  expandOnlyChapter(tid, key, latestProgressCards(), { refreshText: wantPreviewRefresh });
  if (payload?.openConfig) {
    setChapterTab(tid, key, "config");
  } else if (payload?.tab) {
    const t = String(payload.tab || "").trim();
    if (t === "progress" || t === "draft" || t === "preview" || t === "process" || t === "text") {
      setChapterTab(tid, key, "text");
    } else if (t === "content") {
      setChapterTab(tid, key, "config");
    } else {
      setChapterTab(tid, key, t);
    }
  }
}

function onDocWindowElementsSaved(payload) {
  const tid = String(payload?.taskId || "").trim();
  if (!tid || !Array.isArray(payload?.elementNodes)) return;
  taskElementNodes.value = {
    ...taskElementNodes.value,
    [tid]: payload.elementNodes.map((item) => ({
      ...item,
      knowledge_folder_ids: [...(item.knowledge_folder_ids || [])],
      tags: [...(item.tags || [])],
    })),
  };
  bumpDocOutlineReload({ force: true });
  bumpClientConfigRevision();
}

/** 文档窗保存的项目资料根：开写时一并带入（会话侧由后端写入 freeform） */
const pendingProjectFolders = ref({ ids: [], label: "" });

function onDocWindowProjectFoldersSaved(payload) {
  pendingProjectFolders.value = {
    ids: (payload?.project_folder_ids || []).map((x) => String(x || "").trim()).filter(Boolean),
    label: String(payload?.project_folder_label || "").trim(),
  };
}

function onDocWindowConfigSaved(payload) {
  const tid = String(payload?.taskId || "").trim();
  const key = String(payload?.chapterKey || "").trim();
  const chapter = payload?.chapter;
  if (!tid || !key || !chapter) return;
  if (Array.isArray(payload?.elementNodes)) {
    onDocWindowElementsSaved({ taskId: tid, elementNodes: payload.elementNodes });
  }
  applyChapterSnapshotToSession(tid, key, chapter);

  // 文档窗配置一律静默：已 PUT 到副本，勿再 configure_chapter 刷对话
  if (!payload?.quiet) ElMessage.success("章节配置已保存");
  if (payload?.alreadyPersisted) {
    bumpDocOutlineReload({ force: true });
    bumpClientConfigRevision();
  }
  schedulePersistUiState(true);
}

/** 文档窗 / 关联要素落盘后：会话 chapterEdits、文档预览章、进度卡与本章配置对齐 */
function applyChapterSnapshotToSession(tid, key, chapter) {
  const editKey = chapterEditKey(tid, key);
  const points = Array.isArray(chapter.points) ? chapter.points : [];
  const point_items = Array.isArray(chapter.point_items) ? chapter.point_items : [];
  const cfg =
    chapter.config && typeof chapter.config === "object" ? chapter.config : {};
  const prev = chapterEdits.value[editKey] || {};
  const nextEdit = {
    ...prev,
    text: prev.text || "",
    sequence: String(chapter.sequence || "").trim(),
    title: String(chapter.title || key).trim(),
    pointsText:
      pointsTextFromChapter(chapter, chapter, cfg) || points.join("\n"),
    point_items: point_items.length ? [...point_items] : [...(prev.point_items || [])],
    describes: chapter.describes || cfg.describes || "",
    aux_prompt: chapter.aux_prompt || cfg.aux_prompt || "",
    reference_text: chapter.reference_text || cfg.reference_text || "",
    fixed_output: Boolean(chapter.fixed_output),
    compose: normalizeCompose(chapter.compose || cfg.compose, { fixed_output: chapter.fixed_output, presence: chapter.presence || cfg.presence }),
    presence: presenceForCompose(normalizeCompose(chapter.compose || cfg.compose, { fixed_output: chapter.fixed_output, presence: chapter.presence || cfg.presence })),
    structure_only: Boolean(chapter.structure_only),
    element_ids: unionElementIds(chapter.element_ids, cfg.element_ids, prev.element_ids),
  };
  chapterEdits.value = {
    ...chapterEdits.value,
    [editKey]: nextEdit,
  };
  const patchDocChapter = (list) => {
    if (!Array.isArray(list)) return;
    const row = list.find((ch) => String(ch?.key || "") === key);
    if (!row) return;
    row.sequence = chapter.sequence || row.sequence;
    row.title = chapter.title || row.title;
    row.structure_only = Boolean(chapter.structure_only);
    row.points = [...points];
    row.element_ids = [...nextEdit.element_ids];
    if (point_items.length) row.point_items = [...point_items];
    row.describes = nextEdit.describes;
    row.aux_prompt = nextEdit.aux_prompt;
    row.reference_text = nextEdit.reference_text;
    row.fixed_output = Boolean(chapter.fixed_output);
    row.presence = nextEdit.presence;
    row.compose = nextEdit.compose;
    row.config = {
      ...(row.config || {}),
      points: [...points],
      ...(point_items.length ? { point_items: [...point_items] } : {}),
      describes: nextEdit.describes,
      aux_prompt: nextEdit.aux_prompt,
      reference_text: nextEdit.reference_text,
      fixed_output: Boolean(chapter.fixed_output),
      presence: nextEdit.presence,
      compose: nextEdit.compose,
      structure_only: Boolean(chapter.structure_only),
      element_ids: [...nextEdit.element_ids],
    };
  };
  for (const doc of conversationDocuments.value || []) {
    const docTid = String(doc?.writingTaskId || doc?.taskId || "").trim();
    if (docTid === tid || String(doc?.taskId || "") === tid) {
      patchDocChapter(doc.chapters);
    }
  }
  const progress = (messages.value || [])
    .flatMap((m) => m.cards || [])
    .reverse()
    .find(
      (c) =>
        c?.type === "progress" &&
        String(c?.payload?.task_id || "").trim() === tid
    );
  const chapters = progress?.payload?.chapters || [];
  const hit = chapters.find((ch) => ch?.key === key);
  if (hit) patchDocChapter([hit]);
  patchChapterElementIdsEverywhere(tid, key, nextEdit.element_ids, {
    point_items: nextEdit.point_items,
    points,
  });
}

const docWindowFocusChapter = ref({ taskId: "", key: "" });

function resolveDocWindowDraftTaskIds(preferredTid = "") {
  const focus = String(preferredTid || docWindowFocusChapter.value.taskId || "").trim();
  const active = (conversationDocuments.value || []).find(
    (d) => String(d.id || "") === String(docWindowActiveId.value || "")
  );
  const writing = String(active?.writingTaskId || "").trim();
  const task = String(active?.taskId || active?.blueprintId || "").trim();
  return [...new Set([focus, writing, task].filter(Boolean))];
}

const docWindowDraftText = computed(() => {
  const key = docWindowFocusChapter.value.key;
  if (!key) return "";
  const progress = latestProgressCards().find((c) => c?.type === "progress");
  const ch = (progress?.payload?.chapters || []).find((item) => item?.key === key);
  for (const tid of resolveDocWindowDraftTaskIds()) {
    if (ch) {
      const text = ensureChapterEdit(tid, ch).text || "";
      if (text.trim()) return text;
    }
    const edit = chapterEdits.value[chapterEditKey(tid, key)];
    if (edit?.text?.trim()) return edit.text;
  }
  if (ch) {
    const tid = resolveDocWindowDraftTaskIds()[0];
    return tid ? ensureChapterEdit(tid, ch).text || "" : "";
  }
  return "";
});

function onDocWindowDraftText(text) {
  const key = docWindowFocusChapter.value.key;
  if (!key) return;
  const tids = resolveDocWindowDraftTaskIds();
  const tid = tids[0];
  if (!tid) return;
  const progress = latestProgressCards().find((c) => c?.type === "progress");
  const ch = (progress?.payload?.chapters || []).find((item) => item?.key === key) || {
    key,
    draft_text: "",
  };
  const edit = ensureChapterEdit(tid, ch);
  edit.text = String(text || "");
  markChapterTextTouched(tid, key);
  // 同步到关联写作/蓝本 id 的编辑缓存（不标记 touched，避免挡住服务端草稿刷新）
  for (const other of tids.slice(1)) {
    const otherEdit = ensureChapterEdit(other, ch);
    otherEdit.text = String(text || "");
  }
}

async function onDocWindowWizardAction(payload) {
  const p = payload || {};
  // 库蓝本 id 仅作模版身份；学习/落盘用文档副本 w*
  const bid = String(p.blueprint_id || "").trim();
  const wid = String(
    p.working_task_id || p.writingTaskId || p.writing_task_id || ""
  ).trim();
  const workTid = wid && /^w/i.test(wid) ? wid : String(p.task_id || "").trim();
  const identityBid = bid || (workTid && !/^w/i.test(workTid) ? workTid : "");
  if (!identityBid && !workTid) {
    ElMessage.warning("当前文档没有蓝本/副本 id，无法执行向导操作");
    return;
  }
  const cmd = String(p.wizard_command || "").trim();
  const locateKey = String(
    p.locate_chapter_key || (p.chapter_keys || [])[0] || ""
  ).trim();
  const actionPayload = {
    ...p,
    blueprint_id: identityBid || bid,
    task_id: workTid || identityBid,
    working_task_id: workTid && /^w/i.test(workTid) ? workTid : undefined,
  };
  // 小窗确认目录：立刻写入 live（仅当操作的是当前 live 同蓝本时），避免串改其它蓝本
  const liveBid = String(liveBlueprintWizard.value?.blueprint_id || "").trim();
  if (p.outline_confirmed === true && (!liveBid || liveBid === identityBid)) {
    liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, {
      blueprint_id: identityBid,
      working_task_id: actionPayload.working_task_id || liveBlueprintWizard.value?.working_task_id,
      outline_confirmed: true,
      // 确认后默认进入分析步，避免 live 停在 outline_confirm
      step: String(p.step || "study"),
      trigger_begin_study: false,
    });
  } else if (p.outline_confirmed === false && (!liveBid || liveBid === identityBid)) {
    liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, {
      blueprint_id: identityBid,
      working_task_id: actionPayload.working_task_id || liveBlueprintWizard.value?.working_task_id,
      outline_confirmed: false,
      step: String(p.step || liveBlueprintWizard.value?.step || "outline"),
    });
  }
  if (
    locateKey &&
    (cmd === "go_study" || cmd === "re_study_chapters" || p.step === "study")
  ) {
    driveDocWindow("analyze", {
      blueprintId: identityBid,
      taskId: actionPayload.working_task_id || workTid || identityBid,
      chapterKey: locateKey,
      tab: "config",
    });
  }
  const studyCmd = cmd === "go_study" || cmd === "re_study_chapters";
  if (studyCmd) {
    stashPendingStudySource({
      method: p.study_method || p.study_source || "",
      text: p.study_text || p.text || "",
    });
    const method = String(p.study_method || pendingStudySource.method || "").trim();
    const preparing =
      method === "source"
        ? "正在按原文准备分析…"
        : method === "auto"
          ? "正在准备自动分析…"
          : "正在准备分析…";
    // 只亮「准备分析」锁；切勿提前 study_stream_active=true，否则页面兜底以为已在学而直接 return，造成永久锁死
    setDocWindowStudyBusy(true, preparing);
    liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, {
      blueprint_id: identityBid,
      working_task_id: actionPayload.working_task_id || liveBlueprintWizard.value?.working_task_id,
      step: "study",
      ...(method ? { study_method: method } : {}),
    });
  }
  const latest = findLatestBlueprintWizard();
  try {
    if (latest) {
      await onBlueprintWizard(actionPayload, latest.msgIndex, latest.cardIndex);
    } else {
      await sendAction("blueprint_wizard", actionPayload);
    }
  } finally {
    if (studyCmd) {
      // freeform 路径可能吞掉 done 里的 live：再兜底 kick 一次
      const live = liveBlueprintWizard.value || {};
      const hasTrigger =
        live.trigger_begin_study === true ||
        (Array.isArray(live.trigger_chapter_study) && live.trigger_chapter_study.length > 0);
      // 仅在已选定分析方式时兜底挂 trigger；方式澄清卡回合不得用勾选章抢跑
      if (hasTrigger || (cmd === "re_study_chapters" && String(p.study_method || "").trim())) {
        if (
          cmd === "re_study_chapters" &&
          Array.isArray(p.chapter_keys) &&
          p.chapter_keys.length &&
          !(Array.isArray(live.trigger_chapter_study) && live.trigger_chapter_study.length)
        ) {
          liveBlueprintWizard.value = mergeWizardPayload(live, {
            trigger_chapter_study: p.chapter_keys.map((k) => String(k || "").trim()).filter(Boolean),
            chapter_study_keys: p.chapter_keys.map((k) => String(k || "").trim()).filter(Boolean),
            step: "study",
            ...(String(p.study_method || "").trim()
              ? { study_method: String(p.study_method).trim() }
              : {}),
          });
        }
        if (
          hasTrigger ||
          (Array.isArray(liveBlueprintWizard.value?.trigger_chapter_study) &&
            liveBlueprintWizard.value.trigger_chapter_study.length) ||
          liveBlueprintWizard.value?.trigger_begin_study === true
        ) {
          schedulePageStudyKickoff(80, { arm: true });
        }
      }
    }
    if (
      ["go_study", "re_study_chapters", "go_outline_confirm", "outline_confirm"].includes(cmd) ||
      ["outline_confirm", "study", "study_done"].includes(String(p.step || "")) ||
      p.outline_confirmed === true
    ) {
      bumpDocOutlineReload({ force: true });
    }
  }
}

async function onDocWindowWriteAction(payload) {
  const action = String(payload?.action || "").trim();
  const blueprintId = String(payload?.blueprintId || payload?.taskId || "").trim();
  const writingTid = String(payload?.writingTaskId || "").trim();
  // 重试/写作/确认必须打到写作 clone：要素检索与资料包都在 clone 上；蓝本 id 仅作目录侧
  const writeBound = new Set([
    "retry",
    "save_and_retry",
    "retry_search",
    "confirm",
    "configure",
    "start_writing",
    "pause_writing",
    "associate_elements",
  ]);
  const tid = String(
    (writeBound.has(action) && writingTid ? writingTid : "") ||
      payload?.taskId ||
      writingTid ||
      ""
  ).trim();
  const key = String(payload?.chapterKey || "").trim();
  if (!action) return;
  if (action === "open_element_library") {
    navigateTo?.("elements");
    return;
  }
  if (action === "associate_element_knowledge") {
    ElMessage.info("关联知识库已下线；请用要素「文件」上传并解析，或打开「资料库」管理资料包");
    return;
  }
  if (action === "open_knowledge" || action === "open_materials_library") {
    navigateTo?.("materials");
    return;
  }
  // 文档窗关联：本地弹面板 + 静默落盘，不走对话流、不刷气泡
  if (action === "associate_elements") {
    if (!tid) {
      ElMessage.warning("还没有文档任务，无法关联要素");
      return;
    }
    const fromPayload =
      payload?.chapter && typeof payload.chapter === "object"
        ? { ...payload.chapter, key: String(payload.chapter.key || key || "").trim() }
        : null;
    const chapter =
      (fromPayload?.key ? fromPayload : null) ||
      (key
        ? {
            key,
            sequence: String(payload?.chapter?.sequence || "").trim(),
            title: String(payload?.chapter?.title || key).trim(),
          }
        : null);
    if (!chapter?.key) {
      ElMessage.warning("请先选择章节");
      return;
    }
    const isPointAssociate =
      payload?.pointItemId != null ||
      payload?.pointIndex != null ||
      String(payload?.associateScope || "").trim() === "point";
    if (!isPointAssociate) {
      associatePointContext.value = null;
    }
    const pointSnapshot = Array.isArray(payload?.pointItems)
      ? payload.pointItems.map((row) => ({ ...row }))
      : [];
    associatePointContext.value =
      isPointAssociate &&
      (payload?.pointItemId || payload?.pointIndex != null || payload?.pointIndex === 0)
        ? {
            id: String(payload.pointItemId || "").trim(),
            index: Number(payload.pointIndex),
            slot: String(payload.pointSlot || "").trim(),
            pointItems: pointSnapshot,
          }
        : null;
    ensureChapterEdit(tid, chapter);
    if (pointSnapshot.length) {
      const editKey = chapterEditKey(tid, chapter.key);
      const edit = chapterEdits.value[editKey];
      if (edit) {
        edit.point_items = pointSnapshot;
        edit.pointsText = pointsFromPointItems(pointSnapshot).join("\n");
      }
    }
    await openChapterElementAssociate(tid, chapter);
    driveDocWindow("elements_associated", {
      taskId: tid,
      chapterKey: chapter.key,
      tab: "config",
    });
    return;
  }
  if (!tid) return;
  const progress = latestProgressCards().find((c) => c?.type === "progress");
  const progressPayload = progress?.payload || { task_id: tid, chapters: [] };
  const fromProgress = (progressPayload.chapters || []).find((item) => item?.key === key) || null;
  const fromPayload =
    payload?.chapter && typeof payload.chapter === "object" ? payload.chapter : null;
  const mergedKey = String(key || fromPayload?.key || fromProgress?.key || "").trim();
  const ch = mergedKey
    ? {
        ...(fromProgress || {}),
        ...(fromPayload || {}),
        key: mergedKey,
        sequence: String(fromPayload?.sequence || fromProgress?.sequence || "").trim(),
        title: String(fromPayload?.title || fromProgress?.title || "").trim(),
      }
    : null;
  if (action === "start_writing") {
    armDocWindowAutoOpen();
    const chapterKeys = (payload?.chapterKeys || payload?.chapter_keys || [])
      .map((k) => String(k || "").trim())
      .filter(Boolean);
    const focusKey =
      key ||
      chapterKeys[0] ||
      String(progressPayload.focus_chapter_key || "").trim() ||
      String(
        (progressPayload.chapters || []).find((c) => c?.is_running || c?.is_next)?.key || ""
      ).trim() ||
      String((progressPayload.chapter_keys || [])[0] || "").trim();
    // 开写前待补充检查未通过时勿切预览；等 SSE chapter_start / 无 supplement 卡再定位
    const projectFolderIds = (
      payload?.project_folder_ids?.length
        ? payload.project_folder_ids
        : pendingProjectFolders.value.ids || []
    )
      .map((x) => String(x || "").trim())
      .filter(Boolean);
    const scopedKeys = chapterKeys.length
      ? chapterKeys
      : key
        ? [key]
        : (progressPayload.chapter_keys || []).map((k) => String(k || "").trim()).filter(Boolean);
    const paceRaw = String(payload?.writing_pace || payload?.pace || "").trim();
    await sendAction(
      "start_writing",
      {
        task_id: tid || progressPayload.task_id || "",
        ...(paceRaw ? { writing_pace: paceRaw } : {}),
        // 禁止把 progress 卡整包章节列表带回去当「写全部」；只传勾选/焦点章
        chapter_keys: scopedKeys,
        focus_chapter_key: focusKey || progressPayload.focus_chapter_key || "",
        ...(scopedKeys.length <= 1
          ? {
              chapter_key: focusKey || key,
              sequence: String(payload?.chapter?.sequence || "").trim(),
              title: String(payload?.chapter?.title || "").trim(),
              chapter_title: String(payload?.chapter?.title || "").trim(),
            }
          : {}),
        ...(projectFolderIds.length
          ? {
              project_folder_ids: projectFolderIds,
              project_folder_label:
                String(payload?.project_folder_label || "").trim() ||
                pendingProjectFolders.value.label ||
                "",
            }
          : {}),
        ...(payload?.from_doc_window || payload?.already_persisted
          ? {
              already_persisted: Boolean(payload?.already_persisted),
              from_doc_window: Boolean(payload?.from_doc_window),
            }
          : {}),
        ...(payload?.chapterConfig && typeof payload.chapterConfig === "object"
          ? payload.chapterConfig
          : {}),
      },
      { force: true }
    );
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "write_analyzed_only") {
    const bid = String(blueprintId || tid || "").trim();
    if (!bid) {
      ElMessage.warning("还没有蓝本，无法先写已分析章节");
      return;
    }
    const chapterKeys = (payload?.chapterKeys || payload?.chapter_keys || [])
      .map((k) => String(k || "").trim())
      .filter(Boolean);
    const focusKey =
      key ||
      chapterKeys[0] ||
      String(progressPayload.focus_chapter_key || "").trim() ||
      String(
        (progressPayload.chapters || []).find((c) => c?.is_next || c?.is_running)?.key || ""
      ).trim();
    // 小窗已静默落盘，不再单独 configure_chapter
    if (focusKey) {
      driveDocWindow("write_running", {
        taskId: tid || bid,
        blueprintId: bid,
        chapterKey: focusKey,
        mainView: "chapter",
        tab: "content",
      });
    }
    const projectFolderIds = (
      payload?.project_folder_ids?.length
        ? payload.project_folder_ids
        : pendingProjectFolders.value.ids || []
    )
      .map((x) => String(x || "").trim())
      .filter(Boolean);
    await sendAction("write_analyzed_only", {
      blueprint_id: bid,
      ...(String(payload?.writing_pace || payload?.pace || "").trim()
        ? { writing_pace: String(payload?.writing_pace || payload?.pace || "").trim() }
        : {}),
      ...(chapterKeys.length ? { chapter_keys: chapterKeys } : {}),
      ...(focusKey ? { focus_chapter_key: focusKey, chapter_key: focusKey } : {}),
      sequence: String(payload?.chapter?.sequence || "").trim(),
      title: String(payload?.chapter?.title || "").trim(),
      ...(projectFolderIds.length
        ? {
            project_folder_ids: projectFolderIds,
            project_folder_label:
              String(payload?.project_folder_label || "").trim() ||
              pendingProjectFolders.value.label ||
              "",
          }
        : {}),
    });
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "pause_writing") {
    await sendAction("pause_writing", {});
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "confirm_all") {
    await confirmAllChapters(progressPayload);
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "confirm_freeform_outline") {
    armDocWindowAutoOpen();
    const chapterKeys = (payload?.chapterKeys || payload?.chapter_keys || [])
      .map((k) => String(k || "").trim())
      .filter(Boolean);
    await sendAction("submit_outline", {
      task_id: tid,
      confirm: true,
      message: "确认大纲并继续",
      ...(chapterKeys.length ? { chapter_keys: chapterKeys } : {}),
    });
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "regenerate_freeform_outline") {
    armDocWindowAutoOpen();
    const regenTid = String(payload?.taskId || payload?.task_id || tid || "").trim();
    if (!regenTid) {
      ElMessage.warning("当前没有可重新生成的大纲任务");
      return;
    }
    const constraint = String(
      sessionRuntime.value?.workflows?.freeform?.outline_constraints ||
        sessionRuntime.value?.outline_constraints ||
        ""
    ).trim();
    let lastUser = "";
    for (let i = (messages.value || []).length - 1; i >= 0; i -= 1) {
      const m = messages.value[i];
      if (m?.role === "user" && String(m.text || "").trim()) {
        lastUser = String(m.text || "").trim();
        break;
      }
    }
    const revision = constraint || lastUser;
    await sendAction("submit_outline", {
      task_id: regenTid,
      regenerate: true,
      outline_text: "",
      revision_instructions: revision,
      message: revision,
    });
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "save_freeform_as_blueprint") {
    const saveTid =
      String(payload?.taskId || payload?.task_id || tid || composerSaveBlueprint.value?.task_id || "").trim();
    if (!saveTid) {
      ElMessage.warning("请先确认大纲后再收录到蓝本库");
      return;
    }
    const ok = await collectBlueprintWithMeta(saveTid, {
      title: payload?.title || composerSaveBlueprint.value?.suggested_title || "",
      category: payload?.category || "",
      tags: payload?.tags || [],
    });
    if (ok) await softSyncSessionAfterWizardAction();
    return;
  }
  if (action === "sync_document_to_blueprint_library") {
    const syncTid = String(payload?.taskId || payload?.task_id || tid || taskId.value || "").trim();
    await syncBlueprintToLibrary(syncTid);
    return;
  }
  if (action === "generate_freeform_points") {
    armDocWindowAutoOpen();
    const chapterKeys = (payload?.chapterKeys || payload?.chapter_keys || [])
      .map((k) => String(k || "").trim())
      .filter(Boolean);
    await sendAction("generate_freeform_points", {
      task_id: tid,
      ...(chapterKeys.length ? { chapter_keys: chapterKeys } : {}),
      ...(key ? { focus_chapter_key: key, chapter_key: key } : {}),
      sequence: String(payload?.chapter?.sequence || "").trim(),
      title: String(payload?.chapter?.title || "").trim(),
    });
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (!ch?.key) {
    ElMessage.warning("请先选择章节");
    return;
  }
  if (action === "generate_chapter_boilerplate") {
    await sendAction("generate_chapter_boilerplate", {
      task_id: tid,
      chapter_key: ch.key,
      sequence: String(ch.sequence || "").trim(),
      title: String(ch.title || "").trim(),
      chapter_title: String(ch.title || "").trim(),
      source_text: payload?.source_text || "",
      message: payload?.message || "",
    });
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "generate_chapter_describes") {
    await sendAction("generate_chapter_describes", {
      task_id: tid,
      chapter_key: ch.key,
      sequence: String(ch.sequence || "").trim(),
      title: String(ch.title || "").trim(),
      chapter_title: String(ch.title || "").trim(),
      point_items: payload?.point_items || [],
    });
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "configure") {
    await saveChapterConfig(tid, ch);
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "retry" || action === "save_and_retry") {
    // 小窗已静默落盘；会话指令：已写作→保存并重写；未写作→保存并开始写作
    const edit = chapterEdits.value[chapterEditKey(tid, ch.key)];
    const editPlain = String(edit?.text || "")
      .replace(/<[^>]+>/g, " ")
      .trim();
    const written = Boolean(
      payload?.writeMode === "rewrite" ||
        action === "save_and_retry" ||
        ch.has_draft ||
        ch.draft_text ||
        ch.draft_preview ||
        editPlain ||
        ["confirmed", "awaiting_confirm"].includes(String(ch.status || ""))
    );
    const writeMode =
      payload?.writeMode || (written ? "rewrite" : "start");
    await retryChapter(tid, ch, {
      writeMode,
      alreadyPersisted: Boolean(payload?.already_persisted || payload?.from_doc_window),
      chapterConfig: payload?.chapterConfig,
    });
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "retry_search") {
    await retrySearchChapter(tid, ch);
    bumpDocOutlineReload({ force: true });
    return;
  }
  if (action === "confirm") {
    await confirmOneChapter(progressPayload, ch);
    bumpDocOutlineReload({ force: true });
    return;
  }
}

function expandOnlyChapter(taskId, chapterKey, cards, { refreshText = true } = {}) {
  if (!taskId || !chapterKey) return;
  const progress = (cards || []).find((c) => c?.type === "progress");
  const chapters = progress?.payload?.chapters || [];
  const nextOpen = { ...openChapters.value };
  for (const item of chapters) {
    if (!item?.key) continue;
    const k = chapterEditKey(taskId, item.key);
    nextOpen[k] = item.key === chapterKey;
  }
  nextOpen[chapterEditKey(taskId, chapterKey)] = true;
  openChapters.value = nextOpen;
  activeRunChapterKey.value = chapterKey;
  const ch =
    chapters.find((item) => item?.key === chapterKey) ||
    (progress?.payload?.chapters || []).find((item) => item?.key === chapterKey);
  if (ch && refreshText) {
    ensureChapterEdit(taskId, ch, { refreshText: true });
    if (canConfirmChapter(ch)) {
      setChapterTab(taskId, chapterKey, "text");
    }
  }
}

function openFocusChapter(cards, preferChapterKey = "") {
  const progress = (cards || []).find((c) => c?.type === "progress");
  const task = progress?.payload?.task_id;
  const chapters = progress?.payload?.chapters || [];
  const prefer = String(preferChapterKey || "");
  if (task && prefer && chapters.some((ch) => ch?.key === prefer)) {
    expandOnlyChapter(task, prefer, cards, { refreshText: false });
    return;
  }
  const focus =
    progress?.payload?.focus_chapter_key ||
    chapters.find((ch) => ch?.is_running)?.key ||
    "";
  if (!task || !focus) return;
  expandOnlyChapter(task, focus, cards, { refreshText: false });
  scheduleScrollChatToBottom(true);
}

function getChapterTab(taskId, chapterKey) {
  return chapterTabs.value[chapterEditKey(taskId, chapterKey)] || "text";
}

function setChapterTab(taskId, chapterKey, name) {
  chapterTabs.value = {
    ...chapterTabs.value,
    [chapterEditKey(taskId, chapterKey)]: name || "text",
  };
}

function markChapterTextTouched(taskId, chapterKey) {
  const key = chapterEditKey(taskId, chapterKey);
  const edit = chapterEdits.value[key];
  if (edit) edit._touched = true;
}

function unionElementIds(...candidates) {
  const out = [];
  const seen = new Set();
  for (const raw of candidates) {
    if (!Array.isArray(raw)) continue;
    for (const id of raw) {
      const s = String(id || "").trim();
      if (!s || seen.has(s)) continue;
      seen.add(s);
      out.push(s);
    }
  }
  return out;
}

function ensureChapterEdit(taskId, ch, { refreshText = false } = {}) {
  const key = chapterEditKey(taskId, ch?.key);
  const raw = ch?.draft_text || ch?.draft_preview || "";
  const html = toDisplayHtml(raw) || raw;
  if (!chapterEdits.value[key]) {
    const cfg = ch?.config || {};
    chapterEdits.value = {
      ...chapterEdits.value,
      [key]: {
        text: html,
        sequence: String(ch?.sequence || cfg.sequence || "").trim(),
        title: String(ch?.title || cfg.title || ch?.key || "").trim(),
        pointsText: pointsTextFromChapter(null, ch, cfg) || (cfg.points || []).join("\n"),
        point_items: Array.isArray(ch?.point_items)
          ? [...ch.point_items]
          : Array.isArray(cfg.point_items)
            ? [...cfg.point_items]
            : [],
        describes: cfg.describes || "",
        aux_prompt: cfg.aux_prompt || "",
        reference_text: cfg.reference_text || "",
        fixed_output: Boolean(cfg.fixed_output),
        compose: normalizeCompose(cfg.compose, { fixed_output: cfg.fixed_output, presence: cfg.presence }),
        presence: presenceForCompose(normalizeCompose(cfg.compose, { fixed_output: cfg.fixed_output, presence: cfg.presence })),
        structure_only: Boolean(cfg.structure_only || ch?.structure_only),
        element_ids: unionElementIds(cfg.element_ids, ch?.element_ids),
      },
    };
  } else if (refreshText && !chapterEdits.value[key]._touched) {
    if (html.trim()) chapterEdits.value[key].text = html;
  } else if (!String(chapterEdits.value[key].text || "").trim() && html.trim()) {
    chapterEdits.value[key].text = html;
  }
  const edit = chapterEdits.value[key];
  if (edit && edit.structure_only == null) {
    edit.structure_only = Boolean(ch?.config?.structure_only || ch?.structure_only);
  }
  if (edit) {
    edit.element_ids = unionElementIds(
      edit.element_ids,
      ch?.element_ids,
      ch?.config?.element_ids
    );
  }
  return edit;
}

function configPayload(taskId, ch) {
  const edit = ensureChapterEdit(taskId, ch);
  let elementIds = Array.isArray(edit.element_ids) ? [...edit.element_ids] : [];
  // 写作 clone 与蓝本要素/关联可能只落在一侧：重试前互相回落，避免空检索退回「按要点写」
  if (!elementIds.length) {
    const docs = conversationDocuments.value || [];
    const tid = String(taskId || "").trim();
    const hit =
      docs.find((d) => String(d.writingTaskId || "").trim() === tid) ||
      docs.find((d) => String(d.taskId || "").trim() === tid) ||
      null;
    const twin = String(
      (hit && String(hit.writingTaskId || "").trim() === tid
        ? hit.blueprintId || hit.taskId
        : hit?.writingTaskId) || ""
    ).trim();
    if (twin && twin !== tid) {
      const twinEdit = chapterEdits.value[chapterEditKey(twin, ch.key)];
      if (Array.isArray(twinEdit?.element_ids) && twinEdit.element_ids.length) {
        elementIds = [...twinEdit.element_ids];
      } else if (Array.isArray(ch?.element_ids) && ch.element_ids.length) {
        elementIds = [...ch.element_ids];
      } else if (Array.isArray(ch?.config?.element_ids) && ch.config.element_ids.length) {
        elementIds = [...ch.config.element_ids];
      }
    }
  }
  const pointsLines = String(edit.pointsText || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const point_items = syncPointItemsFromPoints(pointsLines, edit.point_items || []);
  const payload = {
    task_id: taskId,
    chapter_key: ch.key,
    sequence: edit.sequence,
    title: edit.title,
    points_text: edit.pointsText,
    point_items,
    describes: edit.describes,
    aux_prompt: edit.aux_prompt,
    reference_text: edit.reference_text,
    fixed_output: false,
    compose: normalizeCompose(edit.compose, { fixed_output: edit.fixed_output, presence: edit.presence }),
    presence: presenceForCompose(normalizeCompose(edit.compose, { fixed_output: edit.fixed_output, presence: edit.presence })),
    structure_only: Boolean(edit.structure_only),
  };
  if (elementIds.length) payload.element_ids = elementIds;
  // 要素体以库为准：configure_chapter 不传 elements，避免进度卡瘦身列表盖回任务
  return payload;
}

async function confirmAllChapters(payload) {
  const taskId = payload?.task_id;
  if (!taskId || sending.value) return;
  const chapters = (payload?.chapters || [])
    .filter((ch) => ch?.editable)
    .map((ch) => {
      const key = chapterEditKey(taskId, ch.key);
      const edited = chapterEdits.value[key];
      return {
        chapter_key: ch.key,
        text: sanitizeHtml(edited?.text || ch.draft_text || "") || "",
      };
    });
  if (!chapters.length) {
    ElMessage.warning("没有待确认的章节");
    return;
  }
  await sendAction("confirm_all_chapters", {
    task_id: taskId,
    skip_empty: true,
    chapters,
  });
}

async function confirmOneChapter(payload, ch) {
  const taskId = payload?.task_id;
  if (!taskId || !ch?.key || sending.value) return;
  const edit = ensureChapterEdit(taskId, ch);
  const text = sanitizeHtml(edit?.text || ch.draft_text || "") || "";
  if (!text.trim()) {
    ElMessage.warning("本章还没有正文，请先「保存并重试本章」生成正文后再确认；确认后才会进入下一章");
    return;
  }
  await sendAction("confirm_chapter", {
    task_id: taskId,
    chapter_key: ch.key,
    sequence: String(ch.sequence || "").trim(),
    title: String(ch.title || "").trim(),
    chapter_title: String(ch.title || "").trim(),
    text,
    decision: "approve",
  });
}

async function startWithPace(card, pace, msgIndex, cardIndex) {
  markCardUsed(msgIndex, cardIndex);
  const tid = String(card?.payload?.task_id || "").trim();
  const focusKey = String(card?.payload?.focus_chapter_key || "").trim();
  const keys = (card?.payload?.write_chapter_keys || card?.payload?.chapter_keys || [])
    .map((k) => String(k || "").trim())
    .filter(Boolean);
  armDocWindowAutoOpen();
  // 多章时 writing_pace 交给后端澄清；单章或卡片已带 pace 则原样传递
  const paceFromCard = String(card?.payload?.writing_pace || "").trim();
  await sendAction(
    "start_writing",
    {
      ...(card?.payload || {}),
      ...(paceFromCard ? { writing_pace: paceFromCard } : {}),
      ...(keys.length ? { chapter_keys: keys } : {}),
    },
    { force: true }
  );
}

function openWritingSupplementItem(card, item) {
  const pl = card?.payload || {};
  const tid = String(pl.task_id || "").trim();
  const chapterKey = String(item?.chapter_key || "").trim();
  const elementId = String(item?.element_id || "").trim();
  if (elementId) {
    driveDocWindow("open_doc_window", {
      taskId: tid,
      chapterKey,
      tab: "content",
      elementId,
    });
    return;
  }
  supplementGapPick.value = { card, item };
  supplementGapDialogVisible.value = true;
}

function supplementOpenDocContent() {
  const pick = supplementGapPick.value;
  supplementGapDialogVisible.value = false;
  if (!pick?.card || !pick?.item) return;
  driveDocWindow("open_doc_window", {
    taskId: pick.card.payload?.task_id,
    chapterKey: pick.item.chapter_key,
    tab: "content",
  });
}

function supplementAssociateFromGap() {
  const pick = supplementGapPick.value;
  if (!pick?.card || !pick?.item) return;
  const tid = String(pick.card.payload?.task_id || "").trim();
  const ck = String(pick.item.chapter_key || "").trim();
  if (!tid || !ck) return;
  supplementGapDialogVisible.value = false;
  const label = String(pick.item.chapter_label || "").trim();
  const seq = label.split(/\s+/)[0] || "";
  const title =
    label.replace(new RegExp(`^${seq}\\s*`), "").trim() || ck;
  const ch = { key: ck, sequence: seq, title };
  ensureChapterEdit(tid, ch);
  associatePointContext.value = {
    id: String(pick.item.point_id || "").trim(),
    index: Number(pick.item.point_index),
    slot: String(pick.item.slot_label || pick.item.element_name || "").trim(),
    pointItems: chapterPointRowsForSupplement(tid, ck),
  };
  openChapterElementAssociate(tid, ch);
}

async function supplementCreateElementFromGap() {
  const pick = supplementGapPick.value;
  if (!pick?.card || !pick?.item) return;
  const tid = String(pick.card.payload?.task_id || "").trim();
  const ck = String(pick.item.chapter_key || "").trim();
  if (!tid || !ck || sending.value) return;
  supplementGapDialogVisible.value = false;
  const name =
    String(pick.item.slot_label || pick.item.element_name || "").trim() || "新要素";
  await sendAction("add_chapter_element", {
    task_id: tid,
    chapter_key: ck,
    name,
    kind: "kv",
    hint: String(pick.item.display_line || pick.item.reason || "").slice(0, 400),
    point_index: pick.item.point_index,
    slot: String(pick.item.slot_label || pick.item.element_name || name).trim(),
  });
}

function supplementItemKindLabel(item) {
  const kind = String(item?.element_kind || "").trim();
  if (!kind) {
    return item?.element_id ? "要素" : item?.point_index != null && item.point_index >= 0 ? "要点" : "";
  }
  return elementKindLabel(kind) || kind;
}

const ELEMENT_ID_IN_LINE_RE = /\{\{?\s*el-[a-z0-9-]+/i;
const GAP_DISPLAY_BRACE_RE = /\{\{[^}]*\}\}/g;

function stripSupplementGapDisplay(text) {
  let t = String(text || "");
  t = t.replace(GAP_DISPLAY_BRACE_RE, "");
  t = t.replace(/（待补充[：:][^）)]*）/g, "");
  t = t.replace(/（待填写）/g, "");
  t = t.replace(/\s{2,}/g, " ").trim();
  t = t.replace(/[：:]\s*$/g, "").trim();
  return t;
}

function supplementGapDisplayLine(item, taskId) {
  if (!item || typeof item !== "object") return "待补充项";
  const eid = String(item.element_id || "").trim();
  let name = humanLabel(item.element_name, item.slot_label);
  if (eid) {
    const el = elementsForTask(taskId).find((row) => String(row?.id || "") === eid);
    name = humanLabel(el?.name, name, item.slot_label);
  }
  const raw = String(item.display_line || "").trim();
  let line = raw;
  if (
    eid &&
    name &&
    (!raw || raw.includes(eid) || ELEMENT_ID_IN_LINE_RE.test(raw) || raw.startsWith("已挂要素："))
  ) {
    line = name;
  }
  line = stripSupplementGapDisplay(line);
  if (line) return line;
  if (name) return name;
  return "待补充项";
}

function namesOverlap(a, b) {
  const x = String(a || "").trim();
  const y = String(b || "").trim();
  if (!x || !y) return false;
  return x === y || x.includes(y) || y.includes(x);
}

function chapterPointRowsForSupplement(taskId, chapterKey) {
  const edit = chapterEdits.value[chapterEditKey(taskId, chapterKey)];
  return Array.isArray(edit?.point_items) ? edit.point_items : [];
}

function resolvedPointElementId(row, taskId, chapterKey) {
  if (!row || typeof row !== "object") return "";
  const eid = String(row.element_id || "").trim();
  if (eid && elementsForTask(taskId).some((el) => String(el?.id || "") === eid)) {
    return eid;
  }
  const slot = String(row.slot || row.element_name || "").trim();
  if (!slot) return "";
  const edit = chapterEdits.value[chapterEditKey(taskId, chapterKey)];
  const eids = Array.isArray(edit?.element_ids) ? edit.element_ids : [];
  for (const cid of eids) {
    const id = String(cid || "").trim();
    const el = elementsForTask(taskId).find((e) => String(e?.id || "") === id);
    if (el?.name && namesOverlap(slot, el.name)) return id;
  }
  return "";
}

function pointRowSupplementResolved(row, taskId, chapterKey = "") {
  if (!row || typeof row !== "object") return false;
  const ck = String(chapterKey || "").trim();
  if (ck && resolvedPointElementId(row, taskId, ck)) return true;
  const eid = String(row.element_id || "").trim();
  if (eid && elementsForTask(taskId).some((el) => String(el?.id || "") === eid)) {
    return true;
  }
  const text = String(row.text || row.label || "");
  if (!row.needs_supplement && !textNeedsSupplement(text)) return true;
  return false;
}

function writingSupplementGapResolved(item, taskId) {
  if (!item || typeof item !== "object") return false;
  const tid = String(taskId || "").trim();
  const ck = String(item.chapter_key || "").trim();
  const eid = String(item.element_id || "").trim();
  const rows = ck ? chapterPointRowsForSupplement(tid, ck) : [];
  if (eid) {
    if (rows.some((r) => String(r?.element_id || "").trim() === eid)) return true;
    if (rows.some((r) => resolvedPointElementId(r, tid, ck) === eid)) return true;
    return false;
  }
  if (!ck) return false;
  const pi = Number(item.point_index);
  if (Number.isFinite(pi) && pi >= 0 && rows[pi]) {
    return pointRowSupplementResolved(rows[pi], tid, ck);
  }
  const slot = String(item.slot_label || "").trim();
  if (slot && rows.length) {
    for (const row of rows) {
      const rs = String(row?.slot || row?.element_name || "").trim();
      if (!rs || (rs !== slot && !rs.includes(slot) && !slot.includes(rs))) continue;
      if (pointRowSupplementResolved(row, tid, ck)) return true;
    }
  }
  return false;
}

function visibleWritingSupplementItems(card) {
  return (card?.payload?.items || []).filter((item) => item && typeof item === "object");
}

function writingSupplementHint(card) {
  const pl = card?.payload || {};
  const n = visibleWritingSupplementItems(card).length;
  if (n > 0) {
    return `写作前还有 ${n} 处配置待补；补上后检索会更准，不补也可先写。`;
  }
  return (
    pl.hint ||
    card?.title ||
    "按写作要点与关联要素检查仍缺配置项"
  );
}

async function onElementConfigureSaved(result) {
  await applyElementConfigureSavedResult(result);
  // 抑制自动弹窗：加要素后只入队待收录角标，不弹出收录弹窗
  await restoreCurrentSession({ suppressAutoPopups: true });
  await applyElementConfigureSavedResult(result);
}

async function applyElementConfigureSavedResult(result) {
  if (!result || typeof result !== "object") return;
  const tid = String(result.task_id || "").trim();
  const ck = String(result.chapter_key || "").trim();
  const el = result.element;
  if (tid && el?.id) {
    const prev = elementsForTask(tid);
    const without = prev.filter((row) => String(row?.id || "") !== String(el.id));
    taskElementNodes.value = {
      ...taskElementNodes.value,
      [tid]: [...without, { ...el }],
    };
  }
  if (tid) {
    try {
      const task = await request(`/api/writing/tasks/${encodeURIComponent(tid)}`);
      if (Array.isArray(task?.element_nodes)) {
        syncTaskElementsFromProgress({ task_id: tid, element_nodes: task.element_nodes });
      }
    } catch {
      /* 本地 result.element 已合并 */
    }
  }
  if (!tid || !ck) return;
  const progress = latestProgressCards().find((c) => c?.type === "progress");
  const progCh = (progress?.payload?.chapters || []).find((c) => c?.key === ck);
  const prevLinked = unionElementIds(
    chapterEdits.value[chapterEditKey(tid, ck)]?.element_ids,
    progCh?.element_ids,
    progCh?.config?.element_ids
  );
  const elementIds = unionElementIds(prevLinked, result.element_ids);
  const pointItems = Array.isArray(result.point_items)
    ? result.point_items.map((row) => ({ ...row }))
    : [];
  const points = Array.isArray(result.points)
    ? result.points.map((line) => String(line || "")).filter(Boolean)
    : [];
  const ch = {
    key: ck,
    sequence: progCh?.sequence || "",
    title: progCh?.title || ck,
    element_ids: elementIds,
    point_items: pointItems,
    points,
  };
  ensureChapterEdit(tid, ch);
  const editKey = chapterEditKey(tid, ck);
  const edit = chapterEdits.value[editKey];
  if (edit) {
    edit.element_ids = [...elementIds];
    if (pointItems.length) {
      edit.point_items = pointItems.map((row) => ({ ...row }));
      edit.pointsText = points.join("\n");
    }
  }
  patchChapterElementIdsEverywhere(tid, ck, elementIds, {
    point_items: pointItems,
    points,
  });
  driveDocWindow("elements_associated", {
    taskId: tid,
    chapterKey: ck,
    tab: "config",
    elementIds,
    elementNodes: elementsForTask(tid),
    newElementIds: el?.id ? [String(el.id)] : [],
    pointItems,
  });
  bumpDocOutlineReload({ force: true });
}

/** 关联要素确认后写入对话（打开弹窗不写气泡，避免刷屏） */
function onSupplementGapRowClick(card, item, msgIndex, cardIndex, event) {
  if (card?.payload?.readonly || isCardUsed(msgIndex, cardIndex)) return;
  const tag = String(event?.target?.tagName || "").toLowerCase();
  if (tag === "button" || event?.target?.closest?.("button")) return;
  openWritingSupplementItem(card, item);
}

async function dismissWritingSupplement(card, item, msgIndex, cardIndex) {
  const pl = card?.payload || {};
  await sendAction("dismiss_writing_supplement", {
    task_id: pl.task_id,
    gap_id: item?.id,
    chapter_key: item?.chapter_key,
    element_id: item?.element_id,
    chapter_label: item?.chapter_label,
    sequence: item?.sequence,
    title: item?.title || item?.chapter_title,
    pointSlot: item?.slot_label || item?.element_name || item?.slot,
    slot: item?.slot_label || item?.element_name || item?.slot,
  });
  markCardUsed(msgIndex, cardIndex);
}

async function skipWritingSupplementCheck(card, msgIndex, cardIndex) {
  const pl = card?.payload || {};
  const firstGap = (pl.items || pl.gaps || [])[0] || {};
  await sendAction(
    "skip_writing_supplement_check",
    {
      task_id: pl.task_id,
      chapter_keys: pl.chapter_keys || [],
      writing_pace: pl.writing_pace || "",
      resume_action: pl.resume_action || "",
      chapter_key: pl.chapter_key || firstGap.chapter_key || "",
      sequence: pl.sequence || firstGap.sequence || "",
      title: pl.title || firstGap.title || "",
      chapter_label: pl.chapter_label || firstGap.chapter_label || "",
      _write_mode: pl._write_mode || "",
    },
    { force: true }
  );
  markCardUsed(msgIndex, cardIndex);
  bumpDocOutlineReload({ force: true });
}

async function recheckWritingSupplements(card) {
  const pl = card?.payload || {};
  await sendAction("check_writing_supplements", {
    task_id: pl.task_id,
    chapter_keys: pl.chapter_keys || [],
    writing_pace: pl.writing_pace || "",
    resume_action: pl.resume_action || "",
    chapter_key: pl.chapter_key || "",
    sequence: pl.sequence || "",
    title: pl.title || "",
    chapter_label: pl.chapter_label || "",
    _write_mode: pl._write_mode || "",
  });
}

async function saveChapterConfig(taskId, ch) {
  // 文档窗/进度卡「保存配置」：静默落盘，不刷对话气泡
  const tid = String(taskId || "").trim();
  const key = String(ch?.key || "").trim();
  if (!tid || !key) return;
  try {
    const body = configPayload(tid, ch);
    const slim = await request(
      docOutlineUrl(tid, { fresh: true, writingTaskId: writingTaskIdForTask(tid) })
    );
    const chapters = (slim?.chapters || []).map((item) => ({ ...item }));
    const idx = chapters.findIndex((item) => String(item.key || "") === key);
    if (idx < 0) throw new Error("未找到该章节");
    const prev = chapters[idx] || {};
    const pointItems = Array.isArray(body.point_items)
      ? body.point_items.map((row) => ({ ...row }))
      : Array.isArray(prev.point_items)
        ? prev.point_items.map((row) => ({ ...row }))
        : [];
    const points = String(body.points_text || "")
      .split(/\n+/)
      .map((line) => line.trim())
      .filter(Boolean);
    const savedChapter = {
      ...prev,
      key,
      sequence: body.sequence || prev.sequence,
      title: body.title || prev.title || key,
      points: points.length ? points : Array.isArray(prev.points) ? [...prev.points] : [],
      point_items: pointItems,
      describes: body.describes ?? prev.describes,
      aux_prompt: body.aux_prompt ?? prev.aux_prompt,
      reference_text: body.reference_text ?? prev.reference_text,
      compose: body.compose || prev.compose,
      presence: body.presence || prev.presence,
      structure_only: Boolean(body.structure_only),
      element_ids: Array.isArray(body.element_ids)
        ? [...body.element_ids]
        : Array.isArray(prev.element_ids)
          ? [...prev.element_ids]
          : [],
    };
    chapters[idx] = savedChapter;
    await request(`/api/writing/tasks/${encodeURIComponent(tid)}/outline`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ chapters }),
    });
    applyChapterSnapshotToSession(tid, key, savedChapter);
    ElMessage.success("章节配置已保存");
    driveDocWindow("config_saved", {
      taskId: tid,
      chapterKey: key,
      chapter: savedChapter,
      tab: "config",
      alreadyPersisted: true,
      syncSession: false,
      quiet: true,
    });
  } catch (error) {
    ElMessage.error(error?.message || "保存章节配置失败");
  }
}

async function retryChapter(taskId, ch, { writeMode = "start", alreadyPersisted = false, chapterConfig = null } = {}) {
  const key = chapterEditKey(taskId, ch?.key);
  const edit = chapterEdits.value[key];
  // 重试前清掉「已手改」标记，完成后用新草稿刷新编辑区
  if (edit) edit._touched = false;
  const chapterKey = String(ch?.key || "").trim();
  const overlay =
    chapterConfig && typeof chapterConfig === "object" && !Array.isArray(chapterConfig)
      ? chapterConfig
      : {};
  let sequence = String(
    edit?.sequence || ch?.sequence || overlay.sequence || ""
  ).trim();
  let title = String(edit?.title || ch?.title || overlay.title || "").trim();
  if (isPlaceholderChapterName(title) || isInternalChapterSlug(title, chapterKey)) {
    title = "";
  }
  const resolved = chapterUserLabel(taskId, chapterKey, {
    task_id: taskId,
    sequence,
    title,
  });
  if ((!sequence || !title) && !isPlaceholderChapterName(resolved)) {
    const m = String(resolved).match(/^(\d+(?:\.\d+)*)\s+(.+)$/);
    if (m) {
      sequence = sequence || m[1];
      title = title || m[2];
    } else if (!title) {
      title = resolved;
    }
  }
  const packId = String(
    sessionRuntime.value?.pending_materials_pack_id ||
      (sessionRuntime.value?.pending_materials_pack_by_blueprint || {})[
        String(selectedBlueprint.value || "").trim()
      ] ||
      ""
  ).trim();
  const base = {
    task_id: taskId,
    chapter_key: chapterKey,
    sequence,
    title,
    chapter_title: title,
    _write_mode: writeMode === "rewrite" ? "rewrite" : "start",
    ...(packId ? { materials_pack_id: packId, pack_id: packId } : {}),
  };
  const preferDisk =
    alreadyPersisted ||
    /^w/i.test(String(taskId || "").trim()) ||
    Boolean(chapterConfig && typeof chapterConfig === "object");
  const body = preferDisk
    ? { ...overlay, ...base, already_persisted: true, from_doc_window: true }
    : { ...configPayload(taskId, ch), ...overlay, ...base };
  await sendAction("retry_chapter", body, { force: true });
}

function resetSearchBranchDialog() {
  searchBranchDialog.value = emptySearchBranchDialog();
}

function applySearchBranchResult(payload) {
  if (!payload) return;
  searchBranchDialog.value = {
    ...searchBranchDialog.value,
    visible: true,
    loading: false,
    title: payload.title || payload.chapter_key || "",
    chapterKey: payload.chapter_key || "",
    adopted: Number(payload.adopted) || 0,
    note: payload.note || "",
    cards: Array.isArray(payload.cards) ? payload.cards : [],
  };
}

async function streamSearchBranch(action_payload) {
  const title =
    action_payload?.chapter_title ||
    action_payload?.title ||
    action_payload?.chapter_key ||
    "本章";
  searchBranchDialog.value = {
    ...emptySearchBranchDialog(),
    visible: true,
    loading: true,
    title: String(title),
    chapterKey: action_payload?.chapter_key || "",
  };

  const response = await fetch("/api/writing/chat/stream", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      message: "",
      robot_id: robotId.value || "",
      session_id: sessionId.value || "",
      action: "retry_search_chapter",
      action_payload,
    }),
  });
  if (!response.ok) {
    throw new Error(`请求失败 (${response.status})`);
  }

  let gotResult = false;
  await readStream(response, (event) => {
    if (!event || typeof event !== "object") return;
    if (event.type === "session") {
      applyChatSessionEvent(event);
      return;
    }
    if (event.type === "search_branch_result") {
      gotResult = true;
      applySearchBranchResult(event);
      return;
    }
    if (event.branch !== "search") {
      if (event.type === "process" && SEARCH_BRANCH_NOISE_RE.test(String(event.message || ""))) return;
      if (event.type === "step" && event.step && event.step !== "search") return;
      if (event.type === "chapter_start" || event.type === "chapter_done") return;
      if (event.type === "process" || event.type === "step") return;
    }
    if (event.type === "step" && event.step === "search") {
      const line = formatStepEvent(event);
      const progress = [...(searchBranchDialog.value.progress || []), line];
      searchBranchDialog.value = { ...searchBranchDialog.value, progress: progress.slice(-6) };
      return;
    }
    if (event.type === "process" && event.message) {
      const progress = [...(searchBranchDialog.value.progress || []), event.message];
      searchBranchDialog.value = { ...searchBranchDialog.value, progress: progress.slice(-6) };
      return;
    }
    if (event.type === "error") {
      throw new Error(event.detail || "检索失败");
    }
    if (event.type === "done") {
      if (!gotResult && Array.isArray(event.events)) {
        const hit = event.events.find((e) => e?.type === "search_branch_result");
        if (hit) {
          gotResult = true;
          applySearchBranchResult(hit);
        }
      }
      if (!gotResult) {
        searchBranchDialog.value = {
          ...searchBranchDialog.value,
          loading: false,
          note: "未收到检索结果，请重启后端后重试。",
        };
      }
    }
  });

  if (searchBranchDialog.value.loading) {
    searchBranchDialog.value = { ...searchBranchDialog.value, loading: false };
  }
  if (gotResult) {
    ElMessage.success(`「${searchBranchDialog.value.title}」检索完成`);
  }
}

async function retrySearchChapter(taskId, ch) {
  if (sending.value) return;
  sending.value = true;
  const payload = enrichActionPayload("retry_search_chapter", {
    ...configPayload(taskId, ch),
    sequence: String(ch?.sequence || "").trim(),
    title: String(ch?.title || "").trim(),
    chapter_title: String(ch?.title || "").trim(),
  });
  messages.value.push({
    role: "user",
    text: actionDisplayLabel("retry_search_chapter", payload),
    cards: [],
    steps: [],
  });
  try {
    await streamSearchBranch(payload);
  } catch (error) {
    searchBranchDialog.value = {
      ...searchBranchDialog.value,
      visible: true,
      loading: false,
      note: error?.message || "检索失败",
    };
    ElMessage.error(error?.message || "检索失败");
  } finally {
    sending.value = false;
    await loadHistory();
  }
}

const props = defineProps({
  resumeToken: { type: Number, default: 0 },
  resumeSessionId: { type: String, default: "" },
  resumeBlueprintId: { type: String, default: "" },
});

const navigateTo = inject("navigateTo", null);

function sessionStorageKey() {
  return `ay_chat_session_id_${getUserId() || "default"}`;
}

const editorEl = ref(null);
const chatStageEl = ref(null);
let chatScrollRaf = 0;

/** 对话区滚到底；流式/发送中始终跟随，避免进度卡片变高时停在中间。 */
function scheduleScrollChatToBottom(force = false) {
  if (chatScrollRaf) cancelAnimationFrame(chatScrollRaf);
  chatScrollRaf = requestAnimationFrame(() => {
    chatScrollRaf = 0;
    const el = chatStageEl.value;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (!force && !sending.value && gap > 220) return;
    el.scrollTop = el.scrollHeight;
  });
}

function scrollChatToBottom(force = true) {
  nextTick(() => scheduleScrollChatToBottom(force));
}

const draftHtml = ref("");
const draftPlain = ref("");
const messages = ref([]);
const robots = ref([]);
const robotId = ref("");
const currentRobot = ref(null);
const sending = ref(false);
/** 发送中或写作锁时禁止对话框输入；分析中仍可说话（例如「切换到另一份」） */
const composerLocked = computed(
  () =>
    Boolean(sending.value) ||
    Boolean(pointsDraftInflight.value) ||
    isDocWindowWriteLockActive()
);

const pauseBusy = ref(false);
const activeRunChapterKey = ref("");
let streamAbortController = null;
const sessionId = ref(localStorage.getItem(sessionStorageKey()) || "");
const phase = ref("intent");
const taskId = ref("");
const selectedBlueprint = ref("");
const pickedBlueprintId = ref("");
const usedCardKeys = ref(new Set());
const blueprintWizardLog = ref([]);
/** 与后端 task / session.freeform 对齐的向导步（覆盖历史卡片过期 step） */
const liveBlueprintWizard = ref(null);
/** 每份文档自己的 live 向导（切档只换投影，不互相覆盖） */
const liveWizardsByDoc = ref({});
/** 服务端 reconcile 后的会话运行时（蓝本向导 / 写作 / 模版向导等） */
const sessionRuntime = ref(null);
const liveTemplateWizard = ref(null);

function mergeDocChapters(baseList, progressList) {
  const base = Array.isArray(baseList) ? baseList : [];
  const prog = Array.isArray(progressList) ? progressList : [];
  if (!prog.length) return base;
  if (!base.length) return prog;
  const byKey = new Map(prog.filter((ch) => ch?.key).map((ch) => [String(ch.key), ch]));
  // 只合并要点/状态；禁止丢节点、禁止改 parent/level/sequence（分析时目录变形）
  return base.map((ch) => {
    const hit = byKey.get(String(ch?.key || ""));
    if (!hit) return ch;
    return {
      ...ch,
      ...hit,
      key: ch.key,
      parent: ch.parent,
      parent_sequence: ch.parent_sequence ?? hit.parent_sequence,
      level: ch.level ?? hit.level,
      sequence: ch.sequence || hit.sequence,
      config: { ...(ch.config || {}), ...(hit.config || {}) },
      points: hit.points || ch.points,
      draft_text: hit.draft_text || hit.draft_preview || ch.draft_text || "",
      has_draft: Boolean(hit.has_draft || hit.draft_text || ch.has_draft),
      status: hit.status || ch.status,
      status_label: hit.status_label || ch.status_label,
      steps: hit.steps?.length ? hit.steps : ch.steps,
      checks: hit.checks?.length ? hit.checks : ch.checks,
      evidence: hit.evidence?.length ? hit.evidence : ch.evidence,
      is_running: Boolean(hit.is_running),
      is_next: Boolean(hit.is_next),
    };
  });
}

function foldConversationDoc(primary, extra) {
  if (!primary || !extra || primary === extra) return primary;
  const extraKind = String(extra.kind || "");
  const tid = String(extra.taskId || "").trim();
  const wid = String(extra.writingTaskId || "").trim();
  if (String(primary.kind || "") === "blueprint") {
    if (extraKind === "writing" && tid) primary.writingTaskId = primary.writingTaskId || tid;
    else if (wid) primary.writingTaskId = primary.writingTaskId || wid;
    if (tid && tid !== String(primary.blueprintId || primary.taskId || "")) {
      primary.writingTaskId = primary.writingTaskId || tid;
    }
  }
  primary.hasWritingProgress = Boolean(
    primary.hasWritingProgress || extra.hasWritingProgress || extraKind === "writing"
  );
  if (extra.status) primary.status = extra.status;
  if (extra.wizardStep && !primary.wizardStep) primary.wizardStep = extra.wizardStep;
  if (extra.outlineConfirmed) primary.outlineConfirmed = true;
  if (Number(extra.study_analyzed || 0) > Number(primary.study_analyzed || 0)) {
    primary.study_analyzed = extra.study_analyzed;
    primary.study_unanalyzed = extra.study_unanalyzed;
    primary.study_mode = extra.study_mode;
  }
  if ((extra.chapters || []).length) {
    const primaryIsBlueprint = String(primary.kind || "") === "blueprint";
    const extraIsWorkingCopy = String(extra.kind || "") !== "blueprint";
    // 同一文档 id 折在一起时，写作副本上的目录是改章/重新生成后的最新一份
    if (primaryIsBlueprint && extraIsWorkingCopy) {
      primary.chapters = extra.chapters;
    } else {
      primary.chapters = mergeDocChapters(primary.chapters || [], extra.chapters || []);
    }
  }
  return primary;
}

function conversationDocRank(d) {
  const kind = String(d?.kind || "");
  const chapters = Array.isArray(d?.chapters) ? d.chapters.length : 0;
  return (
    (kind === "blueprint" ? 300 : kind === "outline" ? 100 : kind === "writing" ? 50 : 0) +
    (d?.writingTaskId ? 20 : 0) +
    (d?.hasWritingProgress ? 10 : 0) +
    Math.min(chapters, 80)
  );
}

/** 去掉写作 clone 后缀，供文档窗展示。 */
function cleanConversationDocTitle(title) {
  let name = String(title || "").trim();
  if (name.endsWith("（对话写作）")) {
    name = name.slice(0, -"（对话写作）".length).trim() || name;
  }
  return name;
}

/** 像 task id / 占位名，不应盖掉对话里定下的文档名。 */
function looksLikeRawDocTitle(title) {
  const s = cleanConversationDocTitle(title);
  if (!s) return true;
  if (s === "未命名文档" || s === "蓝本目录" || s === "写作文档" || s === "章节目录") return true;
  if (/^(task:|bp:)/i.test(s)) return true;
  if (/^w\d{10,}[a-f0-9]*$/i.test(s)) return true;
  if (/^bp\d{10,}[a-f0-9]*$/i.test(s)) return true;
  return false;
}

function preferConversationDocTitle(prev, next) {
  const n = cleanConversationDocTitle(next);
  const p = cleanConversationDocTitle(prev);
  if (!n) return p;
  if (!p || looksLikeRawDocTitle(p)) return n;
  if (looksLikeRawDocTitle(n)) return p;
  // 两边都是可读名：保留先写入的（会话文档名），避免蓝本库/向导标题盖掉用户定名
  return p;
}

/** 仅当两份文档已有蓝本/写作 id 关联时才可按同标题归并。 */
function docsShareIdentity(a, b) {
  if (!a || !b) return false;
  const aBid = String(a.blueprintId || (a.kind === "blueprint" ? a.taskId : "") || "").trim();
  const bBid = String(b.blueprintId || (b.kind === "blueprint" ? b.taskId : "") || "").trim();
  const aWid = String(a.writingTaskId || "").trim();
  const bWid = String(b.writingTaskId || "").trim();
  const aTid = String(a.taskId || "").trim();
  const bTid = String(b.taskId || "").trim();
  if (aBid && bBid && aBid === bBid) return true;
  if (aWid && bWid && aWid === bWid) return true;
  if (aWid && (aWid === bTid || aWid === bBid)) return true;
  if (bWid && (bWid === aTid || bWid === aBid)) return true;
  if (aBid && (aBid === bTid || aBid === bWid)) return true;
  if (bBid && (bBid === aTid || bBid === aWid)) return true;
  return false;
}

/** 同标题且一侧无蓝本、一侧有蓝本：仅当已有写作副本/收录关联时才合并。两份不同蓝本或两份无关文档不合并。 */
function docsShouldCollapseSameTitle(a, b) {
  if (!a || !b) return false;
  const aBid = String(a.blueprintId || (a.kind === "blueprint" ? a.taskId : "") || "").trim();
  const bBid = String(b.blueprintId || (b.kind === "blueprint" ? b.taskId : "") || "").trim();
  if (aBid && bBid && aBid !== bBid) return false;
  // 必须已有身份关联（同 writingTaskId / 收录挂接），禁止仅凭同标题把两份报告捏成一份
  if (!docsShareIdentity(a, b)) return false;
  if (aBid && !bBid) return true;
  if (bBid && !aBid) return true;
  return false;
}

/**
 * 同一报告只留一条：只按蓝本 id / 写作副本 id 合并。
 * 标题相同但 id 不同的是两份文档，改章或重新生成目录也只更新这份 id 上的最新章节。
 */
function dedupeConversationDocuments(docs) {
  const list = Array.isArray(docs) ? docs.map((d) => ({ ...d })) : [];
  if (list.length <= 1) return list;

  const parentOf = new Map(); // id -> canonical id
  const find = (id) => {
    let cur = id;
    while (parentOf.get(cur) && parentOf.get(cur) !== cur) cur = parentOf.get(cur);
    return cur;
  };
  const unite = (a, b) => {
    const ra = find(a);
    const rb = find(b);
    if (ra === rb) return;
    const da = list.find((d) => d.id === ra);
    const db = list.find((d) => d.id === rb);
    if (conversationDocRank(db) > conversationDocRank(da)) parentOf.set(ra, rb);
    else parentOf.set(rb, ra);
  };

  for (const d of list) parentOf.set(d.id, d.id);

  // 1) 同一蓝本 id / 写作 clone id 归并
  const byBid = new Map();
  const byWid = new Map();
  for (const d of list) {
    const bid = String(d.blueprintId || (d.kind === "blueprint" ? d.taskId : "") || "").trim();
    const wid = String(d.writingTaskId || "").trim();
    const tid = String(d.taskId || "").trim();
    if (bid) {
      if (byBid.has(bid)) unite(byBid.get(bid), d.id);
      else byBid.set(bid, d.id);
    }
    if (wid) {
      if (byWid.has(wid)) unite(byWid.get(wid), d.id);
      else byWid.set(wid, d.id);
    }
    // task:写作id 挂到已声明 writingTaskId 的蓝本
    if (tid && byWid.has(tid)) unite(byWid.get(tid), d.id);
    if (tid && byBid.has(tid)) unite(byBid.get(tid), d.id);
  }

  // 2) 同标题：蓝本↔其写作 clone 归并
  const byTitle = new Map();
  for (const d of list) {
    const title = cleanConversationDocTitle(d.title);
    if (!title || looksLikeRawDocTitle(title)) continue;
    if (byTitle.has(title)) {
      const other = list.find((x) => x.id === byTitle.get(title));
      if (docsShareIdentity(other, d) || docsShouldCollapseSameTitle(other, d)) {
        unite(byTitle.get(title), d.id);
      }
    } else {
      byTitle.set(title, d.id);
    }
  }

  // 3) 收录后的写作副本与库蓝本：只按同一 writingTaskId 合并，不按标题
  const savedBp = String(
    sessionRuntime.value?.workflows?.freeform?.freeform_saved_blueprint_id ||
      sessionRuntime.value?.freeform_saved_blueprint_id ||
      ""
  ).trim();
  if (savedBp) {
    const bpDoc = list.find(
      (d) =>
        String(d.blueprintId || "").trim() === savedBp ||
        String(d.id || "") === `bp:${savedBp}`
    );
    if (bpDoc) {
      for (const d of list) {
        if (d.id === bpDoc.id) continue;
        const linkedWid =
          String(d.taskId || d.writingTaskId || "").trim() &&
          String(d.taskId || d.writingTaskId || "").trim() ===
            String(bpDoc.writingTaskId || "").trim();
        if (linkedWid) unite(bpDoc.id, d.id);
      }
    }
  }

  const groups = new Map();
  for (const d of list) {
    const root = find(d.id);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(d);
  }
  const out = [];
  for (const group of groups.values()) {
    group.sort((a, b) => conversationDocRank(b) - conversationDocRank(a));
    const primary = group[0];
    for (const extra of group.slice(1)) foldConversationDoc(primary, extra);
    if (primary.title) primary.title = cleanConversationDocTitle(primary.title);
    out.push(primary);
  }
  return out;
}

const conversationDocuments = computed(() => {
  const byId = new Map();
  const upsert = (id, patch) => {
    const prev = byId.get(id) || {
      id,
      title: "未命名文档",
      kind: "task",
      kindLabel: "文档",
      chapters: [],
      status: "",
    };
    const nextChapters = Array.isArray(patch.chapters) ? patch.chapters : null;
    const prevChapters = Array.isArray(prev.chapters) ? prev.chapters : [];
    const chapters =
      nextChapters && nextChapters.length
        ? patch.mergeChapters || prevChapters.length > nextChapters.length
          ? mergeDocChapters(prevChapters, nextChapters)
          : nextChapters
        : prevChapters;
    const { mergeChapters: _m, ...rest } = patch;
    // 分析进度/向导合入时常带空 writingTaskId：勿抹掉已有文档副本指针，否则「同步到蓝本库」会消失
    if (!String(rest.writingTaskId || "").trim() && String(prev.writingTaskId || "").trim()) {
      delete rest.writingTaskId;
    }
    // 未传 blueprintId 时保留旧值；显式传 "" 则清空（无蓝本防串上一份库蓝本）
    // 严禁把写作副本 w* 落成 blueprintId（分析开跑污染会导致同步按钮消失）
    if (!Object.prototype.hasOwnProperty.call(rest, "blueprintId")) {
      /* keep prev.blueprintId via spread */
    } else if (!String(rest.blueprintId || "").trim()) {
      rest.blueprintId = "";
    } else {
      rest.blueprintId = sanitizeLibraryBlueprintId(rest.blueprintId, prev.blueprintId);
    }
    const title =
      rest.title != null
        ? preferConversationDocTitle(prev.title, rest.title)
        : cleanConversationDocTitle(prev.title) || prev.title;
    const next = { ...prev, ...rest, id, chapters, title: title || prev.title || "未命名文档" };
    if (!Object.prototype.hasOwnProperty.call(rest, "blueprintId")) {
      next.blueprintId = sanitizeLibraryBlueprintId(next.blueprintId, prev.blueprintId);
    }
    if (String(next.writingTaskId || "").trim()) {
      next.kindLabel = isLibraryBlueprintId(next.blueprintId) ? "文档副本" : next.kindLabel;
      next.hasWritingProgress = true;
    }
    byId.set(id, next);
  };
  // 会话登记表：保证同会话触及过的蓝本/大纲都在下拉里，不被历史卡缺失挡住
  const registry = sessionRuntime.value?.session_documents;
  if (registry && typeof registry === "object") {
    for (const [rawDocKey, meta] of Object.entries(registry)) {
      if (!meta || typeof meta !== "object") continue;
      const docKey = normalizeDocumentRegistryKey(rawDocKey);
      const kind = String(meta.kind || "").trim();
      const bid = String(meta.blueprint_id || "").trim();
      const tid = String(meta.task_id || "").trim();
      const wid = String(meta.writing_task_id || "").trim();
      const title = String(meta.title || bid || tid || docKey).trim();
      // 脏键 bp:w*（或 blueprint_id=w*）一律按写作任务挂 task:w*
      const writingFromBpSlot =
        (String(rawDocKey).startsWith("bp:") && isWritingCopyId(String(rawDocKey).slice(3))) ||
        (isWritingCopyId(bid) && (!tid || tid === bid || isWritingCopyId(tid)));
      if (writingFromBpSlot) {
        const writeId = isWritingCopyId(tid)
          ? tid
          : isWritingCopyId(bid)
            ? bid
            : String(rawDocKey).startsWith("bp:")
              ? String(rawDocKey).slice(3)
              : tid || bid;
        if (!writeId) continue;
        upsert(`task:${writeId}`, {
          kind: kind === "writing" ? "writing" : "outline",
          kindLabel: kind === "writing" ? "写作文档" : "章节目录",
          taskId: writeId,
          writingTaskId: wid || writeId,
          title: title && !looksLikeRawDocTitle(title) ? title : "未命名文档",
          blueprintId: "",
        });
        continue;
      }
      // doc: 隔离槽 / bp: 蓝本槽：都是「文档引用库蓝本」，写作态只看 writing_task_id
      if (
        String(docKey).startsWith("doc:") ||
        String(docKey).startsWith("bp:") ||
        kind === "blueprint"
      ) {
        const id = String(docKey).startsWith("doc:") || String(docKey).startsWith("bp:")
          ? docKey
          : `bp:${bid || tid}`;
        const libraryBid = sanitizeLibraryBlueprintId(bid, tid);
        upsert(id, {
          kind: "blueprint",
          // 有写作副本时这是「文档工作区」；库蓝本只是模版源
          kindLabel: wid ? "文档副本" : "蓝本模版",
          blueprintId: libraryBid,
          taskId: libraryBid || (isLibraryBlueprintId(tid) ? tid : ""),
          writingTaskId: wid || (isWritingCopyId(tid) ? tid : undefined),
          hasWritingProgress: Boolean(wid || isWritingCopyId(tid)),
          title: title || (wid ? "文档副本" : "蓝本模版"),
        });
      } else if (tid) {
        // 写作 clone：仅按蓝本/写作 id 挂到已有蓝本项，禁止仅凭同标题合并（同会话可有多份同名文档）
        if (kind === "writing") {
          // 只挂到已声明同一 writingTaskId 的文档；禁止仅凭同蓝本 id 串写作态
          const bp = [...byId.values()].find((d) => {
            if (String(d.kind || "") !== "blueprint") return false;
            const dWid = String(d.writingTaskId || "").trim();
            return dWid && (dWid === tid || (wid && dWid === wid));
          });
          if (bp?.id) {
            upsert(bp.id, {
              writingTaskId: tid || wid || bp.writingTaskId,
              hasWritingProgress: true,
              ...(title ? { title } : {}),
            });
            continue;
          }
        }
        const id = String(docKey).startsWith("task:") ? docKey : `task:${tid}`;
        // 无蓝本未收录：登记表残留的 blueprint_id 勿带进文档窗（否则串到上一份并误显示「同步」）
        const freeformBare =
          String(sessionRuntime.value?.writing_path || "").trim() === "freeform" &&
          !String(
            sessionRuntime.value?.collected_blueprint_id ||
              sessionRuntime.value?.freeform_saved_blueprint_id ||
              ""
          ).trim();
        upsert(id, {
          kind: kind === "writing" ? "writing" : "outline",
          kindLabel: kind === "writing" ? "写作文档" : "章节目录",
          taskId: tid,
          writingTaskId: wid || tid || undefined,
          title: title || tid,
          ...(freeformBare ? { blueprintId: "" } : bid ? { blueprintId: bid } : {}),
        });
      }
    }
  }
  // 只取每个 task 最新一份 progress / outline_preview，避免历史卡复活多份同名文档
  const latestProgressByTid = new Map();
  const latestOutlineByTid = new Map();
  for (const msg of messages.value || []) {
    for (const card of msg.cards || []) {
      if (card.type === "progress" && card.payload?.task_id) {
        latestProgressByTid.set(String(card.payload.task_id), card);
      }
      if (card.type === "outline_preview") {
        const tid = String(card.payload?.task_id || taskId.value || "").trim();
        if (!tid) continue;
        latestOutlineByTid.set(tid, card);
      }
    }
  }
  for (const [tid, card] of latestOutlineByTid) {
    const docTitle = String(card.payload?.doc_title || card.title || "").trim();
    const displayName =
      docTitle || String(card.payload?.doc_category || "").trim() || "章节目录";
    upsert(`task:${tid}`, {
      kind: "outline",
      kindLabel: displayName,
      taskId: tid,
      title: displayName,
      outlineConfirmed: !Boolean(card.payload?.pending_confirm || card.payload?.editable),
      chapters: (card.payload?.chapters || []).map(normalizeDocWindowChapter),
    });
  }
  for (const msg of messages.value || []) {
    for (const card of msg.cards || []) {
      if (card.type === "blueprint_wizard") {
        // 仅同 blueprint_id 才合入 live，避免旧报告项被当前向导盖掉
        const allowLive = liveWizardMatchesCard(card.payload || {}, liveBlueprintWizard.value);
        const merged = allowLive
          ? mergeWizardPayload(card.payload || {}, liveBlueprintWizard.value || {})
          : freezeWizardCardPayload(card.payload || {});
        const bid = String(merged.blueprint_id || "").trim();
        if (!bid) continue;
        const step = normalizeWizardStep(merged.step);
        const sampleMode = isFreeformSampleWizardMode(merged);
        const workTid = String(
          merged.working_task_id || (isWritingCopyId(bid) ? bid : "") || ""
        ).trim();
        // 无蓝本·依据样本：blueprint_id 槽里是 w*，必须落 task: 大纲项，禁止 bp: 蓝本模版
        if (sampleMode || isWritingCopyId(bid)) {
          const tid = workTid || bid;
          // 已收录：勿用历史向导卡复活无库的 task:w*（否则同步按钮被样本身份挡掉）
          const collectedNow = String(
            collectedBlueprintId.value ||
              sessionRuntime.value?.collected_blueprint_id ||
              ""
          ).trim();
          const alreadyBp = [...byId.values()].some((d) => {
            const dWid = String(d.writingTaskId || "").trim();
            const dBid = String(d.blueprintId || "").trim();
            return (
              dWid === tid &&
              (isLibraryBlueprintId(dBid) ||
                String(d.id || "") === `bp:${collectedNow}` ||
                (collectedNow && dBid === collectedNow))
            );
          });
          if (
            (collectedNow && !/^w/i.test(collectedNow)) ||
            alreadyBp ||
            (collectedNow && [...byId.keys()].some((k) => k === `bp:${collectedNow}`))
          ) {
            continue;
          }
          const sampleId = String(tid).startsWith("task:") ? tid : `task:${tid}`;
          const existingSample = byId.get(sampleId);
          const keepSampleTitle =
            cleanConversationDocTitle(existingSample?.title) &&
            !looksLikeRawDocTitle(existingSample?.title);
          upsert(sampleId, {
            kind: "outline",
            kindLabel: "无蓝本·依据样本",
            taskId: tid,
            writingTaskId: tid,
            blueprintId: "",
            hasWritingProgress: true,
            wizardMode: "freeform_sample",
            ...(keepSampleTitle
              ? {}
              : { title: merged.title || existingSample?.title || tid }),
            status: wizardStepCardHeadline(step, { ...merged, mode: "freeform_sample" }) || "依据样本",
            wizardStep: step,
            outlineConfirmed:
              Boolean(merged.outline_confirmed) || step === "study" || step === "done",
            study_analyzed: Number(merged.study_analyzed ?? 0),
            study_unanalyzed: Number(merged.study_unanalyzed ?? 0),
            study_mode: String(merged.study_mode || ""),
            chapters: (merged.chapters || []).map(normalizeDocWindowChapter),
          });
          continue;
        }
        // 多文档共用库蓝本时优先更新 doc: 隔离槽，避免写回 bp: 串台
        const docSlotId =
          [...byId.entries()].find(
            ([id, d]) =>
              String(id).startsWith("doc:") && String(d.blueprintId || "").trim() === bid
          )?.[0] || "";
        const slotId = docSlotId || `bp:${bid}`;
        const existing = byId.get(slotId);
        const keepDocTitle =
          cleanConversationDocTitle(existing?.title) &&
          !looksLikeRawDocTitle(existing?.title);
        upsert(slotId, {
          kind: "blueprint",
          kindLabel: workTid ? "文档副本" : "蓝本模版",
          blueprintId: bid,
          taskId: bid,
          writingTaskId: workTid || undefined,
          hasWritingProgress: Boolean(workTid || existing?.hasWritingProgress),
          // 已有会话文档名时不写 title，避免蓝本库条目标题盖掉
          ...(keepDocTitle ? {} : { title: merged.title || bid || (workTid ? "文档副本" : "蓝本模版") }),
          status: wizardStepCardHeadline(step, merged) || "蓝本",
          wizardStep: step,
          outlineConfirmed:
            Boolean(merged.outline_confirmed) || step === "study" || step === "done",
          study_analyzed: Number(merged.study_analyzed ?? 0),
          study_unanalyzed: Number(merged.study_unanalyzed ?? 0),
          study_mode: String(merged.study_mode || ""),
          chapters: (merged.chapters || []).map(normalizeDocWindowChapter),
        });
      }
    }
  }
  // live 向导只写入匹配 blueprint_id 的文档项，避免新建报告后盖掉旧报告进度
  const liveBp = liveBlueprintWizard.value;
  const liveRawBid = String(liveBp?.blueprint_id || "").trim();
  const liveWidEarly = String(liveBp?.working_task_id || "").trim();
  // blueprint_id 若被污染成 w*，回退 source_blueprint_id / 会话选用蓝本
  const liveBid = sanitizeLibraryBlueprintId(
    liveRawBid,
    liveBp?.source_blueprint_id || selectedBlueprint.value || ""
  );
  const liveWid = isWritingCopyId(liveRawBid)
    ? liveRawBid
    : liveWidEarly;
  if (liveBp && (liveBid || liveWid)) {
    const liveChapters = Array.isArray(liveBp.chapters) ? liveBp.chapters : [];
    const liveStep = normalizeWizardStep(liveBp.step);
    const matchBid = liveBid || liveRawBid;
    const liveSample = isFreeformSampleWizardMode(liveBp) || isWritingCopyId(liveRawBid);
    const liveCollected = String(collectedBlueprintId.value || "").trim();
    const liveAlreadyCollected =
      (liveCollected && !/^w/i.test(liveCollected)) ||
      (liveWid &&
        [...byId.values()].some(
          (d) =>
            String(d.writingTaskId || "").trim() === liveWid &&
            isLibraryBlueprintId(d.blueprintId)
        ));
    if (liveSample && liveWid && !liveAlreadyCollected) {
      const sampleId = `task:${liveWid}`;
      const existingLive = byId.get(sampleId);
      const keepLiveTitle =
        cleanConversationDocTitle(existingLive?.title) &&
        !looksLikeRawDocTitle(existingLive?.title);
      upsert(sampleId, {
        kind: "outline",
        kindLabel: "无蓝本·依据样本",
        taskId: liveWid,
        writingTaskId: liveWid,
        blueprintId: "",
        hasWritingProgress: true,
        wizardMode: "freeform_sample",
        ...(keepLiveTitle ? {} : { title: liveBp.title || existingLive?.title || liveWid }),
        status:
          wizardStepCardHeadline(liveStep, { ...liveBp, mode: "freeform_sample" }) || "依据样本",
        wizardStep: liveStep,
        outlineConfirmed:
          Boolean(liveBp.outline_confirmed) || liveStep === "study" || liveStep === "done",
        study_analyzed: Number(liveBp.study_analyzed ?? 0),
        study_unanalyzed: Number(liveBp.study_unanalyzed ?? 0),
        study_mode: String(liveBp.study_mode || ""),
        chapter_count: Number(liveBp.chapter_count || 0) || liveChapters.length || 0,
        chapters: liveChapters.map(normalizeDocWindowChapter),
        mergeChapters: true,
      });
    } else if (!(liveSample && liveWid && liveAlreadyCollected)) {
    const liveDocSlot =
      [...byId.entries()].find(
        ([id, d]) =>
          String(id).startsWith("doc:") &&
          (String(d.blueprintId || "").trim() === matchBid ||
            String(d.writingTaskId || "").trim() === liveWid)
      )?.[0] || "";
    const liveSlotId =
      liveDocSlot || (liveBid ? `bp:${liveBid}` : liveWid ? `task:${liveWid}` : "");
    if (!liveSlotId) {
      /* skip */
    } else {
    const existingLive = byId.get(liveSlotId);
    const keepLiveTitle =
      cleanConversationDocTitle(existingLive?.title) &&
      !looksLikeRawDocTitle(existingLive?.title);
    const onlyLive = {
      kind: "blueprint",
      kindLabel: liveWid ? "文档副本" : "蓝本模版",
      blueprintId: liveBid || sanitizeLibraryBlueprintId(existingLive?.blueprintId, ""),
      taskId: liveBid || existingLive?.taskId || liveWid,
      writingTaskId: liveWid || existingLive?.writingTaskId || undefined,
      hasWritingProgress: Boolean(liveWid || existingLive?.hasWritingProgress),
      ...(keepLiveTitle ? {} : { title: liveBp.title || liveBid || (liveWid ? "文档副本" : "蓝本模版") }),
      status: wizardStepCardHeadline(liveStep, liveBp) || "蓝本",
      wizardStep: liveStep,
      wizardMode:
        String(liveBp.mode || existingLive?.wizardMode || "").trim() || undefined,
      outlineConfirmed:
        Boolean(liveBp.outline_confirmed) || liveStep === "study" || liveStep === "done",
      study_analyzed: Number(liveBp.study_analyzed ?? 0),
      study_unanalyzed: Number(liveBp.study_unanalyzed ?? 0),
      study_mode: String(liveBp.study_mode || ""),
      chapter_count: Number(liveBp.chapter_count || 0) || liveChapters.length || 0,
      chapters: liveChapters.map(normalizeDocWindowChapter),
      mergeChapters: true,
    };
    upsert(liveSlotId, onlyLive);
    }
    }
  }
  // 写作进度只合入「拥有该写作副本」的文档；严禁把 A 的已写状态画到未开写的 B 上
  for (const [tid, card] of latestProgressByTid) {
    const progChapters = (card.payload?.chapters || []).map(normalizeDocWindowChapter);
    const writeStatus = card.payload?.status_label || statusLabel(card.payload?.status) || "";
    const progTitle = cleanConversationDocTitle(card.payload?.title);
    const payloadBp = String(card.payload?.blueprint_id || "").trim();
    const sourceBp = payloadBp;
    // 优先：已声明 writingTaskId===tid 的文档（含 doc: 隔离槽）
    const owned = [...byId.entries()].find(
      ([, d]) => String(d.writingTaskId || "").trim() === tid
    );
    if (owned) {
      const [target, prev] = owned;
      const keepBp = sanitizeLibraryBlueprintId(prev.blueprintId, sourceBp);
      const isBlueprintDoc =
        String(prev.kind || "") === "blueprint" ||
        String(target).startsWith("bp:") ||
        String(target).startsWith("doc:");
      upsert(target, {
        chapters: progChapters,
        mergeChapters: true,
        status: writeStatus || prev.status,
        hasWritingProgress: true,
        writingTaskId: tid,
        ...(progTitle ? { title: progTitle } : {}),
        ...(isBlueprintDoc
          ? {
              blueprintId: keepBp || sanitizeLibraryBlueprintId(prev.blueprintId, ""),
              taskId: keepBp || prev.taskId || tid,
            }
          : { taskId: tid }),
      });
      continue;
    }
    const librarySourceBp = sanitizeLibraryBlueprintId(sourceBp, "");
    const candidates = [
      `task:${tid}`,
      librarySourceBp ? `bp:${librarySourceBp}` : "",
    ].filter(Boolean);
    const target = candidates.find((k) => byId.has(k));
    if (target) {
      const prev = byId.get(target);
      const prevWid = String(prev.writingTaskId || "").trim();
      // 该蓝本槽已绑定其它文档的写作副本 → 进度独立成项，不串台
      if (prevWid && prevWid !== tid) {
        upsert(`task:${tid}`, {
          kind: "writing",
          kindLabel: "写作文档",
          taskId: tid,
          writingTaskId: tid,
          blueprintId: sanitizeLibraryBlueprintId(sourceBp, prev.blueprintId),
          title: progTitle || prev.title || "写作文档",
          status: writeStatus,
          hasWritingProgress: true,
          chapters: progChapters,
        });
        continue;
      }
      const keepBp = sanitizeLibraryBlueprintId(prev.blueprintId, librarySourceBp);
      const isBlueprintDoc = String(prev.kind || "") === "blueprint" || target.startsWith("bp:");
      upsert(target, {
        chapters: progChapters,
        mergeChapters: true,
        status: writeStatus || prev.status,
        hasWritingProgress: true,
        writingTaskId: tid,
        ...(progTitle ? { title: progTitle } : {}),
        ...(isBlueprintDoc
          ? {
              blueprintId: keepBp || sourceBp || prev.blueprintId,
              taskId: keepBp || sourceBp || prev.taskId || tid,
            }
          : { taskId: tid }),
        ...(sourceBp && !keepBp ? { blueprintId: sourceBp } : {}),
      });
      continue;
    }
    if (sourceBp) {
      // 有源蓝本但文档项尚未建好：仅当 bp 槽空闲时挂上；否则独立写作项
      const bpKey = `bp:${sourceBp}`;
      const prevBp = byId.get(bpKey);
      const prevWid = String(prevBp?.writingTaskId || "").trim();
      if (prevWid && prevWid !== tid) {
        upsert(`task:${tid}`, {
          kind: "writing",
          kindLabel: "写作文档",
          taskId: tid,
          writingTaskId: tid,
          blueprintId: sourceBp,
          title: progTitle || sourceBp || "写作文档",
          status: writeStatus,
          hasWritingProgress: true,
          chapters: progChapters,
        });
        continue;
      }
      upsert(bpKey, {
        kind: "blueprint",
        kindLabel: "文档副本",
        blueprintId: sourceBp,
        taskId: sourceBp,
        writingTaskId: tid,
        title: progTitle || sourceBp || "文档副本",
        status: writeStatus,
        hasWritingProgress: true,
        chapters: progChapters,
      });
      continue;
    }
    // 无蓝本（自由写作）才允许独立写作文档项
    upsert(`task:${tid}`, {
      kind: "writing",
      kindLabel: "写作文档",
      taskId: tid,
      title: cleanConversationDocTitle(card.payload?.title) || "写作文档",
      status: writeStatus,
      hasWritingProgress: true,
      chapters: progChapters,
    });
  }
  // 当前写作任务：仅当进度卡已声明 blueprint_id 时才挂到对应蓝本；否则自由写作建 task 项
  if (taskId.value && !byId.has(`task:${taskId.value}`) && !byId.has(`bp:${taskId.value}`)) {
    const progCard = latestProgressByTid.get(String(taskId.value));
    const progBp = String(progCard?.payload?.blueprint_id || "").trim();
    if (progBp && byId.has(`bp:${progBp}`)) {
      upsert(`bp:${progBp}`, {
        writingTaskId: taskId.value,
        hasWritingProgress: true,
      });
    } else if (!progBp) {
      const progTitle = cleanConversationDocTitle(progCard?.payload?.title);
      upsert(`task:${taskId.value}`, {
        kind: "writing",
        kindLabel: "写作文档",
        taskId: taskId.value,
        title: progTitle || "写作文档",
      });
    }
  }
  const docs = dedupeConversationDocuments(applyLiveWritingOverlayToDocs([...byId.values()]));
  // 无蓝本：runtime 已确认大纲时，覆盖历史「可调整」预览卡残留的待确认态
  const ff = sessionRuntime.value?.workflows?.freeform || {};
  const ffPhase = String(ff.phase || "").toLowerCase();
  const pointsStep = String(ff.points_step || "").toLowerCase();
  const ffConfirmed =
    Boolean(ff.outline_confirmed) ||
    ffPhase.includes("points") ||
    ffPhase === "freeform_points" ||
    ffPhase === "ready_to_write" ||
    ffPhase.includes("writing") ||
    ffPhase === "confirm_export" ||
    ["generated", "skipped", "ask"].includes(pointsStep);
  const focusKey = String(sessionRuntime.value?.focus_document_key || "").trim();
  // 文档窗 tab：会话文档名优先；勿用蓝本库条目标题（与 live 向导 title 相同则不覆盖）
  const liveBpTitle = cleanConversationDocTitle(liveBlueprintWizard.value?.title || "");
  const namedTitle = cleanConversationDocTitle(
    sessionRuntime.value?.writing_doc_title ||
      ff.title ||
      sessionRuntime.value?.session_goal?.doc_title ||
      ""
  );
  const focusMeta = focusKey
    ? sessionRuntime.value?.session_documents?.[focusKey]
    : null;
  const registryTitle = cleanConversationDocTitle(focusMeta?.title || "");
  let displayTitle = "";
  if (namedTitle && !looksLikeRawDocTitle(namedTitle) && namedTitle !== liveBpTitle) {
    displayTitle = namedTitle;
  } else if (
    registryTitle &&
    !looksLikeRawDocTitle(registryTitle) &&
    registryTitle !== liveBpTitle
  ) {
    displayTitle = registryTitle;
  }
  if (displayTitle) {
    // 仅盖到真正焦点文档；且命名标题须与该文档 registry/task 对得上，禁止把文档2标题画到文档1章节上
    const focusTid = String(
      focusMeta?.writing_task_id || focusMeta?.task_id || ""
    ).trim();
    const focusTitle = cleanConversationDocTitle(focusMeta?.title || "");
    const titleMatchesFocus =
      !focusTitle ||
      !namedTitle ||
      focusTitle === namedTitle ||
      looksLikeRawDocTitle(focusTitle);
    for (const d of docs) {
      const id = String(d.id || "").trim();
      const bid = String(d.blueprintId || "").trim();
      const dTid = String(d.writingTaskId || d.taskId || "").trim();
      const isFocus =
        (focusKey && id === focusKey) ||
        (focusKey.startsWith("bp:") && bid && `bp:${bid}` === focusKey) ||
        (focusKey.startsWith("task:") && dTid && focusKey === `task:${dTid}`) ||
        (focusTid && dTid && focusTid === dTid);
      if (!isFocus) {
        if (!(!focusKey && docs.length === 1)) continue;
        // 无焦点时才用标题对齐，避免把文档2名字画到文档1；焦点文档直接盖会话定名
        if (namedTitle && !titleMatchesFocus) continue;
      }
      d.title = displayTitle;
    }
  }
  if (!ffConfirmed) return docs;
  const focusTid = focusKey.startsWith("task:") ? focusKey.slice(5) : "";
  for (const d of docs) {
    if (String(d.kind || "") === "blueprint" || String(d.blueprintId || "").trim()) continue;
    const tid = String(d.taskId || "").trim();
    if (focusTid && tid && tid !== focusTid) continue;
    d.outlineConfirmed = true;
  }
  return docs;
});

/** 当前文档窗应对应的预建资料包 id（按文档隔离，勿串上一份蓝本的包） */
const docWindowPendingMaterialsPackId = computed(() => {
  const rt = sessionRuntime.value || {};
  const byBp = rt.pending_materials_pack_by_blueprint || {};
  const active = conversationDocuments.value.find((d) => d.id === docWindowActiveId.value)
    || conversationDocuments.value[0]
    || null;
  const bid = String(active?.blueprintId || "").trim();
  // 仅用当前文档自己的库蓝本 id 查预建包；无蓝本文档不要回落全局 pending（会串上一份）
  if (bid && !/^w/i.test(bid) && byBp[bid]) return String(byBp[bid] || "").trim();
  if (isFreeformWritingSession()) return "";
  if (bid && !/^w/i.test(bid)) return String(rt.pending_materials_pack_id || "").trim();
  return "";
});

/** 蓝本目录提取是否已全部结束（可安全打开文档窗） */
function isOutlineExtractFullySettled(live = liveBlueprintWizard.value || {}) {
  const liveBid = String(live.blueprint_id || "").trim();
  // 无蓝本路径不走向导提取态
  if (!liveBid) return true;
  if (live.outline_extract_active || live.trigger_outline_extract) return false;
  const liveStep = normalizeWizardStep(live.step);
  // 必须已到「确认目录」及之后；提取中途即便有部分章节预览也不开窗
  return wizardStepIndex(liveStep) >= wizardStepIndex("outline_confirm");
}

function docWindowChapterCount(doc, live = liveBlueprintWizard.value || {}) {
  const liveBid = String(live.blueprint_id || "").trim();
  const docTid = String(doc?.taskId || doc?.blueprintId || "").trim();
  const liveChapters =
    liveBid && docTid === liveBid && Array.isArray(live.chapters) ? live.chapters.length : 0;
  return (
    liveChapters ||
    (doc?.chapters || []).length ||
    Number(liveBid && docTid === liveBid ? live.chapter_count || 0 : 0) ||
    Number(doc?.chapter_count || 0) ||
    0
  );
}

async function openDocWindowWhenOutlineSynced(doc) {
  const tid = String(doc?.taskId || doc?.blueprintId || doc?.writingTaskId || "").trim();
  const docId = String(doc?.id || "").trim();
  if ((!tid && !docId) || docWindowOpening.value) return;
  if (sessionHydrating.value || !docWindowAutoOpenArmed.value) return;
  const live = liveBlueprintWizard.value || {};
  const liveBid = String(live.blueprint_id || "").trim();
  const focusKey = String(sessionRuntime.value?.focus_document_key || "").trim();
  const isFocusTarget = Boolean(
    focusKey && (docId === focusKey || (tid && focusKey === `task:${tid}`))
  );
  const isFreeformOutline =
    String(doc?.kind || "") === "outline" ||
    String(doc?.kind || "") === "writing" ||
    (!liveBid && String(doc?.kind || "") !== "blueprint");
  // 有 live 蓝本时只对该蓝本自动开窗；焦点已切到无蓝本新文档时例外
  if (liveBid && tid && tid !== liveBid && !isFocusTarget) return;
  if (liveBid && !isFocusTarget && !isOutlineExtractFullySettled(live)) return;
  if (!liveBid && !isFreeformOutline && String(doc?.kind || "") === "blueprint") return;

  const chapterN = docWindowChapterCount(doc, live);
  // 新定名空文档也要切过去，勿因无章节卡在上一份旧目录
  if (!chapterN && !isFocusTarget) return;

  const autoKey = `${sessionId.value || "nosession"}:${doc.id}:confirm:${chapterN || 0}`;
  if (docWindowAutoOpenedKey.value === autoKey) return;
  if (docWindowOpen.value) {
    docWindowAutoOpenedKey.value = autoKey;
    docWindowActiveId.value = doc.id;
    if (isFocusTarget) realignLiveWizardToActiveDoc(doc);
    return;
  }

  docWindowAutoOpenedKey.value = autoKey;
  docWindowActiveId.value = doc.id;
  if (isFocusTarget) realignLiveWizardToActiveDoc(doc);
  // 自动开窗：先挂载窗口（自带读数遮罩），避免无反馈
  docWindowOpen.value = true;
}

function armDocWindowAutoOpen() {
  if (sessionHydrating.value) return;
  docWindowAutoOpenArmed.value = true;
}

function pickDocWindowTarget(docs = conversationDocuments.value) {
  const list = Array.isArray(docs) ? docs : [];
  // 当前小窗文档优先：生成大纲/softSync 时勿落到列表第一项蓝本
  const activeNorm = normalizeDocumentRegistryKey(String(docWindowActiveId.value || "").trim());
  if (activeNorm) {
    const cur = list.find((d) => String(d.id || "") === activeNorm);
    if (cur) return cur;
  }
  // 服务端焦点优先：新定名文档应立刻切过去，勿被旧蓝本/向导抢走
  const focusKey = normalizeDocumentRegistryKey(
    String(sessionRuntime.value?.focus_document_key || "").trim()
  );
  if (focusKey) {
    const byKey = list.find((d) => String(d.id || "") === focusKey);
    if (byKey) return byKey;
  }
  // 无蓝本：优先当前写作/大纲任务，勿被同会话旧蓝本抢走焦点
  if (isFreeformWritingSession()) {
    const tid = String(taskId.value || "").trim();
    if (tid) {
      const hit =
        list.find((d) => String(d.id || "") === `task:${tid}`) ||
        list.find((d) => String(d.taskId || "") === tid && String(d.kind || "") === "outline") ||
        list.find((d) => String(d.taskId || "") === tid);
      if (hit) return hit;
    }
    const outlines = list.filter((d) => d.kind === "outline" && (d.chapters || []).length > 0);
    if (outlines.length) return outlines[outlines.length - 1];
  }
  // 优先当前会话活动蓝本（含已合入写作进度的蓝本项），避免落到 clone 写作文档
  const preferIds = [
    sanitizeLibraryBlueprintId(liveBlueprintWizard.value?.blueprint_id, ""),
    String(selectedBlueprint.value || "").trim(),
    String(taskId.value || "").trim(),
  ].filter(Boolean);
  for (const id of preferIds) {
    const hit =
      list.find((d) => String(d.id || "") === `bp:${id}`) ||
      list.find((d) => String(d.blueprintId || "") === id) ||
      list.find((d) => String(d.writingTaskId || "") === id) ||
      list.find((d) => String(d.taskId || "") === id && String(d.kind || "") !== "writing") ||
      list.find((d) => String(d.id || "") === `task:${id}`);
    if (hit) return hit;
  }
  // 无明确活动项：优先蓝本/大纲，最后才是独立写作文档
  return (
    list.find((d) => d.kind === "blueprint" && (d.chapters || []).length > 0) ||
    list.find((d) => d.kind === "outline" && (d.chapters || []).length > 0) ||
    list.find((d) => (d.chapters || []).length > 0 && d.kind !== "writing") ||
    list.find((d) => (d.chapters || []).length > 0) ||
    null
  );
}

/** 本轮出现新蓝本目录、无蓝本大纲、文档窗提示或要素配置时，武装并尝试打开文档窗 */
function noteFreshDocWindowTriggers(cards = []) {
  const list = Array.isArray(cards) ? cards : [];
  const hasElementConfigure = list.some((c) => c?.type === "element_configure_pending");
  const hasDocHint = list.some((c) => c?.type === "doc_window_hint");
  const wiz = list.find((c) => c?.type === "blueprint_wizard");
  const wizStep = normalizeWizardStep(wiz?.payload?.step);
  const hasNewBlueprintOutline =
    Boolean(String(wiz?.payload?.blueprint_id || "").trim()) &&
    wizardStepIndex(wizStep) >= wizardStepIndex("outline_confirm");
  const outlinePreview = list.find((c) => c?.type === "outline_preview");
  const freeformChapters = outlinePreview?.payload?.chapters || [];
  const freeformTid = String(outlinePreview?.payload?.task_id || "").trim();
  const hasFreeformOutline =
    Boolean(outlinePreview) && (freeformChapters.length > 0 || Boolean(freeformTid));
  if (!hasElementConfigure && !hasDocHint && !hasNewBlueprintOutline && !hasFreeformOutline) return;
  armDocWindowAutoOpen();

  // 同会话新无蓝本大纲：强制切到 task:{id} 并刷新，避免仍停在旧蓝本
  if (hasFreeformOutline && freeformTid) {
    taskId.value = freeformTid;
    selectedBlueprint.value = "";
    liveBlueprintWizard.value = null;
    const docId = `task:${freeformTid}`;
    const openFreeform = () => {
      docWindowActiveId.value = docId;
      if (!docWindowOpen.value) docWindowOpen.value = true;
      bumpDocOutlineReload({ force: true });
      const target =
        (conversationDocuments.value || []).find((d) => d.id === docId) ||
        pickDocWindowTarget();
      if (target) openDocWindowWhenOutlineSynced(target).catch(() => {});
    };
    openFreeform();
    nextTick(openFreeform);
    return;
  }

  // 蓝本大纲卡片可能刚写入 messages，下一 tick 再取文档列表更稳
  const openNow = () => {
    const target = pickDocWindowTarget();
    if (target) openDocWindowWhenOutlineSynced(target).catch(() => {});
  };
  openNow();
  nextTick(openNow);
}

watch(
  () =>
    [
      Boolean(liveBlueprintWizard.value?.outline_extract_active),
      Boolean(liveBlueprintWizard.value?.trigger_outline_extract),
      String(liveBlueprintWizard.value?.blueprint_id || ""),
    ].join("~"),
  (sig, prevSig) => {
    if (sessionHydrating.value) return;
    const [active, trigger, bid] = sig.split("~");
    const [pActive, pTrigger, pBid] = String(prevSig || "").split("~");
    if (active === "true" || trigger === "true") {
      armDocWindowAutoOpen();
      return;
    }
    if (bid && bid !== (pBid || "")) armDocWindowAutoOpen();
    if (pActive === "true" || pTrigger === "true") armDocWindowAutoOpen();
  }
);

watch(
  () =>
    [
      conversationDocuments.value
        .map((d) => `${d.id}:${(d.chapters || []).length}:${d.wizardStep || ""}`)
        .join("|"),
      liveBlueprintWizard.value?.step,
      liveBlueprintWizard.value?.outline_extract_active,
      liveBlueprintWizard.value?.trigger_outline_extract,
      liveBlueprintWizard.value?.blueprint_id,
      docWindowAutoOpenArmed.value,
    ].join("~"),
  () => {
    const docs = conversationDocuments.value;
    if (!docs.length) {
      docWindowActiveId.value = "";
      return;
    }
    if (docWindowOpen.value && !docs.some((item) => item.id === docWindowActiveId.value)) {
      const activeRaw = String(docWindowActiveId.value || "").trim();
      const activeNorm = normalizeDocumentRegistryKey(activeRaw);
      const runtimeFocus = normalizeDocumentRegistryKey(
        String(sessionRuntime.value?.focus_document_key || "").trim()
      );
      // 新建样本登记晚一拍：active/runtime 已是 task:w* 时不要 snap 到其它文档
      const pendingNewTask =
        activeNorm.startsWith("task:") &&
        (activeNorm === runtimeFocus || Boolean(streamFocusPinnedKey));
      const remapped = docs.find((item) => item.id === activeNorm);
      if (remapped?.id) {
        docWindowActiveId.value = remapped.id;
      } else if (pendingNewTask) {
        docWindowActiveId.value = activeNorm;
      } else if (runtimeFocus && docs.some((d) => d.id === runtimeFocus)) {
        docWindowActiveId.value = runtimeFocus;
      } else {
        const prefer = pickDocWindowTarget(docs);
        // 禁止无脑落到列表头：优先当前 taskId 对应项
        const tid = String(taskId.value || "").trim();
        const byTid = tid
          ? docs.find((d) => d.id === `task:${tid}`) ||
            docs.find((d) => String(d.taskId || d.writingTaskId || "") === tid)
          : null;
        docWindowActiveId.value = prefer?.id || byTid?.id || docs[docs.length - 1].id;
      }
    }
    if (sessionHydrating.value || !docWindowAutoOpenArmed.value) return;
    if (!isOutlineExtractFullySettled()) return;
    const target = pickDocWindowTarget(docs);
    if (!target) return;
    openDocWindowWhenOutlineSynced(target).catch(() => {});
  },
  { immediate: true }
);

let uiStatePersistTimer = 0;
/** 非响应式：章节勾选组件实例 */
const pickerCompRefs = new Map();
const lastBindings = ref({ blueprint_ids: [], render_template_ids: [], word_style_ids: [] });
const historyItems = ref([]);
const historyLoading = ref(false);
const chapterEdits = ref({});
const chapterTabs = ref({});
const openChapters = ref({});
const taskElementNodes = ref({});

function syncTaskElementsFromProgress(payload) {
  const taskId = payload?.task_id;
  if (!taskId || !Array.isArray(payload?.element_nodes)) return;
  const prev = taskElementNodes.value[taskId] || [];
  const merged = mergeElementNodes(prev, payload.element_nodes).map((item) => ({
    ...item,
    knowledge_folder_ids: [...(item.knowledge_folder_ids || [])],
    tags: [...(item.tags || [])],
  }));
  taskElementNodes.value = {
    ...taskElementNodes.value,
    [taskId]: merged,
  };
}

function elementsForTask(taskId) {
  return taskElementNodes.value[taskId] || [];
}

function taskIdForCard(card) {
  const fromItem = card?.payload?.items?.[0]?.task_id;
  return fromItem || taskId.value || "";
}

function touchTaskElements(taskId) {
  if (!taskId) return;
  taskElementNodes.value = { ...taskElementNodes.value };
}

function syncChapterElementIdsFromConfig(taskId, ch) {
  const edit = ensureChapterEdit(taskId, ch);
  edit.element_ids = unionElementIds(edit.element_ids, ch?.config?.element_ids, ch?.element_ids);
  return edit;
}

function chapterLinkedElements(cardPayload, ch) {
  const taskId = cardPayload?.task_id;
  const edit = syncChapterElementIdsFromConfig(taskId, ch);
  const ids = edit.element_ids || [];
  const all = elementsForTask(taskId);
  return ids.map((id) => all.find((item) => item.id === id)).filter(Boolean);
}

const chapterElementAssociate = ref({
  visible: false,
  taskId: "",
  chapterKey: "",
  label: "",
  linkedIds: [],
  associateScope: "chapter",
  seedCreateDraft: null,
});
const associatePackSlots = ref([]);
const associatePointContext = ref(null);

async function loadPackSlotOptionsForTask(taskId) {
  const tid = String(taskId || "").trim();
  if (!tid) return [];
  try {
    const task = await request(`/api/writing/tasks/${encodeURIComponent(tid)}`);
    const pid = String(task?.materials_pack_id || "").trim();
    if (!pid) return [];
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
    return [...byNo.values()].sort((a, b) => a.no - b.no);
  } catch {
    return [];
  }
}

function slotFromPointText(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const m = raw.match(/（待补充[：:]([^）)]*)）/);
  return (m && String(m[1] || "").trim()) || "";
}

function fallbackElementDraftFromPoint({ slot, pointText, title, sequence }) {
  const name = (String(slot || "").trim() || String(pointText || "").trim() || "写作要素").slice(0, 24);
  const label = `${sequence || ""} ${title || ""}`.trim() || "本章";
  const purpose = `用于「${label}」中「${name}」要点的资料要素`;
  const blob = `${slot || ""} ${pointText || ""}`;
  const looksFile = /责任书|报告|方案|合同|协议|批复|文件|资料|证书|清单|表格|证明|纪要|通知|制度|标准/.test(blob);
  if (looksFile) {
    return {
      kind: "file",
      name,
      category: name,
      purpose,
      tags: [name],
      query: String(slot || pointText || name).trim().slice(0, 48),
      value: "",
      prompt: "",
      kv_pairs: [{ key: "", value: "" }],
    };
  }
  return {
    kind: "kv",
    name,
    category: name,
    purpose,
    tags: [name],
    query: "",
    value: "",
    prompt: "",
    kv_pairs: [{ key: slot || name, value: "" }],
  };
}

async function collectChapterLinkedIds(taskId, ch, edit) {
  const tid = String(taskId || "").trim();
  const ck = String(ch?.key || "").trim();
  const progress = latestProgressCards().find((c) => c?.type === "progress");
  const progCh = (progress?.payload?.chapters || []).find((c) => c?.key === ck);
  let ids = unionElementIds(
    edit?.element_ids,
    ch?.element_ids,
    ch?.config?.element_ids,
    progCh?.element_ids,
    progCh?.config?.element_ids
  );
  if (ids.length || !tid || !ck) return ids;
  try {
    const slim = await request(
      docOutlineUrl(tid, { writingTaskId: writingTaskIdForTask(tid) })
    );
    const node = (slim?.chapters || []).find((item) => String(item?.key || "") === ck);
    ids = unionElementIds(ids, node?.element_ids, node?.config?.element_ids);
  } catch {
    /* 无目录时仍可打开空关联面板 */
  }
  return ids;
}

async function openChapterElementAssociate(taskId, ch) {
  if (!taskId || !ch?.key) return;
  const edit = syncChapterElementIdsFromConfig(taskId, ch);
  const key = chapterEditKey(taskId, ch.key);
  const chapterEdit = chapterEdits.value[key] || edit;
  let seedCreateDraft = null;
  const ctx = associatePointContext.value;
  if (ctx) {
    let pointText = "";
    let slot = String(ctx.slot || "").trim();
    const items = Array.isArray(ctx.pointItems) && ctx.pointItems.length
      ? ctx.pointItems
      : Array.isArray(chapterEdit.point_items)
        ? chapterEdit.point_items
        : [];
    const idx = Number(ctx.index);
    let row = null;
    if (Number.isFinite(idx) && idx >= 0 && items[idx]) {
      row = items[idx];
    } else if (ctx.id) {
      row = items.find((r) => String(r?.id || "") === String(ctx.id).trim()) || null;
    }
    if (row) {
      pointText = String(row.text || row.label || "").trim();
      if (!slot) slot = String(row.slot || "").trim();
    }
    if (!slot) slot = slotFromPointText(pointText);
    const title = String(ch.title || chapterEdit.title || "").trim();
    const sequence = String(ch.sequence || chapterEdit.sequence || "").trim();
    seedCreateDraft = fallbackElementDraftFromPoint({ slot, pointText, title, sequence });
    const loading = ElMessage({
      message: "正在生成要素配置…",
      type: "info",
      duration: 0,
    });
    try {
      const data = await request(
        `/api/writing/tasks/${encodeURIComponent(taskId)}/elements/draft-from-point`,
        {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            chapter_key: ch.key,
            title,
            sequence,
            describes: String(chapterEdit.describes || "").trim(),
            point_text: pointText,
            slot,
            point_id: String(ctx.id || "").trim(),
          }),
        }
      );
      if (data?.draft && typeof data.draft === "object") {
        seedCreateDraft = { ...seedCreateDraft, ...data.draft };
      }
      ElMessage.success(data?.from_llm ? "已生成要素草稿，请确认" : "已预填要素草稿，请确认");
    } catch (error) {
      ElMessage.warning(error?.message || "已用要点预填要素草稿，请核对");
    } finally {
      loading.close();
    }
  }
  chapterElementAssociate.value = {
    visible: true,
    taskId,
    chapterKey: ch.key,
    label: `${ch.sequence || ""} ${ch.title || ch.key}`.trim(),
    linkedIds: await (async () => {
      if (ctx) {
        const items = Array.isArray(ctx.pointItems) && ctx.pointItems.length
          ? ctx.pointItems
          : Array.isArray(chapterEdit.point_items)
            ? chapterEdit.point_items
            : [];
        const idx = Number(ctx.index);
        let row = null;
        if (Number.isFinite(idx) && idx >= 0 && items[idx]) row = items[idx];
        else if (ctx.id) {
          row = items.find((r) => String(r?.id || "") === String(ctx.id).trim()) || null;
        }
        const eid = String(row?.element_id || "").trim();
        return eid ? [eid] : [];
      }
      return collectChapterLinkedIds(taskId, ch, edit);
    })(),
    associateScope: ctx ? "point" : "chapter",
    seedCreateDraft,
  };
  associatePackSlots.value = await loadPackSlotOptionsForTask(taskId);
}

function patchChapterElementIdsEverywhere(tid, chapterKey, linkedIds, pointPatch = null) {
  const ids = [...linkedIds];
  const pointItems = Array.isArray(pointPatch?.point_items) ? pointPatch.point_items : null;
  const points = Array.isArray(pointPatch?.points) ? pointPatch.points : null;
  const targetTid = String(tid || "").trim();
  // 只改本 task 的进度卡，禁止按 chapter.key 串改其它文档
  const progress = (messages.value || [])
    .flatMap((m) => m.cards || [])
    .reverse()
    .find(
      (c) =>
        c?.type === "progress" &&
        String(c?.payload?.task_id || "").trim() === targetTid
    );
  const hit = (progress?.payload?.chapters || []).find((ch) => ch?.key === chapterKey);
  if (hit) {
    hit.config = {
      ...(hit.config || {}),
      element_ids: ids,
      ...(pointItems?.length
        ? { point_items: pointItems.map((row) => ({ ...row })), points: [...(points || [])] }
        : {}),
    };
    hit.element_ids = ids;
    if (pointItems?.length) {
      hit.point_items = pointItems.map((row) => ({ ...row }));
      hit.points = [...(points || [])];
    }
  }
  const live = liveBlueprintWizard.value;
  const liveBid = String(live?.blueprint_id || "").trim();
  // 禁止 !liveBid 时改 live：避免无蓝本焦点时误写向导章节
  if (
    live &&
    Array.isArray(live.chapters) &&
    targetTid &&
    (liveBid === targetTid || String(live.task_id || "") === targetTid)
  ) {
    liveBlueprintWizard.value = {
      ...live,
      chapters: live.chapters.map((ch) =>
        String(ch?.key || "") === chapterKey
          ? {
              ...ch,
              element_ids: ids,
              config: {
                ...(ch.config || {}),
                element_ids: ids,
                ...(pointItems?.length
                  ? {
                      point_items: pointItems.map((row) => ({ ...row })),
                      points: [...(points || [])],
                    }
                  : {}),
              },
              ...(pointItems?.length
                ? {
                    point_items: pointItems.map((row) => ({ ...row })),
                    points: [...(points || [])],
                  }
                : {}),
            }
          : ch
      ),
    };
  }
}

async function onChapterElementAssociateConfirm({ linkedIds, newElements }) {
  const { taskId: tid, chapterKey } = chapterElementAssociate.value;
  if (!tid || !chapterKey) return;
  const key = chapterEditKey(tid, chapterKey);
  let all = [...elementsForTask(tid)];
  for (const el of newElements || []) {
    if (!all.some((item) => item.id === el.id)) all.push(el);
  }
  taskElementNodes.value = { ...taskElementNodes.value, [tid]: all };
  const edit = chapterEdits.value[key] || ensureChapterEdit(tid, { key: chapterKey });
  const prevChapterIds = (Array.isArray(edit.element_ids) ? edit.element_ids : [])
    .map((id) => String(id || "").trim())
    .filter(Boolean);
  const prevIds = [...prevChapterIds];
  const newIds = linkedIds
    .map((id) => String(id || "").trim())
    .filter(Boolean)
    .filter((id) => !prevIds.includes(id));
  let pointItems = Array.isArray(edit.point_items) ? [...edit.point_items] : [];
  if (!pointItems.length && associatePointContext.value?.pointItems?.length) {
    pointItems = associatePointContext.value.pointItems.map((row) => ({ ...row }));
  }
  if (!pointItems.length) {
    pointItems = syncPointItemsFromPoints(
      String(edit.pointsText || "")
        .split(/\n+/)
        .map((line) => line.trim())
        .filter(Boolean),
      []
    );
  }
  const pointItemLink = associatePointContext.value;
  const isPointScope = Boolean(pointItemLink);
  let chapterElementIds = [...prevChapterIds];
  if (isPointScope) {
    pointItems = linkPointItemsToElements(pointItems, all, {
      elementIds: linkedIds,
      newElementIds: newIds,
      pointLink: pointItemLink,
      assignOrphans: false,
    });
  } else {
    // 关联面板提交的是本章完整列表（含取消勾选）；勿再并回 prev，否则无法取消关联
    chapterElementIds = [...linkedIds];
    edit.element_ids = [...chapterElementIds];
  }
  const pointsLines = pointsFromPointItems(pointItems);
  if (!isPointScope) {
    edit.element_ids = [...chapterElementIds];
  }
  edit.point_items = pointItems;
  edit.pointsText = pointsLines.join("\n");
  chapterEdits.value = { ...chapterEdits.value, [key]: { ...edit } };
  associatePointContext.value = null;
  touchTaskElements(tid);
  const pointPatch = {
    point_items: pointItems.map((row) => ({ ...row })),
    points: pointsLines,
  };
  patchChapterElementIdsEverywhere(tid, chapterKey, chapterElementIds, pointPatch);

  const elementsAssociatedLocate = {
    taskId: tid,
    chapterKey,
    tab: "config",
    elementNodes: all,
    elementIds: [...chapterElementIds],
    newElementIds: newIds,
    pointItemLink,
    associateScope: isPointScope ? "point" : "chapter",
    pointItems: pointItems.map((row) => ({ ...row })),
  };

  // 先乐观回显，再静默落库；勿走 configure_chapter 刷「保存章节配置」，
  // 否则会占住 sending，随后点「重写本章」会被静默丢掉。
  driveDocWindow("elements_associated", elementsAssociatedLocate);

  try {
    if (all.length) {
      await request(`/api/writing/tasks/${encodeURIComponent(tid)}/elements`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ elements: all }),
      });
    }
    const slim = await request(
      docOutlineUrl(tid, { fresh: true, writingTaskId: writingTaskIdForTask(tid) })
    );
    const chapters = (slim?.chapters || []).map((item) => ({ ...item }));
    const idx = chapters.findIndex((item) => String(item.key || "") === chapterKey);
    let savedChapter = null;
    if (idx >= 0) {
      const prev = chapters[idx];
      savedChapter = {
        ...prev,
        element_ids: [...chapterElementIds],
        point_items: pointItems.map((row) => ({ ...row })),
        points: pointsLines,
        config: {
          ...(prev.config && typeof prev.config === "object" ? prev.config : {}),
          element_ids: [...chapterElementIds],
          point_items: pointItems.map((row) => ({ ...row })),
          points: pointsLines,
        },
      };
      chapters[idx] = savedChapter;
      await request(`/api/writing/tasks/${encodeURIComponent(tid)}/outline`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ chapters }),
      });
    }
    ElMessage.success("要素关联已保存");
    if (savedChapter) {
      applyChapterSnapshotToSession(tid, chapterKey, savedChapter);
    }
  } catch (error) {
    ElMessage.error(error?.message || "保存要素关联失败");
  }

  // 落盘后再推一次，防止 configure 的 config_saved / loadHistory 冲掉乐观态
  patchChapterElementIdsEverywhere(tid, chapterKey, chapterElementIds, pointPatch);
  driveDocWindow("elements_associated", elementsAssociatedLocate);
  // 新建要素：入队待收录（只亮角标，不自动弹窗）；纯勾选已有库要素不打扰
  const brandNew = (newElements || []).filter(
    (el) => el?.id && !String(el.library_item_id || "").trim()
  );
  if (brandNew.length) {
    await queueElementLibraryPending({
      taskId: tid,
      chapterKey,
      chapterTitle: String(chapterElementAssociate.value?.label || "").trim(),
      elementIds: brandNew.map((el) => el.id),
      elements: brandNew,
      openDialog: false,
    });
  }
  bumpDocOutlineReload({ force: true });
}
const renderTemplates = ref([]);
const wordStyles = ref([]);
const exportTemplateId = ref("");
const exportWordStyleId = ref("ws_default");
const exportConfirmedOnly = ref(false);
const exportBusy = ref(false);
const exportPdfBusy = ref(false);
const saveBlueprintBusy = ref(false);
const syncBlueprintBusy = ref(false);
/** 收录成功后立刻隐藏入口，不等 softSync 刷新 runtime */
const saveBlueprintHiddenLocal = ref(false);
const saveBlueprintHiddenForTask = ref("");
const collectBlueprintDialog = ref({
  visible: false,
  taskId: "",
  title: "",
  category: "",
  tags: [],
  resolve: null,
});

function openCollectBlueprintDialog({
  taskId = "",
  title = "",
  category = "",
  tags = [],
} = {}) {
  return new Promise((resolve) => {
    collectBlueprintDialog.value = {
      visible: true,
      taskId: String(taskId || "").trim(),
      title: String(title || "").trim(),
      category: String(category || "").trim(),
      tags: (tags || []).map((t) => String(t || "").trim()).filter(Boolean),
      resolve,
    };
  });
}

function onCollectBlueprintCancel() {
  const resolve = collectBlueprintDialog.value.resolve;
  collectBlueprintDialog.value = {
    visible: false,
    taskId: "",
    title: "",
    category: "",
    tags: [],
    resolve: null,
  };
  if (typeof resolve === "function") resolve(null);
}

function onCollectBlueprintConfirm(meta) {
  const resolve = collectBlueprintDialog.value.resolve;
  const taskId = collectBlueprintDialog.value.taskId;
  collectBlueprintDialog.value = {
    visible: false,
    taskId: "",
    title: "",
    category: "",
    tags: [],
    resolve: null,
  };
  if (typeof resolve === "function") {
    resolve({
      task_id: taskId,
      title: meta?.title || "",
      category: meta?.category || "",
      tags: Array.isArray(meta?.tags) ? meta.tags : [],
    });
  }
}

async function collectBlueprintWithMeta(sourceTaskId, defaults = {}) {
  const tid = String(sourceTaskId || "").trim();
  if (!tid || saveBlueprintBusy.value || sending.value) return false;
  const meta = await openCollectBlueprintDialog({
    taskId: tid,
    title: defaults.title || composerSaveBlueprint.value?.suggested_title || "",
    category: defaults.category || "",
    tags: defaults.tags || [],
  });
  if (!meta) return false;
  saveBlueprintBusy.value = true;
  try {
    await streamChat({
      message: "",
      action: "save_freeform_as_blueprint",
      action_payload: {
        task_id: meta.task_id || tid,
        title: meta.title,
        category: meta.category,
        tags: meta.tags,
      },
      client_sync: buildUiStatePayload(),
    });
    // 立刻藏收录入口，并 softSync 拉齐 collected / session_documents，否则「同步到蓝本库」要等下一轮才出现
    markFreeformSavedAsBlueprint();
    await softSyncSessionAfterWizardAction();
    const collected = String(
      sessionRuntime.value?.collected_blueprint_id ||
        sessionRuntime.value?.freeform_saved_blueprint_id ||
        ""
    ).trim();
    if (collected) {
      markFreeformSavedAsBlueprint(collected);
      selectedBlueprint.value = collected;
      if (!isWritingCopyId(String(taskId.value || "").trim())) {
        taskId.value = tid;
      }
      // 焦点切到收录后的 bp 槽时保留写作副本，方便同步按钮判定
      const focusKey = `bp:${collected}`;
      if ((conversationDocuments.value || []).some((d) => d.id === focusKey)) {
        docWindowActiveId.value = focusKey;
      }
      bumpDocOutlineReload({ force: true });
    }
    ElMessage.success("已收录到蓝本库，下次写同类文档可直接选用。");
    return true;
  } catch (error) {
    if (!isAbortError(error)) {
      const msg = String(error?.message || "");
      if (/同名|已有/.test(msg)) {
        ElMessage.warning(msg);
        saveBlueprintBusy.value = false;
        // 名称冲突：带着已填分类/标签再弹一次改名
        return collectBlueprintWithMeta(tid, {
          title: "",
          category: meta.category,
          tags: meta.tags,
        });
      }
      ElMessage.error(msg || "收录到蓝本库失败");
    }
    return false;
  } finally {
    saveBlueprintBusy.value = false;
  }
}
const outlineDrafts = ref({});

const composerSaveBlueprint = computed(() => {
  const raw = sessionRuntime.value?.composer_save_blueprint;
  if (!raw || !raw.show || !raw.task_id) return null;
  if (
    saveBlueprintHiddenLocal.value &&
    String(saveBlueprintHiddenForTask.value || "").trim() === String(raw.task_id || "").trim()
  ) {
    return null;
  }
  return raw;
});
/** 本任务已收录到蓝本库的 id（勿用 sync_blueprint_id：其中可能串了上一份选用蓝本） */
const collectedBlueprintId = computed(() =>
  String(
    sessionRuntime.value?.collected_blueprint_id ||
      sessionRuntime.value?.workflows?.freeform?.collected_blueprint_id ||
      sessionRuntime.value?.workflows?.freeform?.freeform_saved_blueprint_id ||
      sessionRuntime.value?.freeform_saved_blueprint_id ||
      ""
  ).trim()
);
/** 大纲卡「收录到蓝本库」：跟文档窗同一套条件，不要被上一份收录的本地标记整会话藏掉 */
const showOutlineCollectBlueprint = computed(
  () => Boolean(composerSaveBlueprint.value) && !collectedBlueprintId.value
);

/** 当前文档窗焦点文档（蓝本只是模版；操作落在 writingTaskId 副本上） */
function activeConversationDocument() {
  const id = String(docWindowActiveId.value || "").trim();
  const docs = conversationDocuments.value || [];
  if (id) {
    const hit = docs.find((d) => String(d.id || "") === id);
    if (hit) return hit;
  }
  return docs[0] || null;
}

/** 文档副本 id：优先当前文档 writingTaskId，其次会话 taskId（须为 w*） */
function activeWritingCopyTaskId() {
  const doc = activeConversationDocument();
  const fromDoc = String(doc?.writingTaskId || "").trim();
  if (fromDoc && /^w/i.test(fromDoc)) return fromDoc;
  const docTask = String(doc?.taskId || "").trim();
  if (docTask && /^w/i.test(docTask)) return docTask;
  const tid = String(taskId.value || "").trim();
  if (tid && /^w/i.test(tid)) return tid;
  const sess = String(sessionRuntime.value?.task_id || "").trim();
  return sess && /^w/i.test(sess) ? sess : "";
}

/** 当前焦点文档同步用的会话上下文（库蓝本 / 收录） */
function syncIdentitySession() {
  return {
    selectedBlueprint: selectedBlueprint.value,
    liveWizard: liveBlueprintWizard.value || {},
    runtimeBlueprintId: sessionRuntime.value?.blueprint_id || "",
    collectedBlueprintId: collectedBlueprintId.value,
  };
}

/** 当前焦点文档自身的库蓝本源（含会话兜底，拒绝 w*） */
function activeDocLibraryBlueprintId() {
  const doc = activeConversationDocument();
  const wid = activeWritingCopyTaskId();
  const docWithWid = doc
    ? { ...doc, writingTaskId: doc.writingTaskId || wid }
    : { writingTaskId: wid };
  return resolveLibraryBlueprintId(docWithWid, syncIdentitySession());
}

/**
 * 同步目标库蓝本 id（按「当前文档」判定，勿被会话 writing_path 串台）：
 * 1) 文档自身挂的源蓝本（选用线副本）
 * 2) 本任务已收录 id（无蓝本收录后）
 * 3) 选用线：会话蓝本（文档项短暂丢 blueprintId 时兜底）
 */
const sourceBlueprintIdForSync = computed(() => activeDocLibraryBlueprintId());

/**
 * 同步按钮：选用蓝本并已有文档副本 → 一直显示；
 * 无蓝本路径：仅收录后显示。
 */
const hasDocCopyWithLibrarySource = computed(() => {
  const wid = activeWritingCopyTaskId();
  if (!isWritingCopyId(wid)) return false;
  const doc = activeConversationDocument();
  // 依据样本未收录：绝不当「有源蓝本副本」
  if (
    (isFreeformSampleDocument(doc) ||
      isFreeformSampleWizardMode(currentFreeformSampleWizard()) ||
      isFreeformSampleWizardMode({ mode: doc?.wizardMode })) &&
    !collectedBlueprintId.value
  ) {
    return false;
  }
  const docWithWid = doc
    ? { ...doc, writingTaskId: doc.writingTaskId || wid }
    : { writingTaskId: wid };
  return hasWritingCopyWithLibrarySource(docWithWid, syncIdentitySession());
});

/** 引用库蓝本 / 收录后 → 同步到蓝本库 */
const showDocSyncToBlueprintLibrary = computed(() => hasDocCopyWithLibrarySource.value);

/**
 * 收录：无蓝本 / 依据样本且尚未入库；选用库蓝本副本只走同步。
 * 依据样本是无蓝本创作支线（不是「创建到蓝本库」），收录前不得被会话残留库蓝本 id 藏掉。
 */
const showDocSaveAsBlueprint = computed(() => {
  const doc = activeConversationDocument();
  const wiz = currentFreeformSampleWizard();
  const sample =
    isFreeformSampleWizardMode(wiz) ||
    isFreeformSampleWizardMode({ mode: doc?.wizardMode }) ||
    isFreeformSampleDocument(doc);
  if (sample) {
    // 本任务已收录 → 改走同步
    if (collectedBlueprintId.value) return false;
    if (hasDocCopyWithLibrarySource.value) return false;
    return Boolean(composerSaveBlueprint.value);
  }
  if (hasDocCopyWithLibrarySource.value) return false;
  const docBp = activeDocLibraryBlueprintId();
  // 当前文档已挂库蓝本 → 不收录
  if (docBp && !/^w/i.test(docBp)) return false;
  // 选用库蓝本模版项：不收录
  if (String(doc?.kind || "") === "blueprint") {
    return false;
  }
  return Boolean(composerSaveBlueprint.value);
});

function markFreeformSavedAsBlueprint(blueprintId = "") {
  saveBlueprintHiddenLocal.value = true;
  saveBlueprintHiddenForTask.value = String(
    activeWritingCopyTaskId() || taskId.value || ""
  ).trim();
  const rt = sessionRuntime.value;
  const bid = String(
    blueprintId ||
      rt?.collected_blueprint_id ||
      rt?.freeform_saved_blueprint_id ||
      ""
  ).trim();
  if (rt && typeof rt === "object") {
    sessionRuntime.value = {
      ...rt,
      composer_save_blueprint: { show: false },
      ...(bid
        ? {
            collected_blueprint_id: bid,
            freeform_saved_blueprint_id: bid,
          }
        : {}),
    };
  }
}

const OPENING_GREETING = `嗨，我是小墨，智能文档协作里的写作搭档。

你直接说想写什么就行：有现成蓝本我帮你挑、配章节；没有模板也可以说「不用蓝本」，我帮你在卡片里搭目录，再一章章写下去。写完了还能导出 Word。

不知道从哪开始？下面有几句常见开头，点一下就能发；想先摸清流程可以点「使用说明」。`;

const welcomeSuggests = [
  "帮我写一份述职报告",
  "不用蓝本，直接写",
  "帮我起草一份工作说明",
  "写一封正式函件模板",
  "看看有哪些蓝本",
  "在对话里建一份蓝本",
];

const showOpeningSuggests = computed(() => {
  if (sending.value) return false;
  return !messages.value.some((m) => m.role === "user");
});

function seedOpeningGreeting() {
  if (messages.value.some((m) => m.role === "user")) return;
  if (messages.value.length === 1 && messages.value[0]?.role === "assistant") {
    messages.value[0].text = OPENING_GREETING;
    return;
  }
  if (messages.value.length) return;
  messages.value = [
    {
      role: "assistant",
      text: OPENING_GREETING,
      cards: [],
      steps: [],
      streaming: false,
    },
  ];
}
const usageGuideLoading = ref(false);

const phaseLabels = {
  intent: "意图识别",
  blueprint_select: "选择蓝本",
  create_handoff: "完善蓝本",
  freeform_ask: "收集标题",
  freeform_outline: "确认大纲",
  freeform_points: "确认要点",
  ready_to_write: "待开写",
  writing: "写作中",
  confirm_export: "确认/导出",
};

const robotSummary = computed(() => {
  const robot = currentRobot.value;
  if (!robot) return "";
  const bp = (lastBindings.value.blueprint_ids || robot.blueprint_ids || []).length;
  const rt = (lastBindings.value.render_template_ids || robot.render_template_ids || []).length;
  const ws = (lastBindings.value.word_style_ids || robot.word_style_ids || []).length;
  return `${robot.name} · 蓝本 ${bp} · 渲染 ${rt} · 样式 ${ws}`;
});

const exportWordStyles = computed(() => {
  const all = wordStyles.value || [];
  const bound =
    (lastBindings.value.word_style_ids && lastBindings.value.word_style_ids.length
      ? lastBindings.value.word_style_ids
      : null) ||
    (currentRobot.value?.word_style_ids?.length ? currentRobot.value.word_style_ids : null);
  if (!bound || !bound.length) return all;
  const allowed = new Set(bound.map(String));
  const filtered = all.filter((item) => allowed.has(String(item.id)));
  return filtered.length ? filtered : all;
});

const exportRenderTemplates = computed(() => {
  const fromRobot = renderTemplates.value || [];
  const bound =
    (lastBindings.value.render_template_ids && lastBindings.value.render_template_ids.length
      ? lastBindings.value.render_template_ids
      : null) ||
    (currentRobot.value?.render_template_ids?.length ? currentRobot.value.render_template_ids : null);
  if (!bound || !bound.length) return fromRobot;
  const allowed = new Set(bound.map(String));
  const filtered = fromRobot.filter((item) => allowed.has(String(item.id)));
  return filtered.length ? filtered : fromRobot;
});

const writingRuntime = computed(() => sessionRuntime.value?.workflows?.writing || null);

/** 入口 onboarding 澄清（定名 / 有蓝本·无蓝本 / 模版·自动大纲）进行中 */
function hasActiveEntryOnboardingClarify(cards) {
  return (cards || []).some((c) => {
    if (c?.type !== "intent_clarify") return false;
    const src = String(c?.payload?.source || "").trim();
    return src === "doc_name" || src === "entry_branch" || src === "entry_freeform_mode";
  });
}

/** 当前是否无蓝本·依据样本向导（含旧 mode=create） */
function currentFreeformSampleWizard(sessionOrRuntime) {
  return (
    pickNonEmptyWizard(
      sessionOrRuntime?.runtime?.workflows?.blueprint_wizard,
      sessionOrRuntime?.freeform?.blueprint_wizard,
      sessionOrRuntime?.blueprint_wizard,
      liveBlueprintWizard.value,
      sessionRuntime.value?.workflows?.blueprint_wizard
    ) || {}
  );
}

/** 从会话消息取最新向导 payload（live/runtime 被清时仍识别样本向导） */
function latestBlueprintWizardPayloadFromMessages() {
  const latest = findLatestBlueprintWizard();
  if (latest) {
    return messages.value[latest.msgIndex]?.cards?.[latest.cardIndex]?.payload || null;
  }
  const list = messages.value || [];
  for (let mi = list.length - 1; mi >= 0; mi -= 1) {
    const cards = list[mi]?.cards || [];
    for (let ci = cards.length - 1; ci >= 0; ci -= 1) {
      if (cards[ci]?.type === "blueprint_wizard") return cards[ci].payload || null;
    }
  }
  return null;
}

/** 无蓝本路径：不展示/不恢复「选用库蓝本」向导；依据样本仍是无蓝本支线 */
function isFreeformWritingSession(session) {
  const chatCards = (messages.value || []).flatMap((m) => m.cards || []);
  const cardPayload = latestBlueprintWizardPayloadFromMessages();
  const focusLive =
    storedLiveWizardForDoc({
      id: currentFocusDocumentId(),
      writingTaskId: taskId.value,
      taskId: taskId.value,
    }) || liveBlueprintWizard.value;
  // 依据样本早期步（上传/提取）：暂不当「纯无蓝本」，否则 ui-state 会清空向导卡
  if (
    isActiveEarlySampleWizard(session) ||
    isChatOperableSampleWizardPayload(cardPayload) ||
    isChatOperableSampleWizardPayload(focusLive)
  ) {
    return false;
  }
  // 早期创建卡在会话里：同理保留向导
  const wiz =
    pickNonEmptyWizard(
      session?.runtime?.workflows?.blueprint_wizard,
      session?.freeform?.blueprint_wizard,
      session?.blueprint_wizard,
      focusLive,
      cardPayload
    ) || {};
  if (hasEarlyCreateWizardCard(chatCards)) {
    if (isFreeformSampleWizardMode(wiz) || isChatOperableSampleWizardPayload(cardPayload)) {
      const step = normalizeWizardStep(wiz?.step || cardPayload?.step);
      // 已到确认目录 / 分析：按无蓝本，露出「收录到蓝本库」
      if (["outline_confirm", "study", "study_done", "done"].includes(step)) return true;
    }
    return false;
  }
  // 已定名但尚未选路：禁止沿用上一份 freeform 阶段/线路，避免「已改为无蓝本」串台
  if (hasActiveEntryOnboardingClarify(chatCards)) return false;
  const named = Boolean(
    session?.runtime?.writing_doc_named ??
      session?.freeform?.writing_doc_named ??
      sessionRuntime.value?.writing_doc_named
  );
  const path =
    session?.runtime?.writing_path ||
    session?.runtime?.chain_debug?.writing_path ||
    session?.runtime?.intent?.writing_path ||
    session?.freeform?.writing_path ||
    sessionRuntime.value?.writing_path ||
    sessionRuntime.value?.chain_debug?.writing_path ||
    sessionRuntime.value?.intent?.writing_path;
  const pathVal = String(path || "").trim();
  if (pathVal === "template" || pathVal === "match") return false;
  if (named && !pathVal) return false;
  const ph = String(session?.phase || phase.value || "").trim();
  if (ph.startsWith("freeform_") && pathVal === "freeform") return true;
  if (pathVal === "freeform") return true;
  return false;
}

/** 依据样本 / 创建早期向导进行中：即使 writing_path=freeform 也要保留上传卡 */
function isActiveEarlySampleWizard(sessionOrRuntime) {
  const cardPayload = latestBlueprintWizardPayloadFromMessages();
  // 空 {} 不能当命中：softSync 里 runtime.workflows.blueprint_wizard={} 会挡住 freeform 真值
  const wiz =
    pickNonEmptyWizard(
      sessionOrRuntime?.runtime?.workflows?.blueprint_wizard,
      sessionOrRuntime?.freeform?.blueprint_wizard,
      sessionOrRuntime?.blueprint_wizard,
      liveBlueprintWizard.value,
      cardPayload
    ) || {};
  if (isChatOperableSampleWizardPayload(wiz) || isChatOperableSampleWizardPayload(cardPayload)) {
    return true;
  }
  const step = normalizeWizardStep(wiz?.step || cardPayload?.step);
  if (!["meta", "upload", "outline", "upload_done"].includes(step)) return false;
  const mode = String(wiz?.mode || cardPayload?.mode || "").trim();
  // 必须显式样本/创建 mode；禁止仅凭 w* blueprint_id 保活（纯无蓝本大纲会被误判）
  return mode === "freeform_sample" || mode === "create";
}

/** 本轮已下发创建可操作向导卡（名称/上传/提取/确认）→ 即使用户刚从无蓝本改口，也必须展示 */
function hasEarlyCreateWizardCard(cards) {
  return (cards || []).some((c) => {
    if (c?.type !== "blueprint_wizard") return false;
    const step = normalizeWizardStep(c?.payload?.step);
    if (!["meta", "upload", "outline", "outline_confirm"].includes(step)) return false;
    const mode = String(c?.payload?.mode || "").trim();
    return mode !== "selected";
  });
}

/** 无蓝本且无早期向导 → 才清/吞蓝本向导 */
function shouldSuppressBlueprintWizardForFreeform(sessionLike, cards) {
  if (hasEarlyCreateWizardCard(cards)) return false;
  if (isChatOperableSampleWizardPayload(latestBlueprintWizardPayloadFromMessages())) {
    return false;
  }
  // softSync 的 applyDoneMeta 常不带 cards：仍看会话消息里是否已有提取目录卡
  if (
    !cards?.length &&
    hasEarlyCreateWizardCard((messages.value || []).flatMap((m) => m.cards || []))
  ) {
    return false;
  }
  if (isActiveEarlySampleWizard(sessionLike)) return false;
  const rt = sessionLike?.runtime || sessionLike || {};
  const wf = rt?.workflows?.blueprint_wizard || sessionLike?.blueprint_wizard || {};
  const mode = String(wf?.mode || "").trim();
  if (mode === "create" || mode === "freeform_sample") return false;
  const goal = String(rt?.session_goal?.goal || rt?.plan?.goal || "").trim();
  if (goal === "template_wizard") return false;
  const step = normalizeWizardStep(wf?.step);
  if (WIZARD_STEPS_CHAT.includes(step)) return false;
  return isFreeformWritingSession(sessionLike);
}

const suggestedNextLabel = computed(() => {
  const raw = sessionRuntime.value?.suggested_next_step;
  const label = String(raw?.label || "").trim();
  return label;
});

const phaseLabel = computed(() => {
  let base = phaseLabels[phase.value] || "";
  const path =
    sessionRuntime.value?.writing_path ||
    sessionRuntime.value?.chain_debug?.writing_path ||
    sessionRuntime.value?.intent?.writing_path;
  if (path === "freeform") {
    base = base ? `${base} · 无蓝本` : "无蓝本";
  }
  const wr = writingRuntime.value;
  const prog = wr?.progress;
  if (wr?.active && prog && Number(prog.total) > 0) {
    const running = Boolean(prog.running);
    const extra = ` · 章 ${prog.confirmed || 0}/${prog.total}`;
    if (!running && phase.value === "writing") {
      base = "待继续";
    }
    return base ? `${base}${extra}` : extra.slice(3);
  }
  if (wr?.paused && base) return `${base} · 已暂停`;
  return base;
});

const chainDebugDev = computed(() => {
  if (!import.meta.env.DEV) return "";
  const tail =
    sessionRuntime.value?.chain_debug?.agent_chain_tail ||
    sessionRuntime.value?.intent?.agent_chain_tail;
  if (!Array.isArray(tail) || !tail.length) return "";
  return tail
    .slice(-4)
    .map((row) => {
      const step = row.step || row.summary || "?";
      const sum = String(row.summary || "").slice(0, 40);
      return sum ? `${step}:${sum}` : step;
    })
    .join(" | ");
});

const statusHint = computed(() => {
  if (!robotId.value) {
    return "未选机器人 · 对话由总控 Agent 调度工具 · 富文本";
  }
  return "已选机器人 · 提示词与蓝本按机器人配置 · 富文本";
});

function syncDraftFromEditor() {
  const el = editorEl.value;
  if (!el) {
    draftHtml.value = "";
    draftPlain.value = "";
    return;
  }
  draftHtml.value = el.innerHTML || "";
  draftPlain.value = htmlToPlain(el.innerHTML || "");
}

function onEditorInput() {
  syncDraftFromEditor();
}

function onEditorPaste(event) {
  if (composerLocked.value) {
    event.preventDefault();
    return;
  }
  const html = clipboardToHtml(event);
  if (!html) return;
  event.preventDefault();
  if (editorEl.value) insertHtmlAtCursor(editorEl.value, html);
  syncDraftFromEditor();
}

function onEditorKeydown(event) {
  if (composerLocked.value) {
    event.preventDefault();
    return;
  }
  if (event.key !== "Enter" || event.isComposing) return;
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    send();
  }
}

function fmt(command) {
  editorEl.value?.focus();
  document.execCommand(command, false);
  syncDraftFromEditor();
}

function clearEditor() {
  if (editorEl.value) editorEl.value.innerHTML = "";
  draftHtml.value = "";
  draftPlain.value = "";
}

function applySuggest(text) {
  if (!editorEl.value) return;
  editorEl.value.innerHTML = `<p>${escapeHtml(text)}</p>`;
  syncDraftFromEditor();
  editorEl.value.focus();
}

async function showUsageGuide() {
  if (usageGuideLoading.value || sending.value) return;
  usageGuideLoading.value = true;
  try {
    await sendAction("usage_guide", {}, { force: true });
  } finally {
    usageGuideLoading.value = false;
  }
}

function payloadMessage() {
  const html = sanitizeHtml(draftHtml.value);
  const plain = draftPlain.value.trim();
  if (!plain) return "";
  // 有排版则传 HTML，便于会话回放；后端会去标签做意图解析
  if (html && /<(?:p|br|strong|em|b|i|u|ul|ol|li|div)\b/i.test(html)) {
    return html;
  }
  return plain;
}

async function loadRobots() {
  try {
    robots.value = (await request("/api/writing/robots")) || [];
  } catch {
    robots.value = [];
  }
}

function syncDefaultWordStyle() {
  const list = exportWordStyles.value;
  if (!list.length) {
    exportWordStyleId.value = "ws_default";
    return;
  }
  if (list.some((item) => item.id === exportWordStyleId.value)) return;
  const def = list.find((item) => item.id === "ws_default");
  exportWordStyleId.value = def ? def.id : list[0].id;
}

function syncDefaultExportTemplate() {
  if (!exportTemplateId.value) return;
  if (!exportRenderTemplates.value.some((item) => item.id === exportTemplateId.value)) {
    exportTemplateId.value = "";
  }
}

async function onRobotChange(id, options = {}) {
  const skipResourceCheck = Boolean(options.skipResourceCheck);
  lastBindings.value = { blueprint_ids: [], render_template_ids: [], word_style_ids: [] };
  if (!id) {
    currentRobot.value = null;
    await loadRenderTemplatesForRobot("");
    syncDefaultWordStyle();
    syncDefaultExportTemplate();
    return;
  }
  try {
    currentRobot.value = await request(`/api/writing/robots/${encodeURIComponent(id)}`);
    lastBindings.value = {
      blueprint_ids: [...(currentRobot.value.blueprint_ids || [])],
      render_template_ids: [...(currentRobot.value.render_template_ids || [])],
      word_style_ids: [...(currentRobot.value.word_style_ids || [])],
    };
    const sidecars = [loadRenderTemplatesForRobot(id), loadWordStyles()];
    if (!options.skipHistory) sidecars.push(loadHistory());
    await Promise.all(sidecars);
    syncDefaultExportTemplate();
    if (
      !skipResourceCheck &&
      sessionId.value &&
      messages.value.length === 0 &&
      !sending.value
    ) {
      await sendAction("resource_check", {});
    }
  } catch (error) {
    ElMessage.error(error.message || "加载机器人失败");
    currentRobot.value = null;
    renderTemplates.value = [];
  }
}

function persistSession(id) {
  const next = id || "";
  if (next !== sessionId.value) {
    lastUiStateFingerprint = "";
    lastUiStateAt = 0;
    persistUiStateQueued = false;
  }
  sessionId.value = next;
  const key = sessionStorageKey();
  if (id) localStorage.setItem(key, id);
  else localStorage.removeItem(key);
}

/** 进度卡历史上塞过整章 draft_text / 完整要素节点；加载/落库时裁掉，避免会话变慢 */
function slimProgressCardPayload(payload) {
  if (!payload || typeof payload !== "object") return payload || {};
  const chapters = (Array.isArray(payload.chapters) ? payload.chapters : []).filter(
    (ch) => chapterWasWrittenOrWriting(ch)
  );
  const slimChapters = chapters.map((ch) => {
    if (!ch || typeof ch !== "object") return ch;
    const cfg = ch.config && typeof ch.config === "object" ? ch.config : null;
    const next = {
      ...ch,
      draft_preview: String(ch.draft_preview || ch.draft_text || "").slice(0, 1800),
      evidence: Array.isArray(ch.evidence)
        ? ch.evidence.slice(0, 8).map((ev) => ({
            ...ev,
            excerpt: String(ev?.excerpt || "").slice(0, 180),
            query: String(ev?.query || "").slice(0, 80),
          }))
        : ch.evidence,
      element_results: Array.isArray(ch.element_results)
        ? ch.element_results.slice(0, 12).map((row) => ({
            ...row,
            value: String(row?.value || "").slice(0, 240),
            query: String(row?.query || "").slice(0, 120),
          }))
        : ch.element_results,
      uncovered_points: Array.isArray(ch.uncovered_points)
        ? ch.uncovered_points.slice(0, 12)
        : ch.uncovered_points,
      steps: Array.isArray(ch.steps) ? ch.steps.slice(-8) : ch.steps,
    };
    delete next.draft_text;
    if (cfg) {
      next.config = {
        ...cfg,
        reference_text: "",
        describes: String(cfg.describes || ""),
        aux_prompt: String(cfg.aux_prompt || "").slice(0, 240),
        points: Array.isArray(cfg.points) ? cfg.points.slice(0, 20) : cfg.points,
        point_items: Array.isArray(cfg.point_items)
          ? cfg.point_items.slice(0, 20).map((row, i) => ({
              id: String(row?.id || `p${i}`),
              text: String(row?.text || "").slice(0, 200),
              ...(String(row?.element_id || "").trim()
                ? {
                    element_id: String(row.element_id).trim(),
                    element_name: String(row?.element_name || "").slice(0, 80),
                  }
                : {}),
              ...(String(row?.slot || "").trim() ? { slot: String(row.slot).trim() } : {}),
            }))
          : cfg.point_items,
        extracts: [],
      };
    }
    return next;
  });
  const nodes = Array.isArray(payload.element_nodes) ? payload.element_nodes : [];
  const slimNodes = nodes.slice(0, 64).map((item) => ({
    id: String(item?.id || "").trim(),
    name: String(item?.name || item?.id || "").trim(),
    kind: String(item?.kind || "").trim(),
    query: String(item?.query || "").slice(0, 120),
    result: String(item?.result || item?.value || "").slice(0, 240),
    required: item?.required !== false,
    library_item_id: String(item?.library_item_id || "").trim(),
    knowledge_folder_ids: Array.isArray(item?.knowledge_folder_ids)
      ? item.knowledge_folder_ids.slice(0, 8)
      : [],
  })).filter((row) => row.id);
  return { ...payload, chapters: slimChapters, element_nodes: slimNodes };
}

function slimMessageCard(card) {
  if (!card || typeof card !== "object") return card;
  if (card.type === "blueprint_wizard") {
    return { ...card, payload: freezeWizardCardPayload(card.payload || {}) };
  }
  if (card.type === "progress") {
    return { ...card, payload: slimProgressCardPayload(card.payload || {}) };
  }
  return card;
}

function mapSessionMessages(session) {
  return (session?.messages || []).map((m) => {
    const steps = Array.isArray(m.steps) ? m.steps : [];
    const docKey = String(m.document_key || m.document_id || "").trim();
    return {
      role: m.role,
      text: m.text,
      document_key: docKey,
      document_id: String(m.document_id || m.document_key || "").trim() || docKey,
      cards: (m.cards || []).map((c) => slimMessageCard(c)),
      // 历史过程条若停在「正在… / ▶ 正在分析」则收成完成态
      steps: steps.map((row) => {
        const msg = String(row?.message || "");
        if (
          !msg ||
          (!/^正在/.test(msg) &&
            !/正在自动生成/.test(msg) &&
            !isStudyRunningProcessMessage(msg))
        ) {
          return row;
        }
        return { ...row, message: processMessageAsDone(msg) };
      }),
    };
  });
}

function currentFocusDocumentId() {
  // 小窗选中优先于 runtime（避免 A 操作落到 B）
  const active = String(docWindowActiveId.value || "").trim();
  if (active) return active;
  return String(
    sessionRuntime.value?.focus_document_key ||
      sessionRuntime.value?.workflows?.focus_document_key ||
      ""
  ).trim();
}

function isClientSideStreamBug(error) {
  const raw = String(error?.message || "");
  return (
    error instanceof TypeError ||
    /Cannot read propert|Cannot set propert|is not a function|is not iterable/.test(raw)
  );
}

/** sendAction 会先 push 用户动作泡，末条不一定是助手 */
function lastAssistantMessageInChat() {
  const list = messages.value || [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i]?.role === "assistant") return list[i];
  }
  return null;
}

function _docKeyStrip(v) {
  return String(v || "")
    .replace(/^(task:|bp:)/, "")
    .trim();
}

/** 消息是否属于当前焦点文档（多文档隔离：旧文档向导/卡勿被当前线路改写） */
function messageBelongsToFocusDocument(msg) {
  const focus = currentFocusDocumentId();
  const key = String(msg?.document_key || msg?.document_id || "").trim();
  if (!focus) return true;
  if (!key) {
    // 空盖戳：若本条带有当前焦点的早期向导卡，仍视为属于焦点（新建样本时常见）
    const cards = Array.isArray(msg?.cards) ? msg.cards : [];
    if (cards.some((c) => wizardCardBelongsToFocus(c, msg))) return true;
    const docs = conversationDocuments.value || [];
    return docs.length <= 1;
  }
  if (key === focus) return true;
  const a = _docKeyStrip(key);
  const b = _docKeyStrip(focus);
  return Boolean(a && b && a === b);
}

/** 同会话多文档：对话连续可见；document_key 仅用于路由/轻量标签 */
function messageVisibleInThread(_msg) {
  return true;
}

function messageDocTag(msg) {
  const docs = conversationDocuments.value || [];
  if (docs.length <= 1) return "";
  const key = normalizeDocumentRegistryKey(
    String(msg?.document_key || msg?.document_id || "").trim()
  );
  if (!key) return "";
  const focus = normalizeDocumentRegistryKey(currentFocusDocumentId());
  if (focus && key === focus) return "";
  let doc =
    docs.find((d) => d.id === key) ||
    (key.startsWith("task:")
      ? docs.find(
          (d) =>
            String(d.taskId || "") === key.slice(5) ||
            String(d.writingTaskId || "") === key.slice(5)
        )
      : null) ||
    (key.startsWith("bp:")
      ? docs.find((d) => String(d.blueprintId || d.taskId || "") === key.slice(3))
      : null);
  const title = cleanConversationDocTitle(doc?.title || "");
  return title && !looksLikeRawDocTitle(title) ? title : "";
}

/** 切文档后滚到该文档最近一条可见消息 */
function scrollChatToFocusThread() {
  nextTick(() => {
    const list = messages.value || [];
    for (let i = list.length - 1; i >= 0; i -= 1) {
      if (!messageVisibleInThread(list[i])) continue;
      const el = chatStageEl.value?.querySelectorAll(".chat-bubble, .bubble")?.[i];
      el?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      scheduleScrollChatToBottom(true);
      break;
    }
  });
}

/** 小窗切文档：服务端对齐焦点；用户手动切换时可写入「已切换到…」 */
async function syncFocusDocumentWithServer(doc, { announce = true } = {}) {
  if (!sessionId.value || sending.value) return;
  const key = normalizeDocumentRegistryKey(
    String(doc?.id || docWindowActiveId.value || "").trim()
  );
  if (!key) return;
  const session = await request(
    `/api/writing/chat/sessions/${encodeURIComponent(sessionId.value)}/focus-document`,
    {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ focus_document_key: key, announce: Boolean(announce) }),
    }
  );
  if (announce && Array.isArray(session?.messages)) {
    messages.value = mapSessionMessages(session);
  }
  applyDoneMeta({
    session_id: session.id,
    phase: session.phase,
    task_id: session.task_id,
    runtime: session.runtime,
    events: [{ type: "focus_document", focus_document_key: key }],
  });
}

function wizardCardIdentityIds(card) {
  const p = card?.payload || {};
  return [p.blueprint_id, p.working_task_id, p.task_id]
    .map((x) => _docKeyStrip(x))
    .filter(Boolean);
}

function isEarlyCreateWizardCard(card) {
  if (!card || card.type !== "blueprint_wizard") return false;
  const p = card.payload || {};
  const step = normalizeWizardStep(p.step);
  if (!WIZARD_STEPS_CHAT.includes(step)) return false;
  const mode = String(p.mode || "").trim();
  if (mode === "selected") return false;
  return (
    mode === "create" ||
    mode === "freeform_sample" ||
    isChatOperableSampleWizardPayload(p) ||
    !mode
  );
}

/** 向导卡按 payload 蓝本/任务 id 对齐焦点，避免流式盖戳滞后把上传卡藏掉 */
function wizardCardBelongsToFocus(card, msg) {
  if (!card || card.type !== "blueprint_wizard") return false;
  const ids = wizardCardIdentityIds(card);
  const step = normalizeWizardStep(card.payload?.step);
  const msgKey = _docKeyStrip(msg?.document_key || msg?.document_id || "");
  const focus = currentFocusDocumentId();
  const focusId = focus ? _docKeyStrip(focus) : "";
  if (isEarlyCreateWizardCard(card)) {
    if (msgKey && ids.some((id) => id === msgKey)) return true;
    if (focusId && ids.some((id) => id === focusId)) return true;
    if (msgKey && ids.length === 0 && msgKey === focusId) return true;
    return false;
  }
  if (!ids.length && WIZARD_STEPS_CHAT.includes(step)) {
    return !focusId || msgKey === focusId;
  }
  const sessTid = _docKeyStrip(
    taskId.value || sessionRuntime.value?.task_id || ""
  );
  const liveBid = _docKeyStrip(liveBlueprintWizard.value?.blueprint_id || "");
  const cardBid = ids[0] || "";
  if (cardBid && sessTid && cardBid === sessTid) return true;
  if (cardBid && liveBid && cardBid === liveBid) return true;
  if (msgKey && ids.some((id) => id === msgKey)) return true;
  if (!focusId) return true;
  if (ids.some((id) => id === focusId)) return true;
  return false;
}

function blueprintWizardStateFromSession(session) {
  const runtimeObj = pickNonEmptyWizard(session?.runtime?.workflows?.blueprint_wizard);
  const legacyObj = pickNonEmptyWizard(session?.freeform?.blueprint_wizard);
  if (runtimeObj && legacyObj) return mergeWizardPayload(legacyObj, runtimeObj);
  if (runtimeObj) return mergeWizardPayload({}, runtimeObj);
  if (legacyObj) return mergeWizardPayload({}, legacyObj);
  return null;
}

function workflowStateFromSession(session, name) {
  const wf = session?.runtime?.workflows?.[name];
  if (wf && typeof wf === "object") return { ...wf };
  if (name === "blueprint_wizard") return blueprintWizardStateFromSession(session);
  const legacy = session?.freeform?.[name];
  return legacy && typeof legacy === "object" ? { ...legacy } : null;
}

function restoreWorkflowUiFromSession(session, { preferLocalAdvance = false } = {}) {
  sessionRuntime.value =
    session?.runtime && typeof session.runtime === "object" ? { ...session.runtime } : null;
  const w = blueprintWizardStateFromSession(session);
  blueprintWizardLog.value = [...(w?.process_log || [])];
  // 以当前会话快照为准（applySession 已清空上一会话 live）
  // 服务端快照一律剥掉一次性自驱标记，避免刷新/softSync 又自动重学；
  // 本轮正在分析时，本地 live 上的 trigger 经下方 merge 仍会保留。
  const mergedSnap = w && typeof w === "object" ? mergeWizardPayload({}, w) : null;
  const restored = mergedSnap ? stripEphemeralWizardTriggers(mergedSnap) : null;
  const serverClearedWizard =
    !w || (typeof w === "object" && !String(w.blueprint_id || "").trim() && !String(w.step || "").trim());
  if (preferLocalAdvance && liveBlueprintWizard.value && restored) {
    // softSync：本地若已前进到 upload+，严禁被滞后服务端快照打回 meta
    liveBlueprintWizard.value = mergeWizardPayload(restored, liveBlueprintWizard.value);
  } else if (preferLocalAdvance && liveBlueprintWizard.value && !restored) {
    // 「新开一份」等会 pop 服务端 blueprint_wizard：禁止保留本地学习 trigger，否则旧章分析会接着跑
    if (serverClearedWizard || !String(session?.blueprint_id || "").trim()) {
      try {
        pageStudyAbortController?.abort();
      } catch {
        /* ignore */
      }
      releaseStudyOpsLock({ notify: false });
      liveBlueprintWizard.value = null;
      pageStudyToken = "";
    }
    /* 否则保留本地进行中向导 */
  } else {
    liveBlueprintWizard.value = restored;
  }
  // 恢复会话时不要立刻再打 ui-state，避免与 GET 会话形成落库环
  const tpl = workflowStateFromSession(session, "template_wizard");
  liveTemplateWizard.value = tpl && typeof tpl === "object" ? { ...tpl } : null;
  const ex = workflowStateFromSession(session, "export");
  if (ex) {
    if (ex.template_id) exportTemplateId.value = String(ex.template_id);
    if (ex.style_id) exportWordStyleId.value = String(ex.style_id);
    if (ex.confirmed_only !== undefined) exportConfirmedOnly.value = Boolean(ex.confirmed_only);
  }
}

function buildChapterEditsSync() {
  const out = {};
  for (const [key, edit] of Object.entries(chapterEdits.value || {})) {
    if (!edit || typeof edit !== "object") continue;
    const text = sanitizeHtml(edit.text || "");
    if (!text.trim()) continue;
    if (!edit._touched) continue;
    out[key] = { text, touched: true };
  }
  return out;
}

function slimWizardPayload(payload) {
  const next = { ...(payload || {}) };
  const n =
    Number(next.chapter_count || 0) ||
    (Array.isArray(next.chapters) ? next.chapters.length : 0);
  if (n) next.chapter_count = n;
  if (Array.isArray(next.chapters) && next.chapters.length > 8) {
    next.chapters = next.chapters.slice(0, 8);
  }
  return next;
}

/** 新开创建向导整段替换 live；续写同蓝本才 merge，避免旧目录挂到新标题 */
function applyLiveWizardFromSource(source) {
  if (!source || typeof source !== "object") return;
  if (isFreshBlueprintWizardStart(source)) {
    liveBlueprintWizard.value = slimWizardPayload({
      ...source,
      blueprint_id: "",
      chapters: Array.isArray(source.chapters) ? source.chapters : [],
    });
  } else {
    liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, source);
  }
  const key = wizardDocKeyFromPayload(liveBlueprintWizard.value, currentFocusDocumentId());
  if (key) rememberLiveWizardForDoc(key, liveBlueprintWizard.value);
}

function buildUiStatePayload() {
  const message_patches = [];
  messages.value.forEach((msg, mi) => {
    const steps = msg.steps || [];
    const cards = msg.cards || [];
    const hasWizard = cards.some((c) => c?.type === "blueprint_wizard");
    const hasProgress = cards.some((c) => c?.type === "progress");
    // 过程条 / 向导 / 进度卡需落库；纯气泡已由 turn 写入。进度卡历史会塞整章正文，必须回写瘦身后的快照
    if (!steps.length && !hasWizard && !hasProgress) return;
    // 不回写 text：服务端已落库。错位序号曾把 AI 正文写成用户的话。
    const patch = { index: mi, role: String(msg.role || "") };
    if (steps.length) patch.steps = steps.slice(-30);
    if (cards.length) {
      patch.cards = cards.map((c) => {
        if (c?.type === "blueprint_wizard") {
          // 最新卡也冻 trigger：进行中的自驱只活在内存 live，不写回消息快照
          return {
            ...c,
            payload: slimWizardPayload(freezeWizardCardPayload({ ...(c?.payload || {}) })),
          };
        }
        if (c?.type === "progress") {
          return { ...c, payload: slimProgressCardPayload(c?.payload || {}) };
        }
        return { ...c, payload: { ...(c?.payload || {}) } };
      });
    }
    message_patches.push(patch);
  });
  // 进度真值在 freeform / runtime.workflows；卡片 payload.step 保持各轮落库快照（由服务端合并护栏防回退）
  const freeform = isFreeformWritingSession();
  const liveBp = freeform ? null : liveBlueprintWizard.value;
  // 落库不含一次性自驱标记，刷新/重进不得靠 ui-state 再点燃
  const rawBp = slimWizardPayload(
    liveBp && typeof liveBp === "object" ? stripEphemeralWizardTriggers(liveBp) : {}
  );
  const bpPatch = freeform
    ? {}
    : {
        process_log: [...blueprintWizardLog.value].slice(-20),
        ...rawBp,
      };
  const tplPatch =
    liveTemplateWizard.value && typeof liveTemplateWizard.value === "object"
      ? { ...liveTemplateWizard.value }
      : {};
  const exportPatch = {
    template_id: exportTemplateId.value || "",
    style_id: exportWordStyleId.value || "ws_default",
    confirmed_only: Boolean(exportConfirmedOnly.value),
  };
  const workflows = {
    ...(freeform ? {} : { blueprint_wizard: bpPatch }),
    template_wizard: tplPatch,
    export: exportPatch,
  };
  const chapter_edits = buildChapterEditsSync();
  const focusKey = String(docWindowActiveId.value || "").trim();
  const activeDoc =
    (conversationDocuments.value || []).find((d) => d.id === focusKey) || null;
  // focus_task_id 必须跟当前文档窗一致，禁止用过期的 taskId.value
  const focusTaskFromDoc = String(
    activeDoc?.writingTaskId || activeDoc?.taskId || ""
  ).trim();
  const focusBpFromDoc = String(activeDoc?.blueprintId || "").trim();
  const freeform_patch = {
    runtime: { workflows },
    template_wizard: tplPatch,
    export_prefs: exportPatch,
    chapter_edits,
    config_revision: clientConfigRevision.value,
  };
  if (focusKey) {
    freeform_patch.focus_document_key = focusKey;
  }
  // 无蓝本时显式清空服务端残留向导，避免 ui-state 合并又复活「上传样板」
  if (freeform) {
    freeform_patch.blueprint_wizard = {};
  } else {
    freeform_patch.blueprint_wizard = bpPatch;
  }
  return {
    used_card_keys: [...usedCardKeys.value],
    message_patches,
    chapter_edits,
    freeform_patch,
    // 小窗切换后对齐服务端会话焦点，避免无点名聊天仍落在旧文档
    focus_task_id: focusTaskFromDoc || String(taskId.value || "").trim(),
    focus_blueprint_id:
      (focusBpFromDoc && !/^w/i.test(focusBpFromDoc) ? focusBpFromDoc : "") ||
      String(selectedBlueprint.value || "").trim(),
    focus_document_key: focusKey,
  };
}

/** 流式开写前只带焦点/向导/章节草稿，不扫全部历史卡 */
function buildStreamClientSync() {
  const full = buildUiStatePayload();
  return {
    used_card_keys: full.used_card_keys,
    chapter_edits: full.chapter_edits,
    freeform_patch: full.freeform_patch,
    focus_task_id: full.focus_task_id,
    focus_blueprint_id: full.focus_blueprint_id,
    focus_document_key: full.focus_document_key,
  };
}

let persistUiStateInflight = false;
let persistUiStateQueued = false;
let lastUiStateFingerprint = "";
let lastUiStateAt = 0;

function uiStateFingerprintOf(payload) {
  const bp = payload?.freeform_patch?.blueprint_wizard || {};
  return [
    sessionId.value,
    bp.step,
    bp.blueprint_id,
    bp.chapter_count,
    bp.outline_confirmed,
  ].join("|");
}

function schedulePersistUiState(flush = false) {
  if (!sessionId.value) return;
  clearTimeout(uiStatePersistTimer);
  if (flush) {
    persistUiState().catch(() => {});
    return;
  }
  uiStatePersistTimer = setTimeout(() => {
    persistUiState().catch(() => {});
  }, 1500);
}

async function persistUiState() {
  if (!sessionId.value) return;
  // 流式回合进行中禁止 ui-state 落库，避免用旧消息列表冲掉服务端刚写入的「已建好」轮次
  if (sending.value) return;
  if (persistUiStateInflight) {
    persistUiStateQueued = true;
    return;
  }
  const ownerSid = String(sessionId.value || "").trim();
  const ownerEpoch = currentSessionEpoch();
  const payload = buildUiStatePayload();
  const fingerprint = uiStateFingerprintOf(payload);
  if (fingerprint && fingerprint === lastUiStateFingerprint) return;
  const now = Date.now();
  if (now - lastUiStateAt < 1200 && lastUiStateFingerprint) {
    persistUiStateQueued = true;
    schedulePersistUiState();
    return;
  }
  persistUiStateInflight = true;
  try {
    // 换会话后禁止把旧 payload 写到新 session_id
    if (!isCurrentSessionContext(ownerEpoch, ownerSid)) return;
    await request(`/api/writing/chat/sessions/${encodeURIComponent(ownerSid)}/ui-state`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
    if (!isCurrentSessionContext(ownerEpoch, ownerSid)) return;
    lastUiStateFingerprint = fingerprint;
    lastUiStateAt = Date.now();
  } finally {
    persistUiStateInflight = false;
    if (persistUiStateQueued) {
      persistUiStateQueued = false;
      if (!isCurrentSessionContext(ownerEpoch, ownerSid)) return;
      const again = uiStateFingerprintOf(buildUiStatePayload());
      if (again && again !== lastUiStateFingerprint) schedulePersistUiState();
    }
  }
}

function restoreUiStateFromSession(session, { preferLocalAdvance = false } = {}) {
  const keys = new Set((session?.freeform?.used_card_keys || []).map(String));
  messages.value.forEach((msg, mi) => {
    (msg.cards || []).forEach((card, ci) => {
      if (card.used) keys.add(cardKey(mi, ci));
    });
  });
  usedCardKeys.value = keys;
  restoreWorkflowUiFromSession(session, { preferLocalAdvance });
}

function onWizardLiveStep(payload) {
  const step = String(payload?.step || "").trim();
  const blueprint_id = String(payload?.blueprint_id || "").trim();
  if (!step) return;
  if (step === "meta" && !blueprint_id) {
    // 蓝本已创建后忽略滞后的 meta live-step，严禁打回「填写名称」
    if (String(liveBlueprintWizard.value?.blueprint_id || "").trim()) return;
    if (wizardStepIndex(liveBlueprintWizard.value?.step) > wizardStepIndex("meta")) return;
    liveBlueprintWizard.value = { ...(liveBlueprintWizard.value || {}), ...payload, step, blueprint_id: "" };
    rememberLiveWizardForDoc(
      wizardDocKeyFromPayload(liveBlueprintWizard.value, currentFocusDocumentId()),
      liveBlueprintWizard.value
    );
    return;
  }
  if (!blueprint_id) return;
  // 禁止旧蓝本卡的 live-step 覆盖当前正在操作的蓝本
  const curBid = String(liveBlueprintWizard.value?.blueprint_id || "").trim();
  if (curBid && curBid !== blueprint_id) return;
  const prev = liveBlueprintWizard.value || {};
  const next = { ...prev, ...payload, step, blueprint_id };
  if (payload?.trigger_outline_extract === false) {
    delete next.trigger_outline_extract;
  }
  if (
    Object.prototype.hasOwnProperty.call(payload || {}, "pending_wizard_command") &&
    !String(payload.pending_wizard_command || "").trim()
  ) {
    delete next.pending_wizard_command;
  }
  const extractInflight =
    payload?.outline_extract_active === true ||
    next.outline_extract_active === true ||
    String(payload?.pending_wizard_command || next.pending_wizard_command || "").trim() ===
      "re_extract_outline";
  if (wizardStepIndex(step) >= wizardStepIndex("outline_confirm") && !extractInflight) {
    delete next.pending_wizard_command;
    delete next.trigger_outline_extract;
    delete next.outline_extract_active;
  }
  if (payload?.outline_extract_active === false && !extractInflight) {
    delete next.outline_extract_active;
  }
  if (Array.isArray(payload?.trigger_chapter_study) && payload.trigger_chapter_study.length === 0) {
    delete next.trigger_chapter_study;
    delete next.chapter_study_keys;
  }
  if (payload?.trigger_begin_study === false || payload?.trigger_begin_study == null) {
    if (Object.prototype.hasOwnProperty.call(payload || {}, "trigger_begin_study")) {
      delete next.trigger_begin_study;
    }
  }
  if (payload?.study_stream_active === false) {
    delete next.study_stream_active;
  }
  // 学习进度数字未变时不要整对象替换，避免文档窗/ui-state 连环请求
  const sig = (w) =>
    [
      w.step,
      w.blueprint_id,
      w.outline_confirmed,
      w.study_mode,
      w.study_analyzed,
      w.study_unanalyzed,
      w.study_total,
      w.study_pending_confirm,
      w.study_stream_active,
      w.outline_extract_active,
      w.trigger_begin_study,
      Array.isArray(w.trigger_chapter_study) ? w.trigger_chapter_study.join(",") : "",
      Array.isArray(w.chapter_study_keys) ? w.chapter_study_keys.join(",") : "",
      Array.isArray(w.chapters) ? w.chapters.length : 0,
      w.chapter_count,
    ].join("|");
  if (sig(prev) === sig(next)) return;
  // 只更新会话 live；勿改写消息卡片快照，完整步骤靠 wizard-action 落库
  liveBlueprintWizard.value = slimWizardPayload(next);
  rememberLiveWizardForDoc(
    wizardDocKeyFromPayload(liveBlueprintWizard.value, currentFocusDocumentId()),
    liveBlueprintWizard.value
  );
  if (
    !next.outline_extract_active &&
    !next.trigger_outline_extract &&
    wizardStepIndex(String(next.step || "")) >= wizardStepIndex("outline_confirm") &&
    !next.study_stream_active &&
    !pageStudyInflight
  ) {
    setDocWindowStudyBusy(false);
  }
  if (sending.value) return;
  // 学习进度 / 提取瞬时态只留在内存；ui-state 只在步骤或蓝本 id 真变时落
  const persistWorthy =
    String(prev.step || "") !== String(next.step || "") ||
    String(prev.blueprint_id || "") !== String(next.blueprint_id || "") ||
    Boolean(prev.outline_confirmed) !== Boolean(next.outline_confirmed) ||
    Number(prev.chapter_count || 0) !== Number(next.chapter_count || 0);
  if (!persistWorthy) return;
  schedulePersistUiState();
}

function applyChatSessionEvent(event) {
  const evSid = String(event?.session_id || "").trim();
  const cur = String(sessionId.value || "").trim();
  // 严禁用其它会话的 session 事件覆盖当前 UI（新建对话后迟到的旧流）
  if (evSid && cur && evSid !== cur) return;
  if (evSid && !cur) persistSession(evSid);
  if (event.phase) phase.value = event.phase;
  const prevFf = sessionRuntime.value?.workflows?.freeform || {};
  if (event.runtime && typeof event.runtime === "object") {
    sessionRuntime.value = { ...event.runtime };
  }
  const bp =
    event.runtime?.workflows?.blueprint_wizard ||
    (event.blueprint_wizard && typeof event.blueprint_wizard === "object" ? event.blueprint_wizard : null);
  if (bp) {
    applyLiveWizardFromSource(bp);
  }
  const tpl = event.runtime?.workflows?.template_wizard;
  if (tpl && typeof tpl === "object") liveTemplateWizard.value = { ...tpl };
  const ex = event.runtime?.workflows?.export;
  if (ex && typeof ex === "object") {
    if (ex.template_id) exportTemplateId.value = String(ex.template_id);
    if (ex.style_id) exportWordStyleId.value = String(ex.style_id);
    if (ex.confirmed_only !== undefined) exportConfirmedOnly.value = Boolean(ex.confirmed_only);
  }
  // 无蓝本大纲确认 / 要点完成 / 待开写：强制刷新文档窗，避免仍显示「待确认目录」或「待配要点」
  const nextFf = event.runtime?.workflows?.freeform || {};
  const nextPhase = String(nextFf.phase || "").toLowerCase();
  const prevPhase = String(prevFf.phase || "").toLowerCase();
  const becameConfirmed =
    (!Boolean(prevFf.outline_confirmed) && Boolean(nextFf.outline_confirmed)) ||
    (prevPhase !== nextPhase &&
      /points|ready_to_write|writing|confirm_export/.test(nextPhase)) ||
    (String(prevFf.points_step || "") !== String(nextFf.points_step || "") &&
      ["generated", "skipped"].includes(String(nextFf.points_step || "").toLowerCase()));
  if (becameConfirmed) {
    bumpDocOutlineReload({ force: true });
  }
  // 对话切文档：runtime 焦点变化 → 文档窗口跟随
  const focusKey = normalizeDocumentRegistryKey(
    String(event.runtime?.focus_document_key || "").trim()
  );
  if (focusKey) {
    syncDocWindowToFocusKey(focusKey);
  }
}

function onTemplateLiveStep(payload) {
  const resource = String(payload?.resource || "").trim();
  const step = String(payload?.step || "").trim();
  if (!resource || !step) return;
  liveTemplateWizard.value = { ...(liveTemplateWizard.value || {}), ...payload, resource, step };
}

function onBlueprintWizardProcess(line, { skipPush = false } = {}) {
  const text = String(line || "").trim();
  if (!text) return;
  const live = liveBlueprintWizard.value || {};
  const bid = String(live.blueprint_id || "").trim();
  const taskId = String(live.working_task_id || bid).trim();

  if (isOutlineExtractActive(live) && isOutlineExtractProgressMessage(text)) {
    if (!skipPush) {
      pushStudyProgressLine(text, "process", {
        blueprintId: bid,
        taskId,
        invokeHook: false,
      });
    }
    setDocWindowStudyBusy(true, text.slice(0, 96) || "正在从样板提取目录…");
    armDocWindowAutoOpen();
    scheduleScrollChatToBottom();
    return;
  }

  if (!isStudyStreamActive(live)) {
    if (!skipPush && isStudyProgressMessage(text)) {
      pushStudyProgressLine(text, "process", { blueprintId: bid, taskId, invokeHook: false });
      scheduleScrollChatToBottom();
    }
    return;
  }
  if (!isStudyProgressMessage(text) && !/▶|✓/.test(text)) return;
  if (skipPush) {
    scheduleScrollChatToBottom();
    return;
  }
  noteStudyProgressHeartbeat();
  pushStudyProgressLine(text, "process", { blueprintId: bid, taskId, invokeHook: false });
  scheduleScrollChatToBottom();
}

function conversationHasUploadDoneTurn() {
  return (messages.value || []).some((msg) => {
    const text = String(msg?.text || "");
    if (msg?.role === "user" && text.includes("完成上传样板")) return true;
    return (msg?.cards || []).some(
      (c) =>
        c?.type === "blueprint_wizard" &&
        ["outline", "outline_confirm", "study", "done"].includes(
          normalizeWizardStep(c?.payload?.step)
        )
    );
  });
}

function conversationHasOutlineDoneTurn() {
  return (messages.value || []).some((msg) => {
    const text = String(msg?.text || "");
    if (msg?.role === "user" && text.includes("完成目录提取")) return true;
    return (msg?.cards || []).some(
      (c) =>
        c?.type === "blueprint_wizard" &&
        normalizeWizardStep(c?.payload?.step) === "outline_confirm"
    );
  });
}

/** 按任务真实进度补写缺失的向导对话（上传完成 / 目录提取完成） */
async function backfillMissingWizardTurns() {
  const live = liveBlueprintWizard.value || {};
  const bid = String(live.blueprint_id || selectedBlueprint.value || "").trim();
  if (!bid) return;
  const latest = findLatestBlueprintWizard();
  if (!latest) return;
  let task = null;
  try {
    task = await request(`/api/writing/tasks/${encodeURIComponent(bid)}`);
  } catch {
    task = null;
  }
  const sampleReady = Boolean(task?.sample_ready);
  const chapterN = task?.outline?.chapters?.length || 0;
  // 样板已在任务里，但对话没有「完成上传样板」
  if (sampleReady && !conversationHasUploadDoneTurn()) {
    await onBlueprintWizard(
      { step: "upload_done", blueprint_id: bid },
      latest.msgIndex,
      latest.cardIndex
    );
  }
  const liveStep = normalizeWizardStep(liveBlueprintWizard.value?.step || live.step);
  // 目录已提取，但对话没有「完成目录提取」/确认卡
  if (
    chapterN > 0 &&
    wizardStepIndex(liveStep) >= wizardStepIndex("outline_confirm") &&
    !conversationHasOutlineDoneTurn()
  ) {
    const tip = findLatestBlueprintWizard();
    if (!tip) return;
    await onBlueprintWizard(
      { step: "outline_done", blueprint_id: bid },
      tip.msgIndex,
      tip.cardIndex
    );
  }
}

/** 会话加载后：若 runtime 仍在对话向导步但消息无卡，补到最后一条助手气泡 */
function ensureSessionWizardCardVisible() {
  const rt = sessionRuntime.value || {};
  const flat = (messages.value || []).flatMap((m) => m.cards || []);
  if (hasEarlyCreateWizardCard(flat)) return;
  const synth = synthesizeChatWizardCardFromRuntime(rt, "");
  if (!synth) return;
  for (let mi = (messages.value || []).length - 1; mi >= 0; mi -= 1) {
    const msg = messages.value[mi];
    if (msg?.role !== "assistant") continue;
    const cards = [...(msg.cards || [])];
    if (cards.some((c) => c?.type === "blueprint_wizard")) return;
    msg.cards = [...cards, synth];
    return;
  }
}

/** 待收录要素改到文档窗口角标，不再往消息流补卡 */
function ensureLibraryPendingCardVisible(_session) {
  /* no-op */
}

const elementLibraryPendingItems = computed(() => {
  const fromRuntime = sessionRuntime.value?.element_library_pending;
  if (Array.isArray(fromRuntime)) return fromRuntime;
  return [];
});

async function onLibraryPendingChanged(payload) {
  const sid = String(sessionId.value || "").trim();
  if (!sid) return;
  try {
    const session = await request(`/api/writing/chat/sessions/${encodeURIComponent(sid)}`);
    const pending =
      session?.runtime?.element_library_pending ||
      session?.freeform?.element_library_pending ||
      [];
    sessionRuntime.value = {
      ...(sessionRuntime.value || {}),
      ...(session?.runtime && typeof session.runtime === "object" ? session.runtime : {}),
      element_library_pending: Array.isArray(pending) ? pending : [],
    };
  } catch {
    /* 刷新失败不挡操作；角标会在下次 runtime 事件更新 */
  }
  if (Array.isArray(payload?.element_ids) && payload.element_ids.length) {
    bumpDocOutlineReload({ force: true });
  }
}

/** 文档窗静默新增要素后入队待收录，并刷新角标（默认不弹窗，用户点「待收录要素」再开） */
async function queueElementLibraryPending({
  taskId,
  chapterKey = "",
  chapterTitle = "",
  elementIds = [],
  elements = [],
  openDialog = false,
} = {}) {
  const sid = String(sessionId.value || "").trim();
  const tid = String(taskId || "").trim();
  if (!sid || !tid) return null;
  const ids = (elementIds || []).map((x) => String(x || "").trim()).filter(Boolean);
  const els = Array.isArray(elements) ? elements.filter((e) => e && typeof e === "object") : [];
  if (!ids.length && !els.length) return null;
  try {
    const data = await request("/api/writing/chat/element-library/queue", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        session_id: sid,
        task_id: tid,
        chapter_key: String(chapterKey || "").trim(),
        chapter_title: String(chapterTitle || "").trim(),
        element_ids: ids,
        elements: els,
      }),
    });
    const pending = Array.isArray(data?.element_library_pending)
      ? data.element_library_pending
      : [];
    sessionRuntime.value = {
      ...(sessionRuntime.value || {}),
      element_library_pending: pending,
    };
    const queued = Number(data?.queued) || 0;
    if (queued > 0) {
      ElMessage.info("有新要素可收入个人库，请在文档窗点「待收录要素」");
      if (openDialog) {
        driveDocWindow("library_pending", {
          taskId: tid,
          chapterKey: String(chapterKey || "").trim(),
          tab: "config",
        });
      }
    }
    return data;
  } catch {
    return null;
  }
}

async function applySession(session, { suppressAutoPopups = true } = {}) {
  if (!session?.id) return;
  if (suppressAutoPopups) {
    sessionHydrating.value = true;
    suppressElementConfigureAutoOpen.value = true;
    docWindowOpen.value = false;
    docWindowAutoOpenArmed.value = false;
    docWindowAutoOpenedKey.value = "";
  }
  try {
    const nextSid = String(session?.id || "").trim();
    const prevSid = String(sessionId.value || "").trim();
    const sessionChanged = !prevSid || !nextSid || prevSid !== nextSid;
    // 仅在真正换会话时掐断旧流；同会话刷新不得 abort 正在进行的分析
    if (sessionChanged) {
      hardResetCrossSessionUi({ closeDocWindow: Boolean(suppressAutoPopups) });
      saveBlueprintHiddenLocal.value = false;
      saveBlueprintHiddenForTask.value = "";
    } else {
      stashLiveWizardForCurrentDoc();
      liveTemplateWizard.value = null;
      blueprintWizardLog.value = [];
      sessionRuntime.value = null;
    }
    persistSession(session.id);
    messages.value = mapSessionMessages(session);
    for (const m of messages.value) {
      if ((m.cards || []).some((c) => c?.type === "progress")) syncWritingProgressToBubble(m);
    }
    if (!messages.value.length) seedOpeningGreeting();
    phase.value = session.phase || "intent";
    taskId.value = session.task_id || "";
    selectedBlueprint.value = session.blueprint_id || "";
    pickedBlueprintId.value = session.blueprint_id || "";
    restoreUiStateFromSession(session);
    if (shouldSuppressBlueprintWizardForFreeform(session, session?.messages?.flatMap?.((m) => m.cards || []) || messages.value.flatMap((m) => m.cards || []))) {
      liveBlueprintWizard.value = null;
      blueprintWizardLog.value = [];
    } else {
      reconcileLocalWizardCardsFromSession(session);
      syncLiveWizardFromLatestCard();
      // 卡合并可能又带上历史 trigger：恢复会话必须再剥一层，禁止自动重跑
      if (liveBlueprintWizard.value) {
        liveBlueprintWizard.value = stripEphemeralWizardTriggers(liveBlueprintWizard.value);
      }
      ensureSessionWizardCardVisible();
      unlockLatestBlueprintWizardCard();
    }
    ensureLibraryPendingCardVisible(session);
    if (session.robot_id) {
      robotId.value = session.robot_id;
      // 先出消息，机器人侧车后台补；boot 已拉过 history
      void onRobotChange(session.robot_id, { skipResourceCheck: true, skipHistory: true });
    }
    scrollChatToBottom(true);
  } finally {
    if (suppressAutoPopups) {
      // 等历史卡片挂载完成后再解除抑制，避免 onMounted 误弹要素窗
      await nextTick();
      await nextTick();
      sessionHydrating.value = false;
      suppressElementConfigureAutoOpen.value = false;
    }
  }
}

async function loadHistory() {
  historyLoading.value = true;
  try {
    // 历史侧栏：当前登录用户的全部会话，不按顶部所选机器人过滤
    const params = new URLSearchParams({ limit: "40" });
    historyItems.value = (await request(`/api/writing/chat/sessions?${params}`)) || [];
  } catch {
    historyItems.value = [];
  } finally {
    historyLoading.value = false;
  }
}

async function deleteHistory(item) {
  const id = String(item?.id || "").trim();
  if (!id || sending.value || globalLoading.value) return;
  try {
    await ElMessageBox.confirm("删除后无法恢复。确定删除这条对话？", "删除对话", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    });
  } catch {
    return;
  }
  try {
    await request(`/api/writing/chat/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
    historyItems.value = (historyItems.value || []).filter((row) => row.id !== id);
    ElMessage.success("已删除");
    if (id === sessionId.value) {
      await startNewChat();
    }
  } catch (error) {
    ElMessage.error(error.message || "删除失败");
  }
}

async function openHistory(id) {
  if (!id || sending.value || globalLoading.value || id === sessionId.value) return;
  openingSessionId.value = id;
  globalLoading.value = true;
  globalLoadingText.value = "正在切换对话…";
  try {
    // 先落盘当前会话（含提取中标记），再切换，避免回来丢失进度
    if (sessionId.value) {
      try {
        await persistUiState();
      } catch {
        /* 仍继续切换 */
      }
    }
    const session = await request(`/api/writing/chat/sessions/${encodeURIComponent(id)}`);
    await applySession(session);
  } catch (error) {
    ElMessage.error(error.message || "打开历史失败");
  } finally {
    openingSessionId.value = "";
    globalLoading.value = false;
  }
  // 加载结束后再按任务进度补齐缺失步骤对话
  try {
    await backfillMissingWizardTurns();
  } catch {
    /* 忽略 */
  }
}

async function startNewChat() {
  if (globalLoading.value) return;
  if (streamAbortController) {
    streamAbortController.abort();
    streamAbortController = null;
  }
  sending.value = false;
  // 先掐断旧会话分析流，再清 UI，避免进度写到新问候语/旧文档窗
  hardResetCrossSessionUi({ closeDocWindow: true });
  globalLoading.value = true;
  globalLoadingText.value = "正在开启新对话…";
  try {
    persistSession("");
    messages.value = [];
    phase.value = "intent";
    taskId.value = "";
    selectedBlueprint.value = "";
    pickedBlueprintId.value = "";
    usedCardKeys.value = new Set();
    clearEditor();
    const session = await request("/api/writing/chat/sessions", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ robot_id: robotId.value || "" }),
    });
    await applySession(session);
    await loadHistory();
    scheduleScrollChatToBottom(true);
  } catch (error) {
    ElMessage.error(error.message || "创建新对话失败");
    seedOpeningGreeting();
  } finally {
    globalLoading.value = false;
  }
}

async function restoreCurrentSession({ suppressAutoPopups = true } = {}) {
  const sid = props.resumeSessionId || sessionId.value;
  if (!sid) return false;
  const ownMask = !globalLoading.value;
  if (ownMask) {
    globalLoading.value = true;
    globalLoadingText.value = "正在加载对话…";
  }
  try {
    const session = await request(`/api/writing/chat/sessions/${encodeURIComponent(sid)}`);
    await applySession(session, { suppressAutoPopups });
    try {
      await backfillMissingWizardTurns();
    } catch {
      /* 忽略补写失败 */
    }
    // backfill / softSync 可能又带上服务端残留分析 trigger：恢复后必须再剥，禁止自动重跑
    if (liveBlueprintWizard.value) {
      liveBlueprintWizard.value = stripEphemeralWizardTriggers(liveBlueprintWizard.value);
    }
    pageStudyToken = "";
    suppressStaleStudyKickAfterRestore = true;
    return true;
  } catch {
    if (!props.resumeSessionId) persistSession("");
    return false;
  } finally {
    if (ownMask) globalLoading.value = false;
  }
}

function applyDoneMeta(data) {
  if (data.session_id) persistSession(data.session_id);
  if (data.phase) phase.value = data.phase;
  // 选用其它蓝本后服务端会清空旧写作 task_id；必须同步清空，避免文档窗仍挂旧 clone
  if (Object.prototype.hasOwnProperty.call(data || {}, "task_id")) {
    taskId.value = String(data.task_id || "").trim();
  }
  if (data.runtime && typeof data.runtime === "object") {
    sessionRuntime.value = { ...data.runtime };
    const tpl = data.runtime?.workflows?.template_wizard;
    if (tpl) liveTemplateWizard.value = { ...tpl };
    const ex = data.runtime?.workflows?.export;
    if (ex) {
      if (ex.template_id) exportTemplateId.value = String(ex.template_id);
      if (ex.style_id) exportWordStyleId.value = String(ex.style_id);
      if (ex.confirmed_only !== undefined) exportConfirmedOnly.value = Boolean(ex.confirmed_only);
    }
  }
  // 口语切文档：runtime / events 更新后再同步小窗（依赖登记表）
  const focusFromEvents = Array.isArray(data?.events)
    ? data.events.find((e) => e?.type === "focus_document")
    : null;
  const focusKey = String(
    focusFromEvents?.focus_document_key || data?.runtime?.focus_document_key || ""
  ).trim();
  if (focusKey) {
    syncDocWindowToFocusKey(focusKey);
  } else {
    const tid = String(data.task_id || taskId.value || "").trim();
    if (tid) {
      const taskFocus = normalizeDocumentRegistryKey(`task:${tid}`);
      const activeNorm = normalizeDocumentRegistryKey(String(docWindowActiveId.value || "").trim());
      if (
        isFreeformWritingSession({
          phase: data.phase || phase.value,
          runtime: data.runtime || sessionRuntime.value,
          freeform: { writing_path: data.runtime?.writing_path },
        }) ||
        !activeNorm ||
        activeNorm !== taskFocus
      ) {
        syncDocWindowToFocusKey(`task:${tid}`);
      }
    }
  }
  const wizardFromDone = pickNonEmptyWizard(
    data.blueprint_wizard,
    data.runtime?.workflows?.blueprint_wizard
  );
  // 本轮卡片里若带分析 trigger（study 卡随后会被滤掉），勿在此清 live / 剥 trigger
  const studyPayloadFromCards = (() => {
    for (const c of data?.cards || []) {
      if (c?.type !== "blueprint_wizard") continue;
      const p = c.payload || {};
      const keys = p.trigger_chapter_study || p.chapter_study_keys || [];
      if (
        p.trigger_begin_study === true ||
        (Array.isArray(keys) && keys.length > 0)
      ) {
        return p;
      }
    }
    return null;
  })();
  const studyKickFromWizard = (() => {
    const src = studyPayloadFromCards || wizardFromDone;
    if (!src || typeof src !== "object") return null;
    const keys = (src.trigger_chapter_study || src.chapter_study_keys || [])
      .map((k) => String(k || "").trim())
      .filter(Boolean);
    if (src.trigger_begin_study === true || keys.length) {
      return {
        ...src,
        step: "study",
        ...(keys.length ? { trigger_chapter_study: keys, chapter_study_keys: keys } : {}),
      };
    }
    return null;
  })();
  const liveHasStudyKick = (() => {
    const live = liveBlueprintWizard.value || {};
    const keys = (live.trigger_chapter_study || live.chapter_study_keys || [])
      .map((k) => String(k || "").trim())
      .filter(Boolean);
    return live.trigger_begin_study === true || keys.length > 0;
  })();
  // softSync/GET 路径不得因残留卡 payload 重新武装；仅 SSE done 显式带 kick 时才 arm
  const allowArmFromDone = data?._allowStudyKickArm === true;
  if (studyKickFromWizard && allowArmFromDone) {
    pageStudyKickArmed = true;
    applyLiveWizardFromSource(studyKickFromWizard);
  } else if (studyKickFromWizard && !allowArmFromDone) {
    // GET/softSync 偶发带回 trigger：剥掉，禁止自动开跑
    applyLiveWizardFromSource(stripEphemeralWizardTriggers(studyKickFromWizard));
  } else if (pageStudyInflight || (liveHasStudyKick && pageStudyKickArmed)) {
    // 本轮已排队/开跑的分析：softSync 剥过的 GET 快照不得清掉 trigger
  } else if (liveHasStudyKick) {
    // 上一轮残留 trigger：开写/闲聊 GET 不得再点燃自动分析
    liveBlueprintWizard.value = stripEphemeralWizardTriggers(liveBlueprintWizard.value || {});
  } else if (
    shouldSuppressBlueprintWizardForFreeform(
      {
        phase: data.phase || phase.value,
        runtime: data.runtime || sessionRuntime.value,
        freeform: {
          writing_path: data.runtime?.writing_path,
          blueprint_wizard: wizardFromDone,
        },
        blueprint_wizard: wizardFromDone,
      },
      data?.cards
    )
  ) {
    liveBlueprintWizard.value = null;
    selectedBlueprint.value = "";
  } else {
    const bp = wizardFromDone;
    if (bp) {
      // 会话快照 / softSync 里的 leftover trigger 不得点燃自动分析
      applyLiveWizardFromSource(stripEphemeralWizardTriggers(bp));
    }
  }
  lastBindings.value = {
    blueprint_ids: data.blueprint_ids || [],
    render_template_ids: data.render_template_ids || [],
    word_style_ids: data.word_style_ids || [],
  };
  syncDefaultWordStyle();
  syncDefaultExportTemplate();
}

function formatStepEvent(event) {
  const step = event.step || "";
  const status = event.status || "";
  const title = event.title || event.chapter || "";
  const label = STEP_LABELS[step] || step || "步骤";
  const statusCn = { running: "进行中", done: "完成", skipped: "跳过", error: "失败" }[status] || status;
  const note = String(event?.result?.note || "").trim();
  let line = `${label} · ${statusCn}${title ? ` · ${title}` : ""}`;
  if (note) line += ` ${note}`;
  return line;
}

function pushProcess(msgRef, message, kind = "info", extra = null) {
  if (!msgRef || !message) return;
  if (!msgRef.steps) msgRef.steps = [];
  const text = String(message || "").trim();
  if (!text) return;
  const last = msgRef.steps[msgRef.steps.length - 1];
  const lastMsg = String(last?.message || "");
  // 完成态顶掉上一条「正在…」同义过程，避免「已起草」出现两行
  if (
    last &&
    String(last.kind || "") === String(kind || "") &&
    (lastMsg === text || processMessageAsDone(lastMsg) === text)
  ) {
    msgRef.steps[msgRef.steps.length - 1] = {
      ...last,
      message: text,
      ...(extra && typeof extra === "object" ? extra : {}),
    };
    return;
  }
  const row = { message: text, kind };
  if (extra && typeof extra === "object") Object.assign(row, extra);
  msgRef.steps.push(row);
}

function pushChainDebug(msgRef, event) {
  if (!msgRef || !event) return;
  if (!msgRef.chainLog) msgRef.chainLog = [];
  msgRef.chainLog.push(event);
  pushProcess(msgRef, event.summary || event.step || "调试", "chain", {
    detail: event.detail || {},
    chainStep: event.step || "",
  });
}

async function streamChat({ message = "", action = "", action_payload = {}, client_sync = null } = {}) {
  const ownerEpoch = currentSessionEpoch();
  const ownerSidAtStart = String(sessionId.value || "").trim();
  streamFocusPinnedKey = "";
  const writeStreamAction =
    action === "start_writing" ||
    action === "write_analyzed_only" ||
    action === "retry_chapter" ||
    action === "save_and_retry";
  writingStreamActive = writeStreamAction;
  const stillThisStream = () => {
    if (!isCurrentSessionContext(ownerEpoch)) return false;
    const cur = String(sessionId.value || "").trim();
    // 开流时已有 session：禁止写到已切换走的会话；开流时为空：允许首包绑定
    if (ownerSidAtStart && cur && cur !== ownerSidAtStart) return false;
    return true;
  };
  const streamDocId = currentFocusDocumentId();
  const assistant = {
    role: "assistant",
    text: "",
    cards: [],
    steps: [],
    streaming: true,
    document_key: streamDocId,
    document_id: streamDocId,
  };
  messages.value.push(assistant);
  const msgRef = messages.value[messages.value.length - 1];
  scheduleScrollChatToBottom(true);

  streamAbortController = new AbortController();
  let response;
  try {
    response = await fetch("/api/writing/chat/stream", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      signal: streamAbortController.signal,
      body: JSON.stringify({
        message,
        robot_id: robotId.value || "",
        session_id: sessionId.value || "",
        action,
        action_payload,
        client_sync: client_sync || buildStreamClientSync(),
      }),
    });
  } catch (error) {
    streamAbortController = null;
    if (isAbortError(error)) {
      msgRef.streaming = false;
      return;
    }
    throw error;
  }

  let finished = false;
  let aborted = false;
  try {
  await readStream(response, (event) => {
    if (!event || typeof event !== "object") return;
    // 换会话后丢弃迟到事件，禁止串写消息/runtime/文档窗
    if (!stillThisStream()) return;
    if (event.type === "session") {
      applyChatSessionEvent(event);
      return;
    }
    if (event.type === "focus_document") {
      const key = normalizeDocumentRegistryKey(String(event.focus_document_key || "").trim());
      if (key) {
        streamFocusPinnedKey = key;
        if (sessionRuntime.value && typeof sessionRuntime.value === "object") {
          sessionRuntime.value = {
            ...sessionRuntime.value,
            focus_document_key: key,
          };
        }
        // 本轮流式助手消息盖戳跟上新焦点，避免向导卡被多文档过滤误藏
        if (msgRef && typeof msgRef === "object") {
          msgRef.document_key = key;
          msgRef.document_id = key;
        }
        // 事件可带 task_id / blueprint_id：先对齐再挂窗
        const evTid = String(event.task_id || event.writing_task_id || "").trim();
        const evBid = String(event.blueprint_id || "").trim();
        if (evTid) taskId.value = evTid;
        if (evBid && !/^w/i.test(evBid)) selectedBlueprint.value = evBid;
        stashLiveWizardForCurrentDoc();
        const evPath = String(event.writing_path || "").trim();
        if (evPath && sessionRuntime.value && typeof sessionRuntime.value === "object") {
          sessionRuntime.value = { ...sessionRuntime.value, writing_path: evPath };
        }
        const evWiz = event.wizard;
        if (evWiz && typeof evWiz === "object" && Object.keys(evWiz).length) {
          rememberLiveWizardForDoc(key, evWiz);
          liveBlueprintWizard.value = { ...evWiz };
        }
        syncDocWindowToFocusKey(key, { stashCurrent: false });
        scrollChatToFocusThread();
        ensureSessionWizardCardVisible();
      }
      return;
    }
    if (event.type === "token") {
      msgRef.text += event.text || "";
      scheduleScrollChatToBottom();
      return;
    }
    if (event.type === "chain_debug") {
      if (import.meta.env.DEV) pushChainDebug(msgRef, event);
      return;
    }
    if (event.type === "process") {
      const msg = event.message || "";
      const live = liveBlueprintWizard.value || {};
      if (isOutlineExtractActive(live) && isOutlineExtractProgressMessage(msg)) {
        onBlueprintWizardProcess(msg);
        return;
      }
      // 写作过程：边跑边进行气泡虚线框（不要等整章结束）
      const writeLine =
        isWritingProcessMessage(msg) ||
        (writingStreamActive && /^[　▶✓]/.test(msg));
      if (writeLine) {
        appendWriteProgress(msgRef, msg);
        scheduleScrollChatToBottom();
        return;
      }
      const studyish =
        isStudyStreamActive(live) ||
        pageStudyInflight ||
        isStudyProgressMessage(msg) ||
        /正在处理蓝本向导/.test(msg);
      if (studyish) {
        const bp = sanitizeLibraryBlueprintId(
          live.source_blueprint_id || live.blueprint_id || selectedBlueprint.value || "",
          ""
        );
        const tid = String(live.working_task_id || taskId.value || "").trim();
        if (/正在处理蓝本向导/.test(msg)) {
          pushStudyProgressLine("✓ 已提交分析任务", "process", {
            blueprintId: bp,
            taskId: tid,
            invokeHook: false,
          });
        } else {
          pushStudyProgressLine(msg, "process", { blueprintId: bp, taskId: tid, invokeHook: false });
        }
        if (isPointsDraftProgressMessage(msg)) noteStudyProgressHeartbeat();
        scheduleScrollChatToBottom();
        return;
      }
      pushProcess(msgRef, msg, "process");
      if (isPointsDraftProgressMessage(msg)) noteStudyProgressHeartbeat();
      scheduleScrollChatToBottom();
      return;
    }
    // 无蓝本要点 / 蓝本分析：按章进度 → 定位文档窗并合入要点（与蓝本 study 同事件）
    if (event.type === "study_chapter_progress" && event.chapter) {
      const focusTid = String(event.task_id || taskId.value || "").trim();
      const key = String(event.chapter || "").trim();
      const analyzed = Number(event.study_analyzed || 0);
      const unanalyzed = Number(event.study_unanalyzed || 0);
      const total = Number(event.total || analyzed + unanalyzed || 0);
      const sourceBp = sanitizeLibraryBlueprintId(
        selectedBlueprint.value ||
          liveBlueprintWizard.value?.source_blueprint_id ||
          liveBlueprintWizard.value?.blueprint_id ||
          sessionRuntime.value?.blueprint_id ||
          "",
        ""
      );
      pushStudyProgressLine(
        studyProgressLineFromChapterEvent({ ...event, total: total || analyzed + unanalyzed }),
        "process",
        { blueprintId: sourceBp, taskId: focusTid, invokeHook: false }
      );
      pushStudySupplementProgressFromEvent(event, {
        blueprintId: sourceBp,
        taskId: focusTid,
        invokeHook: false,
      });
      if (focusTid && key) {
        const studyDone = unanalyzed <= 0 && analyzed > 0;
        if (studyDone) {
          setDocWindowStudyBusy(false);
        } else {
          setDocWindowStudyBusy(
            true,
            total > 0 ? `要点进度 ${analyzed}/${total}` : "正在起草写作要点…"
          );
        }
        // 副本 w* 分析时不要把 blueprintId 写成 w*，否则「同步到蓝本库」会丢源蓝本
        if (isWritingCopyId(focusTid) && sourceBp) {
          liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, {
            blueprint_id: sourceBp,
            source_blueprint_id: sourceBp,
            working_task_id: focusTid,
            step: "study",
            study_stream_active: !studyDone,
            study_analyzed: analyzed,
            study_unanalyzed: unanalyzed,
          });
        }
        driveDocWindow("analyze", {
          taskId: focusTid,
          blueprintId: sourceBp,
          chapterKey: key,
          tab: "config",
          points: Array.isArray(event.points) ? event.points : null,
          point_items: Array.isArray(event.point_items) ? event.point_items : null,
          keywords: Array.isArray(event.keywords) ? event.keywords : null,
          describes: event.describes || "",
        });
        // 本章要点已由 locate 合入；立刻拉目录会用库蓝本旧 point_items 盖掉右侧表
        if (unanalyzed <= 0) bumpDocOutlineReload({ force: true });
      }
      scheduleScrollChatToBottom();
      return;
    }
    if (event.type === "step") {
      const stepLine = formatStepEvent(event);
      const writing = isWritingPipelineEvent(event);
      if (writing) {
        // 边跑边追加；写步骤开始时补一次本章要点上下文
        const st = String(event.status || "").trim();
        const step = String(event.step || "").trim();
        if (step === "write" && (st === "running" || !st)) {
          const row =
            findProgressChapterRow(msgRef, event.chapter) ||
            {
              key: event.chapter,
              title: event.title || "",
              ...(event.result?.config || {}),
              points: event.result?.points,
              describes: event.result?.describes,
              aux_prompt: event.result?.aux_prompt,
              config: event.result?.config,
            };
          pushChapterWriteContext(msgRef, row, { title: event.title || "" });
          const defects = Array.isArray(event.result?.check_defects)
            ? event.result.check_defects
            : [];
          if (defects.length) {
            appendWriteProgress(msgRef, `上一轮检查缺陷 · ${defects.length} 条`);
            for (const d of defects.slice(0, 6)) {
              const msg = String(d?.message || "").trim();
              if (!msg) continue;
              const idx = Number(d?.index);
              appendWriteProgress(
                msgRef,
                Number.isFinite(idx) && idx >= 0 ? `改第 ${idx + 1} 段：${msg}` : `待改：${msg}`
              );
            }
          }
        }
        if (step === "write" && st === "done") {
          const paras = event.result?.paragraphs;
          const html = Array.isArray(paras)
            ? paras
                .map((p) => (typeof p === "string" ? p : p?.text || ""))
                .filter(Boolean)
                .join("\n")
            : "";
          const writeTid = String(
            (msgRef.cards || []).find((c) => c?.type === "progress")?.payload?.task_id ||
              event.task_id ||
              ""
          ).trim();
          const ck = String(event.chapter || "").trim();
          if (writeTid && ck && html.trim()) {
            liveWriteHtmlByChapter.set(`${writeTid}::${ck}`, html.trim());
          }
        }
        appendWriteProgress(
          msgRef,
          stepLine,
          event.status === "error" ? "error" : "process"
        );
      } else {
        pushProcess(msgRef, stepLine, event.status === "error" ? "error" : "step");
      }
      if (writing) {
        writingStreamActive = true;
        setDocWindowWriteBusy(true, "正在写作…");
      }
      if (event.chapter) {
        const progress = (msgRef.cards || []).find((c) => c?.type === "progress");
        const writeTid = progress?.payload?.task_id || event.task_id;
        if (writeTid) {
          if (writing) {
            upsertLiveWritingStep(writeTid, event.chapter, event);
          }
          expandOnlyChapter(writeTid, event.chapter, msgRef.cards, { refreshText: false });
          if (writing) {
            const st = String(event.status || "").trim();
            const step = String(event.step || "").trim();
            const running =
              st === "running" ||
              (!st && ["searching", "writing", "checking", "running"].includes(step));
            if (running || st === "done" || !st) {
              driveDocWindow("write_running", {
                taskId: writeTid,
                chapterKey: event.chapter,
                mainView: "chapter",
                tab: "content",
              });
            }
          }
        }
      }
      scheduleScrollChatToBottom();
      return;
    }
    if (event.type === "chapter_start") {
      const idx = event.index && event.total ? `(${event.index}/${event.total}) ` : "";
      const line = `▶ ${idx}开始写「${event.title || event.chapter || "章节"}」`;
      writingStreamActive = true;
      setDocWindowWriteBusy(true, "正在写作…");
      const row =
        findProgressChapterRow(msgRef, event.chapter) ||
        { key: event.chapter, title: event.title || "" };
      pushChapterWriteContext(msgRef, row, { title: event.title || event.chapter || "" });
      appendWriteProgress(msgRef, line);
      if (event.task_id && event.chapter) {
        resetLiveWritingChapter(event.task_id, event.chapter, {
          title: event.title || "",
        });
        expandOnlyChapter(event.task_id, event.chapter, msgRef.cards, { refreshText: true });
        driveDocWindow("write_running", {
          taskId: event.task_id,
          chapterKey: event.chapter,
          mainView: "chapter",
          tab: "content",
        });
      }
      scheduleScrollChatToBottom();
      return;
    }
    if (event.type === "chapter_done") {
      const idx = event.index && event.total ? `(${event.index}/${event.total}) ` : "";
      const st = event.status_label || event.status || "";
      const line = `✓ ${idx}「${event.title || event.chapter || "章节"}」结束${st ? ` · ${st}` : ""}`;
      const doneTid = String(
        (Array.isArray(event.cards) &&
          event.cards.find((c) => c?.type === "progress")?.payload?.task_id) ||
          event.task_id ||
          ""
      ).trim();
      const doneKey = String(event.chapter || "").trim();
      if (Array.isArray(event.cards) && event.cards.length) {
        msgRef.cards = event.cards;
        const progress = event.cards.find((c) => c?.type === "progress");
        const tid = progress?.payload?.task_id || event.task_id;
        const hit = (progress?.payload?.chapters || []).find((c) => c?.key === event.chapter);
        if (tid && hit) adoptServerDraft(tid, hit, { force: true });
        if (tid && event.chapter) clearLiveWritingChapter(tid, event.chapter);
        pushChapterWriteDone(msgRef, hit, {
          title: event.title || hit?.title || "",
          statusLabel: st,
          preview: event.draft_html || event.draft_preview || hit?.draft_preview || "",
        });
        expandOnlyChapter(tid || event.task_id, event.chapter, event.cards, { refreshText: true });
      } else if (event.task_id && event.chapter) {
        clearLiveWritingChapter(event.task_id, event.chapter);
        expandOnlyChapter(event.task_id, event.chapter, msgRef.cards, { refreshText: true });
        const hit = findProgressChapterRow(msgRef, event.chapter);
        pushChapterWriteDone(msgRef, hit, {
          title: event.title || "",
          statusLabel: st,
          preview: event.draft_html || event.draft_preview || hit?.draft_preview || "",
        });
      } else {
        appendWriteProgress(msgRef, line);
      }
      const doneHtml = String(
        event.draft_html ||
          liveWriteHtmlByChapter.get(`${doneTid}::${doneKey}`) ||
          event.draft_preview ||
          ""
      ).trim();
      if (doneTid && doneKey && doneHtml) {
        liveWriteHtmlByChapter.set(`${doneTid}::${doneKey}`, doneHtml);
      }
      bumpDraftPreviewReload();
      if (doneTid && doneKey) {
        driveDocWindow("write_chapter_done", {
          taskId: doneTid,
          chapterKey: doneKey,
          mainView: "preview",
          tab: "content",
          draftHtml: doneHtml,
          draftTitle: event.title || "",
        });
      }
      // 软刷目录：把已写章节 status/has_draft 合回蓝本树；刷完后钉回写作进度（获取到的要素）
      bumpDocOutlineReload({ force: docWindowOpen.value });
      if (doneTid && doneKey) {
        nextTick(() => {
          bumpDraftPreviewReload();
          driveDocWindow("write_chapter_done", {
            taskId: doneTid,
            chapterKey: doneKey,
            mainView: "preview",
            tab: "content",
            draftHtml: doneHtml,
            draftTitle: event.title || "",
          });
        });
      }
      return;
    }
    if (event.type === "writing_paused" || event.type === "run_done") {
      writingStreamActive = false;
      setDocWindowWriteBusy(false);
      bumpDocOutlineReload({ force: true });
      bumpDraftPreviewReload();
      if (event.type === "writing_paused") {
        if (Array.isArray(event.cards) && event.cards.length) {
          msgRef.cards = event.cards;
        }
        pushProcess(msgRef, "写作已暂停，已确认的章节已保存到数据库。", "process");
        onBlueprintWizardProcess("写作已暂停，已确认的章节已保存到数据库。");
        openFocusChapter(msgRef.cards);
        finished = true;
        msgRef.streaming = false;
        scheduleScrollChatToBottom(true);
      }
      return;
    }
    if (event.type === "error") {
      setDocWindowWriteBusy(false);
      pushProcess(msgRef, event.detail || "执行失败", "error");
      if (!msgRef.text) msgRef.text = event.detail || "执行失败";
      msgRef.streaming = false;
      finished = true;
      return;
    }
    if (event.type === "done") {
      finished = true;
      // 写作流水线未结束时保留锁定；由 run_done / writing_paused 解锁
      if (!writingStreamActive) {
        setDocWindowWriteBusy(false);
      }
      // 先记下服务端中文回复。后面刷新目录若空指针，不能把异常盖掉这条。
      const serverReply = String(event.reply || "").trim();
      const doneCardsFallback = Array.isArray(event.cards) ? event.cards : [];
      if (serverReply) msgRef.text = serverReply;
      try {
      applyDoneMeta({ ...event, _allowStudyKickArm: true });
      const doneFocus = normalizeDocumentRegistryKey(
        String(
          event.runtime?.focus_document_key ||
            sessionRuntime.value?.focus_document_key ||
            streamFocusPinnedKey ||
            ""
        ).trim()
      );
      if (doneFocus) {
        streamFocusPinnedKey = streamFocusPinnedKey || doneFocus;
        if (msgRef && typeof msgRef === "object") {
          msgRef.document_key = doneFocus;
          msgRef.document_id = doneFocus;
        }
        syncDocWindowToFocusKey(doneFocus);
      }
      msgRef.text = event.reply || msgRef.text || "（无回复）";
      let nextCards = event.cards || [];
      // 分析卡随后会从气泡剔除：先抠出 trigger，否则 runtime 已剥 trigger、live 被 freeform 清掉后永远不开跑
      let studyCardPayload = null;
      for (const c of nextCards || []) {
        if (c?.type !== "blueprint_wizard") continue;
        const p = c.payload || {};
        const keys = p.trigger_chapter_study || p.chapter_study_keys || [];
        if (
          p.trigger_begin_study === true ||
          (Array.isArray(keys) && keys.length > 0)
        ) {
          studyCardPayload = p;
          break;
        }
      }
      const eventWizard = pickNonEmptyWizard(
        studyCardPayload,
        event.blueprint_wizard,
        event.runtime?.workflows?.blueprint_wizard
      );
      const freeformNow = shouldSuppressBlueprintWizardForFreeform(
        {
          phase: event.phase || phase.value,
          runtime: event.runtime,
          freeform: {
            ...(event.runtime?.workflows?.freeform || {}),
            writing_path: event.runtime?.writing_path,
            blueprint_wizard: eventWizard,
          },
          blueprint_wizard: eventWizard,
        },
        nextCards
      );
      const keepEarlyWizard =
        hasEarlyCreateWizardCard(nextCards) ||
        hasEarlyCreateWizardCard(msgRef?.cards || []);
      // 分析/完成不进会话气泡；名称/上传/提取/确认目录必须保留（否则无「确认目录」按钮）
      const chatWizardSteps = new Set(["meta", "upload", "outline", "outline_confirm"]);
      const writingDoneTurn =
        action === "start_writing" ||
        action === "write_analyzed_only" ||
        action === "retry_chapter" ||
        action === "save_and_retry" ||
        action === "skip_writing_supplement_check";
      const isWriteTurn =
        writingDoneTurn ||
        action === "select_write_chapters" ||
        /还没完成蓝本分析|正在按你说的章节准备开写|正在按刚才分析的章节准备开写|开始写「/.test(
          String(serverReply || "")
        );
      nextCards = (nextCards || []).filter((c) => {
        if (c?.type !== "blueprint_wizard") return true;
        if (writingDoneTurn) return false;
        const step = normalizeWizardStep(c?.payload?.step || eventWizard?.step);
        return chatWizardSteps.has(step);
      });
      if (!writingDoneTurn) {
        nextCards = ensureChatWizardCardsFromRuntime(
          nextCards,
          {
            ...(event.runtime || {}),
            blueprint_wizard: eventWizard || event.blueprint_wizard,
            workflows: {
              ...((event.runtime || {}).workflows || {}),
              blueprint_wizard: eventWizard || event.runtime?.workflows?.blueprint_wizard,
            },
          },
          serverReply || event.reply || ""
        );
      }
      const doneHasStudyKick =
        Boolean(studyCardPayload) ||
        Boolean(eventWizard?.trigger_begin_study === true) ||
        (Array.isArray(eventWizard?.trigger_chapter_study) &&
          eventWizard.trigger_chapter_study.length > 0) ||
        (Array.isArray(eventWizard?.chapter_study_keys) &&
          eventWizard.chapter_study_keys.length > 0);
      if (
        freeformNow &&
        !keepEarlyWizard &&
        !hasEarlyCreateWizardCard(nextCards) &&
        !doneHasStudyKick
      ) {
        // 本轮已无早期卡才清 live；有提取/确认卡或本轮分析 trigger 时必须保留
        // 分析卡会被滤掉，但 done.blueprint_wizard 仍可能带 trigger（澄清选「依据样本」后）
        const hasChatWizard = (nextCards || []).some(
          (c) =>
            c?.type === "blueprint_wizard" &&
            chatWizardSteps.has(normalizeWizardStep(c?.payload?.step))
        );
        if (!hasChatWizard) liveBlueprintWizard.value = null;
      }
      msgRef.cards = nextCards;
      // 实时写作过程已按 step/chapter 增量追加；此处勿整卡重刷，避免一下蹦出
      if (
        (action === "start_writing" ||
          action === "write_analyzed_only" ||
          action === "retry_chapter" ||
          action === "save_and_retry") &&
        (nextCards || []).some((c) => c?.type === "writing_supplement_check")
      ) {
        writingStreamActive = false;
        setDocWindowWriteBusy(false);
        const pl = action_payload || {};
        const prog = (nextCards || []).find((c) => c?.type === "progress");
        const blockTid = String(
          pl.task_id || pl.taskId || prog?.payload?.task_id || taskId.value || ""
        ).trim();
        const blockKeys = (pl.chapter_keys || pl.chapterKeys || [])
          .map((k) => String(k || "").trim())
          .filter(Boolean);
        const blockKey = String(
          pl.focus_chapter_key || pl.chapterKey || blockKeys[0] || ""
        ).trim();
        if (blockTid && blockKey) {
          driveDocWindow("open_doc_window", {
            taskId: blockTid,
            chapterKey: blockKey,
            tab: "content",
          });
        }
      }
      const wizCard = nextCards.find((c) => c?.type === "blueprint_wizard");
      const runtimeBp = eventWizard;
      // 优先用未滤掉前的分析卡 payload（含 trigger_chapter_study）
      const source =
        studyCardPayload || runtimeBp || wizCard?.payload || event.blueprint_wizard;
      const allowWizardLive =
        !freeformNow ||
        keepEarlyWizard ||
        hasEarlyCreateWizardCard(nextCards) ||
        Boolean(studyCardPayload) ||
        doneHasStudyKick;
      // 保存章节配置 / 加要素等不得复活会话里残留的分析 trigger，否则会「保存完就开始分析」
      // resolve_intent_clarify：点选「依据样本分析」后服务端已挂 trigger，但分析卡被滤掉，必须按分析意图开跑
      const studyIntentAction =
        !isWriteTurn &&
        (action === "blueprint_wizard" ||
          action === "re_study_chapters" ||
          action === "go_study" ||
          action === "confirm_outline" ||
          action === "resolve_intent_clarify");
      // 关联要素 / 加要素 / 保存配置等：即使 done 误带残留 trigger 也禁止开跑
      const blockStudyKickAction = new Set([
        "associate_elements",
        "add_chapter_element",
        "configure_chapter_element",
        "save_chapter_config",
        "set_session_meta",
        "confirm_writing_document_name",
        "focus_document",
        "open_element_library",
        "open_knowledge",
      ]);
      const nonStudyAction =
        blockStudyKickAction.has(String(action || "").trim()) ||
        (nextCards || []).some(
          (c) =>
            c?.type === "associate_elements" ||
            c?.type === "element_configure" ||
            c?.type === "element_configure_pending" ||
            c?.type === "chapter_element_picker"
        ) ||
        // 口语「给×章添加要素」action 为空，靠回复/卡识别，禁止残留 trigger 开分析
        /生成.+要素草稿|保存并关联本章|添加章节要素|已为「[^」]+」打开关联面板/.test(
          String(serverReply || event.reply || "")
        );
      if (isWriteTurn || nonStudyAction) {
        pageStudyKickArmed = false;
        liveBlueprintWizard.value = stripEphemeralWizardTriggers(
          liveBlueprintWizard.value || {}
        );
      }
      const hasStudyTriggerInSource =
        source &&
        typeof source === "object" &&
        (source?.trigger_begin_study === true ||
          (Array.isArray(source?.trigger_chapter_study) &&
            source.trigger_chapter_study.length > 0) ||
          (Array.isArray(source?.chapter_study_keys) &&
            source.chapter_study_keys.length > 0));
      // 口语「分析第×章」action 为空，靠 done 上的 trigger；显式非分析动作不得靠残留 trigger 开跑
      const allowStudyKickThisTurn =
        !isWriteTurn &&
        !nonStudyAction &&
        (studyIntentAction || studyCardPayload || doneHasStudyKick);
      // 依据样本在 study 步常被判 freeform：有分析 trigger 时仍须 apply + kick
      if (source && typeof source === "object" && (allowWizardLive || (studyIntentAction && hasStudyTriggerInSource))) {
        let applySource =
          allowStudyKickThisTurn
            ? source
            : stripEphemeralWizardTriggers(source);
        // chapter_study_keys 与 trigger_chapter_study 对齐，kick 两边都能读到
        if (applySource && typeof applySource === "object") {
          const keys = (
            applySource.trigger_chapter_study ||
            applySource.chapter_study_keys ||
            []
          )
            .map((k) => String(k || "").trim())
            .filter(Boolean);
          if (keys.length) {
            applySource = {
              ...applySource,
              step: "study",
              trigger_chapter_study: keys,
              chapter_study_keys: keys,
            };
          } else if (applySource.trigger_begin_study === true) {
            applySource = { ...applySource, step: "study" };
          }
        }
        applyLiveWizardFromSource(applySource);
        const hasStudyTrigger =
          applySource?.trigger_begin_study === true ||
          (Array.isArray(applySource?.trigger_chapter_study) &&
            applySource.trigger_chapter_study.length > 0);
        // 分析卡不进气泡：仅在本轮真有分析意图时兜底开跑
        if (allowStudyKickThisTurn && hasStudyTrigger) {
          if (String(applySource?.study_method || "").trim()) {
            stashPendingStudySource({ method: String(applySource.study_method).trim() });
          }
          schedulePageStudyKickoff(80, { arm: true });
        }
      }
      // 新消息里的向导卡保持服务端快照，禁止用 live 把「上传」卡改写成「确认目录」
      if (allowWizardLive && wizCard) {
        const freshMeta = isFreshBlueprintWizardStart(wizCard.payload || {});
        const cardBid = String(wizCard.payload?.blueprint_id || "").trim();
        const shellTid = String(
          wizCard.payload?.working_task_id || (freshMeta ? "" : source?.working_task_id) || ""
        ).trim();
        let snapStep = normalizeWizardStep(wizCard.payload?.step || (freshMeta ? "" : source?.step));
        // 新开名称卡不得继承上一份文档的 id，否则会被改写成「上传样板」并串到旧 bp
        if (!freshMeta && cardBid && wizardStepIndex(snapStep) <= wizardStepIndex("meta")) {
          snapStep = "upload";
        }
        if (!wizCard.title || wizCard.title === "蓝本创建步骤" || (snapStep !== "meta" && String(wizCard.title || "").includes("填写名称"))) {
          wizCard.title = WIZARD_STEP_CARD_HEADLINE[snapStep] || wizCard.title;
        }
        if (wizCard.payload && snapStep) {
          wizCard.payload = {
            ...wizCard.payload,
            step: snapStep,
            ...(cardBid && !freshMeta ? { blueprint_id: cardBid } : {}),
          };
        }
        if (freshMeta) {
          applyLiveWizardFromSource(wizCard.payload || {});
        } else if (cardBid || shellTid) {
          liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, {
            ...(source && typeof source === "object" ? source : {}),
            ...wizCard.payload,
            ...(cardBid ? { blueprint_id: cardBid } : {}),
            ...(shellTid ? { working_task_id: shellTid } : {}),
            step: snapStep,
          });
          const wKey = wizardDocKeyFromPayload(
            liveBlueprintWizard.value,
            streamFocusPinnedKey || currentFocusDocumentId()
          );
          if (wKey) rememberLiveWizardForDoc(wKey, liveBlueprintWizard.value);
        }
      }
      if (allowWizardLive) {
        reconcileLocalWizardCardsFromSession({
          runtime: { workflows: { blueprint_wizard: liveBlueprintWizard.value || source || {} } },
          freeform: { blueprint_wizard: liveBlueprintWizard.value || source || {} },
        });
      }
      noteFreshDocWindowTriggers(msgRef.cards);
      const progressCard = nextCards.find((c) => c?.type === "progress");
      if (progressCard?.payload?.task_id) {
        const tid = progressCard.payload.task_id;
        for (const row of progressCard.payload.chapters || []) {
          if (row?.has_draft) adoptServerDraft(tid, row);
        }
        // 进度卡若已带要点，立刻强制刷新文档窗（避免空预览盖住已生成要点）
        const hasPoints = (progressCard.payload.chapters || []).some((row) => {
          const pts = row?.points || row?.config?.points || [];
          return Array.isArray(pts) && pts.length > 0;
        });
        if (hasPoints) {
          bumpDocOutlineReload({ force: true });
        }
      }
      openFocusChapter(msgRef.cards);
      const associateMsgIndex = messages.value.indexOf(msgRef);
      nextTick(() => {
        maybeAutoOpenAssociateElements(msgRef.cards, associateMsgIndex);
        maybeAutoOpenDocWindowHint(msgRef.cards, associateMsgIndex);
      });
      finalizeAssistantProcessSteps(msgRef);
      releasePointsDraftLockIfComplete(msgRef, event.runtime, { notifyIncomplete: true });
      msgRef.streaming = false;
      if (!freeformNow) unlockLatestBlueprintWizardCard();
      scheduleScrollChatToBottom(true);
      } catch (error) {
        console.error(error);
        if (!String(msgRef.text || "").trim()) {
          msgRef.text = serverReply || "（无回复）";
        }
        if (!msgRef.cards?.length && doneCardsFallback.length) {
          msgRef.cards = doneCardsFallback;
        }
        msgRef.streaming = false;
      }
    }
  }, { signal: streamAbortController.signal });
  } catch (error) {
    if (isAbortError(error)) {
      aborted = true;
    } else {
      throw error;
    }
  }

  finalizeAssistantProcessSteps(msgRef);
  msgRef.streaming = false;
  streamAbortController = null;
  if (aborted) return;
  schedulePersistUiState();
  unlockLatestBlueprintWizardCard();
  if (!finished && !msgRef.text) {
    msgRef.text = "（无回复）";
  }
  releasePointsDraftLockIfComplete(msgRef, sessionRuntime.value, { notifyIncomplete: true });
  if (!sending.value) {
    activeRunChapterKey.value = "";
  }
  scheduleScrollChatToBottom(true);
}

async function pauseWriting() {
  if (!sessionId.value || pauseBusy.value || sending.value) return;
  pauseBusy.value = true;
  try {
    streamAbortController?.abort();
    streamAbortController = null;
    await sendAction("pause_writing", {});
    ElMessage.success("已暂停写作");
  } catch (error) {
    if (!isAbortError(error)) {
      ElMessage.error(error.message || "暂停失败");
    }
  } finally {
    pauseBusy.value = false;
    activeRunChapterKey.value = "";
  }
}

async function send() {
  if (composerLocked.value) return;
  syncDraftFromEditor();
  const text = payloadMessage();
  if (!text || sending.value) return;
  const focusDocId = currentFocusDocumentId();
  messages.value.push({
    role: "user",
    text,
    cards: [],
    steps: [],
    document_key: focusDocId,
    document_id: focusDocId,
  });
  scheduleScrollChatToBottom(true);
  clearEditor();
  sending.value = true;
  try {
    await streamChat({ message: text });
  } catch (error) {
    if (!isAbortError(error)) {
      if (isClientSideStreamBug(error)) {
        console.error(error);
        const assistantMsg = lastAssistantMessageInChat();
        if (assistantMsg && !String(assistantMsg.text || "").trim()) {
          assistantMsg.text = "（无回复）";
        }
      } else {
        messages.value.push({
          role: "assistant",
          text: error.message || "发送失败",
          cards: [],
          steps: [],
          document_key: focusDocId,
          document_id: focusDocId,
        });
      }
    }
  } finally {
    sending.value = false;
    void kickPageLevelStudyIfNeeded();
    await softSyncSessionAfterWizardAction();
    await loadHistory();
    await nextTick();
    editorEl.value?.focus();
  }
}

function cardKey(msgIndex, cardIndex) {
  return `${msgIndex}:${cardIndex}`;
}

function isCardUsed(msgIndex, cardIndex) {
  if (usedCardKeys.value.has(cardKey(msgIndex, cardIndex))) return true;
  const card = messages.value[msgIndex]?.cards?.[cardIndex];
  return Boolean(card?.used);
}

function findLatestBlueprintWizard(forCard) {
  const focus = _docKeyStrip(currentFocusDocumentId());
  const needle = wizardCardIdentityIds(forCard);
  let found = null;
  let foundAny = null;
  const list = messages.value || [];
  for (let mi = 0; mi < list.length; mi++) {
    const cards = list[mi]?.cards || [];
    for (let ci = 0; ci < cards.length; ci++) {
      if (cards[ci]?.type !== "blueprint_wizard") continue;
      foundAny = { msgIndex: mi, cardIndex: ci };
      const ids = wizardCardIdentityIds(cards[ci]);
      if (needle.length) {
        if (ids.length && needle.some((id) => ids.includes(id))) {
          found = { msgIndex: mi, cardIndex: ci };
        }
        continue;
      }
      if (
        !focus ||
        !ids.length ||
        ids.some((id) => id === focus) ||
        isEarlyCreateWizardCard(cards[ci])
      ) {
        found = { msgIndex: mi, cardIndex: ci };
      }
    }
  }
  if (found) return found;
  if (needle.length) return null;
  return focus ? null : foundAny;
}

/** 仅最新一张蓝本向导可操作；历史步骤卡片只读展示（向导步骤在同一张卡上连续点，不因 used 锁死） */
function isBlueprintWizardStale(msgIndex, cardIndex) {
  const card = messages.value[msgIndex]?.cards?.[cardIndex];
  const latest = findLatestBlueprintWizard(card);
  if (!latest) return false;
  return latest.msgIndex !== msgIndex || latest.cardIndex !== cardIndex;
}

/**
 * 会话里展示创建可操作向导（名称/上传/提取/确认目录）；
 * 分析进度改由文档小窗，不随每轮回复再展示。
 */
function shouldShowWizardCardInChat(card, msgIndex, cardIndex) {
  const msg = messages.value?.[msgIndex];
  const writingHere =
    Boolean((msg?.steps || []).some((s) => s?._writeProgress)) ||
    /写作完成|开始写「|按章检索/.test(String(msg?.text || ""));
  if (writingHere) return false;
  return shouldShowBlueprintWizardInChat(card, msgIndex, cardIndex, {
    isFreeformWritingSession,
    isBlueprintWizardStale,
    wizardEffectiveStep,
    liveWizardForCard,
  });
}

function unlockLatestBlueprintWizardCard() {
  const latest = findLatestBlueprintWizard();
  if (!latest) return;
  const key = cardKey(latest.msgIndex, latest.cardIndex);
  if (!usedCardKeys.value.has(key)) return;
  const next = new Set(usedCardKeys.value);
  next.delete(key);
  usedCardKeys.value = next;
  const msg = messages.value[latest.msgIndex];
  const card = msg?.cards?.[latest.cardIndex];
  if (card?.used) {
    msg.cards[latest.cardIndex] = { ...card, used: false };
  }
}

function markCardUsed(msgIndex, cardIndex) {
  const next = new Set(usedCardKeys.value);
  next.add(cardKey(msgIndex, cardIndex));
  usedCardKeys.value = next;
  const msg = messages.value[msgIndex];
  if (msg?.cards?.[cardIndex]) {
    msg.cards[cardIndex] = { ...msg.cards[cardIndex], used: true };
  }
}

function chaptersToOutlineText(chapters) {
  return (chapters || [])
    .map((ch) => `${String(ch?.sequence || "").trim()} ${String(ch?.title || "").trim()}`.trim())
    .filter(Boolean)
    .join("\n");
}

function getOutlineDraft(card) {
  const tid = card?.payload?.task_id;
  const fromCard = String(card?.payload?.outline_text || "").trim();
  if (!tid) return fromCard || chaptersToOutlineText(card?.payload?.chapters);
  const cached = outlineDrafts.value[tid];
  if (cached !== undefined) return cached;
  return fromCard || chaptersToOutlineText(card?.payload?.chapters);
}

function setOutlineDraft(taskId, value) {
  if (!taskId) return;
  outlineDrafts.value = { ...outlineDrafts.value, [String(taskId)]: value };
}

async function confirmFreeformOutline(card) {
  const tid = card?.payload?.task_id;
  if (!tid || sending.value) return;
  await sendAction("submit_outline", {
    task_id: tid,
    outline_text: "",
    confirm: true,
  });
  bumpDocOutlineReload({ force: true });
}

async function collectFreeformToBlueprint(card) {
  const tid = String(card?.payload?.task_id || taskId.value || "").trim();
  if (!tid || sending.value || saveBlueprintBusy.value) return;
  const editable = Boolean(card?.payload?.editable || card?.payload?.pending_confirm);
  if (editable) {
    ElMessage.warning("请先确认大纲（点「确认大纲并继续」），再收录到蓝本库");
    return;
  }
  await collectBlueprintWithMeta(tid, {
    title: card?.payload?.suggested_title || card?.payload?.doc_title || "",
    category: card?.payload?.doc_category || "",
    tags: card?.payload?.tags || [],
  });
}

async function renameFreeformDocument(card) {
  if (sending.value) return;
  const prevTitle = String(card?.payload?.doc_title || "").trim();
  const prevCat = String(card?.payload?.doc_category || "").trim();
  const defaultLine = prevCat && prevTitle ? `${prevCat}｜${prevTitle}` : prevTitle || "";
  try {
    const { value } = await ElMessageBox.prompt(
      "文档打算叫什么？说说类型和题目就行，例如：述职报告、2026年度个人述职",
      "文档命名",
      {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        inputValue: defaultLine,
        inputPlaceholder: "文档类型或题目",
      }
    );
    const line = String(value || "").trim();
    if (!line) {
      ElMessage.warning("请填写文档名称");
      return;
    }
    await sendAction("set_session_meta", { message: line });
    bumpDocOutlineReload({ force: true });
  } catch {
    /* 取消 */
  }
}

async function onDocWindowMaterialsPackPending(payload) {
  const packId = String(payload?.packId || "").trim();
  const blueprintId = String(payload?.blueprintId || "").trim();
  const skip = Boolean(payload?.skip);
  if (skip || !packId) {
    await sendAction(
      "materials_pack_skip",
      { blueprint_id: blueprintId, task_id: "" },
      { force: true }
    );
  } else {
    await sendAction(
      "materials_pack_use",
      {
        pack_id: packId,
        blueprint_id: blueprintId,
        task_id: "",
        name: payload?.packName || payload?.name || "",
        pack_name: payload?.packName || payload?.name || "",
      },
      { force: true }
    );
  }
  await softSyncSessionAfterWizardAction();
  bumpDocOutlineReload({ force: true });
}

async function onDocWindowRenameDocument(payload) {
  const raw = String(payload?.title || "").trim();
  if (!raw) return;
  const ff = sessionRuntime.value?.workflows?.freeform || {};
  const goal = sessionRuntime.value?.session_goal || {};
  const cat = String(ff.category || goal.doc_category || "").trim();
  // 用户已写「类型｜标题」时原样提交；否则带上已知类型
  const hasPipe = /[｜|]/.test(raw);
  const message = hasPipe || !cat ? raw : `${cat}｜${raw}`;
  const titleOnly = hasPipe
    ? raw.split(/[｜|]/).slice(1).join("｜").trim() || raw
    : raw;
  const wid = String(payload?.writingTaskId || "").trim();
  const tid = String(payload?.taskId || wid || "").trim();
  const bid = String(payload?.blueprintId || "").trim();
  try {
    await sendAction(
      "set_session_meta",
      {
        message,
        title: titleOnly,
        ...(cat && !hasPipe ? { category: cat } : {}),
        // 只带写作任务 id；蓝本 id 不作为 task_id，避免改到蓝本库名称
        ...(tid ? { task_id: tid } : {}),
        ...(wid ? { writing_task_id: wid } : {}),
        ...(bid ? { blueprint_id: bid } : {}),
        rename_document_only: true,
      },
      { force: true }
    );
    // 立刻刷新会话登记标题，文档窗下拉跟着变
    const docId = String(payload?.docId || "").trim();
    if (docId && sessionRuntime.value?.session_documents?.[docId]) {
      const docs = { ...(sessionRuntime.value.session_documents || {}) };
      docs[docId] = { ...docs[docId], title: titleOnly };
      sessionRuntime.value = {
        ...sessionRuntime.value,
        session_documents: docs,
        session_goal: {
          ...(sessionRuntime.value.session_goal || {}),
          doc_title: titleOnly,
        },
        workflows: {
          ...(sessionRuntime.value.workflows || {}),
          freeform: {
            ...((sessionRuntime.value.workflows || {}).freeform || {}),
            title: titleOnly,
          },
        },
      };
    }
    await softSyncSessionAfterWizardAction();
    bumpDocOutlineReload({ force: true });
    const applied =
      String(sessionRuntime.value?.session_goal?.doc_title || "").trim() ||
      String(sessionRuntime.value?.workflows?.freeform?.title || "").trim() ||
      titleOnly;
    ElMessage.success(`文档名称已改为「${applied}」`);
  } catch (error) {
    if (!isAbortError(error)) {
      ElMessage.error(error?.message || "改名失败");
    }
  }
}

async function regenerateFreeformOutline(card) {
  const tid = card?.payload?.task_id;
  if (!tid || sending.value) return;
  const next = { ...outlineDrafts.value };
  delete next[tid];
  outlineDrafts.value = next;
  // 最近用户修订句 / 会话约束：让后端重生成时吃懂「每章三小节」等要求
  const constraint = String(
    sessionRuntime.value?.workflows?.freeform?.outline_constraints ||
      sessionRuntime.value?.outline_constraints ||
      ""
  ).trim();
  let lastUser = "";
  for (let i = (messages.value || []).length - 1; i >= 0; i -= 1) {
    const m = messages.value[i];
    if (m?.role === "user" && String(m.text || "").trim()) {
      lastUser = String(m.text || "").trim();
      break;
    }
  }
  const revision = constraint || lastUser;
  await sendAction("submit_outline", {
    task_id: tid,
    regenerate: true,
    outline_text: "",
    revision_instructions: revision,
    message: revision,
  });
}

async function reopenOutlineEdit(card) {
  const tid = card?.payload?.task_id;
  if (!tid || sending.value) return;
  await sendAction("submit_outline", {
    task_id: tid,
    reopen_edit: true,
  });
}

function patchLatestBlueprintWizardCardFromServer(session) {
  const serverMessages = session?.messages || [];
  let serverCard = null;
  for (let i = serverMessages.length - 1; i >= 0; i -= 1) {
    const cards = serverMessages[i]?.cards || [];
    for (let j = cards.length - 1; j >= 0; j -= 1) {
      if (cards[j]?.type === "blueprint_wizard") {
        serverCard = cards[j];
        break;
      }
    }
    if (serverCard) break;
  }
  if (!serverCard) return;
  const runtimeW = blueprintWizardStateFromSession(session);
  const latest = findLatestBlueprintWizard();
  if (!latest) return;
  const msg = messages.value[latest.msgIndex];
  if (!msg?.cards?.[latest.cardIndex]) return;
  const prev = msg.cards[latest.cardIndex];
  const prevStep = normalizeWizardStep(prev?.payload?.step);
  const serverStep = normalizeWizardStep(serverCard?.payload?.step);
  const runtimeStep = normalizeWizardStep(runtimeW?.step);
  const prevBid = String(prev?.payload?.blueprint_id || "").trim();
  const serverBid = String(serverCard?.payload?.blueprint_id || "").trim();
  const runtimeBid = String(runtimeW?.blueprint_id || "").trim();
  const bid = prevBid || serverBid || runtimeBid;
  // 本地已前进时，严禁用滞后的服务端 meta/旧卡盖掉本轮快照
  if (
    wizardStepIndex(prevStep) > wizardStepIndex(serverStep) &&
    (prevBid || wizardStepIndex(prevStep) > wizardStepIndex("meta"))
  ) {
    if (runtimeW?.blueprint_id) {
      liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, runtimeW);
    }
    return;
  }
  let snapStep = serverStep || prevStep || runtimeStep;
  if (wizardStepIndex(prevStep) > wizardStepIndex(snapStep)) snapStep = prevStep;
  if (bid && wizardStepIndex(snapStep) <= wizardStepIndex("meta")) snapStep = "upload";
  const snapPayload = {
    ...(prev.payload || {}),
    ...(serverCard.payload || {}),
    ...(bid ? { blueprint_id: bid } : {}),
    step: snapStep,
  };
  const title =
    snapStep !== "meta" &&
    (String(serverCard.title || "").includes("填写名称") ||
      String(serverCard.title || "").endsWith("· 名称") ||
      String(prev.title || "").includes("填写名称"))
      ? WIZARD_STEP_CARD_HEADLINE[snapStep] || serverCard.title || prev.title
      : serverCard.title || WIZARD_STEP_CARD_HEADLINE[snapStep] || prev.title;
  msg.cards[latest.cardIndex] = {
    ...prev,
    ...serverCard,
    title,
    payload: snapPayload,
  };
  if (runtimeW?.blueprint_id || bid) {
    liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, {
      ...(runtimeW || {}),
      ...(bid ? { blueprint_id: bid } : {}),
      step: snapStep,
    });
  }
}

let softSyncInflight = null;

async function softSyncSessionAfterWizardAction() {
  const sid = sessionId.value;
  if (!sid) return;
  if (softSyncInflight) return softSyncInflight;
  softSyncInflight = _softSyncSessionAfterWizardAction(sid);
  try {
    await softSyncInflight;
  } finally {
    softSyncInflight = null;
  }
}

async function _softSyncSessionAfterWizardAction(sid) {
  try {
    const session = await request(`/api/writing/chat/sessions/${encodeURIComponent(sid)}`);
    if (session.phase) phase.value = session.phase;
    // taskId 对齐放到 runtime 解析后：优先当前小窗文档，避免串台（见下方）
    // 收录后优先用 collected；勿被残留 freeform_sample 向导清掉同步源
    const softRuntimeEarly =
      session.runtime && typeof session.runtime === "object" ? session.runtime : {};
    const softCollected = String(
      softRuntimeEarly.collected_blueprint_id ||
        softRuntimeEarly.freeform_saved_blueprint_id ||
        ""
    ).trim();
    const softBid = String(session.blueprint_id || "").trim();
    if (softCollected && !/^w/i.test(softCollected)) {
      selectedBlueprint.value = softCollected;
      pickedBlueprintId.value = softCollected;
    } else if (!softBid || /^w/i.test(softBid)) {
      // 空 / w*：清掉上一份库蓝本残留（未收录样本）
      selectedBlueprint.value = "";
      pickedBlueprintId.value = "";
    } else if (!isFreeformSampleWizardMode(currentFreeformSampleWizard(session))) {
      selectedBlueprint.value = softBid;
      pickedBlueprintId.value = softBid;
    }
    const rawRuntime =
      session.runtime && typeof session.runtime === "object" ? { ...session.runtime } : {};
    const rawWf =
      rawRuntime.workflows && typeof rawRuntime.workflows === "object"
        ? { ...rawRuntime.workflows }
        : {};
    if (rawWf.blueprint_wizard && typeof rawWf.blueprint_wizard === "object") {
      rawWf.blueprint_wizard = stripEphemeralWizardTriggers({ ...rawWf.blueprint_wizard });
    }
    rawRuntime.workflows = rawWf;
    const activeBefore = normalizeDocumentRegistryKey(
      String(docWindowActiveId.value || "").trim()
    );
    const serverFocus = normalizeDocumentRegistryKey(
      String(rawRuntime.focus_document_key || "").trim()
    );
    if (serverFocus && rawRuntime.focus_document_key !== serverFocus) {
      rawRuntime.focus_document_key = serverFocus;
    }
    // 勿传 task_id：applyDoneMeta 会覆盖；下面按服务端 focus / 小窗对齐
    applyDoneMeta({
      session_id: session.id,
      phase: session.phase,
      runtime: rawRuntime,
      blueprint_wizard: stripEphemeralWizardTriggers({
        ...(session.freeform?.blueprint_wizard || {}),
      }),
      blueprint_ids: session.runtime?.bindings?.blueprint_ids,
      render_template_ids: session.runtime?.bindings?.render_template_ids,
      word_style_ids: session.runtime?.bindings?.word_style_ids,
    });
    const docsNow = conversationDocuments.value || [];
    const sameWritingTask = (a, b) => {
      if (!a || !b) return false;
      if (a.id === b.id) return true;
      const aw = String(a.writingTaskId || a.taskId || "").trim();
      const bw = String(b.writingTaskId || b.taskId || "").trim();
      return Boolean(aw && bw && aw === bw);
    };
    const beforeDoc = activeBefore ? docsNow.find((d) => d.id === activeBefore) : null;
    const resolveFocusDoc = (focusKey) => {
      if (!focusKey) return null;
      return (
        docsNow.find((d) => d.id === focusKey) ||
        (focusKey.startsWith("task:")
          ? docsNow.find(
              (d) =>
                String(d.taskId || "") === focusKey.slice(5) ||
                String(d.writingTaskId || "") === focusKey.slice(5)
            )
          : null) ||
        null
      );
    };
    let focusDoc = resolveFocusDoc(serverFocus);
    const pinned = normalizeDocumentRegistryKey(streamFocusPinnedKey);
    const activeNorm = normalizeDocumentRegistryKey(
      String(docWindowActiveId.value || activeBefore || "").trim()
    );
    const blockBpRevert =
      Boolean(serverFocus && serverFocus.startsWith("bp:")) &&
      Boolean(
        pinned?.startsWith("task:") ||
          (activeNorm.startsWith("task:") &&
            !String(beforeDoc?.blueprintId || "").trim())
      );
    // 本轮流式已钉新焦点：禁止用旧 activeBefore 写回 runtime，也勿被脏/空 serverFocus 打回
    if (pinned) {
      syncDocWindowToFocusKey(pinned);
      streamFocusPinnedKey = "";
    } else if (blockBpRevert) {
      /* 无蓝本新文档 onboarding：忽略滞后 serverFocus 打回旧蓝本 */
    } else if (serverFocus && focusDoc && !sameWritingTask(beforeDoc, focusDoc)) {
      syncDocWindowToFocusKey(serverFocus);
    } else if (serverFocus && !focusDoc && serverFocus !== activeBefore) {
      syncDocWindowToFocusKey(serverFocus);
    } else if (!serverFocus && activeBefore && docsNow.some((d) => d.id === activeBefore)) {
      // 仅当服务端焦点为空时回落小窗当前项；绝不把 activeBefore 盖写非空 serverFocus
      docWindowActiveId.value = activeBefore;
    }
    const activeDocNow =
      docsNow.find((d) => d.id === String(docWindowActiveId.value || "").trim()) ||
      focusDoc ||
      beforeDoc ||
      null;
    const alignCandidates = [
      activeDocNow?.writingTaskId,
      activeDocNow?.taskId,
      serverFocus.startsWith("task:") ? serverFocus.slice(5) : "",
      session.task_id,
    ].map((x) => String(x || "").trim());
    const alignTid =
      alignCandidates.find((id) => /^w/i.test(id)) ||
      alignCandidates.find(Boolean) ||
      "";
    if (alignTid) taskId.value = alignTid;
    else if (session.task_id) taskId.value = session.task_id;
    const liveBeforeSoft = liveBlueprintWizard.value
      ? { ...liveBlueprintWizard.value }
      : null;
    // 仅「尚未完成」的重新提取/提取中：软同步时不要剥掉标记。
    // 禁止在完成后强行写回 pending+trigger+空章节，否则会循环打 extract-outline。
    const pendingReExtractBefore =
      String(liveBeforeSoft?.pending_wizard_command || "").trim() === "re_extract_outline";
    const extractActiveBefore = Boolean(liveBeforeSoft?.outline_extract_active);
    const preserveInflightExtract = Boolean(
      liveBeforeSoft && (pendingReExtractBefore || extractActiveBefore)
    );
    restoreUiStateFromSession(session, { preferLocalAdvance: true });
    if (liveBlueprintWizard.value && !pageStudyInflight) {
      // 仅本轮已武装的分析 trigger 才保留；残留 trigger + 未武装不得靠 softSync 保活
      const keepStudy =
        pageStudyKickArmed &&
        Array.isArray(liveBlueprintWizard.value.trigger_chapter_study) &&
        liveBlueprintWizard.value.trigger_chapter_study.length > 0;
      const liveNow = liveBlueprintWizard.value;
      const alreadyConfirmed =
        wizardStepIndex(liveNow?.step) >= wizardStepIndex("outline_confirm") &&
        (Number(liveNow?.chapter_count || 0) > 0 ||
          (Array.isArray(liveNow?.chapters) && liveNow.chapters.length > 0)) &&
        String(liveNow?.pending_wizard_command || "").trim() !== "re_extract_outline" &&
        !liveNow?.outline_extract_active;
      if (!keepStudy) {
        if (preserveInflightExtract && !alreadyConfirmed) {
          // 保留进行中标记，但绝不重新点燃已清空的 trigger / 已完成的确认步
          const stripped = stripEphemeralWizardTriggers({ ...liveNow });
          liveBlueprintWizard.value = mergeWizardPayload(stripped, {
            step: "outline",
            ...(pendingReExtractBefore
              ? { pending_wizard_command: "re_extract_outline" }
              : { pending_wizard_command: "" }),
            ...(extractActiveBefore ? { outline_extract_active: true } : {}),
            // 提取流已开始后 trigger 应为 false；仅「待开跑」时保留
            trigger_outline_extract:
              pendingReExtractBefore &&
              liveBeforeSoft.trigger_outline_extract === true &&
              !extractActiveBefore,
          });
        } else {
          liveBlueprintWizard.value = stripEphemeralWizardTriggers(liveBlueprintWizard.value);
        }
      }
    }
    patchLatestBlueprintWizardCardFromServer(session);
    unlockLatestBlueprintWizardCard();
  } catch {
    /* 保留本地消息 */
  }
}

async function reloadSessionFromServer() {
  const sid = sessionId.value;
  if (!sid) return;
  try {
    const session = await request(`/api/writing/chat/sessions/${encodeURIComponent(sid)}`);
    await applySession(session);
  } catch {
    /* 保留本地消息 */
  }
}

async function syncBlueprintToLibrary(explicitTaskId = "") {
  const tid = String(explicitTaskId || activeWritingCopyTaskId() || taskId.value || "").trim();
  const sourceId = sourceBlueprintIdForSync.value;
  if (!tid || !sourceId || syncBlueprintBusy.value || sending.value) return;
  if (!/^w/i.test(tid)) {
    ElMessage.warning("请先打开文档副本，再同步到蓝本库");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "将当前文档副本的目录/要点/要素同步到源蓝本库条目。是否继续？",
      "同步到蓝本库",
      {
        confirmButtonText: "同步",
        cancelButtonText: "取消",
        type: "info",
      }
    );
  } catch {
    return;
  }
  syncBlueprintBusy.value = true;
  try {
    await streamChat({
      message: "",
      action: "sync_document_to_blueprint_library",
      action_payload: { task_id: tid },
      client_sync: buildUiStatePayload(),
    });
    ElMessage.success("已同步到蓝本库");
  } catch (error) {
    if (!isAbortError(error)) {
      ElMessage.error(error.message || "同步到蓝本库失败");
    }
  } finally {
    syncBlueprintBusy.value = false;
  }
}

let lastWizardActionFingerprint = "";
let lastWizardActionAt = 0;

async function createMaterialsPackFromCard(card) {
  const taskId = String(card?.payload?.task_id || "").trim();
  const blueprintId = String(card?.payload?.blueprint_id || "").trim();
  let name = "";
  try {
    const { value } = await ElMessageBox.prompt("给资料包起个名字（新建默认待入库）", "新建资料包", {
      confirmButtonText: taskId ? "创建并绑定" : "创建",
      cancelButtonText: "取消",
      inputPlaceholder: "如：某某项目资料",
      inputValue: "",
    });
    name = String(value || "").trim();
  } catch {
    return;
  }
  await sendAction("materials_pack_create", {
    task_id: taskId,
    blueprint_id: blueprintId,
    name: name || "未命名资料包",
  });
}

async function sendAction(action, action_payload = {}, { force = false } = {}) {
  action_payload = enrichActionPayload(action, action_payload);
  if (sending.value && !force) return;
  // 短窗去重：同一向导动作（尤其创建蓝本）300ms～8s 内不重复发，避免回车+点击打出两条
  if (action === "blueprint_wizard") {
    const step = String(action_payload?.step || "").trim();
    const cmd = String(action_payload?.wizard_command || "").trim();
    const fp = `${step}:${cmd}:${String(action_payload?.title || "").trim()}:${String(action_payload?.blueprint_id || "").trim()}`;
    const now = Date.now();
    const windowMs = step === "meta" ? 8000 : 1200;
    if (fp && fp === lastWizardActionFingerprint && now - lastWizardActionAt < windowMs) {
      return;
    }
    lastWizardActionFingerprint = fp;
    lastWizardActionAt = now;
  }
  sending.value = true;
  const isWriteAction =
    action === "start_writing" ||
    action === "write_analyzed_only" ||
    action === "retry_chapter" ||
    action === "save_and_retry";
  const isPointsOrConfirmAction =
    action === "generate_freeform_points" ||
    (action === "submit_outline" && Boolean(action_payload?.confirm));
  if (isWriteAction) {
    setDocWindowWriteBusy(true, "正在写作…");
  } else if (isPointsOrConfirmAction) {
    beginPointsDraftLock(
      action === "generate_freeform_points" ? "正在起草写作要点…" : "正在确认大纲并生成要点…"
    );
  }
  const actionDocId = currentFocusDocumentId();
  const instruction = actionDisplayLabel(action, action_payload);
  messages.value.push({
    role: "user",
    text: instruction,
    cards: [],
    steps: [],
    document_key: actionDocId,
    document_id: actionDocId,
  });
  scheduleScrollChatToBottom(true);
  try {
    await streamChat({
      message: instruction,
      action,
      action_payload,
      client_sync: buildUiStatePayload(),
    });
  } catch (error) {
    if (!isAbortError(error)) {
      const raw = String(error?.message || "");
      const clientBug = isClientSideStreamBug(error);
      const assistantMsg = lastAssistantMessageInChat();
      const keptServerReply =
        assistantMsg &&
        String(assistantMsg.text || "").trim() &&
        String(assistantMsg.text) !== raw;
      if (clientBug) {
        // 服务端中文回复留在流式气泡里；空指针不写进对话，也不当成「新建失败」
        console.error(error);
        if (assistantMsg && !keptServerReply && !String(assistantMsg.text || "").trim()) {
          assistantMsg.text = "（无回复）";
        }
      } else if (action === "materials_pack_create") {
        ElMessage.error("新建资料包失败");
      } else {
        messages.value.push({
          role: "assistant",
          text: raw || "操作失败",
          cards: [],
          steps: [],
          document_key: actionDocId,
          document_id: actionDocId,
        });
      }
    }
  } finally {
    sending.value = false;
    void kickPageLevelStudyIfNeeded();
    if (isWriteAction && !writingStreamActive) {
      setDocWindowWriteBusy(false);
    } else if (isPointsOrConfirmAction && !writingStreamActive) {
      const last = lastAssistantMessageInChat();
      releasePointsDraftLockIfComplete(last, sessionRuntime.value, { notifyIncomplete: true });
    }
    if (action === "blueprint_wizard") {
      await softSyncSessionAfterWizardAction();
    } else if (
      action === "materials_pack_create" ||
      action === "materials_pack_use" ||
      action === "materials_pack_skip"
    ) {
      await softSyncSessionAfterWizardAction();
      bumpDocOutlineReload({ force: true });
    } else if (action === "template_wizard") {
      await reloadSessionFromServer();
    } else if (action === "configure_chapter") {
      driveDocWindow("config_saved", {
        taskId: action_payload?.task_id || action_payload?.taskId,
        chapterKey: action_payload?.chapter_key || action_payload?.key,
        tab: "config",
        elementIds: Array.isArray(action_payload?.element_ids)
          ? action_payload.element_ids
          : null,
        elementNodes: Array.isArray(action_payload?.elements) ? action_payload.elements : null,
      });
    } else if (action === "add_chapter_element") {
      driveDocWindow("elements_associated", {
        taskId: action_payload?.task_id || action_payload?.taskId,
        chapterKey: action_payload?.chapter_key || action_payload?.key,
        tab: "config",
        elementIds: Array.isArray(action_payload?.element_ids)
          ? action_payload.element_ids
          : null,
        elementNodes: Array.isArray(action_payload?.elements) ? action_payload.elements : null,
      });
    } else if (
      (action === "start_writing" || action === "write_analyzed_only") &&
      writingStreamActive
    ) {
      // 写完必须停在正文：禁止 finally 再 drive write_running→config 冲掉 chapter_done
      const progress = latestProgressCards().find((c) => c?.type === "progress");
      const tid =
        action_payload?.task_id ||
        progress?.payload?.task_id ||
        action_payload?.blueprint_id ||
        "";
      if (tid) {
        const chapters = progress?.payload?.chapters || [];
        const preferKey = String(
          action_payload?.focus_chapter_key ||
            action_payload?.chapter_key ||
            (action_payload?.chapter_keys || [])[0] ||
            progress?.payload?.focus_chapter_key ||
            ""
        ).trim();
        const doneStatuses = new Set(["awaiting_confirm", "confirmed", "needs_human"]);
        const hit =
          chapters.find(
            (c) => String(c?.key || "") === preferKey && doneStatuses.has(String(c?.status || ""))
          ) ||
          chapters.find((c) => doneStatuses.has(String(c?.status || ""))) ||
          chapters.find((c) => String(c?.key || "") === preferKey) ||
          null;
        const key = String(hit?.key || preferKey || "").trim();
        if (key) {
          bumpDraftPreviewReload();
          driveDocWindow("write_chapter_done", {
            taskId: tid,
            chapterKey: key,
            mainView: "preview",
            tab: "content",
            draftHtml: liveWriteHtmlByChapter.get(`${tid}::${key}`) || "",
          });
        }
      }
    }
    await loadHistory();
  }
}

/** 向导过渡步必须逐条落库；sending 中排队，卡片过期则改派到最新卡 */
const WIZARD_PERSIST_STEPS = new Set([
  "meta",
  "upload",
  "upload_done",
  "outline",
  "outline_done",
  "outline_confirm",
  "study",
  "study_done",
  "done",
]);
const WIZARD_ROLLBACK_COMMANDS = new Set([
  "re_upload_sample",
  "re_extract_outline",
  "go_outline_confirm",
]);
const pendingWizardActions = [];
let flushingWizardActions = false;
/** 进行中的向导落库去重键，避免「创建蓝本」连点打出两条对话 */
let inflightWizardDedupeKey = "";

function wizardMustPersist(payload) {
  const cmd = String(payload?.wizard_command || "").trim();
  if (cmd) return true;
  const step = String(payload?.step || "").trim();
  return WIZARD_PERSIST_STEPS.has(step);
}

function wizardActionDedupeKey(payload) {
  const step = String(payload?.step || "").trim();
  const title = String(payload?.title || "").trim();
  const bid = String(payload?.blueprint_id || "").trim();
  const cmd = String(payload?.wizard_command || "").trim();
  if (cmd) return `cmd:${cmd}:${bid || step}`;
  if (step === "meta") return `meta:${title}`;
  return `${step}:${bid}`;
}

async function flushWizardActionQueue() {
  // 已有 flush 在跑时，等待其结束再视情况续跑，避免 await persist 空返回导致调用方误以为已落库
  if (flushingWizardActions) {
    while (flushingWizardActions) {
      await new Promise((r) => setTimeout(r, 40));
    }
    if (pendingWizardActions.length) {
      await flushWizardActionQueue();
    }
    return;
  }
  flushingWizardActions = true;
  try {
    while (pendingWizardActions.length) {
      let waited = 0;
      while (sending.value) {
        await new Promise((r) => setTimeout(r, 40));
        waited += 40;
        // 防止 sending 异常卡住整条向导队列
        if (waited >= 120000) {
          sending.value = false;
          break;
        }
      }
      const item = pendingWizardActions.shift();
      if (!item) continue;
      let { payload, msgIndex, cardIndex } = item;
      const step = String(payload?.step || "").trim();
      // 创建已成功：丢掉排队里重复的 meta，避免第二条「创建蓝本」
      if (step === "meta" && String(liveBlueprintWizard.value?.blueprint_id || "").trim()) {
        continue;
      }
      if (isBlueprintWizardStale(msgIndex, cardIndex)) {
        if (!wizardMustPersist(payload)) continue;
        const latest = findLatestBlueprintWizard();
        if (!latest) continue;
        msgIndex = latest.msgIndex;
        cardIndex = latest.cardIndex;
      }
      const dedupeKey = wizardActionDedupeKey(payload);
      inflightWizardDedupeKey = dedupeKey;
      try {
        await sendAction(
          "blueprint_wizard",
          {
            ...payload,
            card_key: cardKey(msgIndex, cardIndex),
          },
          { force: true }
        );
      } finally {
        if (inflightWizardDedupeKey === dedupeKey) inflightWizardDedupeKey = "";
      }
    }
  } finally {
    flushingWizardActions = false;
    if (pendingWizardActions.length) {
      await flushWizardActionQueue();
    }
  }
}

/** 点选蓝本：一键选用 */
async function pickBlueprint(bp, msgIndex, cardIndex) {
  if (!bp?.id || sending.value || isCardUsed(msgIndex, cardIndex)) return;
  selectedBlueprint.value = bp.id;
  pickedBlueprintId.value = bp.id;
  // 先断开本地写作任务指针，等 done 再按服务端对齐，避免短暂串到上一蓝本 clone
  if (taskId.value && taskId.value !== bp.id) {
    taskId.value = "";
  }
  markBlueprintChoiceCardsUsed(msgIndex);
  await sendAction("select_blueprint", {
    blueprint_id: bp.id,
    title: bp.title || bp.name || "",
  });
}

function markBlueprintChoiceCardsUsed(msgIndex) {
  const cards = messages.value[msgIndex]?.cards || [];
  cards.forEach((card, ci) => {
    if (card?.type === "blueprint_list" || card?.type === "skip_blueprint") {
      markCardUsed(msgIndex, ci);
    }
  });
}

async function pickSkip(msgIndex, cardIndex) {
  if (sending.value || isCardUsed(msgIndex, cardIndex)) return;
  markBlueprintChoiceCardsUsed(msgIndex);
  await sendAction("skip_blueprint");
}

async function resolveIntentClarify(card, option, msgIndex, cardIndex) {
  if (sending.value || isCardUsed(msgIndex, cardIndex)) return;
  const optionId = String(option?.id || "").trim();
  const optionLabel = String(option?.label || "").trim();
  if (!optionId) return;
  markCardUsed(msgIndex, cardIndex);
  await sendAction("resolve_intent_clarify", {
    option_id: optionId,
    option_label: optionLabel,
    clarify_id: card?.payload?.clarify_id || "",
  });
}

async function pickCreateBlueprint(msgIndex, cardIndex) {
  if (sending.value || isCardUsed(msgIndex, cardIndex)) return;
  markBlueprintChoiceCardsUsed(msgIndex);
  await sendAction("begin_blueprint_wizard", {
    force_new: true,
    confirmed_create: true,
  });
}

async function pickResourceChoice(card, accept, msgIndex, cardIndex) {
  if (sending.value || isCardUsed(msgIndex, cardIndex)) return;
  markCardUsed(msgIndex, cardIndex);
  await sendAction("resource_choice", {
    resource: card?.payload?.resource || "",
    accept: !!accept,
  });
}

function navActionLabel(card) {
  const target = String(card?.payload?.target || "").trim();
  if (target === "elements") return "查看要素";
  if (target === "blueprints" || target === "blueprint") return "查看蓝本库";
  if (target === "materials" || target === "knowledge") return "查看资料库";
  return card?.title || "打开";
}

function navBrowseKindLabel(row) {
  const kind = String(row?.kind || "").trim();
  if (kind === "folder") return "目录";
  if (kind === "blueprint") return "蓝本";
  return elementKindLabel(kind);
}

function navBrowseSections(card) {
  const sections = card?.payload?.sections;
  if (!Array.isArray(sections) || !sections.length) return [];
  // 仅要素浏览用分组；资料库保持扁平列表
  if (String(card?.payload?.target || "").trim() !== "elements") return [];
  return sections
    .filter((sec) => sec && typeof sec === "object")
    .map((sec) => ({
      key: String(sec.key || "").trim(),
      title: String(sec.title || "").trim() || "要素",
      items: Array.isArray(sec.items) ? sec.items : [],
    }));
}

function goNavAction(card) {
  const target = String(card?.payload?.target || "").trim();
  if (target === "blueprints" || target === "blueprint") {
    navigateTo?.("blueprint", { from: "chat", session_id: sessionId.value });
    return;
  }
  if (target === "elements" || target === "materials" || target === "knowledge") {
    navigateTo?.(target === "knowledge" ? "materials" : target);
    return;
  }
  const action = String(card?.payload?.action || "").trim();
  if (action === "open_blueprint_library") {
    navigateTo?.("blueprint", { from: "chat", session_id: sessionId.value });
  } else if (action === "open_element_library") navigateTo?.("elements");
  else if (action === "open_knowledge" || action === "open_materials_library") {
    navigateTo?.("materials");
  }
}

function chapterStubFromAssociateCard(card) {
  const key = String(card?.payload?.chapter_key || "").trim();
  if (!key) return null;
  const label = String(card?.payload?.chapter_label || "").trim();
  const parts = label.split(/\s+/).filter(Boolean);
  const sequence = parts.length > 1 && /^\d+(?:\.\d+)*$/.test(parts[0]) ? parts[0] : "";
  const title = sequence ? parts.slice(1).join(" ") : label || key;
  return { key, sequence, title };
}

function onAssociateElementsCard(card, msgIndex, cardIndex) {
  // auto_open 后仍允许「再次打开」；首次点击才记 used
  if (msgIndex >= 0 && cardIndex >= 0 && !isCardUsed(msgIndex, cardIndex)) {
    markCardUsed(msgIndex, cardIndex);
  }
  const tid = String(card?.payload?.task_id || taskId.value || "").trim();
  const ch = chapterStubFromAssociateCard(card);
  if (!tid || !ch) {
    ElMessage.warning("缺少章节信息，请先在文档窗点选章节");
    return;
  }
  ensureChapterEdit(tid, ch);
  const pl = card?.payload || {};
  const isPoint =
    String(pl.associateScope || "") === "point" ||
    pl.pointItemId ||
    pl.pointIndex != null;
  if (isPoint) {
    associatePointContext.value = {
      id: String(pl.pointItemId || "").trim(),
      index: Number(pl.pointIndex),
      slot: String(pl.pointSlot || "").trim(),
      pointItems: Array.isArray(pl.pointItems) ? pl.pointItems.map((row) => ({ ...row })) : [],
    };
  }
  openChapterElementAssociate(tid, ch);
  driveDocWindow("elements_associated", {
    taskId: tid,
    chapterKey: ch.key,
    tab: "config",
  });
}

/** 关联要素卡带 auto_open 时直接弹面板，无需再点按钮 */
function maybeAutoOpenAssociateElements(cards = [], msgIndex = -1) {
  if (sessionHydrating.value || suppressElementConfigureAutoOpen.value) return;
  const list = Array.isArray(cards) ? cards : [];
  for (let ci = 0; ci < list.length; ci++) {
    const card = list[ci];
    if (card?.type !== "associate_elements") continue;
    if (!card?.payload?.auto_open) continue;
    if (msgIndex >= 0 && isCardUsed(msgIndex, ci)) continue;
    onAssociateElementsCard(card, msgIndex, ci);
    break;
  }
}

function onDocWindowHintCard(card, msgIndex = -1, cardIndex = -1) {
  if (msgIndex >= 0 && cardIndex >= 0 && !isCardUsed(msgIndex, cardIndex)) {
    markCardUsed(msgIndex, cardIndex);
  }
  const tid = String(
    card?.payload?.task_id || card?.payload?.blueprint_id || taskId.value || selectedBlueprint.value || ""
  ).trim();
  const action = String(card?.payload?.action || "open_doc_window").trim();
  const chapterKey = String(card?.payload?.chapter_key || "").trim();
  const tabHint = String(card?.payload?.tab || "").trim();
  const tab =
    tabHint ||
    (action === "analyze" || action === "select_write" ? "topology" : chapterKey ? "config" : "chapter");
  driveDocWindow(action || "open_doc_window", {
    taskId: tid,
    blueprintId: String(card?.payload?.blueprint_id || tid).trim(),
    tab,
    chapterKey,
  });
}

function maybeAutoOpenDocWindowHint(cards = [], msgIndex = -1) {
  if (sessionHydrating.value || suppressElementConfigureAutoOpen.value) return;
  const list = Array.isArray(cards) ? cards : [];
  for (let ci = 0; ci < list.length; ci++) {
    const card = list[ci];
    if (card?.type !== "doc_window_hint") continue;
    if (!card?.payload?.auto_open) continue;
    if (msgIndex >= 0 && isCardUsed(msgIndex, ci)) continue;
    onDocWindowHintCard(card, msgIndex, ci);
    break;
  }
}

async function onBlueprintWizard(payload, msgIndex, cardIndex) {
  const step = String(payload?.step || "").trim();
  const cmd = String(payload?.wizard_command || "").trim();
  const mustPersist = wizardMustPersist(payload);
  // 历史卡上的过渡步（上传完成等）仍须落库；仅拦截非持久化操作
  if (isBlueprintWizardStale(msgIndex, cardIndex) && !mustPersist) return;
  // 已有蓝本时禁止再排队「创建」
  if (step === "meta" && !cmd && String(liveBlueprintWizard.value?.blueprint_id || "").trim()) {
    return;
  }
  // 主动回退：立刻改 live，避免等待落库期间仍钉在旧步
  if (WIZARD_ROLLBACK_COMMANDS.has(cmd)) {
    const bid = String(payload?.blueprint_id || liveBlueprintWizard.value?.blueprint_id || "").trim();
    const patch = {
      blueprint_id: bid,
      working_task_id:
        payload?.working_task_id || liveBlueprintWizard.value?.working_task_id || undefined,
      pending_wizard_command: cmd,
      outline_confirmed: cmd === "go_outline_confirm" ? undefined : false,
    };
    if (cmd === "re_upload_sample") {
      patch.step = "upload";
      patch.trigger_outline_extract = false;
    } else if (cmd === "re_extract_outline") {
      patch.step = "outline";
      patch.trigger_outline_extract = true;
      // 清空旧目录预览，避免前端把上次结果当成「已提取」
      patch.chapters = [];
      patch.chapter_count = 0;
      patch.outline_confirmed = false;
    } else if (cmd === "go_outline_confirm") {
      patch.step = "outline_confirm";
      patch.trigger_outline_extract = false;
      patch.pending_wizard_command = "";
    }
    liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, patch);
  }
  const dedupeKey = wizardActionDedupeKey(payload);
  if (mustPersist && inflightWizardDedupeKey === dedupeKey) return;
  // 同键只保留一条待发（后写覆盖前写），防止回车+点击排进两条 meta
  if (mustPersist) {
    for (let i = pendingWizardActions.length - 1; i >= 0; i -= 1) {
      if (wizardActionDedupeKey(pendingWizardActions[i].payload) === dedupeKey) {
        pendingWizardActions.splice(i, 1);
      }
    }
  }
  pendingWizardActions.push({ payload, msgIndex, cardIndex });
  await flushWizardActionQueue();
}

async function onTemplateWizard(payload, msgIndex, cardIndex) {
  if (sending.value) return;
  if (payload?.resource && payload?.step) {
    liveTemplateWizard.value = { ...(liveTemplateWizard.value || {}), ...payload };
    schedulePersistUiState();
  }
  if (payload?.step === "done") markCardUsed(msgIndex, cardIndex);
  await sendAction("template_wizard", { ...payload });
}

async function listBlueprints() {
  if (!robotId.value || sending.value) return;
  await sendAction("list_blueprints", {});
}

/** 会话内浏览蓝本库（管理页），不是选型 list_blueprints */
async function openBlueprintLibraryFromComposer() {
  if (sending.value) return;
  await sendAction("open_blueprint_library", {});
}

function goConfigureBlueprint(card) {
  const q = card?.payload?.query || {};
  const session = q.session_id || card?.payload?.session_id || sessionId.value;
  const blueprintId = q.blueprint_id || card?.payload?.blueprint_id || "";
  if (typeof navigateTo === "function") {
    navigateTo("blueprint", { from: "chat", session_id: session, blueprint_id: blueprintId });
  } else {
    const params = new URLSearchParams({ from: "chat", session_id: session });
    if (blueprintId) params.set("blueprint_id", blueprintId);
    window.location.search = params.toString();
  }
}

async function confirmWriteAnalyzedOnly(card, msgIndex, cardIndex) {
  if (sending.value || isCardUsed(msgIndex, cardIndex)) return;
  markCardUsed(msgIndex, cardIndex);
  await sendAction("write_analyzed_only", card?.payload || {});
}

function setPickerCompRef(msgIndex, cardIndex, el) {
  const key = cardKey(msgIndex, cardIndex);
  if (!el) {
    pickerCompRefs.delete(key);
    return;
  }
  if (pickerCompRefs.get(key) === el) return;
  pickerCompRefs.set(key, el);
}

function chapterTabFor(taskId, ch) {
  const key = chapterEditKey(taskId, ch?.key);
  if (chapterTabs.value[key]) return chapterTabs.value[key];
  if (ch?.structure_only || ch?.config?.structure_only) return "config";
  return ch?.has_draft ? "text" : "config";
}

async function confirmChapterSelection(card, msgIndex, cardIndex) {
  if (sending.value || isCardUsed(msgIndex, cardIndex)) return;
  const comp = pickerCompRefs.get(cardKey(msgIndex, cardIndex));
  const selected = comp?.getSelectedKeys?.() || [];
  if (!selected.length) {
    ElMessage.warning("请至少勾选一章可写章节");
    return;
  }
  markCardUsed(msgIndex, cardIndex);
  await sendAction("select_write_chapters", {
    blueprint_id: card?.payload?.blueprint_id || "",
    chapter_keys: selected,
  });
}

async function loadRenderTemplatesForRobot(robotId) {
  try {
    if (robotId) {
      renderTemplates.value =
        (await request(`/api/writing/robots/${encodeURIComponent(robotId)}/render-templates`)) || [];
    } else {
      renderTemplates.value = (await request("/api/writing/render-templates")) || [];
    }
  } catch {
    renderTemplates.value = [];
  }
  syncDefaultExportTemplate();
}

async function loadWordStyles() {
  try {
    const rid = (robotId.value || "").trim();
    if (rid) {
      wordStyles.value =
        (await request(`/api/writing/robots/${encodeURIComponent(rid)}/word-styles`)) || [];
    } else {
      wordStyles.value = (await request("/api/writing/word-styles")) || [];
    }
  } catch {
    wordStyles.value = [];
  }
  syncDefaultWordStyle();
}

async function doExportWord(id) {
  await downloadRenderedDocument("docx", id);
}

async function doExportPdf(id) {
  await downloadRenderedDocument("pdf", id);
}

async function downloadRenderedDocument(format, id) {
  const tid = id || taskId.value;
  if (!tid) return;
  const isPdf = format === "pdf";
  const busyRef = isPdf ? exportPdfBusy : exportBusy;
  if (busyRef.value) return;
  busyRef.value = true;
  try {
    const params = new URLSearchParams({
      mode: "plain",
      confirmed_only: exportConfirmedOnly.value ? "true" : "false",
    });
    if (exportTemplateId.value) params.set("template_id", exportTemplateId.value);
    if (exportWordStyleId.value) params.set("style_id", exportWordStyleId.value);
    const path = isPdf
      ? `/api/writing/tasks/${encodeURIComponent(tid)}/render/download/pdf`
      : `/api/writing/tasks/${encodeURIComponent(tid)}/render/download`;
    const response = await fetch(`${path}?${params.toString()}`, { headers: authHeaders() });
    if (!response.ok) {
      let detail = "下载失败";
      try {
        const data = await response.json();
        detail = data.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${tid}.${isPdf ? "pdf" : "docx"}`;
    a.click();
    URL.revokeObjectURL(url);
    if (isPdf) {
      ElMessage.success(exportTemplateId.value ? "已按模版渲染并下载 PDF" : "已生成 PDF");
    } else {
      ElMessage.success(exportTemplateId.value ? "已按模版渲染并下载 Word" : "已生成 Word（无模版 / LLM）");
    }
  } catch (error) {
    ElMessage.error(error.message || "导出失败");
  } finally {
    busyRef.value = false;
  }
}

async function resumeFromHandoff() {
  const sid = props.resumeSessionId || sessionId.value;
  if (!sid) return;
  persistSession(sid);
  sending.value = true;
  globalLoading.value = true;
  globalLoadingText.value = "正在加载对话…";
  try {
    if (!messages.value.length) {
      await restoreCurrentSession();
    }
    globalLoading.value = false;
    await streamChat({
      action: "resume",
      action_payload: { blueprint_id: props.resumeBlueprintId || "" },
    });
    await loadHistory();
  } catch (error) {
    if (!isAbortError(error)) {
      ElMessage.error(error.message || "恢复会话失败");
    }
  } finally {
    sending.value = false;
    globalLoading.value = false;
  }
}

watch(
  () => props.resumeToken,
  (token) => {
    if (token) resumeFromHandoff();
  }
);

function onAuthChanged() {
  persistSession("");
  messages.value = [];
  phase.value = "intent";
  taskId.value = "";
  robotId.value = "";
  selectedBlueprint.value = "";
  pickedBlueprintId.value = "";
  usedCardKeys.value = new Set();
  blueprintWizardLog.value = [];
  clearEditor();
  seedOpeningGreeting();
  loadHistory();
}

watch(robotId, () => {
  loadHistory();
});

watch(
  messages,
  () => {
    for (const msg of messages.value || []) {
      for (const card of msg.cards || []) {
        if (card?.type === "progress") syncTaskElementsFromProgress(card.payload);
      }
    }
  },
  { deep: true },
);

/** 学习中：锁定章节操作（无遮罩）；触发到来时若卡未消费则由页面兜底开跑 */
let pendingStudySource = { method: "", text: "" };
const studySourcePasteDrafts = ref({});
let pageStudyToken = "";
let pageStudyInflight = false;
let pageStudyKickArmed = false;
let pageStudyKickTimer = null;
/** 会话世代：换会话/新建时递增，阻断旧分析流回写消息与文档窗 */
let sessionEpoch = 0;
let pageStudyAbortController = null;
let pageStudyOwnerEpoch = 0;
let pageStudyOwnerSessionId = "";
let pageStudyOwnerBlueprintId = "";

function bumpSessionEpoch() {
  sessionEpoch += 1;
  return sessionEpoch;
}

function currentSessionEpoch() {
  return sessionEpoch;
}

function isCurrentSessionContext(epoch, sid = "") {
  if (epoch !== sessionEpoch) return false;
  const want = String(sid || "").trim();
  if (!want) return true;
  return want === String(sessionId.value || "").trim();
}

/** 换会话/新建：掐断在途分析与文档窗，禁止旧流串到新会话 */
function hardResetCrossSessionUi({ closeDocWindow = true } = {}) {
  pageStudyKickArmed = false;
  bumpSessionEpoch();
  if (pageStudyKickTimer) {
    clearTimeout(pageStudyKickTimer);
    pageStudyKickTimer = null;
  }
  clearTimeout(uiStatePersistTimer);
  uiStatePersistTimer = 0;
  persistUiStateQueued = false;
  lastUiStateFingerprint = "";
  lastUiStateAt = 0;
  try {
    streamAbortController?.abort();
  } catch {
    /* ignore */
  }
  streamAbortController = null;
  sending.value = false;
  try {
    pageStudyAbortController?.abort();
  } catch {
    /* ignore */
  }
  pageStudyAbortController = null;
  pageStudyInflight = false;
  pageStudyToken = "";
  pageStudyOwnerEpoch = 0;
  pageStudyOwnerSessionId = "";
  pageStudyOwnerBlueprintId = "";
  pageStudyOwnerTaskId = "";
  releaseStudyOpsLock({ notify: false });
  liveBlueprintWizard.value = null;
  liveWizardsByDoc.value = {};
  liveTemplateWizard.value = null;
  blueprintWizardLog.value = [];
  sessionRuntime.value = null;
  if (closeDocWindow) {
    docWindowOpen.value = false;
    docWindowActiveId.value = "";
    docWindowAutoOpenedKey.value = "";
    docWindowAutoOpenArmed.value = false;
    docWindowClosing.value = false;
    docWindowBusy.value = false;
    docWindowBusyLabel.value = "";
    docWindowOpsLocked.value = false;
    docWindowOpsLockLabel.value = "";
    docWindowLocate.value = {
      token: 0,
      taskId: "",
      chapterKey: "",
      tab: "",
      reason: "",
      elementId: "",
      associateKnowledge: false,
    };
  }
}

function schedulePageStudyKickoff(delayMs = 120, { arm = false } = {}) {
  if (arm) pageStudyKickArmed = true;
  if (pageStudyKickTimer) clearTimeout(pageStudyKickTimer);
  pageStudyKickTimer = setTimeout(() => {
    pageStudyKickTimer = null;
    kickPageLevelStudyIfNeeded();
  }, delayMs);
}

/** 会话刚恢复：若仍有残留分析 trigger，吞掉这一次 kick（不挡之后用户显式「重新分析」） */
let suppressStaleStudyKickAfterRestore = false;

async function kickPageLevelStudyIfNeeded() {
  // 恢复/切换会话水合期间禁止开跑，避免把历史 trigger 再执行一遍
  if (sessionHydrating.value) return;
  if (suppressStaleStudyKickAfterRestore) {
    suppressStaleStudyKickAfterRestore = false;
    const stale = liveBlueprintWizard.value || {};
    const staleKeys = (stale.trigger_chapter_study || stale.chapter_study_keys || [])
      .map((k) => String(k || "").trim())
      .filter(Boolean);
    const staleTrigger =
      Boolean(stale.trigger_begin_study) ||
      Boolean(stale.study_stream_active) ||
      staleKeys.length > 0;
    if (staleTrigger) {
      liveBlueprintWizard.value = stripEphemeralWizardTriggers(stale);
      pageStudyToken = "";
      void persistUiStateForced().catch(() => {});
      return;
    }
  }
  const live = liveBlueprintWizard.value || {};
  const bid = String(live.blueprint_id || "").trim();
  // 选用即 clone / 无蓝本样本：学习流打工作副本
  const studyTaskId = String(live.working_task_id || bid).trim();
  const keys = (live.trigger_chapter_study || live.chapter_study_keys || [])
    .map((k) => String(k || "").trim())
    .filter(Boolean);
  const hasTrigger = Boolean(live.trigger_begin_study) || keys.length > 0;
  if (!pageStudyInflight && !pageStudyKickArmed && hasTrigger && !sending.value) {
    liveBlueprintWizard.value = stripEphemeralWizardTriggers(live);
    if (!pointsDraftInflight.value) {
      setDocWindowStudyBusy(false);
    }
    return;
  }
  const streamFlag = Boolean(live.study_stream_active);
  const beginAll = Boolean(live.trigger_begin_study) && !keys.length;
  const token = `${studyTaskId}:${keys.join(",")}:${beginAll ? "all" : "keys"}`;
  // 无会话或文档登记对不上时，禁止兜底开跑（防止旧 live 触发）
  if (!String(sessionId.value || "").trim()) return;
  // 同一批已经跑完，但 softSync/服务端又把 trigger 写回来 → 再上锁却因 token 相同不开跑 = 死锁
  if (!pageStudyInflight && hasTrigger && pageStudyToken && token === pageStudyToken) {
    if (pageStudyKickArmed) {
      pageStudyToken = "";
    } else {
      clearStudyTriggersAndUnlock({ step: "study" });
      return;
    }
  }
  // 真正在跑 / 待开跑才上锁。裸 study_stream_active 不得在对话已完成后把文档窗锁死。
  if (pageStudyInflight || hasTrigger) {
    const method = String(live.study_method || pendingStudySource.method || "").trim();
    const busyLabel =
      method === "source"
        ? keys.length === 1
          ? "正在按原文分析本章…"
          : "正在按原文分析章节…"
        : method === "auto"
          ? "正在自动分析本章…"
          : keys.length === 1
            ? "正在分析本章…"
            : "正在分析章节…";
    setDocWindowStudyBusy(true, busyLabel);
  } else if (streamFlag) {
    liveBlueprintWizard.value = mergeWizardPayload(live, { study_stream_active: false });
    if (!pointsDraftInflight.value && !sending.value) {
      setDocWindowStudyBusy(false);
    }
  } else if (!pointsDraftInflight.value && !sending.value) {
    setDocWindowStudyBusy(false);
  }
  // 对话回合进行中不要用 leftover trigger 再开分析（会把「切换到另一份」抢回当前文档）
  if (sending.value) return;
  // 分析卡已从会话气泡剔除，必须由页面兜底开跑；sending 结束后再 kick
  if (!studyTaskId || pageStudyInflight) return;
  if (normalizeWizardStep(live.step) !== "study") {
    if (pageStudyKickArmed && hasTrigger) {
      liveBlueprintWizard.value = mergeWizardPayload(live, { step: "study" });
    } else {
      return;
    }
  }
  if (!keys.length && !beginAll) return;
  // 向导卡若已真正开跑（清了 trigger 并置 stream_active），不要抢跑
  if (streamFlag && !hasTrigger) return;
  if (token === pageStudyToken) return;
  pageStudyToken = token;
  await runPageLevelStudy(studyTaskId, keys, { beginAll });
}

watch(
  () => {
    const w = liveBlueprintWizard.value || {};
    const keys = w.trigger_chapter_study || w.chapter_study_keys || [];
    return [
      w.blueprint_id,
      w.step,
      w.study_stream_active,
      w.trigger_begin_study,
      Array.isArray(keys) ? keys.join(",") : "",
      pageStudyInflight ? "1" : "0",
    ].join("|");
  },
  () => {
    schedulePageStudyKickoff(160);
  }
);

function latestAssistantMessage() {
  const list = messages.value || [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i]?.role === "assistant") return list[i];
  }
  return null;
}

function stashPendingStudySource({ method = "", text = "" } = {}) {
  // 用户显式再分析同一章时允许重新 kick（勿被旧 pageStudyToken 当成残留 trigger）
  pageStudyToken = "";
  const nextMethod = String(method || "").trim();
  const nextText = String(text || "");
  if (!nextMethod && !nextText.trim()) return;
  pendingStudySource = {
    method: nextMethod || pendingStudySource.method || "",
    text: nextText.trim() ? nextText : pendingStudySource.text || "",
  };
}

/** 剥掉学习自驱残留并解锁文档窗，同步 per-doc 缓存 */
function clearStudyTriggersAndUnlock(extra = {}) {
  // 分析结束后必须卸武装：否则下一轮关联要素/闲聊若又见到残留 trigger 会再次开跑
  pageStudyKickArmed = false;
  const live = liveBlueprintWizard.value || {};
  const cleaned = mergeWizardPayload(live, {
    study_stream_active: false,
    trigger_begin_study: false,
    trigger_chapter_study: [],
    chapter_study_keys: [],
    ...extra,
  });
  liveBlueprintWizard.value = cleaned;
  const key = wizardDocKeyFromPayload(cleaned, currentFocusDocumentId());
  if (key) rememberLiveWizardForDoc(key, cleaned);
  setDocWindowStudyBusy(false);
}

function consumePendingStudySource(live = {}) {
  const method = String(pendingStudySource.method || live.study_method || "").trim();
  const text = String(pendingStudySource.text || "");
  pendingStudySource = { method: "", text: "" };
  return { method, text };
}

function studySourcePasteText(cardKey) {
  return htmlToPlain(studySourcePasteDrafts.value[cardKey] || "").trim();
}

function onStudySourcePasteHtml(cardKey, html) {
  studySourcePasteDrafts.value = {
    ...studySourcePasteDrafts.value,
    [cardKey]: String(html || ""),
  };
}

async function submitStudySourcePaste(card, msgIndex, cardIndex) {
  if (sending.value || isCardUsed(msgIndex, cardIndex)) return;
  const cardKey = `${msgIndex}-${cardIndex}`;
  const text = studySourcePasteText(cardKey);
  if (!text) {
    ElMessage.warning("请先粘贴本章原文");
    return;
  }
  const payload = card?.payload || {};
  markCardUsed(msgIndex, cardIndex);
  stashPendingStudySource({ method: "source", text });
  await sendAction("blueprint_wizard", {
    wizard_command: "re_study_chapters",
    step: "study",
    study_method: "source",
    study_text: text,
    chapter_keys: payload.chapter_keys || [],
    chapter_sequences: payload.chapter_sequences || [],
    study_scope: payload.study_scope || "self",
    study_message: payload.study_message || payload.chapter_label || "",
    chapter_label: payload.chapter_label || payload.study_message || "",
    sequence: (payload.chapter_sequences || [])[0] || "",
    blueprint_id: payload.blueprint_id || "",
    task_id: payload.task_id || payload.working_task_id || "",
    working_task_id: payload.working_task_id || payload.task_id || "",
    locate_chapter_key: (payload.chapter_keys || [])[0] || "",
    chapter_key: (payload.chapter_keys || [])[0] || "",
  });
}

function studyChapterLabel(event = {}) {
  const result = event.result && typeof event.result === "object" ? event.result : {};
  const seq = String(result.sequence || event.sequence || "").trim();
  const title = String(event.title || "").trim();
  const key = String(event.chapter || "").trim();
  const name = [seq, title].filter(Boolean).join(" ") || title || key || "章节";
  return name;
}

function pushStudyProgress(
  line,
  kind = "process",
  { epoch = null, sid = "", blueprintId = "", taskId = "", invokeHook = true } = {}
) {
  pushStudyProgressLine(line, kind, { epoch, sid, blueprintId, taskId, invokeHook });
}

async function runPageLevelStudy(studyTaskId, chapterKeys, { beginAll = false } = {}) {
  if (!studyTaskId || pageStudyInflight) return;
  const ownerEpoch = currentSessionEpoch();
  const ownerSid = String(sessionId.value || "").trim();
  const ownerStudyTid = String(studyTaskId || "").trim();
  // 学习 API 打工作副本；库蓝本 id 与副本分离，禁止把 w* 写回 blueprint_id
  const libraryBp = sanitizeLibraryBlueprintId(
    liveBlueprintWizard.value?.source_blueprint_id ||
      liveBlueprintWizard.value?.blueprint_id ||
      selectedBlueprint.value ||
      sessionRuntime.value?.blueprint_id ||
      "",
    isLibraryBlueprintId(ownerStudyTid) ? ownerStudyTid : ""
  );
  pageStudyInflight = true;
  pageStudyOwnerEpoch = ownerEpoch;
  pageStudyOwnerSessionId = ownerSid;
  pageStudyOwnerBlueprintId = libraryBp || ownerStudyTid;
  pageStudyOwnerTaskId = ownerStudyTid;
  pageStudyAbortController = new AbortController();
  const studySignal = pageStudyAbortController.signal;
  const stillHere = () => isCurrentSessionContext(ownerEpoch, ownerSid);
  const studyKick = consumePendingStudySource(liveBlueprintWizard.value || {});
  const studyMethod = studyKick.method || (beginAll ? "sample" : "sample");
  const studyText = studyKick.text;
  setDocWindowStudyBusy(
    true,
    studyMethod === "source"
      ? beginAll
        ? "正在按原文分析章节…"
        : "正在按原文分析本章…"
      : beginAll
        ? "正在分析全部章节…"
        : "正在分析章节…"
  );
  if (stillHere()) {
    const wizardPatch = {
      step: "study",
      study_stream_active: true,
      trigger_begin_study: false,
      trigger_chapter_study: [],
      chapter_study_keys: [],
    };
    if (libraryBp) {
      wizardPatch.blueprint_id = libraryBp;
      wizardPatch.source_blueprint_id = libraryBp;
    }
    if (isWritingCopyId(ownerStudyTid)) {
      wizardPatch.working_task_id = ownerStudyTid;
    } else if (libraryBp) {
      /* study target is library bp itself */
    } else if (isLibraryBlueprintId(ownerStudyTid)) {
      wizardPatch.blueprint_id = ownerStudyTid;
    }
    liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, wizardPatch);
    // 立刻落库清掉服务端 leftover trigger，否则刷新会再自动重跑已分析章
    void persistUiStateForced().catch(() => {});
  }
  const focusKey = chapterKeys[0] || "";
  if (focusKey && stillHere()) {
    driveDocWindow("analyze", {
      blueprintId: libraryBp,
      taskId: ownerStudyTid,
      chapterKey: focusKey,
      tab: "config",
      sessionEpoch: ownerEpoch,
      sessionId: ownerSid,
    });
  }
  let resolvedStudyKeys = [...(chapterKeys || [])];
  try {
    let keys = [...resolvedStudyKeys];
    if (beginAll || !keys.length) {
      try {
        const slim = await request(
          docOutlineUrl(ownerStudyTid, {
            fresh: true,
            writingTaskId: writingTaskIdForTask(ownerStudyTid),
          })
        );
        if (!stillHere()) return;
        keys = (slim?.chapters || [])
          .filter((ch) => !ch.structure_only && !(Array.isArray(ch.points) && ch.points.length))
          .map((ch) => String(ch.key || "").trim())
          .filter(Boolean);
      } catch {
        keys = [];
      }
    }
    resolvedStudyKeys = [...keys];
    if (!stillHere()) return;
    if (!keys.length) {
      ElMessage.info("没有待分析的章节");
      return;
    }
    if (studyMethod === "auto") {
      pageStudyInflight = false;
      setDocWindowStudyBusy(true, "正在自动分析本章…");
      await sendAction("generate_freeform_points", {
        task_id: ownerStudyTid,
        chapter_keys: keys,
        focus_chapter_key: keys[0] || "",
      });
      return;
    }
    const useSample = studyMethod !== "source";
    if (studyMethod === "source" && !String(studyText || "").trim()) {
      ElMessage.warning("请先粘贴本章原文");
      return;
    }
    pushStudyProgress(
      beginAll || keys.length > 1
        ? useSample
          ? `开始依据样本分析 ${keys.length} 章写作要点…`
          : `开始按原文分析 ${keys.length} 章写作要点…`
        : useSample
          ? `开始依据样本分析「${keys[0]}」…`
          : `开始按原文分析「${keys[0]}」…`,
      "process",
      {
        epoch: ownerEpoch,
        sid: ownerSid,
        blueprintId: libraryBp || ownerStudyTid,
        taskId: ownerStudyTid,
      }
    );
    const response = await fetch(
      `/api/writing/tasks/${encodeURIComponent(ownerStudyTid)}/study/points/text/stream`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        signal: studySignal,
        body: JSON.stringify({
          chapter_keys: keys,
          text: useSample ? "" : String(studyText || ""),
          use_sample: useSample,
        }),
      }
    );
    await readStream(response, (event) => {
      if (!stillHere()) return;
      if (!event || typeof event !== "object") return;
      noteStudyProgressHeartbeat();
      if (event.type === "step" && (event.step === "study" || !event.step)) {
        const result = event.result && typeof event.result === "object" ? event.result : {};
        const idx = Number(result.index || event.index || 0) || 0;
        const total = Number(result.total || event.total || keys.length) || keys.length;
        const label = studyChapterLabel(event);
        const chapterKey = String(event.chapter || "").trim();
        if (event.status === "running" && chapterKey) {
          const line =
            total > 0
              ? `▶ (${idx}/${total}) 正在分析「${label}」…`
              : `▶ 正在分析「${label}」…`;
          pushStudyProgress(line, "step", {
            epoch: ownerEpoch,
            sid: ownerSid,
            blueprintId: libraryBp || ownerStudyTid,
          });
          setDocWindowStudyBusy(
            true,
            total > 0 ? `正在分析 ${idx}/${total} · ${label}` : `正在分析「${label}」…`
          );
          driveDocWindow("analyze", {
            blueprintId: libraryBp || ownerStudyTid,
            taskId: ownerStudyTid,
            chapterKey,
            tab: "config",
            sessionEpoch: ownerEpoch,
            sessionId: ownerSid,
          });
        } else if (event.status === "done" && chapterKey) {
          const note = String(result.note || "").trim();
          const line =
            total > 0
              ? `✓ (${idx}/${total}) 「${label}」${note ? ` · ${note}` : "完成"}`
              : `✓ 「${label}」${note ? ` · ${note}` : "完成"}`;
          const progressMsg = resolveProgressAssistantMessage(
            libraryBp || ownerStudyTid,
            ownerStudyTid
          );
          settleStudyRunningSteps(progressMsg, { label });
          pushStudyProgress(line, "step", {
            epoch: ownerEpoch,
            sid: ownerSid,
            blueprintId: libraryBp || ownerStudyTid,
          });
          // 学完立刻合入要点/检索词/简述，目录「待分析」马上变「已分析」
          driveDocWindow("analyze", {
            blueprintId: sanitizeLibraryBlueprintId(liveBlueprintWizard.value?.blueprint_id || libraryBp, libraryBp),
            taskId: ownerStudyTid,
            chapterKey,
            tab: "config",
            points: Array.isArray(result.points) ? result.points : null,
            keywords: Array.isArray(result.keywords) ? result.keywords : null,
            describes: result.describes || "",
            structure_only: result.structure_only,
            sessionEpoch: ownerEpoch,
            sessionId: ownerSid,
          });
          pushStudySupplementProgressFromEvent(
            { points: result.points, point_items: result.point_items },
            {
              epoch: ownerEpoch,
              sid: ownerSid,
              blueprintId: libraryBp || ownerStudyTid,
              taskId: ownerStudyTid,
              invokeHook: false,
            }
          );
        } else if (event.status === "done" && !chapterKey && String(event.title || "").includes("完成")) {
          pushStudyProgress(`✓ ${event.title}`, "step", {
            epoch: ownerEpoch,
            sid: ownerSid,
            blueprintId: libraryBp || ownerStudyTid,
          });
        }
      }
      if (event.type === "study_chapter_progress" && event.chapter) {
        if (!stillHere()) return;
        const analyzed = Number(event.study_analyzed || 0);
        const unanalyzed = Number(event.study_unanalyzed || 0);
        const progressTid = String(event.task_id || ownerStudyTid).trim();
        const bpId = sanitizeLibraryBlueprintId(
          liveBlueprintWizard.value?.source_blueprint_id ||
            liveBlueprintWizard.value?.blueprint_id ||
            libraryBp,
          libraryBp
        );
        const workTid = isWritingCopyId(progressTid)
          ? progressTid
          : String(liveBlueprintWizard.value?.working_task_id || ownerStudyTid).trim();
        liveBlueprintWizard.value = mergeWizardPayload(liveBlueprintWizard.value || {}, {
          ...(bpId ? { blueprint_id: bpId, source_blueprint_id: bpId } : {}),
          ...(workTid && isWritingCopyId(workTid) ? { working_task_id: workTid } : {}),
          step: "study",
          study_stream_active: true,
          study_analyzed: analyzed,
          study_unanalyzed: unanalyzed,
        });
        driveDocWindow("analyze", {
          blueprintId: bpId,
          taskId: workTid || progressTid,
          chapterKey: String(event.chapter),
          tab: "config",
          points: Array.isArray(event.points) ? event.points : null,
          point_items: Array.isArray(event.point_items) ? event.point_items : null,
          keywords: Array.isArray(event.keywords) ? event.keywords : null,
          describes: event.describes || "",
          structure_only: event.structure_only,
          sessionEpoch: ownerEpoch,
          sessionId: ownerSid,
        });
        if (unanalyzed <= 0) bumpDocOutlineReload({ force: true });
        const totalNow = analyzed + unanalyzed;
        if (unanalyzed <= 0 && analyzed > 0) {
          setDocWindowStudyBusy(false);
        } else if (totalNow > 0) {
          setDocWindowStudyBusy(true, `分析进度 ${analyzed}/${totalNow}`);
        }
        pushStudyProgressLine(studyProgressLineFromChapterEvent(event), "process", {
          epoch: ownerEpoch,
          sid: ownerSid,
          blueprintId: bpId,
          taskId: workTid || progressTid,
          invokeHook: false,
        });
        pushStudySupplementProgressFromEvent(event, {
          epoch: ownerEpoch,
          sid: ownerSid,
          blueprintId: bpId,
          taskId: workTid || progressTid,
          invokeHook: false,
        });
      }
      if (event.type === "error") throw new Error(event.detail || "分析失败");
      if (event.type === "process" && event.message) {
        const processText = String(event.message || "");
        if (/分析完成|要点分析完成|写作要点分析完成/.test(processText)) {
          const progressMsg = resolveProgressAssistantMessage(
            libraryBp || ownerStudyTid,
            ownerStudyTid
          );
          settleStudyRunningSteps(progressMsg, { all: true });
        }
        pushStudyProgress(processText, "process", {
          epoch: ownerEpoch,
          sid: ownerSid,
          blueprintId: libraryBp || ownerStudyTid,
        });
      }
    });
    if (!stillHere()) return;
    {
      const progressMsg = resolveProgressAssistantMessage(
        libraryBp || ownerStudyTid,
        ownerStudyTid
      );
      settleStudyRunningSteps(progressMsg, { all: true });
      finalizeAssistantProcessSteps(progressMsg);
    }
    pushStudyProgress("✓ 章节分析已完成", "step", {
      epoch: ownerEpoch,
      sid: ownerSid,
      blueprintId: libraryBp || ownerStudyTid,
    });
    ElMessage.success("章节分析已完成");
    bumpDocOutlineReload({ force: true });
  } catch (error) {
    if (!isAbortError(error) && stillHere()) {
      const progressMsg = resolveProgressAssistantMessage(
        libraryBp || ownerStudyTid,
        ownerStudyTid
      );
      settleStudyRunningSteps(progressMsg, { all: true });
      finalizeAssistantProcessSteps(progressMsg);
      // 长分析流易被刷新/代理掐断；服务端常已写完，先刷目录再决定要不要报失败
      if (isTransientNetworkError(error)) {
        pushStudyProgress("连接中断，正在核对分析进度…", "process", {
          epoch: ownerEpoch,
          sid: ownerSid,
          blueprintId: libraryBp || ownerStudyTid,
        });
        try {
          bumpDocOutlineReload({ force: true });
          const slim = await request(
            docOutlineUrl(ownerStudyTid, {
              fresh: true,
              writingTaskId: writingTaskIdForTask(ownerStudyTid),
            })
          );
          if (!stillHere()) return;
          const chapters = Array.isArray(slim?.chapters) ? slim.chapters : [];
          const want = (resolvedStudyKeys || []).map((k) => String(k || "").trim()).filter(Boolean);
          const doneN = chapters.filter((ch) => {
            const key = String(ch?.key || "").trim();
            if (want.length && !want.includes(key)) return false;
            if (ch?.structure_only) return false;
            return Array.isArray(ch?.points) && ch.points.length > 0;
          }).length;
          const targetN = want.length || chapters.filter((ch) => !ch?.structure_only).length;
          if (doneN > 0 && (want.length ? doneN >= want.length : doneN > 0)) {
            pushStudyProgress(
              `✓ 章节分析已完成（连接中断前已落盘 ${doneN}${targetN ? `/${targetN}` : ""} 章）`,
              "step",
              {
                epoch: ownerEpoch,
                sid: ownerSid,
                blueprintId: libraryBp || ownerStudyTid,
              }
            );
            ElMessage.success("章节分析已完成");
          } else {
            pushStudyProgress(
              `分析连接中断：已确认 ${doneN} 章落盘，可再说「继续分析」补跑未完成的章节`,
              "error",
              {
                epoch: ownerEpoch,
                sid: ownerSid,
                blueprintId: libraryBp || ownerStudyTid,
              }
            );
            ElMessage.warning("分析连接中断，已落盘部分可继续分析");
          }
        } catch {
          pushStudyProgress(`分析失败：${error?.message || "网络中断"}`, "error", {
            epoch: ownerEpoch,
            sid: ownerSid,
            blueprintId: libraryBp || ownerStudyTid,
          });
          ElMessage.error(error?.message || "分析失败");
        }
      } else {
        pushStudyProgress(`分析失败：${error?.message || "未知错误"}`, "error", {
          epoch: ownerEpoch,
          sid: ownerSid,
          blueprintId: libraryBp || ownerStudyTid,
        });
        ElMessage.error(error?.message || "分析失败");
      }
    }
  } finally {
    const ownedThisRun =
      pageStudyOwnerEpoch === ownerEpoch &&
      pageStudyOwnerBlueprintId === (libraryBp || ownerStudyTid);
    // 先清 stream_active 再把 inflight 置 false，避免 kickoff watch 在残留标记上重新上锁
    if (stillHere()) {
      const progressMsg = resolveProgressAssistantMessage(
        libraryBp || ownerStudyTid,
        ownerStudyTid
      );
      settleStudyRunningSteps(progressMsg, { all: true });
      finalizeAssistantProcessSteps(progressMsg);
      const endExtra = { step: "study" };
      if (libraryBp) {
        endExtra.blueprint_id = libraryBp;
        endExtra.source_blueprint_id = libraryBp;
      }
      if (isWritingCopyId(ownerStudyTid)) {
        endExtra.working_task_id = ownerStudyTid;
      }
      clearStudyTriggersAndUnlock(endExtra);
      void persistUiStateForced().catch(() => {});
    } else if (ownedThisRun) {
      setDocWindowStudyBusy(false);
    }
    if (ownedThisRun) {
      pageStudyInflight = false;
      pageStudyAbortController = null;
      pageStudyOwnerEpoch = 0;
      pageStudyOwnerSessionId = "";
      pageStudyOwnerBlueprintId = "";
      pageStudyOwnerTaskId = "";
      // 保留 pageStudyToken：用于识别 softSync 写回的同批残留 trigger，避免回锁死锁
    }
  }
}

function onPageHidePersist() {
  if (!sessionId.value) return;
  clearTimeout(uiStatePersistTimer);
  persistUiState().catch(() => {});
}

onMounted(async () => {
  window.addEventListener("rw-auth-change", onAuthChanged);
  window.addEventListener("pagehide", onPageHidePersist);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") onPageHidePersist();
  });
  // 进页资源与历史摘要一次并行；会话恢复与 boot 并行，缩短首屏瀑布
  const bootPromise = Promise.all([
    loadRobots(),
    loadWordStyles(),
    loadRenderTemplatesForRobot(robotId.value),
    loadHistory(),
  ]);
  if (props.resumeSessionId || props.resumeToken) {
    await Promise.all([resumeFromHandoff(), bootPromise]);
    return;
  }
  const [, restored] = await Promise.all([bootPromise, restoreCurrentSession()]);
  if (!restored) seedOpeningGreeting();
});

onUnmounted(() => {
  clearTimeout(uiStatePersistTimer);
  onPageHidePersist();
  stopDocDockResize();
  window.removeEventListener("rw-auth-change", onAuthChanged);
  window.removeEventListener("pagehide", onPageHidePersist);
});
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: row;
  height: 100%;
  min-height: 0;
  background: var(--ay-bg-grad);
  position: relative;
}
.global-loading-mask {
  position: fixed;
  inset: 0;
  z-index: 4000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(248, 250, 252, 0.72);
  backdrop-filter: blur(4px);
}
.global-loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 22px 28px;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
  color: var(--ay-ink);
  font-size: 14px;
}
.global-loading-spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #e2e8f0;
  border-top-color: #4e6ef2;
  border-radius: 50%;
  animation: global-spin 0.75s linear infinite;
}
@keyframes global-spin {
  to {
    transform: rotate(360deg);
  }
}
.history-rail {
  width: 240px;
  flex: 0 0 240px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px 10px;
  border-right: 1px solid var(--ay-line);
  background: #f8fafc;
  overflow: auto;
}
.new-chat {
  border: 1px solid #c9d4f5;
  border-radius: 10px;
  padding: 9px 12px;
  background: #fff;
  color: var(--ay-blue);
  font-size: 13px;
  font-weight: 650;
  cursor: pointer;
}
.new-chat:hover:not(:disabled) {
  background: #eef2ff;
}
.new-chat:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.history-head {
  margin: 8px 4px 2px;
  color: #94a3b8;
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.04em;
}
.history-empty {
  padding: 10px 8px;
  color: #94a3b8;
  font-size: 12px;
}
.history-item {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
}
.history-item:hover {
  background: #fff;
  border-color: var(--ay-line);
}
.history-item.active {
  background: #fff;
  border-color: #c9d4f5;
  box-shadow: 0 1px 4px rgba(78, 110, 242, 0.08);
}
.history-item.loading {
  background: #eef2ff;
  border-color: #c9d4f5;
}
.history-item.loading .history-item-main span {
  color: #4e6ef2;
}
.history-item-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  padding: 9px 26px 9px 10px;
  cursor: pointer;
}
.history-item-main:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}
.history-item-del {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 1;
  width: 18px;
  height: 18px;
  border: 0;
  border-radius: 6px;
  padding: 0;
  background: transparent;
  color: #94a3b8;
  font-size: 14px;
  line-height: 18px;
  cursor: pointer;
}
.history-item-del:hover:not(:disabled) {
  background: #fee2e2;
  color: #dc2626;
}
.history-item-del:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}
.history-item strong {
  color: var(--ay-ink);
  font-size: 13px;
  font-weight: 650;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-item span {
  color: #94a3b8;
  font-size: 11px;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-main {
  flex: 1;
  min-width: 280px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.chat-doc-dock {
  position: relative;
  flex: 0 0 auto;
  min-width: 400px;
  max-width: min(72vw, 920px);
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--ay-line);
  background: #fff;
  overflow: hidden;
}
.chat-doc-split {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  z-index: 5;
  cursor: col-resize;
  background: transparent;
}
.chat-doc-split:hover,
.chat-doc-split:active {
  background: rgba(78, 110, 242, 0.18);
}
.chat-stage {
  flex: 1;
  overflow: auto;
  padding: 32px 24px 12px;
}
.opening-suggests {
  max-width: 720px;
  margin: 8px auto 16px;
  text-align: center;
}
.opening-suggests-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--ay-muted);
}
.welcome-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 720px;
  margin: 0 auto;
}
.suggests {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}
.suggest-guide {
  border-color: #b8c4f0;
  color: var(--ay-blue);
  font-weight: 560;
}
.suggest-guide:hover:not(:disabled) {
  background: #f5f8ff;
}
.suggest {
  border: 1px solid var(--ay-line);
  border-radius: 999px;
  padding: 8px 14px;
  background: #fff;
  color: var(--ay-ink);
  cursor: pointer;
  font-size: 13px;
}
.suggest:hover {
  border-color: #c5d0f5;
  color: var(--ay-blue);
}
.thread {
  max-width: 920px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.chat-bubble.bubble {
  width: 100%;
}
.thread :deep(.ant-bubble) {
  max-width: 100%;
}
.thread :deep(.ant-bubble-content-wrapper) {
  max-width: min(640px, 86vw);
}
.bubble.user :deep(.rich),
.bubble.user :deep(.body) {
  color: #fff;
}
.rich :deep(p) {
  margin: 0 0 0.55em;
}
.rich :deep(p:last-child) {
  margin-bottom: 0;
}
.rich :deep(h1),
.rich :deep(h2),
.rich :deep(h3),
.rich :deep(h4) {
  margin: 0.6em 0 0.35em;
  font-weight: 700;
  line-height: 1.35;
}
.rich :deep(h1) {
  font-size: 1.2em;
}
.rich :deep(h2) {
  font-size: 1.1em;
}
.rich :deep(h3),
.rich :deep(h4) {
  font-size: 1.05em;
}
.rich :deep(hr) {
  border: 0;
  border-top: 1px dashed #c5d0e0;
  margin: 0.85em 0;
}
.bubble.user .rich :deep(hr) {
  border-top-color: rgba(255, 255, 255, 0.45);
}
.rich :deep(blockquote) {
  margin: 0.4em 0;
  padding: 0.2em 0 0.2em 0.75em;
  border-left: 3px solid #c5d0f5;
  color: inherit;
  opacity: 0.95;
}
.rich :deep(ul),
.rich :deep(ol) {
  margin: 0.35em 0;
  padding-left: 1.25em;
}
.rich :deep(li) {
  margin: 0.15em 0;
}
.rich :deep(strong),
.rich :deep(b) {
  font-weight: 700;
}
.rich :deep(pre) {
  margin: 0.45em 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.06);
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
}
.bubble.user .rich :deep(pre) {
  background: rgba(255, 255, 255, 0.16);
}
.rich :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.9em;
  padding: 0.05em 0.35em;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.06);
}
.rich :deep(pre code) {
  padding: 0;
  background: transparent;
}
.bubble.user .rich :deep(code) {
  background: rgba(255, 255, 255, 0.18);
}
.rich :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.4em 0;
  font-size: 12px;
}
.rich :deep(th),
.rich :deep(td) {
  border: 1px solid #dbe2f0;
  padding: 4px 6px;
}
.bubble.user .rich :deep(th),
.bubble.user .rich :deep(td) {
  border-color: rgba(255, 255, 255, 0.35);
}
.rich :deep(a) {
  color: inherit;
  text-decoration: underline;
}
.cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.card {
  --card-inner-pad: 3px;
  padding: var(--card-inner-pad);
  border-radius: 12px;
  border: 1px solid var(--ay-line);
  background: #f8fafc;
  display: flex;
  flex-direction: column;
  gap: var(--card-inner-pad);
  box-sizing: border-box;
}
.card > .card-title,
.card > .gate-hint,
.card > .card-hint,
.card > .confirm-actions,
.card > .outline-list,
.card > .nav-browse-list,
.card > .nav-browse-section,
.card > p:not(.rich *) {
  margin: 0;
  padding: var(--card-inner-pad);
  box-sizing: border-box;
}
.nav-browse-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 4px !important;
  padding-bottom: 4px !important;
}
.nav-browse-section + .nav-browse-section {
  border-top: 1px dashed var(--ay-line);
  margin-top: 4px;
  padding-top: 10px !important;
}
.nav-browse-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  padding: 0 2px;
}
.nav-browse-section-count {
  font-size: 11px;
  font-weight: 500;
  color: #64748b;
  background: #f1f5f9;
  border-radius: 999px;
  padding: 0 7px;
  line-height: 18px;
}
.nav-browse-list {
  list-style: none;
  max-height: 280px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: var(--card-inner-pad) !important;
}
.nav-browse-section .nav-browse-list {
  max-height: 200px;
  padding: 0 !important;
}
.nav-browse-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 8px;
  padding: 6px 8px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid var(--ay-line);
  font-size: 13px;
  line-height: 1.35;
}
.nav-browse-kind {
  flex: 0 0 auto;
  font-size: 11px;
  color: #64748b;
  background: #eef2ff;
  border-radius: 4px;
  padding: 1px 6px;
}
.nav-browse-name {
  flex: 1 1 120px;
  color: #0f172a;
  word-break: break-word;
}
.nav-browse-meta {
  flex: 0 0 auto;
  font-size: 11px;
  color: #94a3b8;
}
.nav-browse-empty {
  color: #64748b;
}
.card .confirm-actions {
  gap: var(--card-inner-pad);
}
.card .chat-action-btn {
  height: auto !important;
  min-height: 0;
  padding: var(--card-inner-pad) !important;
  line-height: 1.4;
}
.card .outline-list {
  padding: var(--card-inner-pad);
  padding-left: calc(var(--card-inner-pad) + 1.1em);
}
.analysis-gate .gate-hint {
  margin: 0;
  font-size: 12px;
  color: #64748b;
}
.analysis-gate .outline-list {
  margin: 0;
  max-height: 140px;
  overflow: auto;
}
.analysis-gate .gate-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--card-inner-pad, 3px);
  margin: 0;
}
.chat-action-btn {
  --chat-btn-h: 28px;
  height: var(--chat-btn-h) !important;
  min-height: var(--chat-btn-h);
  padding: 0 12px !important;
  font-size: 12px !important;
  font-weight: 520;
  border-radius: 6px !important;
  box-sizing: border-box;
}
.chat-action-btn--compact {
  --chat-btn-h: 24px;
  padding: 0 10px !important;
  font-size: 12px !important;
}
.pace-actions .chat-action-btn,
.gate-actions .chat-action-btn {
  min-width: 120px;
  justify-content: center;
}
.skip-blueprint-card .blueprint-path-actions,
.intent-clarify-card .blueprint-path-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: var(--card-inner-pad, 10px);
  box-sizing: border-box;
}
.skip-blueprint-card .blueprint-path-actions .chat-action-btn,
.intent-clarify-card .blueprint-path-actions .chat-action-btn {
  flex: 1 1 140px;
  justify-content: center;
}
.intent-clarify-card .hint {
  margin: 0;
  padding: var(--card-inner-pad, 10px) var(--card-inner-pad, 10px) 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--ay-ink);
}
.study-source-paste-card .hint {
  margin: 0;
  padding: var(--card-inner-pad, 10px) var(--card-inner-pad, 10px) 8px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--ay-ink);
}
.study-source-rich-editor {
  margin: 0 10px 8px;
}
.study-source-paste-card.used .study-source-rich-editor {
  pointer-events: none;
  opacity: 0.7;
}
.chapter-picker .picker-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 4px 0 8px;
}
.chapter-picker .picker-count {
  margin-left: auto;
  font-size: 12px;
  color: #64748b;
}
.chapter-picker .picker-tree {
  max-height: 280px;
  overflow: auto;
  margin-bottom: 10px;
  padding: 8px 6px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid var(--ay-line);
}
.chapter-picker .picker-tree-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 13px;
}
.chapter-picker .picker-tree-row .seq {
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
  min-width: 2.5em;
}
.chapter-picker .picker-tree-row .name {
  color: #0f172a;
}
.fold-empty.soft {
  margin: 0 0 8px;
  color: #64748b;
}
.card-title {
  font-weight: 650;
  margin-bottom: 0;
  font-size: 13px;
}
.card-hint {
  margin: 0;
  font-size: 12px;
  color: var(--ay-muted);
}
.materials-pack-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.writing-supplement-card .supplement-gap-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.writing-supplement-card .supplement-gap-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.04);
}
.writing-supplement-card .supplement-gap-row.clickable {
  cursor: pointer;
}
.writing-supplement-card .supplement-gap-row.clickable:hover {
  background: rgba(78, 110, 242, 0.08);
}
.writing-supplement-card .supplement-gap-kind {
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  color: #4338ca;
  background: #eef2ff;
  border: 1px solid #c7d2fe;
}
.writing-supplement-card .supplement-gap-line {
  flex: 1 1 160px;
  font-size: 13px;
}
.writing-supplement-card .supplement-gap-ch {
  font-size: 12px;
  color: var(--ay-muted);
}
.writing-supplement-card .supplement-gap-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.supplement-gap-dialog-line {
  margin: 0 0 8px;
  font-size: 14px;
  line-height: 1.5;
  color: #0f172a;
}
.supplement-gap-dialog-ch {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--ay-muted);
}
.supplement-gap-dialog-hint {
  margin: 0;
  font-size: 12px;
  color: var(--ay-muted);
}
.materials-pack-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.materials-pack-pending {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.materials-pack-slot-list {
  margin: 0;
  padding-left: 1.2em;
  font-size: 12px;
  color: #334155;
  line-height: 1.45;
}
.materials-pack-slot-list .muted {
  color: #94a3b8;
}
.card.used {
  opacity: 0.55;
  pointer-events: none;
}
.bp-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  margin-bottom: 8px;
  padding: 10px 12px;
  border: 1px solid var(--ay-line);
  border-radius: 12px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.bp-item:hover:not(:disabled) {
  border-color: #4e6ef2;
  background: #f5f7ff;
  box-shadow: 0 4px 14px rgba(78, 110, 242, 0.12);
}
.bp-item.active,
.bp-item.picked {
  border-color: #4e6ef2;
  background: #eef2ff;
}
.bp-item:disabled {
  cursor: default;
}
.bp-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.bp-main span {
  font-size: 12px;
  color: var(--ay-muted);
}
.bp-desc {
  font-style: normal;
  font-size: 13px;
  color: #334155;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-width: 420px;
}
.bp-cta {
  flex: 0 0 auto;
  padding: 4px 10px;
  border-radius: 999px;
  background: #4e6ef2;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}
.bp-table-wrap {
  overflow: auto;
  border: 1px solid var(--ay-line);
  border-radius: 10px;
  background: #fff;
}
.bp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.bp-table th,
.bp-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #eef2f7;
  text-align: left;
  vertical-align: middle;
}
.bp-table th {
  background: #f8fafc;
  color: #64748b;
  font-weight: 600;
  white-space: nowrap;
}
.bp-table tr.picked {
  background: #eef2ff;
}
.bp-table td.desc {
  max-width: 220px;
  color: #334155;
  line-height: 1.4;
}
.composer-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.outline-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--ay-ink);
}
.chapter-elements-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.chapter-element-linked-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  border: 1px solid var(--ay-line);
  border-radius: 8px;
  overflow: hidden;
}
.chapter-element-linked-list li {
  display: grid;
  grid-template-columns: auto 1fr 1.2fr;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  font-size: 13px;
  border-bottom: 1px solid var(--ay-line);
}
.chapter-element-linked-list li:last-child {
  border-bottom: none;
}
.chapter-element-linked-list .sum {
  font-size: 12px;
  color: var(--ay-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cfg-save-hint.muted {
  margin-top: 6px;
}
.chapter-elements {
  margin-top: 4px;
}
.chapter-element-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.chapter-element-row .ant-input {
  flex: 1;
  min-width: 88px;
}
.chapter-element-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
}
.progress-card {
  --card-inner-pad: 0;
  padding: var(--card-inner-pad);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.progress-log-block {
  margin: 0;
}
.progress-log-block.run .fold-process {
  border-color: #93c5fd;
  background: #eff6ff;
}
.progress-score-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.progress-retry-label {
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.3;
}
.progress-score-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 999px;
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #a7f3d0;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.3;
}
.progress-score-chip.bad {
  background: #fff7ed;
  color: #c2410c;
  border-color: #fed7aa;
}
.fold-process > li.emph {
  color: #0f172a;
  font-size: 13px;
  font-weight: 700;
}
.chapter-fold.next {
  border-style: dashed;
  opacity: 0.85;
}
.prog-meta .dot {
  margin: 0 4px;
  color: #cbd5e1;
}
.prog-meta code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: #94a3b8;
}
.prog-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}
.prog-stat {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}
.prog-stat span {
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #64748b;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
}
.chapter-fold-list {
  display: block;
  max-height: min(62vh, 720px);
  overflow: auto;
  padding-right: 2px;
}
.chapter-fold {
  border: 1px solid var(--ay-line);
  border-radius: 12px;
  background: #fff;
  overflow: hidden;
  margin-bottom: 8px;
  flex: none;
  flex-shrink: 0;
  min-height: 44px;
}
.chapter-fold:last-child {
  margin-bottom: 0;
}
.chapter-fold.ok {
  border-color: #bbf7d0;
}
.chapter-fold.warn {
  border-color: #fdba74;
  background: #fffbeb;
}
.chapter-fold.run {
  border-color: #bfdbfe;
}
.fold-summary-inner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  min-height: 40px;
  margin: 0;
  padding: 10px 12px;
  border: 0;
  background: transparent;
  box-sizing: border-box;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.fold-caret {
  flex: 0 0 auto;
  width: 14px;
  color: #94a3b8;
  line-height: 1.4;
}
.fold-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.fold-title {
  color: #1f2937;
  font-weight: 650;
  line-height: 1.4;
  word-break: break-word;
}
.fold-dir-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #e2e8f0;
  color: #475569;
  font-size: 11px;
  font-style: normal;
  font-weight: 600;
  vertical-align: middle;
}
.fold-hint {
  color: #64748b;
  font-size: 11px;
  line-height: 1.35;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.fold-process {
  margin: 0;
  padding: 8px 10px 8px 22px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
  list-style: disc;
  font-size: 12px;
  color: #334155;
  line-height: 1.55;
}
.fold-process > li + li {
  margin-top: 6px;
}
.fold-process > li.nest {
  list-style: circle;
  margin-left: 4px;
}
.fold-process > li.mute {
  color: #64748b;
}
.fold-process-sub {
  margin: 4px 0 0;
  padding-left: 18px;
  list-style: circle;
  color: #475569;
}
.fold-process-sub li + li {
  margin-top: 3px;
}
.fold-status {
  flex: 0 0 auto;
  margin-top: 1px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #64748b;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
}
.chapter-fold.warn .fold-status {
  background: #ffedd5;
  color: #c2410c;
}
.chapter-fold.ok .fold-status {
  background: #ecfdf5;
  color: #047857;
}
.chapter-fold.run .fold-status {
  background: #eff6ff;
  color: #1d4ed8;
}
.fold-body {
  padding: 0 12px 12px;
  border-top: 1px solid #eef2f7;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fold-note,
.fold-empty {
  margin: 0;
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
}
.run-log {
  border: 1px dashed #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
}
.run-log > summary {
  padding: 8px 10px;
  cursor: pointer;
  list-style: none;
  font-size: 12px;
  font-weight: 650;
  color: #64748b;
}
.run-log > summary::-webkit-details-marker {
  display: none;
}
.run-log > summary::before {
  content: "▸ ";
  color: #94a3b8;
}
.run-log[open] > summary::before {
  content: "▾ ";
}
.run-log-body {
  padding: 0 10px 10px;
}
.human-panel {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
  padding: 4px 10px 10px;
}
.chapter-actions-top {
  position: sticky;
  top: 0;
  z-index: 2;
  margin: 0 -10px 10px;
  padding: 10px 12px;
  border-radius: 10px 10px 0 0;
  background: #ecfdf5;
  border-bottom: 1px solid #a7f3d0;
}
.chapter-actions-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #065f46;
  line-height: 1.45;
}
.chapter-actions-copy strong {
  font-size: 13px;
  color: #064e3b;
}
.chapter-actions-btns {
  margin-top: 0 !important;
  padding-top: 0 !important;
  border-top: none !important;
}
.prog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eef2f7;
}
.prog-footer-hint {
  font-size: 12px;
  color: #64748b;
  line-height: 1.4;
}
.export-draft-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #475569;
  white-space: nowrap;
}
.export-card .export-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.human-tabs :deep(.ant-tabs-nav) {
  margin: 0 0 12px;
  border-bottom: none !important;
}
.human-tabs :deep(.ant-tabs-nav-wrap),
.human-tabs :deep(.ant-tabs-nav-wrap) {
  overflow: visible;
}
.human-tabs :deep(.ant-tabs-nav-wrap::after) {
  display: none !important;
  height: 0 !important;
}
.human-tabs :deep(.ant-tabs-nav-list) {
  width: 100%;
  display: flex;
  gap: 8px;
  border: none !important;
  float: none;
}
.human-tabs :deep(.ant-tabs-tab) {
  flex: 1 1 0;
  min-width: 0;
  height: auto;
  min-height: 36px;
  line-height: 1.35;
  padding: 8px 10px;
  font-size: 13px;
  font-weight: 600;
  text-align: center;
  white-space: normal;
  border-radius: 8px;
  border: 1px solid var(--ay-line) !important;
  color: #64748b;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.human-tabs :deep(.ant-tabs-tab.ant-tabs-tab-active) {
  background: #eff6ff;
  border-color: #93c5fd !important;
  color: #1d4ed8;
}
.human-tabs :deep(.ant-tabs-tab:not(.ant-tabs-tab-active)) {
  border-bottom-color: var(--ay-line) !important;
}
.human-tabs :deep(.ant-tabs-ink-bar) {
  display: none;
}
.human-tabs :deep(.ant-tabs-content) {
  padding-top: 4px;
}
.cfg-save-hint {
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  color: #9a3412;
  font-size: 12px;
  line-height: 1.5;
}
.prog-confirm-sticky {
  position: sticky;
  top: 0;
  z-index: 3;
  margin: 0 0 10px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  box-shadow: 0 4px 14px rgba(6, 95, 70, 0.08);
}
.prog-confirm-sticky-inner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.prog-confirm-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
  font-size: 12px;
  color: #065f46;
  line-height: 1.45;
}
.prog-confirm-copy strong {
  font-size: 13px;
  color: #064e3b;
}
.fold-block {
  margin-top: 8px;
}
.fold-block:first-child {
  margin-top: 0;
}
.fold-block-title {
  margin-bottom: 6px;
  color: #475569;
  font-size: 12px;
  font-weight: 700;
}
.fold-steps,
.gap-panel {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fff7ed;
  border: 1px solid #fed7aa;
}
.gap-panel.under-editor {
  margin-top: 12px;
}
.fold-gaps {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: #9a3412;
  line-height: 1.5;
}
.fold-gaps li + li {
  margin-top: 4px;
}
.fold-evidence,
.fold-checks,
.fold-points {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  color: #334155;
  line-height: 1.5;
}
.fold-points li + li {
  margin-top: 4px;
}
.run-meta {
  margin: 6px 0 0;
  font-size: 12px;
  color: #64748b;
  line-height: 1.45;
}
.fold-steps li,
.fold-checks li {
  margin: 4px 0;
}
.fold-steps em {
  margin: 0 6px;
  color: #64748b;
  font-style: normal;
}
.fold-evidence li {
  margin: 0 0 8px;
}
.fold-evidence li.miss .ev-tag.miss {
  background: #ffedd5;
  color: #c2410c;
}
.fold-obtained {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
  padding: 8px 10px;
}
.fold-obtained-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 650;
  color: #475569;
}
.fold-steps {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: #334155;
  line-height: 1.5;
}
.ev-head {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
}
.ev-scope {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
}
.ev-scope.chapter {
  background: #dcfce7;
  color: #15803d;
}
.ev-scope.point {
  background: #ede9fe;
  color: #6d28d9;
}
.ev-tag {
  padding: 0 6px;
  border-radius: 999px;
  background: #ecfdf5;
  color: #047857;
  font-size: 10px;
  font-weight: 700;
}
.ev-tag.ok {
  background: #ecfdf5;
  color: #047857;
}
.ev-tag.miss {
  background: #ffedd5;
  color: #c2410c;
}
.ev-point,
.ev-excerpt {
  margin: 2px 0 0;
  color: #64748b;
}
.fold-draft {
  max-height: 320px;
  overflow: auto;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
}
.fold-draft :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.4em 0;
  font-size: 12px;
}
.fold-draft :deep(th),
.fold-draft :deep(td) {
  border: 1px solid #cbd5e1;
  padding: 4px 6px;
}
.cfg-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 12px;
}
.cfg-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #475569;
  min-width: 0;
}
.cfg-grid label > span {
  font-weight: 650;
}
.cfg-grid .span-2 {
  grid-column: 1 / -1;
}
.cfg-switch {
  flex-direction: row !important;
  align-items: center;
  gap: 8px !important;
}
@media (max-width: 720px) {
  .cfg-grid {
    grid-template-columns: 1fr;
  }
  .cfg-grid .span-2 {
    grid-column: auto;
  }
  .prog-head {
    flex-direction: column;
  }
  .prog-stat {
    justify-content: flex-start;
  }
}
.draft {
  max-height: 220px;
  overflow: auto;
  margin: 0 0 8px;
  padding: 8px;
  background: #fff;
  border-radius: 8px;
  font-size: 12px;
}
.confirm-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.composer-wrap {
  padding: 8px 16px 20px;
}
.composer-tools :deep(.is-doc-win-on) {
  background: #eef2ff;
  border-color: #c9d4f5;
  color: #4e6ef2;
}
.composer-export {
  max-width: 920px;
  margin: 0 auto 10px;
  padding: 10px 14px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 16px;
  border: 1px solid #d4e8dc;
  border-radius: 14px;
  background: #f4fbf6;
}
.composer-export-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}
.composer-export-save {
  flex-shrink: 0;
  margin-left: auto;
}
.save-bp-offer-card--legacy {
  border-style: dashed;
  background: #f8fafc;
}
.composer-export-label {
  font-size: 13px;
  font-weight: 600;
  color: #1a6b42;
}
.composer-export-only {
  margin: 0;
  font-size: 12px;
}
.robot-chip {
  max-width: 920px;
  margin: 0 auto 8px;
  font-size: 12px;
  color: var(--ay-muted);
  display: flex;
  gap: 12px;
}
.phase {
  color: var(--ay-blue);
}
.composer {
  max-width: 920px;
  margin: 0 auto;
  padding: 12px;
  border: 1px solid var(--ay-line);
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(20, 30, 60, 0.06);
}
.composer-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.fmt-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.fmt {
  border: 1px solid var(--ay-line);
  background: #f8fafc;
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
  color: var(--ay-ink);
  cursor: pointer;
  line-height: 1.2;
}
.fmt:hover {
  border-color: #c5d0f5;
  color: var(--ay-blue);
}
.composer-lock-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  padding: 8px 12px;
  border-radius: 10px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  color: #475569;
  font-size: 13px;
  line-height: 1.4;
}
.composer-unlock {
  flex-shrink: 0;
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  line-height: 1.3;
}
.composer-unlock:hover {
  border-color: #4e6ef2;
  color: #4e6ef2;
}
.rich-editor {
  min-height: 72px;
  max-height: 180px;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid var(--ay-line);
  border-radius: 12px;
  background: #fff;
  line-height: 1.55;
  font-size: 14px;
  outline: none;
}
.rich-editor.is-locked {
  background: #f8fafc;
  color: #94a3b8;
  cursor: not-allowed;
}
.rich-editor:empty::before {
  content: attr(data-placeholder);
  color: #94a3b8;
  pointer-events: none;
}
.rich-editor:focus {
  border-color: #4e6ef2;
  box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.12);
}
.rich-editor :deep(p) {
  margin: 0 0 0.4em;
}
.rich-editor :deep(p:last-child) {
  margin-bottom: 0;
}
.rich-editor :deep(ul),
.rich-editor :deep(ol) {
  margin: 0.3em 0;
  padding-left: 1.2em;
}
.composer-hints {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.composer-hints .suggested-next {
  color: var(--ay-blue);
  font-weight: 500;
}
.intent-audit-dev {
  font-size: 11px;
  line-height: 1.3;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
}
.hint {
  font-size: 12px;
  color: var(--ay-muted);
}
:global(.search-branch-dialog.ant-modal) {
  margin: 0 !important;
  position: fixed !important;
  right: 16px;
  bottom: 72px;
  top: auto !important;
  left: auto !important;
  max-height: min(70vh, 520px);
  display: flex;
  flex-direction: column;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.18);
  border: 1px solid #e2e8f0;
}
:global(.search-branch-dialog .ant-modal__body) {
  flex: 1;
  overflow: auto;
  padding-top: 8px;
}
.search-branch-body {
  min-height: 80px;
}
.search-branch-loading {
  display: flex;
  align-items: center;
  gap: 10px;
}
.search-branch-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid #cbd5e1;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: search-branch-spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes search-branch-spin {
  to {
    transform: rotate(360deg);
  }
}
.search-branch-hint,
.search-branch-foot {
  font-size: 13px;
  color: #475569;
  margin: 0 0 12px;
}
.search-branch-summary {
  margin: 0 0 12px;
  font-size: 14px;
  color: #1e293b;
}
.search-branch-empty {
  color: #64748b;
  font-size: 13px;
}
.search-branch-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 50vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.search-branch-list li {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
}
.search-branch-list li.adopted {
  border-color: #bbf7d0;
  background: #f0fdf4;
}
.search-branch-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
}
.search-branch-point {
  font-weight: 600;
  color: #334155;
}
.search-branch-src {
  color: #64748b;
}
.search-branch-excerpt {
  font-size: 13px;
  line-height: 1.5;
  color: #0f172a;
  white-space: pre-wrap;
}
.search-branch-progress {
  margin: 12px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: #1d4ed8;
  line-height: 1.5;
}
</style>
