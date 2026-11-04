<template>
  <div
    class="doc-win"
    :class="{ docked }"
    :style="docked ? undefined : boxStyle"
    role="dialog"
    aria-label="文档目录与写作进度"
  >
      <header class="doc-win-bar" @mousedown.prevent="startDrag">
        <div class="doc-win-bar-main">
          <strong>文档窗口</strong>
          <el-select
            v-if="documents.length"
            :model-value="activeId"
            size="small"
            filterable
            placeholder="切换文档"
            class="doc-win-select"
            :disabled="docSwitchBlocked"
            :title="docSwitchBlocked ? docSwitchBlockReason : '切换文档'"
            @mousedown.stop
            @change="onPickDocument"
          >
            <el-option
              v-for="doc in documents"
              :key="doc.id"
              :label="documentOptionLabel(doc)"
              :value="doc.id"
            >
              <span>{{ documentOptionLabel(doc) }}</span>
            </el-option>
          </el-select>
          <button
            v-if="activeDoc"
            type="button"
            class="doc-win-rename"
            title="修改文档名称"
            :disabled="maskVisible || busy || renameBusy || opsLocked"
            @mousedown.stop
            @click="renameCurrentDocument"
          >
            {{ renameBusy ? "改名中…" : "改名" }}
          </button>
        </div>
          <div class="doc-win-ops" @mousedown.stop>
          <button
            v-if="collectElementsCount > 0"
            type="button"
            class="doc-win-pending-lib"
            title="把本文件里尚未收入个人要素库的要素收集入库"
            :disabled="maskVisible || collectElementsBusy || opsLocked"
            @click="openCollectElements"
          >
            {{ collectElementsBusy ? "收集中…" : "收集要素" }}
            <span class="doc-win-pending-badge">{{ collectElementsCount > 99 ? "99+" : collectElementsCount }}</span>
          </button>
          <button
            v-if="showSyncToBlueprintLibrary"
            type="button"
            class="doc-win-sync"
            title="副本里改过的目录/要点/要素，回写到已有源蓝本库条目"
            :disabled="maskVisible || busy || opsLocked"
            @click="runPhaseAction({ id: 'sync_to_blueprint_library' })"
          >
            同步到蓝本库
          </button>
          <button
            v-else-if="showSaveAsBlueprint"
            type="button"
            class="doc-win-sync"
            title="当前还不在蓝本库：把目录与写作要点收录为新蓝本，下次可选用"
            :disabled="maskVisible || busy || opsLocked"
            @click="runPhaseAction({ id: 'save_as_blueprint' })"
          >
            收录到蓝本库
          </button>
          <button
            type="button"
            class="doc-win-icon"
            title="刷新当前文档"
            :disabled="refreshDisabled"
            @click="refreshCurrentDoc"
          >
            ↻
          </button>
          <button type="button" class="doc-win-icon" title="缩小内容" :disabled="maskVisible" @click="nudgeZoom(-0.1)">−</button>
          <span class="doc-win-zoom">{{ Math.round(zoom * 100) }}%</span>
          <button type="button" class="doc-win-icon" title="放大内容" :disabled="maskVisible" @click="nudgeZoom(0.1)">+</button>
          <button type="button" class="doc-win-icon" title="关闭" :disabled="isClosing" @click="requestClose">×</button>
        </div>
      </header>

      <div class="doc-win-body" :style="{ fontSize: `${13 * zoom}px` }">
        <p v-if="!documents.length" class="doc-win-empty">
          当前对话还没有文档。创建蓝本或开始写作后，目录和各章写作进度会出现在这里。
        </p>
        <p v-else-if="activeId && !activeDoc" class="doc-win-empty">
          正在切换到所选文档，请稍候…
        </p>
        <template v-else>
          <div class="doc-win-meta">
            <span>{{ docWorkspaceLabel }}</span>
            <span v-if="activeDoc?.status">· {{ activeDoc.status }}</span>
            <span>· {{ chapters.length }} 章</span>
            <span v-if="outlineConfirmed">· 目录已确认</span>
            <span v-else-if="chapters.length">· 目录待确认</span>
            <span v-if="maskVisible">· {{ maskLabel }}</span>
            <span v-else-if="opsLocked" class="ops-live">· {{ opsStatusLabel }}</span>
            <span v-if="materialsPackLabelDisplay">· 资料包 {{ materialsPackLabelDisplay }}</span>
            <span v-else-if="projectFolderLabelDisplay">· 项目资料 {{ projectFolderLabelDisplay }}</span>
          </div>
          <div
            v-if="showMaterialsPackPanel"
            class="doc-win-project-pack materials-pack-panel"
            @mousedown.stop
          >
            <div class="materials-pack-head">
              <label class="project-pack-label">资料包</label>
              <span v-if="materialsPack?.name" class="materials-pack-name">{{ materialsPack.name }}</span>
              <span v-else class="materials-pack-name muted">未绑定</span>
              <button
                v-if="canEditMaterialsPack"
                type="button"
                class="cfg-save ghost"
                :disabled="saving || blockingLoad"
                @click="bindExistingMaterialsPack"
              >
                换绑
              </button>
              <button
                v-if="canEditMaterialsPack"
                type="button"
                class="cfg-save ghost"
                :disabled="saving || blockingLoad"
                @click="createAndBindMaterialsPack"
              >
                新建
              </button>
              <button
                v-if="canEditMaterialsPack && materialsPackIdLocal"
                type="button"
                class="cfg-save ghost"
                :disabled="saving || blockingLoad"
                @click="unbindMaterialsPack"
              >
                解绑
              </button>
              <button
                v-if="canEditMaterialsPack && materialsPack && !materialsPack.in_library"
                type="button"
                class="cfg-save ghost"
                :disabled="saving || blockingLoad"
                @click="confirmMaterialsPackLibrary"
              >
                确认入库
              </button>
            </div>
            <template v-if="canEditMaterialsPack && materialsPackIdLocal">
              <p v-if="materialsPackPreviewMode" class="materials-pack-empty muted">
                已为本文档预建资料包（开写时自动绑定）。可先按槽位上传文件。
              </p>
              <MaterialsSlotCards
                layout="rail"
                :rows="materialsSlotRows"
                editable
                can-add
                can-remove
                can-reorder
                can-upload
                can-clear
                can-parse
                :show-query="false"
                :disabled="saving || isClosing || opsLocked"
                @add="addMaterialsSlot"
                @remove="removeMaterialsSlot"
                @move="moveMaterialsSlot"
                @update="updateMaterialsSlot"
                @upload="uploadMaterialsSlot"
                @clear="clearMaterialsSlot"
                @parse="parseMaterialsSlot"
              />
              <p v-if="!materialsSlotRows.length" class="materials-pack-empty muted">
                资料包尚无槽位：点「+ 添加槽位」规划编号与名称，再上传并解析文件。检索词请在要素「引用槽位」里配置。
              </p>
            </template>
            <p v-else-if="showMaterialsPackBlueprintHint" class="materials-pack-empty muted">
              本蓝本有要素引用了资料包槽位。可先「新建」资料包并上传；开写时会自动绑到本文档。不配则不检索资料包。
            </p>
            <p v-else class="materials-pack-empty muted">
              请先「新建」或「换绑」资料包，再在资料包上配置槽位并上传文件。
            </p>
          </div>
          <p v-if="opsLocked && opsLockDetail" class="doc-win-ops-banner" role="status">
            {{ opsLockDetail }}
          </p>

          <ol
            v-if="(isBlueprintDoc || isFreeformSampleDoc) && wizardStepperSteps.length"
            class="doc-win-wizard-steps"
            aria-label="蓝本进度"
          >
            <li
              v-for="(row, idx) in wizardStepperSteps"
              :key="row.id"
              class="doc-win-wizard-step"
              :class="{
                done: row.done,
                current: row.current,
                upcoming: !row.done && !row.current,
                clickable: row.done && !row.current,
              }"
              :tabindex="row.done && !row.current ? 0 : -1"
              :title="row.done && !row.current ? `回到「${row.label}」` : undefined"
              @click="onWizardStepperClick(row)"
              @keydown.enter.prevent="onWizardStepperClick(row)"
              @keydown.space.prevent="onWizardStepperClick(row)"
            >
              <span class="step-index">{{ row.done ? "✓" : idx + 1 }}</span>
              <span class="step-label">{{ row.label }}</span>
            </li>
          </ol>

          <div class="phase-actions" role="toolbar" aria-label="按阶段操作">
            <button
              v-for="act in phaseActions"
              :key="act.id"
              type="button"
              class="cfg-save"
              :class="{ ghost: !act.primary, danger: act.danger, compact: act.compact }"
              :disabled="blockingLoad || saving || act.disabled || (busy && !isOutlinePhaseAction(act))"
              @click="runPhaseAction(act)"
            >
              {{ act.label }}
            </button>
          </div>

          <div class="view-tabs" role="tablist">
            <button
              type="button"
              class="view-tab"
              role="tab"
              :aria-selected="mainView === 'chapter'"
              :class="{ active: mainView === 'chapter' }"
              :disabled="tabControlsDisabled"
              @click="switchMainView('chapter')"
            >
              章节详情
            </button>
            <button
              type="button"
              class="view-tab"
              role="tab"
              :aria-selected="mainView === 'topology'"
              :class="{ active: mainView === 'topology' }"
              :disabled="tabControlsDisabled"
              @click="switchMainView('topology')"
            >
              拓扑图
            </button>
            <button
              type="button"
              class="view-tab"
              role="tab"
              :aria-selected="mainView === 'preview'"
              :class="{ active: mainView === 'preview' }"
              :disabled="tabControlsDisabled"
              @click="switchMainView('preview')"
            >
              全文预览
            </button>
          </div>

          <!-- v-show 保活：避免切 tab 反复销毁 200+ 章树 / VueFlow / 预览 HTML -->
          <div v-show="mainView === 'preview'" class="doc-win-preview">
            <div class="preview-toolbar">
              <span class="preview-meta">
                {{ previewMetaLabel }}
              </span>
              <button
                type="button"
                class="cfg-save ghost"
                :disabled="previewLoading || !previewTaskId"
                @click="loadDraftPreview({ force: true })"
              >
                {{ previewLoading ? "加载中…" : "刷新预览" }}
              </button>
            </div>
            <p v-if="previewError" class="doc-win-empty sm">{{ previewError }}</p>
            <p v-else-if="previewLoading && !previewHtml" class="doc-win-empty sm">正在组合已写章节…</p>
            <p v-else-if="!previewHtml" class="doc-win-empty sm">
              当前文档还没有可预览的正文。写完若干章后再来看全文
              <template v-if="previewTaskId">（任务 {{ previewTaskId }}）</template>。
            </p>
            <div
              v-else
              class="preview-article"
              v-html="previewHtml"
            />
          </div>

          <div v-show="mainView === 'topology'" class="doc-win-topology">
            <OutlineWorkspace
              v-if="topologyEverOpened && topologyOutline?.chapters?.length"
              :key="topologyRenderKey"
              :outline="topologyOutline"
              :confirmed="outlineConfirmed"
              :busy="saving || blockingLoad"
              :focus="topologyFocus"
              :task-id="taskId"
              :element-nodes="elementNodes"
              :locate-key="selectedKey"
              :visible="mainView === 'topology'"
              canvas-only
              :persist-chapters="persistFromTopology"
              :robot-id="robotId"
              @change="persistFromTopology"
              @add="onTopologyAdd"
              @remove="onTopologyRemove"
              @select="onTopologySelect"
              @change-elements="saveElementNodes"
            />
            <p v-else-if="topologyEverOpened" class="doc-win-empty sm">还没有章节，提取或确认目录后这里会显示拓扑图。</p>
          </div>

          <div v-show="mainView === 'chapter'" class="doc-win-split">
            <aside class="doc-win-tree" aria-label="章节目录">
              <div v-if="flatTree.length && selectableChapterKeys.length" class="tree-bulk">
                <el-checkbox
                  class="tree-bulk-all"
                  :model-value="allSelectableChecked"
                  :indeterminate="someSelectableChecked && !allSelectableChecked"
                  :disabled="blockingLoad"
                  @change="toggleSelectAll"
                >
                  全选
                </el-checkbox>
                <span v-if="checkedKeys.length" class="tree-bulk-count">已选 {{ checkedKeys.length }}</span>
                <div v-if="checkedKeys.length" class="tree-bulk-actions">
                  <button
                    type="button"
                    class="cfg-save ghost"
                    :disabled="blockingLoad"
                    @click="runBulkStudy"
                  >
                    分析
                  </button>
                  <button
                    type="button"
                    class="cfg-save"
                    :disabled="blockingLoad"
                    @click="runBulkWrite"
                  >
                    写作
                  </button>
                </div>
              </div>
              <div v-if="!tree.length" class="doc-win-empty sm">还没有章节目录</div>
              <el-tree
                v-else
                ref="chapterOutlineTreeRef"
                class="doc-outline-tree"
                :data="outlineTreeData"
                node-key="key"
                show-checkbox
                highlight-current
                :indent="18"
                :default-expanded-keys="outlineExpandedKeys"
                :expand-on-click-node="false"
                :check-on-click-node="false"
                :props="outlineTreeProps"
                :current-node-key="selectedKey || undefined"
                @check="onOutlineTreeCheck"
                @node-click="onOutlineTreeNodeClick"
              >
                <template #default="{ data }">
                  <span
                    class="tree-node-row"
                    :class="{
                      active: data.key === selectedKey,
                      run: data.is_running || chapterIsAnalyzing(data),
                    }"
                    :title="chapterTreeTitle(data)"
                  >
                    <el-tooltip
                      v-if="chapterSlotMisaligned(data)"
                      :content="chapterSlotWarnTitle(data)"
                      placement="top"
                      :show-after="200"
                    >
                      <i class="slot-warn" aria-label="槽位未对齐" tabindex="0">!</i>
                    </el-tooltip>
                    <span class="label">{{ chapterLabel(data) }}</span>
                    <em v-if="chapterStateLabel(data)" :class="['tone', chapterStateTone(data)]">
                      {{ chapterStateLabel(data) }}
                    </em>
                  </span>
                </template>
              </el-tree>
            </aside>
            <section class="doc-win-detail" aria-label="章节配置与写作信息">
              <template v-if="selectedChapter">
                <div class="detail-sticky">
                  <div class="detail-head">
                    <div class="detail-head-main">
                      <el-tooltip
                        v-if="chapterSlotMisaligned(selectedChapter)"
                        :content="chapterSlotWarnTitle(selectedChapter)"
                        placement="top"
                        :show-after="200"
                      >
                        <i class="slot-warn" aria-label="槽位未对齐" tabindex="0">!</i>
                      </el-tooltip>
                      <strong>{{ chapterLabel(selectedChapter) }}</strong>
                    </div>
                    <em :class="['tone', chapterStateTone(selectedChapter)]">
                      {{ chapterStateLabel(selectedChapter) || "—" }}
                    </em>
                  </div>
                  <div class="detail-tabs" role="tablist">
                    <button
                      type="button"
                      class="detail-tab"
                      role="tab"
                      :aria-selected="detailTab === 'config'"
                      :class="{ active: detailTab === 'config' }"
                      :disabled="tabControlsDisabled"
                      @click="switchDetailTab('config')"
                    >
                      基本配置
                    </button>
                    <button
                      type="button"
                      class="detail-tab"
                      role="tab"
                      :aria-selected="detailTab === 'content'"
                      :class="{ active: detailTab === 'content' }"
                      :disabled="tabControlsDisabled"
                      @click="switchDetailTab('content')"
                    >
                      写作准备
                    </button>
                  </div>

                  <div class="write-actions" aria-label="章节操作">
                    <button
                      v-for="act in chapterActions"
                      :key="act.id"
                      type="button"
                      class="cfg-save compact"
                      :class="{ ghost: !act.primary, danger: act.danger }"
                      :disabled="
                        blockingLoad ||
                        saving ||
                        act.disabled ||
                        (busy && !isOutlinePhaseAction(act)) ||
                        (act.id === 'save_config' && (!taskId || busy))
                      "
                      @click="runPhaseAction(act)"
                    >
                      {{
                        act.id === "save_config" && saving ? "保存中…" : act.label
                      }}
                    </button>
                    <span v-if="saveHint" class="cfg-hint">{{ saveHint }}</span>
                  </div>
                </div>

                <div v-if="detailTab === 'config'" class="cfg-pane" :aria-disabled="blockingLoad">
                  <div v-if="configChapterMetaBanner.length" class="cfg-meta-banner span-2">
                    <span
                      v-for="(line, bi) in configChapterMetaBanner"
                      :key="`cfg-meta-${bi}`"
                      class="cfg-meta-line"
                    >{{ line }}</span>
                  </div>
                  <div class="cfg-grid" :class="{ 'is-ops-locked': blockingLoad }">
                    <label>
                      <span>章节序号</span>
                      <input
                        v-model="configDraft.sequence"
                        class="cfg-input"
                        placeholder="如 1.1"
                        :disabled="blockingLoad"
                      />
                    </label>
                    <label>
                      <span>章节标题</span>
                      <input
                        v-model="configDraft.title"
                        class="cfg-input"
                        placeholder="本章标题"
                        :disabled="blockingLoad"
                      />
                    </label>
                    <label class="span-2">
                      <span>上级节点</span>
                      <el-select
                        v-model="configDraft.parent"
                        clearable
                        filterable
                        placeholder="无（顶级）"
                        class="cfg-parent-select"
                        :disabled="blockingLoad"
                      >
                        <el-option
                          v-for="item in configParentOptions"
                          :key="item.key"
                          :label="`${item.sequence || '—'} ${item.title || item.key}`"
                          :value="item.key"
                        />
                      </el-select>
                    </label>
                    <el-checkbox
                      v-model="configDraft.structure_only"
                      class="cfg-switch span-2"
                      :disabled="blockingLoad"
                    >
                      仅目录节点（不解析 / 不写正文）
                    </el-checkbox>
                    <label v-if="configDraft.structure_only" class="span-2">
                      <span>章节说明</span>
                      <textarea
                        v-model="configDraft.describes"
                        class="cfg-input"
                        rows="2"
                        placeholder="目录节点说明"
                        :disabled="blockingLoad"
                      />
                    </label>
                  </div>
                </div>

                <div v-else-if="detailTab === 'content'" class="cfg-pane" :aria-disabled="blockingLoad">
                  <div v-if="configChapterMetaBanner.length" class="cfg-meta-banner span-2">
                    <span
                      v-for="(line, bi) in configChapterMetaBanner"
                      :key="`cfg-meta-c-${bi}`"
                      class="cfg-meta-line"
                    >{{ line }}</span>
                  </div>
                  <div class="cfg-grid" :class="{ 'is-ops-locked': blockingLoad }">
                    <template v-if="configDraft.structure_only">
                      <p class="cfg-hint span-2">仅目录节点无需配置写作要点；可在「基本配置」中编辑说明。</p>
                    </template>
                    <template v-else>
                      <div class="cfg-point-items span-2">
                        <div class="cfg-point-items-head">
                          <span>写作要点</span>
                          <button
                            type="button"
                            class="cfg-el-manage"
                            :disabled="blockingLoad"
                            @click="addConfigPointRow"
                          >
                            添加一行
                          </button>
                        </div>
                        <div class="cfg-point-table-wrap">
                          <table v-if="configDraft.point_items.length" class="cfg-point-table">
                            <thead>
                              <tr>
                                <th class="col-idx">#</th>
                                <th class="col-text">要点内容</th>
                                <th class="col-slot">待补项</th>
                                <th class="col-el">关联要素</th>
                                <th class="col-state">状态</th>
                                <th class="col-op" aria-label="操作" />
                              </tr>
                            </thead>
                            <tbody>
                              <tr
                                v-for="(row, pi) in configDraft.point_items"
                                :key="String(row.id || pi)"
                              >
                                <td class="col-idx">{{ pi + 1 }}</td>
                                <td class="col-text">
                                  <span
                                    class="cfg-point-text"
                                    :title="String(row.text || '')"
                                  >{{ row.text || "—" }}</span>
                                </td>
                                <td class="col-slot">
                                  <span
                                    class="cfg-point-slot"
                                    :title="String(row.slot || '')"
                                  >{{ row.slot || "—" }}</span>
                                </td>
                                <td class="col-el">
                                  <button
                                    v-if="rowNeedsPointAssociate(row)"
                                    type="button"
                                    class="cfg-point-link-el"
                                    :disabled="blockingLoad || !taskId"
                                    @click="associatePointItemElement(row, pi)"
                                  >
                                    关联要素
                                  </button>
                                  <button
                                    v-else-if="pointItemElementLabel(row)"
                                    type="button"
                                    class="cfg-point-el-btn"
                                    :title="pointItemElementLabel(row)"
                                    :disabled="saving || busy"
                                    @click="openPointLinkedElement(row)"
                                  >
                                    {{ pointItemElementLabel(row) }}
                                  </button>
                                  <span v-else class="muted">—</span>
                                </td>
                                <td class="col-state">
                                  <span
                                    v-if="row.needs_supplement"
                                    class="cfg-point-badge warn"
                                  >待补充</span>
                                  <span v-else class="muted">就绪</span>
                                </td>
                                <td class="col-op">
                                  <button
                                    type="button"
                                    class="cfg-point-edit"
                                    :disabled="blockingLoad"
                                    @click="openPointItemEditor(pi)"
                                  >
                                    编辑
                                  </button>
                                  <button
                                    type="button"
                                    class="cfg-point-del"
                                    :disabled="blockingLoad"
                                    @click="removeConfigPointRow(pi)"
                                  >
                                    删除
                                  </button>
                                </td>
                              </tr>
                            </tbody>
                          </table>
                          <p v-else class="cfg-hint">暂无要点。可手动添加，或先「保存并分析本章」生成。</p>
                        </div>
                      </div>
                      <div v-if="configReferenceKeywords.length" class="cfg-ref-keywords span-2">
                        <div class="cfg-ref-keywords-head">
                          <span>参考检索词</span>
                          <span class="cfg-ref-keywords-hint">分析生成 · 仅供参考</span>
                        </div>
                        <div class="cfg-ref-keywords-tags">
                          <span
                            v-for="(kw, ki) in configReferenceKeywords"
                            :key="`${ki}-${kw}`"
                            class="cfg-ref-kw"
                          >{{ kw }}</span>
                        </div>
                      </div>
                      <label class="span-2">
                        <span>章节说明（写作背景）</span>
                        <textarea
                          v-model="configDraft.describes"
                          class="cfg-input"
                          rows="2"
                          :disabled="blockingLoad"
                        />
                      </label>
                      <label class="span-2">
                        <span>补充提示词</span>
                        <textarea
                          v-model="configDraft.aux_prompt"
                          class="cfg-input"
                          rows="2"
                          placeholder="写法/体裁约束，勿写工程名、投资额等项目元数据。例：只输出表格，不要导语"
                          :disabled="blockingLoad"
                        />
                      </label>
                      <el-checkbox
                        v-model="configDraft.fixed_output"
                        class="cfg-switch span-2"
                        :disabled="blockingLoad"
                      >
                        固定正文输出
                      </el-checkbox>
                      <label v-if="configDraft.fixed_output" class="span-2">
                        <span>固定正文</span>
                        <textarea
                          v-model="configDraft.reference_text"
                          class="cfg-input"
                          rows="3"
                          :disabled="blockingLoad"
                        />
                      </label>
                      <label v-else class="span-2">
                        <span>参考正文</span>
                        <textarea
                          v-model="configDraft.reference_text"
                          class="cfg-input"
                          rows="2"
                          placeholder="可选，供写作参考"
                          :disabled="blockingLoad"
                        />
                      </label>
                    </template>
                    <div
                      v-if="selectedChapterWriteProcess.length || selectedChapterEvidence.length"
                      class="write-collect-panel span-2"
                    >
                      <div v-if="selectedChapterWriteProcess.length" class="write-collect-block">
                        <div class="write-collect-head">写作过程</div>
                        <ol class="write-collect-steps">
                          <li
                            v-for="(row, ri) in selectedChapterWriteProcess"
                            :key="`${row.step || ri}-${row.status || ''}`"
                            :class="String(row.status || '')"
                          >
                            <strong>{{ row.step_label || row.step || "步骤" }}</strong>
                            <span class="st">{{ row.status_label || row.status || "" }}</span>
                            <ul v-if="(row.notes || []).length" class="write-collect-notes">
                              <li v-for="(n, ni) in row.notes" :key="ni">{{ n }}</li>
                            </ul>
                            <p v-else-if="row.note" class="write-collect-note">{{ row.note }}</p>
                          </li>
                        </ol>
                      </div>
                      <div v-if="selectedChapterEvidence.length" class="write-collect-block">
                        <div class="write-collect-head">
                          要素收集 · {{ selectedChapterEvidence.length }}
                        </div>
                        <ul class="write-collect-evidence">
                          <li v-for="(ev, ei) in selectedChapterEvidence" :key="ev.id || ei">
                            <div class="ev-title">
                              <span class="ev-name">{{ ev.point || "要素" }}</span>
                              <span v-if="ev.location" class="ev-kind">{{ elementKindLabel(ev.location) }}</span>
                            </div>
                            <p v-if="ev.query" class="ev-query">检索词：{{ ev.query }}</p>
                            <pre v-if="ev.excerpt" class="ev-excerpt">{{ ev.excerpt }}</pre>
                          </li>
                        </ul>
                      </div>
                    </div>
                  </div>
                </div>
              </template>
              <p v-else class="doc-win-empty sm">在左侧点一章，查看并修改配置。</p>
            </section>
          </div>
        </template>
        <div
          v-if="maskVisible"
          class="doc-win-mask"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <div class="doc-win-mask-card">
            <span class="doc-win-spinner" aria-hidden="true" />
            <strong>{{ maskTitle }}</strong>
            <p>{{ maskHint }}</p>
          </div>
        </div>
      </div>
      <div
        v-if="!docked"
        class="doc-win-resize"
        title="拖动调整大小"
        @mousedown.prevent="startResize"
      />

    <el-dialog
      v-model="elementDialogVisible"
      :title="elementDraft?.name ? `要素 · ${elementDraft.name}` : '查看 / 修改要素'"
      width="760px"
      destroy-on-close
      append-to-body
      align-center
      class="doc-win-element-dialog"
      @closed="onElementDialogClosed"
    >
      <el-form v-if="elementDraft" label-position="top">
        <ElementConfigFields
          v-model="elementDraft"
          :ref-target-options="elementRefTargetOptions"
          :pack-slot-options="packSlotSelectOptions"
          :task-id="taskId"
          :element-id="elementDraft?.id"
        />
      </el-form>
      <template #footer>
        <el-button
          type="danger"
          plain
          :disabled="saving || !elementDraft?.id"
          @click="unlinkCurrentElement"
        >
          取消关联
        </el-button>
        <el-button @click="elementDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="saving" :disabled="saving" @click="saveElementDraft">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="libraryPendingDialogVisible"
      title="待收录要素"
      width="720px"
      destroy-on-close
      append-to-body
      align-center
      class="doc-win-library-pending-dialog"
      @closed="onLibraryPendingDialogClosed"
    >
      <ChatElementLibraryPendingCard
        v-if="libraryPendingDialogVisible && libraryPendingCard"
        :card="libraryPendingCard"
        :session-id="sessionId"
        @changed="onLibraryPendingCardChanged"
        @done="onLibraryPendingDone"
      />
    </el-dialog>

    <el-dialog
      v-model="restudyDialogVisible"
      title="重新分析本章"
      width="640px"
      destroy-on-close
      append-to-body
      align-center
      class="doc-win-restudy-dialog"
      @closed="onRestudyDialogClosed"
    >
      <div class="restudy-hero">
        <strong>{{ restudyTarget.label || "当前章节" }}</strong>
        <p>选择分析方式。根据原文分析时，请把本章内容粘贴到文本框。</p>
      </div>
      <div class="restudy-modes">
        <button
          type="button"
          class="restudy-mode"
          :class="{ active: restudyMethod === 'source' }"
          @click="restudyMethod = 'source'"
        >
          <span class="restudy-mode-title">根据原文内容分析</span>
          <span class="restudy-mode-desc">粘贴本章原文，按正文提炼写作要点</span>
        </button>
        <button
          type="button"
          class="restudy-mode"
          :class="{ active: restudyMethod === boundRestudyMethod }"
          @click="restudyMethod = boundRestudyMethod"
        >
          <span class="restudy-mode-title">{{ hasSample ? "依据样本分析" : "自动分析" }}</span>
          <span class="restudy-mode-desc">{{
            hasSample
              ? sampleNameLocal
                ? `按已解析样本「${sampleNameLocal}」定位本章`
                : "按已上传样板定位本章正文"
              : "没有样本时由模型直接起草本章写作要点"
          }}</span>
        </button>
      </div>
      <div v-if="restudyMethod === 'source'" class="restudy-pane">
        <div
          ref="restudyPasteBox"
          class="restudy-rich-paste"
          contenteditable="true"
          data-placeholder="在此粘贴本章原文（从 Word / 网页复制即可）…"
          @input="onRestudyPasteInput"
          @paste="onRestudyRichPaste"
        />
      </div>
      <template #footer>
        <el-button @click="restudyDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="restudyMethod === 'source' && !restudyPasteText.trim()"
          @click="submitRestudyDialog"
        >
          开始分析
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="pointItemDialogVisible"
      title="编辑写作要点"
      width="520px"
      destroy-on-close
      append-to-body
      align-center
      class="doc-win-point-item-dialog"
      @closed="onPointItemDialogClosed"
    >
      <el-form label-position="top" class="point-item-edit-form">
        <el-form-item label="要点内容">
          <el-input
            v-model="pointItemEditDraft.text"
            type="textarea"
            :rows="4"
            placeholder="写作要点描述"
            :disabled="blockingLoad"
          />
        </el-form-item>
        <el-form-item label="待补项">
          <el-input
            v-model="pointItemEditDraft.slot"
            placeholder="待补项名称，如：投资额"
            :disabled="blockingLoad"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pointItemDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="blockingLoad" @click="savePointItemEditor">
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";

import { recoverClipboardPlain } from "../chatHtml";
import { authHeaders, request } from "../api";
import {
  elementKindLabel,
  elementSummary,
  kvPairsFromElement,
  normalizeKvElementDraft,
} from "../elementUi";
import {
  humanLabel,
  linkPointItemsToElements,
  parseSlotFromText,
  elementIdsFromPointItems,
  pickPointItemsAuthoritative,
  pickRicherPointItems,
  pointItemsFromChapter,
  pointItemsSignature,
  pointsFromPointItems,
  syncPointItemsFromPoints,
  textNeedsSupplement,
} from "../pointItemsSync";
import {
  documentWindowOptionLabel,
  hasWritingCopyWithLibrarySource,
  isWritingCopyId,
  resolveLibraryBlueprintId,
} from "../utils/documentBlueprintIdentity";
import ChatElementLibraryPendingCard from "./ChatElementLibraryPendingCard.vue";
import ElementConfigFields from "./ElementConfigFields.vue";
import MaterialsSlotCards from "./MaterialsSlotCards.vue";
import OutlineWorkspace from "./OutlineWorkspace.vue";

function documentOptionLabel(doc) {
  return documentWindowOptionLabel(doc);
}

const STATUS_LABELS = {
  pending: "未开写",
  searching: "检索中",
  writing: "写作中",
  checking: "检查中",
  awaiting_confirm: "已写作",
  confirmed: "已写作",
  needs_human: "已写作",
  skipped: "已跳过",
  studying: "分析中",
  running: "进行中",
  completed: "已完成",
  done: "完成",
  accept: "通过",
  error: "失败",
};

function statusLabel(value) {
  const key = String(value || "").trim();
  return STATUS_LABELS[key] || key || "";
}

function statusTone(value) {
  const key = String(value || "").trim();
  if (key === "confirmed") return "ok";
  if (key === "awaiting_confirm") return "confirm";
  if (key === "needs_human" || key === "skipped" || key === "error") return "warn";
  if (key === "searching" || key === "writing" || key === "checking" || key === "running") return "run";
  return "";
}

function emptyConfigDraft() {
  return {
    sequence: "",
    title: "",
    parent: "",
    point_items: [],
    describes: "",
    aux_prompt: "",
    reference_text: "",
    fixed_output: false,
    structure_only: false,
  };
}

const props = defineProps({
  documents: { type: Array, default: () => [] },
  activeId: { type: String, default: "" },
  busy: { type: Boolean, default: false },
  /** 父级同步/关窗时的遮罩文案（仅小窗内，不盖整页） */
  busyLabel: { type: String, default: "" },
  /** 分析/写作进行中：禁止章节操作，并盖遮罩 */
  opsLocked: { type: Boolean, default: false },
  opsLockLabel: { type: String, default: "" },
  /** 父级发起关窗时为 true，小窗内显示关闭遮罩直至卸掉 */
  forceClosing: { type: Boolean, default: false },
  draftText: { type: String, default: "" },
  /** 学习计数等软刷新：可走缓存 */
  outlineReloadToken: { type: Number, default: 0 },
  /** 配置/分析/写作操作完成后父组件递增：强制 fresh 直读库 */
  outlineForceReloadToken: { type: Number, default: 0 },
  /** 父组件 DocWindowDriver 定位指令 */
  locate: {
    type: Object,
    default: () => ({
      token: 0,
      taskId: "",
      chapterKey: "",
      tab: "config",
      mainView: "",
      reason: "",
      elementId: "",
      associateKnowledge: false,
    }),
  },
  /** step | batch | all — 影响是否展示「确认本章」 */
  writingPace: { type: String, default: "step" },
  /** 无蓝本写作：禁止「依据样本学习」，改为生成写作要点 */
  isFreeform: { type: Boolean, default: false },
  /** 尚未入库：可「收录到蓝本库」 */
  canSaveAsBlueprint: { type: Boolean, default: false },
  /** 已是库蓝本的文档副本（w*）：可「同步到蓝本库」 */
  canSyncToBlueprintLibrary: { type: Boolean, default: false },
  /** 侧栏并排停靠：不遮挡会话，占满父容器 */
  docked: { type: Boolean, default: true },
  /** 开写前会话预建/预选的资料包 id（按当前蓝本解析后传入） */
  pendingMaterialsPackId: { type: String, default: "" },
  /** 当前会话 id：收入/暂不收录待办需要 */
  sessionId: { type: String, default: "" },
  /** 对话所选机器人：限定要素库/资料包范围 */
  robotId: { type: String, default: "" },
  /** 会话里待收入个人要素库的队列（runtime.element_library_pending） */
  elementLibraryPending: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "close",
  "update:activeId",
  "focus-chapter",
  "config-saved",
  "elements-saved",
  "update:draftText",
  "write-action",
  "wizard-action",
  "sync-request",
  "project-folders-saved",
  "library-pending-changed",
  "rename-document",
  /** 开写前预建/换绑资料包：父级发 materials_pack_* 写入会话 pending */
  "materials-pack-pending",
]);

const renameBusy = ref(false);

const robotId = computed(() => String(props.robotId || "").trim());

async function renameCurrentDocument() {
  const doc = activeDoc.value;
  if (!doc || maskVisible.value || props.busy || renameBusy.value) return;
  const prev = String(doc.title || "").trim();
  try {
    const { value } = await ElMessageBox.prompt(
      "文档打算叫什么？可写类型和题目，例如：述职报告｜2026年度个人述职",
      "修改文档名称",
      {
        confirmButtonText: "确定",
        cancelButtonText: "取消",
        inputValue: prev,
        inputPlaceholder: "文档名称",
      }
    );
    const title = String(value || "").trim();
    if (!title) {
      ElMessage.warning("请填写文档名称");
      return;
    }
    if (title === prev) return;
    renameBusy.value = true;
    // 改的是会话「文档名」；有写作 clone 优先带 writingTaskId，避免误改蓝本库条目
    const wid = String(doc.writingTaskId || "").trim();
    const kind = String(doc.kind || "").trim();
    const bid = String(doc.blueprintId || "").trim();
    const tid = String(doc.taskId || "").trim();
    emit("rename-document", {
      taskId: wid || (kind === "blueprint" ? "" : tid) || "",
      writingTaskId: wid,
      blueprintId: bid,
      title,
      previousTitle: prev,
      kind,
      docId: String(doc.id || "").trim(),
      renameDocumentOnly: true,
    });
  } catch {
    /* 取消 */
  } finally {
    renameBusy.value = false;
  }
}

function initialBox() {
  const w = Math.min(940, Math.max(700, window.innerWidth - 72));
  const h = Math.min(660, Math.max(480, window.innerHeight - 96));
  return {
    x: Math.max(12, window.innerWidth - w - 16),
    y: 36,
    w,
    h,
  };
}

const zoom = ref(1);
const box = ref(initialBox());
const selectedKey = ref("");
/** 左侧勾选：批量分析 / 写作（el-tree 父子联动） */
const chapterOutlineTreeRef = ref(null);
const checkedKeys = ref([]);
const checkedKeySet = computed(() => new Set(checkedKeys.value));
const outlineTreeProps = {
  label: "title",
  children: "children",
  disabled: "checkDisabled",
};
const extraChapters = ref([]);
const outlineNodes = ref([]);
const elementNodes = ref([]);
const outlineConfirmedLocal = ref(false);
const sampleReadyLocal = ref(false);
const sampleNameLocal = ref("");
const restudyDialogVisible = ref(false);
const restudyMethod = ref("auto");
const restudyPasteText = ref("");
const restudyPasteBox = ref(null);
const restudyTarget = ref({ keys: [], sequences: [], label: "", key: "" });
const loading = ref(true); // 打开即遮罩，等首轮读数结束再关
const outlineFullyLoaded = ref(false);
const saving = ref(false);
const closing = ref(false);
const tabSwitching = ref(false);
const tabSwitchLabel = ref("正在切换视图…");
const saveHint = ref("");
const loadLabel = ref("正在打开文档窗口…");
let tabSwitchTimer = 0;
const isClosing = computed(() => closing.value || Boolean(props.forceClosing));
/** 打开 / 关闭 / 读数 / 保存 / 写作分析锁：遮小窗本身。切 tab 不再遮罩（否则会连带重算 200+ 章树导致卡顿） */
const maskVisible = computed(
  () =>
    loading.value ||
    saving.value ||
    isClosing.value ||
    (Boolean(props.busy) && !props.opsLocked)
);
const maskTitle = computed(() => {
  if (isClosing.value) return "正在关闭文档窗口…";
  if (saving.value) {
    const hint = String(saveHint.value || "").trim();
    if (hint && !hint.includes("已")) return hint;
    return "正在保存…";
  }
  if (props.opsLocked) {
    const label = String(props.opsLockLabel || "").trim();
    if (/写作|书写/.test(label)) return label || "正在写作…";
    if (/分析|学习|要点|解析/.test(label)) return label || "正在分析…";
    return label || "请稍候…";
  }
  if (props.busy) {
    const label = String(props.busyLabel || "").trim();
    return label || "请稍候…";
  }
  return loadLabel.value || "正在加载…";
});
const maskHint = computed(() => {
  if (isClosing.value) return "正在同步本地修改，请稍候";
  if (saving.value) return "请稍候，保存完成前请勿切换章节";
  if (props.opsLocked) {
    const label = String(props.opsLockLabel || "").trim();
    if (/写作|书写/.test(label)) return "写作进行中，完成后可继续操作";
    if (/分析|学习|要点|解析/.test(label)) return "分析/要点进行中，请稍候";
    return "当前操作进行中，请稍候";
  }
  if (props.busy) return "请稍候";
  return "请稍候，读取完成前不可操作";
});
const maskLabel = computed(() => {
  if (isClosing.value) return "关闭中…";
  if (saving.value) return "保存中…";
  if (props.busy) return "处理中…";
  return "加载中…";
});
/** 加载/保存/分析中：禁用章节操作；切 tab 不计入，避免整树 checkDisabled 重算 */
const blockingLoad = computed(
  () =>
    loading.value ||
    saving.value ||
    isClosing.value ||
    Boolean(props.busy) ||
    Boolean(props.opsLocked)
);
/** 切顶栏/配置页签：加载保存中禁用；分析写作中仍可看拓扑/正文 */
const tabControlsDisabled = computed(
  () => loading.value || saving.value || isClosing.value || Boolean(props.busy)
);
/** 分析/写作/配置进行中：禁止切换其它文档 */
const docSwitchBlocked = computed(
  () =>
    Boolean(props.opsLocked) ||
    loading.value ||
    saving.value ||
    isClosing.value ||
    Boolean(props.busy)
);
const docSwitchBlockReason = computed(() => {
  if (saving.value) return "正在保存配置，请稍后再切换文档";
  if (props.opsLocked) {
    const label = String(props.opsLockLabel || "").trim();
    if (/写作|书写/.test(label)) return "正在写作，请稍后再切换文档";
    if (/分析|学习|要点|解析/.test(label)) return "正在分析/解析，请稍后再切换文档";
    return label ? `${label}，请稍后再切换文档` : "当前文档操作进行中，请稍后再切换";
  }
  if (loading.value || props.busy) return "文档加载中，请稍后再切换";
  return "当前不可切换文档";
});
const refreshDisabled = computed(
  () =>
    isClosing.value ||
    saving.value ||
    Boolean(props.opsLocked) ||
    Boolean(props.busy) ||
    loading.value
);
const opsStatusLabel = computed(() => {
  const label = String(props.opsLockLabel || "").trim();
  if (label.includes("写作") || label.includes("书写")) return "写作中…";
  if (label.includes("分析") || label.includes("学习")) {
    // 保留「正在分析 1/3 · 2.2 建设规模」这类细进度
    if (/\d+\s*\/\s*\d+/.test(label) || label.includes("·")) return label;
    return "分析中…";
  }
  return label || "处理中…";
});
const opsLockDetail = computed(() => {
  if (!props.opsLocked) return "";
  const label = String(props.opsLockLabel || "").trim();
  if (!label) return "";
  if (label.includes("分析") || label.includes("学习") || label.includes("写作")) return label;
  return "";
});
/** 当前正在分析的章节（文档窗定位 analyze 时写入） */
const analyzingKey = ref("");
const detailTab = ref("config");
const mainView = ref("chapter");
/** 全文预览：按当前文档 writingTaskId/taskId 隔离缓存 */
const previewLoading = ref(false);
const previewError = ref("");
const previewHtml = ref("");
const previewWrittenCount = ref(0);
const previewChapterCount = ref(0);
const previewLoadedTid = ref("");
const previewCacheByTask = new Map();
const topologyFocus = ref(0);
/** 仅目录结构变化时递增；切 tab 不再整图 remount */
const topologyRenderKey = ref(0);
/** 首次进入拓扑后再挂载 VueFlow，之后用 v-show 保活 */
const topologyEverOpened = ref(false);
const configDraft = reactive(emptyConfigDraft());
/** 按 taskId 缓存目录/要素，切回时先恢复本蓝本，禁止用其它蓝本填充 */
const outlineCacheByTask = new Map();
let drag = null;
let fetchToken = 0;
let outlineInflight = null;
let outlineInflightTid = "";
let outlineLoadTimer = 0;
/** 当前本地 outline 所属 taskId，切文档时用于落盘缓存与清空 */
let loadedOutlineTaskId = "";

/** activeId 已指定但列表尚未登记时返回 null，禁止回落 documents[0]（否则名是新文档、大纲仍是旧文档） */
const activeDoc = computed(() => {
  const id = String(props.activeId || "").trim();
  const docs = props.documents || [];
  if (id) {
    const hit = docs.find((item) => item.id === id);
    return hit || null;
  }
  return docs[0] || null;
});
const taskId = computed(() =>
  String(activeDoc.value?.taskId || activeDoc.value?.blueprintId || "").trim()
);
/** 写作 clone 优先；无则回落蓝本/任务 id（后端 draft-preview 会对蓝本再解析 clone） */
function resolveWritingTaskId(doc = activeDoc.value) {
  return String(
    doc?.writingTaskId || doc?.taskId || doc?.blueprintId || ""
  ).trim();
}
/**
 * 目录/配置真源：有文档副本（w*）只读写副本；库 bp* 仅作选用源，改动靠「同步到蓝本库」。
 */
function outlineSourceTaskId(doc = activeDoc.value) {
  const wid = String(doc?.writingTaskId || "").trim();
  if (wid) return wid;
  return String(doc?.taskId || doc?.blueprintId || "").trim();
}
/**
 * 要素/章节配置落盘目标：有写作 clone 时只写 clone，禁止双写库蓝本（否则刷新又从 bp* 盖回）。
 */
function configPersistTaskIds() {
  const tid = outlineSourceTaskId();
  return tid ? [tid] : [];
}
/** 预览/正文真值优先写作 clone，避免蓝本目录与另一份文档串稿 */
const previewTaskId = computed(() => resolveWritingTaskId(activeDoc.value));
const previewMetaLabel = computed(() => {
  const title = String(activeDoc.value?.title || "").trim() || "当前文档";
  if (previewLoading.value && !previewHtml.value) return `${title} · 加载中`;
  if (previewWrittenCount.value > 0) {
    return `${title} · 已写 ${previewWrittenCount.value}/${previewChapterCount.value || "—"} 章`;
  }
  return `${title} · 暂无正文`;
});
const isWritingDoc = computed(() => {
  const kind = String(activeDoc.value?.kind || "");
  return kind === "writing" || kind === "outline";
});
/** 无蓝本·依据样本支线（文档项带 wizardMode / 步骤条，但未入库） */
const isFreeformSampleDoc = computed(() => {
  const mode = String(activeDoc.value?.wizardMode || "").trim();
  if (mode === "freeform_sample" || mode === "create") return true;
  if (props.isFreeform && String(activeDoc.value?.wizardStep || "").trim()) return true;
  return false;
});
/** 库蓝本 / 选用副本：走依据样本学习；无蓝本·依据样本也有同款步骤条，但不算库蓝本 */
const isBlueprintDoc = computed(() => {
  if (isFreeformSampleDoc.value) return false;
  if (props.isFreeform) return false;
  const kind = String(activeDoc.value?.kind || "");
  if (kind === "outline" || kind === "writing") return false;
  return kind === "blueprint";
});
/** 纯无蓝本自动大纲（生成写作要点）；依据样本支线不算这里 */
const isFreeformDoc = computed(() => {
  if (isFreeformSampleDoc.value) return false;
  return props.isFreeform || String(activeDoc.value?.kind || "") === "outline";
});
/** 库蓝本或无蓝本·依据样本：共用「确认目录 / 依据样本分析」操作 */
const isSampleStudyDoc = computed(() => isBlueprintDoc.value || isFreeformSampleDoc.value);
const hasSample = computed(() => Boolean(sampleReadyLocal.value));
const boundRestudyMethod = computed(() => (hasSample.value ? "sample" : "auto"));

/** 蓝本进度步进：只读当前文档的 wizardStep，不串其它报告 */
const WIZARD_STEPPER = [
  { id: "meta", label: "填写名称", match: ["meta"] },
  { id: "upload", label: "上传样板", match: ["upload", "upload_done"] },
  { id: "outline", label: "提取目录", match: ["outline", "outline_done"] },
  { id: "outline_confirm", label: "确认目录", match: ["outline_confirm"] },
  { id: "study", label: "学习分析", match: ["study", "study_done", "done"] },
];
const WIZARD_STEPPER_ORDER = [
  "meta",
  "upload",
  "upload_done",
  "outline",
  "outline_done",
  "outline_confirm",
  "study",
  "study_done",
  "done",
];

function wizardStepRank(step) {
  const s = String(step || "").trim();
  const i = WIZARD_STEPPER_ORDER.indexOf(s);
  return i >= 0 ? i : -1;
}

const wizardStepperSteps = computed(() => {
  // 库蓝本创建/选用，或无蓝本·依据样本：都展示上传→提取→确认→分析步骤条
  if (!isBlueprintDoc.value && !isFreeformSampleDoc.value) return [];
  const cur = String(activeDoc.value?.wizardStep || "").trim();
  if (!cur) return [];
  const curRank = wizardStepRank(cur);
  const studyComplete =
    String(activeDoc.value?.study_mode || "").trim() === "complete" ||
    cur === "done" ||
    cur === "study_done";
  return WIZARD_STEPPER.map((row) => {
    const ranks = row.match.map(wizardStepRank).filter((n) => n >= 0);
    const rowRank = ranks.length ? Math.min(...ranks) : -1;
    if (row.id === "study" && studyComplete) {
      return { id: row.id, label: row.label, current: false, done: true };
    }
    const current = row.match.includes(cur);
    const done = !current && curRank > rowRank && rowRank >= 0;
    return { id: row.id, label: row.label, current, done };
  });
});
/** 任务真值 + 向导已到学习分析时对齐，避免小窗还卡在「待确认」 */
const outlineConfirmed = computed(() => {
  if (outlineConfirmedLocal.value) return true;
  if (activeDoc.value?.outlineConfirmed) return true;
  const step = String(activeDoc.value?.wizardStep || "");
  return step === "study" || step === "study_done" || step === "done";
});
const topologyOutline = computed(() => {
  // 全量 outlineNodes 未到时，先用文档预览章节（向导卡 / live）画拓扑，避免「同步中」空窗卡住
  if (outlineNodes.value.length) return { chapters: outlineNodes.value };
  if (chapters.value.length) return { chapters: chapters.value };
  return null;
});

/** 关联 id：优先非空列表。文档/进度卡常带空 []，不能用 || 盖住大纲刚写入的关联 */
function pickElementIds(...candidates) {
  let fallback = null;
  for (const raw of candidates) {
    if (!Array.isArray(raw)) continue;
    const ids = raw.map((id) => String(id || "").trim()).filter(Boolean);
    if (fallback === null) fallback = ids;
    if (ids.length) return ids;
  }
  return fallback ? [...fallback] : [];
}

/** 关联确认后、目录尚未拉齐时暂存；拉齐/刷新时合并，避免标签被空数据冲掉 */
const pendingChapterElementIds = new Map();

function rememberPendingElementIds(chapterKey, ids) {
  const key = String(chapterKey || "").trim();
  if (!key) return;
  const list = (ids || []).map((id) => String(id || "").trim()).filter(Boolean);
  if (list.length) pendingChapterElementIds.set(key, list);
  else pendingChapterElementIds.delete(key);
}

function mergeElementNodesLocal(prev, next) {
  const byId = new Map();
  for (const el of prev || []) {
    const id = String(el?.id || "").trim();
    if (id) byId.set(id, { ...el });
  }
  for (const el of next || []) {
    const id = String(el?.id || "").trim();
    if (!id) continue;
    const prevEl = byId.get(id) || {};
    const merged = { ...prevEl, ...el };
    const keepName = humanLabel(el?.name, prevEl?.name);
    if (keepName) merged.name = keepName;
    else if (humanLabel(prevEl?.name)) merged.name = String(prevEl.name).trim();
    // 服务端偶发带回空 file_slots 时保留本地已配引用
    const prevSlots = Array.isArray(prevEl.file_slots) ? prevEl.file_slots : [];
    const nextSlots = Array.isArray(el.file_slots) ? el.file_slots : [];
    const prevHas = prevSlots.some((s) => Number(s?.no) > 0);
    const nextHas = nextSlots.some((s) => Number(s?.no) > 0);
    if (prevHas && !nextHas) merged.file_slots = prevSlots.map((s) => ({ ...s }));
    else if (nextHas) merged.file_slots = nextSlots.map((s) => ({ ...s }));
    byId.set(id, merged);
  }
  return [...byId.values()];
}

function pickRicherList(nextVal, prevVal) {
  const next = Array.isArray(nextVal) ? nextVal : null;
  const prev = Array.isArray(prevVal) ? prevVal : null;
  if (next?.length) return [...next];
  if (prev?.length) return [...prev];
  if (next) return [...next];
  if (prev) return [...prev];
  return [];
}

function pickRicherText(nextVal, prevVal) {
  const next = String(nextVal ?? "").trim();
  const prev = String(prevVal ?? "").trim();
  return next || prev || "";
}

function mergeOutlinePreserveElementIds(prevList, nextList, { keepMissing = false } = {}) {
  const prevByKey = new Map(
    (prevList || []).map((ch) => [String(ch?.key || "").trim(), ch]).filter(([k]) => k)
  );
  const writtenStatuses = new Set(["confirmed", "awaiting_confirm", "needs_human"]);
  const mergeOne = (ch) => {
    const key = String(ch?.key || "").trim();
    const prev = prevByKey.get(key);
    const pending = pendingChapterElementIds.get(key);
    const ids = pickElementIds(
      pending,
      ch?.element_ids,
      ch?.config?.element_ids,
      prev?.element_ids,
      prev?.config?.element_ids
    );
    const nextStatus = String(ch?.status || "").trim();
    const prevStatus = String(prev?.status || "").trim();
    const prevWritten =
      Boolean(prev?.has_draft) ||
      Boolean(prev?.draft_text) ||
      Boolean(prev?.draft_preview) ||
      writtenStatuses.has(prevStatus);
    const nextWritten =
      Boolean(ch?.has_draft) ||
      Boolean(ch?.draft_text) ||
      Boolean(ch?.draft_preview) ||
      writtenStatuses.has(nextStatus);
    // 软刷新偶发丢写作态时，保留本地已写作标记，避免目录又变回「已分析」
    const keepWritten = prevWritten && !nextWritten;
    // 进度卡/文档预览常把要点放在 config.points，顶层可能是空数组：取最「满」的一份
    const points = pickRicherList(
      ch?.points,
      pickRicherList(
        ch?.config?.points,
        pickRicherList(prev?.points, prev?.config?.points)
      )
    );
    const keywords = pickRicherList(
      ch?.keywords,
      pickRicherList(ch?.config?.keywords, pickRicherList(prev?.keywords, prev?.config?.keywords))
    );
    const describes = pickRicherText(
      ch?.describes ?? ch?.config?.describes,
      prev?.describes ?? prev?.config?.describes
    );
    const auxPrompt = pickRicherText(
      ch?.aux_prompt ?? ch?.config?.aux_prompt,
      prev?.aux_prompt ?? prev?.config?.aux_prompt
    );
    const referenceText = pickRicherText(
      ch?.reference_text ?? ch?.config?.reference_text,
      prev?.reference_text ?? prev?.config?.reference_text
    );
    const point_items = pickPointItemsAuthoritative(
      [ch?.point_items, ch?.config?.point_items],
      prev?.point_items,
      prev?.config?.point_items
    );
    const structureOnly = Boolean(
      Object.prototype.hasOwnProperty.call(ch || {}, "structure_only")
        ? ch.structure_only
        : prev?.structure_only
    );
    const prevCfg = prev?.config && typeof prev.config === "object" ? prev.config : {};
    const nextCfg = ch?.config && typeof ch.config === "object" ? ch.config : {};
    return {
      ...prev,
      ...ch,
      points,
      point_items,
      keywords,
      describes,
      aux_prompt: auxPrompt,
      reference_text: referenceText,
      structure_only: structureOnly,
      element_ids: [...ids],
      config: {
        ...prevCfg,
        ...nextCfg,
        points,
        point_items,
        keywords,
        describes,
        aux_prompt: auxPrompt,
        reference_text: referenceText,
        structure_only: structureOnly,
        element_ids: [...ids],
      },
      ...(keepWritten
        ? {
            status: prevStatus || "confirmed",
            status_label: "已写作",
            has_draft: true,
            draft_text: prev?.draft_text || ch?.draft_text || "",
            draft_preview: prev?.draft_preview || ch?.draft_preview || "",
          }
        : {}),
    };
  };
  const mergedNext = (nextList || []).map(mergeOne);
  if (!keepMissing) return mergedNext;
  // 短预览/当前分析子树只覆盖同 key；已加载的其余章必须保留，且维持原顺序
  const nextByKey = new Map(
    mergedNext.map((ch) => [String(ch?.key || "").trim(), ch]).filter(([k]) => k)
  );
  const seen = new Set();
  const out = [];
  for (const prev of prevList || []) {
    const key = String(prev?.key || "").trim();
    if (key && nextByKey.has(key)) {
      out.push(nextByKey.get(key));
    } else {
      out.push(prev);
    }
    if (key) seen.add(key);
  }
  for (const ch of mergedNext) {
    const key = String(ch?.key || "").trim();
    if (key && !seen.has(key)) {
      out.push(ch);
      seen.add(key);
    }
  }
  return out;
}

/** 写完某章后立刻更新目录状态（不等待刷新） */
function markChapterWrittenLocal(chapterKey, extra = {}) {
  const key = String(chapterKey || "").trim();
  if (!key) return;
  const patch = (list) =>
    (list || []).map((ch) => {
      if (String(ch?.key || "") !== key) return ch;
      return {
        ...ch,
        status: "confirmed",
        status_label: "已写作",
        has_draft: true,
        ...(extra.draft_text
          ? { draft_text: extra.draft_text, draft_preview: String(extra.draft_text).slice(0, 400) }
          : {}),
      };
    });
  outlineNodes.value = patch(outlineNodes.value);
  extraChapters.value = patch(extraChapters.value);
}

/** 要点生成进度：立刻把本章 points 合入目录（与蓝本 study_chapter_progress 同节奏） */
function applyChapterPointsLocal(chapterKey, extra = {}) {
  const key = String(chapterKey || "").trim();
  if (!key) return;
  const points = Array.isArray(extra.points) ? [...extra.points] : null;
  const keywords = Array.isArray(extra.keywords) ? [...extra.keywords] : null;
  const describes =
    extra.describes != null && String(extra.describes).trim()
      ? String(extra.describes).trim()
      : null;
  if (!points?.length && !keywords?.length && describes == null) return;
  const patchOne = (ch) => {
    const nextPoints = points?.length ? points : pickRicherList(ch.points, ch.config?.points);
    const nextKeywords = keywords?.length
      ? keywords
      : pickRicherList(ch.keywords, ch.config?.keywords);
    const nextDescribes =
      describes != null ? describes : pickRicherText(ch.describes, ch.config?.describes);
    const cfg = ch.config && typeof ch.config === "object" ? ch.config : {};
    return {
      ...ch,
      key: ch.key || key,
      points: nextPoints,
      keywords: nextKeywords,
      describes: nextDescribes,
      structure_only: Boolean(extra.structure_only ?? ch.structure_only),
      config: {
        ...cfg,
        points: nextPoints,
        keywords: nextKeywords,
        describes: nextDescribes,
        structure_only: Boolean(extra.structure_only ?? cfg.structure_only ?? ch.structure_only),
      },
    };
  };
  const patch = (list) =>
    (list || []).map((ch) => (String(ch?.key || "") === key ? patchOne(ch) : ch));
  let nextOutline = patch(outlineNodes.value);
  if (!nextOutline.some((ch) => String(ch?.key || "") === key)) {
    const fromDoc = (activeDoc.value?.chapters || []).find((ch) => String(ch?.key || "") === key);
    nextOutline = [...nextOutline, patchOne(fromDoc || { key })];
  }
  outlineNodes.value = nextOutline;
  extraChapters.value = patch(extraChapters.value);
  if (String(selectedKey.value || "") === key) {
    hydrateConfigDraft();
  }
}

function applyChapterElementIdsLocal(chapterKey, ids, incomingElements, opts = {}) {
  const key = String(chapterKey || "").trim();
  if (!key) return;
  const list = (ids || []).map((id) => String(id || "").trim()).filter(Boolean);
  rememberPendingElementIds(key, list);
  if (Array.isArray(incomingElements) && incomingElements.length) {
    elementNodes.value = mergeElementNodesLocal(elementNodes.value, incomingElements);
  }
  const isPointScope =
    String(opts.associateScope || "").trim() === "point" ||
    Boolean(opts.pointItemLink || pendingPointItemLink.value);
  const linkOpts = {
    elementIds: isPointScope ? list : [],
    newElementIds: opts.newElementIds || [],
    pointLink: opts.pointItemLink || pendingPointItemLink.value || null,
    assignOrphans: false,
  };
  let linkedItems = null;
  if (isPointScope) {
    const baseItems =
      Array.isArray(opts.pointItems) && opts.pointItems.length
        ? opts.pointItems.map((row) => ({ ...row }))
        : configDraft.point_items.map((row) => ({ ...row }));
    if (baseItems.length) {
      linkedItems = linkPointItemsToElements(baseItems, elementNodes.value, {
        ...linkOpts,
        elementIds: list.length ? list : linkOpts.elementIds,
      });
    }
  }
  if (String(selectedKey.value || "") === key && linkedItems?.length) {
    configDraft.point_items.splice(0, configDraft.point_items.length, ...linkedItems);
    saveHint.value = "";
  }
  const chapterIds = isPointScope
    ? (outlineNodes.value.find((item) => String(item.key || "") === key)?.element_ids || []).map(
        (id) => String(id || "").trim()
      ).filter(Boolean)
    : list;
  if (isPointScope && !chapterIds.length) {
    const pending = pendingChapterElementIds.get(key);
    if (Array.isArray(pending) && pending.length) {
      chapterIds.push(...pending);
    }
  }
  const patchPointItems = (ch) => {
    if (!linkedItems?.length) return ch;
    return {
      ...ch,
      point_items: linkedItems.map((row) => ({ ...row })),
      points: pointsFromPointItems(linkedItems),
      config: {
        ...(ch.config && typeof ch.config === "object" ? ch.config : {}),
        point_items: linkedItems.map((row) => ({ ...row })),
        points: pointsFromPointItems(linkedItems),
      },
    };
  };
  const idx = outlineNodes.value.findIndex((item) => String(item.key || "") === key);
  if (idx >= 0) {
    outlineNodes.value = outlineNodes.value.map((item, i) =>
      i === idx
        ? patchPointItems({
            ...item,
            element_ids: [...chapterIds],
            config: {
              ...(item.config && typeof item.config === "object" ? item.config : {}),
              element_ids: [...chapterIds],
            },
          })
        : item
    );
  }
  // 目录尚未到位：写入 extraChapters 占位，等全量大纲合并时仍能回显
  if (idx < 0 && chapterIds.length) {
    const extraIdx = extraChapters.value.findIndex((item) => String(item.key || "") === key);
    if (extraIdx >= 0) {
      extraChapters.value = extraChapters.value.map((item, i) =>
        i === extraIdx ? patchPointItems({ ...item, element_ids: [...chapterIds] }) : item
      );
    }
  }
  if (!isPointScope) {
    rememberPendingElementIds(key, chapterIds);
  }
  pendingPointItemLink.value = null;
}

const chapters = computed(() => {
  const fromDoc = Array.isArray(activeDoc.value?.chapters) ? activeDoc.value.chapters : [];
  const progressByKey = new Map(
    fromDoc.filter((ch) => ch?.key).map((ch) => [String(ch.key), ch])
  );
  // 全量 outline 作骨架，写作状态/正文以 progress 预览合并（会话进度卡隐藏后小窗依赖此合并）
  const mergeWriteState = (base) => {
    const list = Array.isArray(base) ? base : [];
    if (!progressByKey.size) return list;
    return list.map((ch) => {
      const key = String(ch?.key || "");
      const prog = progressByKey.get(key);
      if (!prog) return ch;
      const baseCfg = ch.config && typeof ch.config === "object" ? ch.config : {};
      const progCfg = prog.config && typeof prog.config === "object" ? prog.config : {};
      // 列表字段取更「满」的一侧，禁止空 points [] 盖掉进度卡里已生成的要点
      const mergedPoints = pickRicherList(
        ch?.points,
        pickRicherList(baseCfg.points, pickRicherList(prog?.points, progCfg.points))
      );
      const mergedPointItems = pickPointItemsAuthoritative(
        [ch?.point_items, ch?.config?.point_items, baseCfg.point_items],
        prog?.point_items,
        progCfg.point_items
      );
      const mergedKeywords = pickRicherList(
        ch?.keywords,
        pickRicherList(baseCfg.keywords, pickRicherList(prog?.keywords, progCfg.keywords))
      );
      const mergedDescribes = pickRicherText(
        ch?.describes ?? baseCfg.describes,
        prog?.describes ?? progCfg.describes
      );
      const mergedAux = pickRicherText(
        ch?.aux_prompt ?? baseCfg.aux_prompt,
        prog?.aux_prompt ?? progCfg.aux_prompt
      );
      const mergedRef = pickRicherText(
        ch?.reference_text ?? baseCfg.reference_text,
        prog?.reference_text ?? progCfg.reference_text
      );
      const mergedConfig = {
        ...progCfg,
        ...baseCfg,
        points: mergedPoints,
        point_items: mergedPointItems,
        keywords: mergedKeywords,
        describes: mergedDescribes,
        aux_prompt: mergedAux,
        reference_text: mergedRef,
      };
      if (Object.prototype.hasOwnProperty.call(ch, "fixed_output")) {
        mergedConfig.fixed_output = ch.fixed_output;
      } else if (Object.prototype.hasOwnProperty.call(baseCfg, "fixed_output")) {
        mergedConfig.fixed_output = baseCfg.fixed_output;
      }
      if (Object.prototype.hasOwnProperty.call(ch, "structure_only")) {
        mergedConfig.structure_only = ch.structure_only;
      } else if (Object.prototype.hasOwnProperty.call(baseCfg, "structure_only")) {
        mergedConfig.structure_only = baseCfg.structure_only;
      } else if (Object.prototype.hasOwnProperty.call(prog, "structure_only")) {
        mergedConfig.structure_only = prog.structure_only;
      } else if (Object.prototype.hasOwnProperty.call(progCfg, "structure_only")) {
        mergedConfig.structure_only = progCfg.structure_only;
      }
      // 大纲优先：蓝本/进度章节 normalize 后常有空 element_ids，勿盖住 outline 已关联的 id
      const elementIds = pickElementIds(
        ch.element_ids,
        baseCfg.element_ids,
        prog.element_ids,
        progCfg.element_ids
      );
      // 进度卡只叠写作态；目录/要点/是否目录节点以 outline 真源为准，禁止 prog 旧值盖回
      const structureOnly = Boolean(
        Object.prototype.hasOwnProperty.call(mergedConfig, "structure_only")
          ? mergedConfig.structure_only
          : ch.structure_only
      );
      return {
        ...ch,
        ...prog,
        sequence: ch.sequence ?? prog.sequence,
        title: ch.title ?? prog.title,
        parent: ch.parent ?? prog.parent,
        parent_sequence: ch.parent_sequence ?? prog.parent_sequence,
        level: ch.level ?? prog.level,
        structure_only: structureOnly,
        fixed_output: Boolean(
          Object.prototype.hasOwnProperty.call(ch, "fixed_output")
            ? ch.fixed_output
            : Object.prototype.hasOwnProperty.call(mergedConfig, "fixed_output")
              ? mergedConfig.fixed_output
              : prog.fixed_output
        ),
        config: { ...mergedConfig, element_ids: [...elementIds], structure_only: structureOnly },
        element_ids: [...elementIds],
        points: mergedPoints,
        point_items: mergedPointItems,
        keywords: mergedKeywords,
        aux_prompt: mergedAux,
        reference_text: mergedRef,
        describes: mergedDescribes,
        draft_text: prog.draft_text || prog.draft_preview || ch.draft_text || "",
        draft_preview: prog.draft_preview || ch.draft_preview || "",
        status: (() => {
          const ps = String(prog.status || "").trim();
          const cs = String(ch.status || "").trim();
          const written = new Set(["confirmed", "awaiting_confirm", "needs_human"]);
          // 大纲已写作时，勿被进度卡旧 pending 盖回「已分析」
          if (written.has(cs) && !written.has(ps) && !prog.is_running) return cs;
          return ps || cs;
        })(),
        status_label: (() => {
          const ps = String(prog.status || "").trim();
          const cs = String(ch.status || "").trim();
          const written = new Set(["confirmed", "awaiting_confirm", "needs_human"]);
          if (written.has(cs) && !written.has(ps) && !prog.is_running) {
            return "已写作";
          }
          return prog.status_label || ch.status_label;
        })(),
        is_running: Boolean(prog.is_running),
        is_next: Boolean(prog.is_next),
        has_draft: Boolean(
          prog.has_draft ||
            prog.draft_text ||
            prog.draft_preview ||
            ch.has_draft ||
            ch.draft_text ||
            ch.draft_preview ||
            ["confirmed", "awaiting_confirm"].includes(String(ch.status || "")) ||
            ["confirmed", "awaiting_confirm"].includes(String(prog.status || ""))
        ),
        steps: prog.steps?.length ? prog.steps : ch.steps,
        checks: prog.checks?.length ? prog.checks : ch.checks,
        evidence: prog.evidence?.length ? prog.evidence : ch.evidence,
      };
    });
  };
  if (outlineNodes.value.length && outlineNodes.value.length >= fromDoc.length) {
    return mergeWriteState(outlineNodes.value);
  }
  if (!extraChapters.value.length) return mergeWriteState(fromDoc);
  const byKey = new Map();
  for (const ch of extraChapters.value) byKey.set(String(ch.key || ""), ch);
  for (const ch of fromDoc) byKey.set(String(ch.key || ""), { ...byKey.get(String(ch.key || "")), ...ch });
  const order = extraChapters.value.length >= fromDoc.length ? extraChapters.value : fromDoc;
  const seen = new Set();
  const out = [];
  for (const ch of order) {
    const key = String(ch.key || "");
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(byKey.get(key));
  }
  for (const [key, ch] of byKey) {
    if (!seen.has(key)) out.push(ch);
  }
  return mergeWriteState(out);
});

const tree = computed(() => buildTree(chapters.value));
const flatTree = computed(() => flattenTree(tree.value));
/** 仅展开一级目录，避免 200+ 章 default-expand-all 拖垮切 tab */
const outlineExpandedKeys = computed(() =>
  (tree.value || [])
    .filter((n) => n?.key && (n.children || []).length)
    .map((n) => String(n.key))
);
/**
 * 目录树数据。checkDisabled 只跟真实阻塞态走，且用浅缓存：
 * 切 tab 不再触发 200+ 节点整树 map（此前卡顿主因之一）。
 */
let _outlineTreeCache = { locked: null, src: null, data: [] };
const outlineTreeData = computed(() => {
  const locked = Boolean(blockingLoad.value);
  const src = tree.value;
  if (_outlineTreeCache.src === src && _outlineTreeCache.locked === locked) {
    return _outlineTreeCache.data;
  }
  const mark = (nodes) =>
    (nodes || []).map((n) => ({
      ...n,
      checkDisabled: locked,
      children: mark(n.children || []),
    }));
  const data = mark(src);
  _outlineTreeCache = { locked, src, data };
  return data;
});
const selectableChapterKeys = computed(() =>
  flatTree.value
    .filter((n) => n?.key && !chapterIsStructure(n))
    .map((n) => String(n.key))
);
const allSelectableChecked = computed(
  () =>
    selectableChapterKeys.value.length > 0 &&
    selectableChapterKeys.value.every((k) => checkedKeySet.value.has(k))
);
const someSelectableChecked = computed(() =>
  selectableChapterKeys.value.some((k) => checkedKeySet.value.has(k))
);

function syncCheckedKeysFromTree() {
  const allowed = new Set(selectableChapterKeys.value);
  const raw = chapterOutlineTreeRef.value?.getCheckedKeys?.(false) || [];
  checkedKeys.value = raw.map(String).filter((k) => allowed.has(k));
}

function applyCheckedKeysToTree(keys) {
  nextTick(() => {
    chapterOutlineTreeRef.value?.setCheckedKeys?.(keys || []);
  });
}

function onOutlineTreeCheck() {
  syncCheckedKeysFromTree();
}

function onOutlineTreeNodeClick(data) {
  const key = String(data?.key || "").trim();
  if (!key || blockingLoad.value) return;
  selectChapter(key);
}

function toggleSelectAll(on) {
  const keys = on ? [...selectableChapterKeys.value] : [];
  checkedKeys.value = keys;
  applyCheckedKeysToTree(keys);
}

watch(selectedKey, (key) => {
  const k = String(key || "").trim();
  if (!k) return;
  nextTick(() => {
    chapterOutlineTreeRef.value?.setCurrentKey?.(k);
  });
});

watch(
  () => selectableChapterKeys.value.join(","),
  () => {
    // 目录键变化时裁掉失效勾选并回写树（父子联动由 el-tree 负责）
    const allowed = new Set(selectableChapterKeys.value);
    const keep = checkedKeys.value.filter((k) => allowed.has(k));
    checkedKeys.value = keep;
    applyCheckedKeysToTree(keep);
  }
);

function orderedCheckedKeys() {
  const want = checkedKeySet.value;
  return selectableChapterKeys.value.filter((k) => want.has(k));
}

/** 树有勾选时优先用勾选范围；否则用传入兜底（文档级操作）。 */
function scopedOrFallbackKeys(fallbackKeys = []) {
  const checked = orderedCheckedKeys();
  if (checked.length) return checked;
  return (fallbackKeys || []).map((k) => String(k || "").trim()).filter(Boolean);
}

/** 多章不传 pace，由对话侧弹出书写节奏澄清；单章默认 batch。 */
function buildWriteActionPayload(chapterKeys, chapterKey, extra = {}) {
  const keys = (chapterKeys || []).map((k) => String(k || "").trim()).filter(Boolean);
  const payload = {
    chapterKeys: keys,
    chapterKey: String(chapterKey || keys[0] || "").trim(),
    ...extra,
  };
  if (keys.length < 2) payload.pace = "batch";
  return payload;
}

async function runBulkStudy() {
  if (blockingLoad.value) return;
  const keys = orderedCheckedKeys();
  if (!keys.length) {
    ElMessage.info("请先勾选要分析的章节");
    return;
  }
  if (isSampleStudyDoc.value && !outlineConfirmed.value) {
    ElMessage.info("请先确认目录后再分析");
    emitWizard({ step: "outline_confirm", wizard_command: "go_outline_confirm" });
    return;
  }
  if (!(await flushConfigBeforeWrite())?.ok) return;
  const focusKey = keys[0];
  selectChapter(focusKey, "config");
  const seqs = keys
    .map((k) => {
      const ch = chapters.value.find((c) => String(c?.key || "") === k);
      return String(ch?.sequence || "").trim();
    })
    .filter(Boolean);
  if (isFreeformDoc.value) {
    emitWrite("generate_freeform_points", { chapterKeys: keys, chapterKey: focusKey });
    return;
  }
  emitWizard({
    step: "study",
    wizard_command: "re_study_chapters",
    chapter_keys: keys,
    chapter_sequences: seqs,
    study_message: keys.length === 1 ? chapterLabel(chapters.value.find((c) => c.key === focusKey)) : `已选 ${keys.length} 章`,
    study_scope: keys.length === 1 ? "self" : "selected",
    locate_chapter_key: focusKey,
  });
}

async function runBulkWrite() {
  if (blockingLoad.value) return;
  const keys = orderedCheckedKeys();
  if (!keys.length) {
    ElMessage.info("请先勾选要写作的章节");
    return;
  }
  const writable = keys.filter((k) => {
    const ch = chapters.value.find((c) => String(c?.key || "") === k);
    return ch && !chapterIsStructure(ch) && !chapterNeedsStudy(ch);
  });
  if (!writable.length) {
    ElMessage.warning("勾选的章节还没有写作要点，请先分析后再写作");
    return;
  }
  if (writable.length < keys.length) {
    ElMessage.info(`已跳过 ${keys.length - writable.length} 章未分析内容，将写 ${writable.length} 章`);
  }
  if (!(await flushConfigBeforeWrite())?.ok) return;
  const focusKey = writable[0];
  selectChapter(focusKey, "preview", { mainView: "preview" });
  if (isSampleStudyDoc.value) {
    emitWrite("write_analyzed_only", buildWriteActionPayload(writable, focusKey));
    return;
  }
  emitWrite("start_writing", buildWriteActionPayload(writable, focusKey));
}
const selectedChapter = computed(() => chapters.value.find((ch) => ch?.key === selectedKey.value) || null);
const selectedOutlineNode = computed(
  () => outlineNodes.value.find((ch) => String(ch?.key || "") === String(selectedKey.value || "")) || null
);
/** 正文页：写作过程步骤（检索/写作/检查） */
const selectedChapterWriteProcess = computed(() => {
  const steps = selectedChapter.value?.steps;
  return Array.isArray(steps) ? steps.filter((s) => s && (s.step || s.step_label || s.note)) : [];
});
/** 正文页：要素解析收集到的资料（证据卡） */
const selectedChapterEvidence = computed(() => {
  const list = selectedChapter.value?.evidence;
  if (!Array.isArray(list)) return [];
  return list
    .filter((ev) => ev && typeof ev === "object")
    .map((ev) => ({
      id: String(ev.id || ""),
      point: String(ev.point || ev.name || "要素").trim() || "要素",
      location: String(ev.location || ev.kind || ""),
      query: String(ev.query || "").trim(),
      excerpt: String(ev.excerpt || ev.value || "").trim(),
    }))
    .filter((ev) => ev.excerpt || ev.query || ev.point);
});

/** 上级节点选项：排除自身及子孙，避免成环 */
const configParentOptions = computed(() => {
  const selfKey = String(selectedKey.value || "").trim();
  const nodes = outlineNodes.value || [];
  if (!selfKey) {
    return nodes.filter((item) => String(item?.key || "").trim());
  }
  const children = new Map();
  for (const n of nodes) {
    const k = String(n?.key || "").trim();
    const p = String(n?.parent || "").trim();
    if (!k) continue;
    if (!children.has(p)) children.set(p, []);
    children.get(p).push(k);
  }
  const blocked = new Set([selfKey]);
  const stack = [selfKey];
  while (stack.length) {
    const cur = stack.pop();
    for (const child of children.get(cur) || []) {
      if (blocked.has(child)) continue;
      blocked.add(child);
      stack.push(child);
    }
  }
  return nodes.filter((item) => {
    const k = String(item?.key || "").trim();
    return k && !blocked.has(k);
  });
});

const elementDialogVisible = ref(false);
const elementDraft = ref(null);
const libraryPendingDialogVisible = ref(false);
const projectFolderIdsLocal = ref([]);
const projectFolderLabel = ref("");
const materialsPackIdLocal = ref("");
const materialsPack = ref(null);
const libraryPackOptions = ref([]);

/** 当前文档相关的待收录要素（按 task / writing / blueprint id 过滤） */
const pendingLibraryItems = computed(() => {
  const items = Array.isArray(props.elementLibraryPending) ? props.elementLibraryPending : [];
  const doc = activeDoc.value;
  const allowed = new Set(
    [doc?.taskId, doc?.writingTaskId, doc?.blueprintId]
      .map((x) => String(x || "").trim())
      .filter(Boolean)
  );
  const visible = items.filter((row) => {
    if (!row || typeof row !== "object") return false;
    if (String(row.element?.library_item_id || "").trim()) return false;
    if (!allowed.size) return true;
    return allowed.has(String(row.task_id || "").trim());
  });
  return visible;
});
const pendingLibraryCount = computed(() => pendingLibraryItems.value.length);
/** 本文件要素节点里尚未挂 library_item_id 的（含蓝本自带未收集要素） */
const localUncollectedElements = computed(() =>
  (elementNodes.value || []).filter((el) => {
    if (!el || typeof el !== "object") return false;
    if (!String(el.id || "").trim()) return false;
    return !String(el.library_item_id || "").trim();
  })
);
const collectElementsCount = computed(() => {
  const pendingIds = new Set(
    pendingLibraryItems.value
      .map((r) => String(r?.element?.id || r?.element_id || "").trim())
      .filter(Boolean)
  );
  let n = pendingLibraryItems.value.length;
  for (const el of localUncollectedElements.value) {
    const id = String(el.id || "").trim();
    if (id && !pendingIds.has(id)) n += 1;
  }
  return n;
});
/** 文档工作区文案：库蓝本是模版；有 writingTaskId 时操作落在副本 */
const docWorkspaceLabel = computed(() => {
  const doc = activeDoc.value;
  if (isFreeformSampleDoc.value) {
    return String(doc?.kindLabel || "无蓝本·依据样本").trim() || "无蓝本·依据样本";
  }
  const wid = String(doc?.writingTaskId || "").trim();
  if (isWritingCopyId(wid)) {
    // 父级 canSync 已含会话库蓝本兜底；本地再解析文档自身非 w* 源
    return props.canSyncToBlueprintLibrary || resolveLibraryBlueprintId(doc)
      ? "文档副本（源自蓝本模版）"
      : "文档副本";
  }
  return String(doc?.kindLabel || "文档").trim() || "文档";
});

/**
 * 同步 / 收录按「当前文档」判定（勿被会话 isFreeform 一刀切藏掉选用线同步）：
 * - 有写作副本 w* + 库蓝本源 → 同步到蓝本库（选用副本 / 收录后）
 * - 无蓝本·依据样本 / 尚未入库 → 收录到蓝本库
 */
const showSyncToBlueprintLibrary = computed(() => {
  // 父级已判定可同步（含收录后）→ 直接显示，勿被样本身份二次挡死
  if (props.canSyncToBlueprintLibrary) return true;
  if (isFreeformSampleDoc.value) return false;
  return hasWritingCopyWithLibrarySource(activeDoc.value);
});
const showSaveAsBlueprint = computed(
  () =>
    Boolean(props.canSaveAsBlueprint) &&
    !showSyncToBlueprintLibrary.value &&
    (!isBlueprintDoc.value || isFreeformSampleDoc.value)
);
const libraryPendingCard = computed(() => ({
  type: "element_library_pending",
  title: "收集要素到个人库",
  payload: {
    items: pendingLibraryItems.value,
    count: pendingLibraryItems.value.length,
    hint:
      "勾选要收入的要素；已入库的不会出现在列表。分类/标签/作用已带入时可直接收入。",
  },
}));

let openLibraryPendingAfterRefresh = false;
const collectElementsBusy = ref(false);

function openLibraryPendingDialog(force = false) {
  if (!force && !collectElementsCount.value) return;
  libraryPendingDialogVisible.value = true;
}

async function openCollectElements() {
  if (maskVisible.value || collectElementsBusy.value) return;
  const pendingIds = new Set(
    pendingLibraryItems.value
      .map((r) => String(r?.element?.id || r?.element_id || "").trim())
      .filter(Boolean)
  );
  const toQueue = localUncollectedElements.value.filter((el) => {
    const id = String(el.id || "").trim();
    return id && !pendingIds.has(id);
  });
  const tid = String(
    materialsPackTargetTid.value ||
      activeDoc.value?.writingTaskId ||
      activeDoc.value?.taskId ||
      activeDoc.value?.blueprintId ||
      ""
  ).trim();
  const sid = String(props.sessionId || "").trim();
  if (toQueue.length && tid && sid) {
    collectElementsBusy.value = true;
    try {
      await request("/api/writing/chat/element-library/queue", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          session_id: sid,
          task_id: tid,
          chapter_key: String(selectedKey.value || "").trim(),
          chapter_title: chapterLabel(selectedChapter.value),
          element_ids: toQueue.map((el) => String(el.id)),
          elements: toQueue,
        }),
      });
      openLibraryPendingAfterRefresh = true;
      emit("library-pending-changed");
    } catch (error) {
      ElMessage.error(error?.message || "收集要素失败");
      collectElementsBusy.value = false;
      return;
    } finally {
      collectElementsBusy.value = false;
    }
  }
  if (collectElementsCount.value > 0 || toQueue.length) {
    openLibraryPendingDialog(true);
  } else {
    ElMessage.info("当前没有可收集的要素");
  }
}

function onLibraryPendingDialogClosed() {
  /* keep queue; parent refreshes via library-pending-changed */
}

function onLibraryPendingCardChanged(payload) {
  emit("library-pending-changed", payload);
  const removed = Array.isArray(payload?.element_ids)
    ? payload.element_ids.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  if (removed.length) {
    const drop = new Set(removed);
    elementNodes.value = (elementNodes.value || []).filter(
      (el) => !drop.has(String(el?.id || ""))
    );
    void loadFullOutline({ force: true });
  }
}

function onLibraryPendingDone() {
  libraryPendingDialogVisible.value = false;
  emit("library-pending-changed");
}

watch(
  () => collectElementsCount.value,
  (n) => {
    if (openLibraryPendingAfterRefresh && n > 0) {
      openLibraryPendingAfterRefresh = false;
      libraryPendingDialogVisible.value = true;
    }
  }
);

const projectFoldersTargetTid = computed(() => {
  const wid = String(activeDoc.value?.writingTaskId || "").trim();
  if (wid) return wid;
  const tid = String(activeDoc.value?.taskId || "").trim();
  // 写作任务 id 通常以 w 开头；蓝本 bp 上也可预选，开写时带入会话
  if (tid && /^w/i.test(tid)) return tid;
  return tid || String(activeDoc.value?.blueprintId || "").trim();
});
/** 资料包正式绑定目标：写作文档 id（开写后） */
const materialsPackTargetTid = computed(() => {
  const wid = String(activeDoc.value?.writingTaskId || "").trim();
  if (wid) return wid;
  const tid = String(activeDoc.value?.taskId || "").trim();
  if (tid && /^w/i.test(tid)) return tid;
  return "";
});
/** 开写前预建的资料包：可展示/改槽位，正式绑定仍等开写 */
const pendingMaterialsPackIdLocal = computed(() =>
  String(props.pendingMaterialsPackId || "").trim()
);
const materialsPackPreviewMode = computed(
  () => !materialsPackTargetTid.value && Boolean(pendingMaterialsPackIdLocal.value)
);
const canEditProjectFolders = computed(() => Boolean(projectFoldersTargetTid.value));
/** 开写前蓝本/大纲窗也可预建（写入会话 pending）；不依赖要素是否已有 file_slot。 */
const canEditMaterialsPack = computed(() => {
  if (materialsPackTargetTid.value || pendingMaterialsPackIdLocal.value) return true;
  if (isSampleStudyDoc.value) return true;
  const kind = String(activeDoc.value?.kind || "").trim();
  return kind === "outline" || kind === "writing" || kind === "task";
});
/** 文档窗始终展示资料包条：蓝本目录态不再整块隐藏。 */
const showMaterialsPackPanel = computed(() => {
  if (canEditMaterialsPack.value) return true;
  if (isSampleStudyDoc.value) return true;
  return Boolean(materialsPackIdLocal.value || materialsPack.value);
});
/** 蓝本视图且尚无预建包：补充 file_slot 提示（面板本身已始终可显）。 */
const showMaterialsPackBlueprintHint = computed(() => {
  if (materialsPackTargetTid.value || pendingMaterialsPackIdLocal.value) return false;
  if (!isBlueprintDoc.value) return false;
  if (materialsPackIdLocal.value) return false;
  return (elementNodes.value || []).some((el) => {
    if (String(el?.kind || "").trim() !== "file_slot") return false;
    return Array.isArray(el?.file_slots) && el.file_slots.length > 0;
  });
});
const projectFolderLabelDisplay = computed(() => {
  if (projectFolderLabel.value) return projectFolderLabel.value;
  return folderLabelFromTree(projectFolderIdsLocal.value);
});

/** 无显式 label 时按已选数量兜底，避免 emitWrite 引用未定义函数 */
function folderLabelFromTree(ids = []) {
  const list = (Array.isArray(ids) ? ids : [])
    .map((x) => String(x || "").trim())
    .filter(Boolean);
  if (!list.length) return "";
  return list.length === 1 ? "已选 1 项" : `已选 ${list.length} 项`;
}
const materialsPackLabelDisplay = computed(() => {
  if (materialsPack.value?.name) return materialsPack.value.name;
  return materialsPackIdLocal.value || "";
});
/** 要素上的引用槽位（仅作补充展示；资料包 schema 才是槽位真源） */
const materialsSlotSchemaFromElements = computed(() => {
  const byNo = new Map();
  for (const el of elementNodes.value || []) {
    if (String(el?.kind || "").trim() !== "file_slot") continue;
    for (const slot of el.file_slots || []) {
      const no = Number(slot?.no) > 0 ? Number(slot.no) : 1;
      const prev = byNo.get(no) || { no, name: "", query: "" };
      const name = String(slot?.name || "").trim();
      const query = String(slot?.query || "").trim();
      if (name) prev.name = name;
      if (query) prev.query = query;
      byNo.set(no, prev);
    }
  }
  return [...byNo.values()].sort((a, b) => a.no - b.no);
});
/** @deprecated 兼容旧调用名：现以资料包 schema 为主 */
const materialsSlotSchema = materialsSlotSchemaFromElements;

/** 要素配置：下拉可选的资料包槽位 */
const packSlotSelectOptions = computed(() => {
  const byNo = new Map();
  for (const s of materialsPack.value?.slot_schema || []) {
    const no = Number(s?.no) > 0 ? Number(s.no) : 0;
    if (no < 1) continue;
    byNo.set(no, { no, name: String(s?.name || "").trim() });
  }
  const ordered = [];
  const seen = new Set();
  for (const r of materialsSlotRows.value || []) {
    const no = Number(r?.no) > 0 ? Number(r.no) : 0;
    if (no < 1 || seen.has(no)) continue;
    const prev = byNo.get(no) || { no, name: "" };
    if (!prev.name && r.name) prev.name = String(r.name).trim();
    ordered.push(prev);
    seen.add(no);
  }
  for (const [no, opt] of byNo) {
    if (seen.has(no)) continue;
    ordered.push(opt);
  }
  return ordered;
});

const materialsSlotRows = computed(() => {
  const fills = new Map(
    (materialsPack.value?.slots_fill || []).map((f) => [Number(f.slot_no) || 0, f])
  );
  // 资料包 slot_schema 优先；要素引用仅补名称/检索词，不决定能否配置/上传
  const packSchema = Array.isArray(materialsPack.value?.slot_schema)
    ? materialsPack.value.slot_schema
    : [];
  const byNo = new Map();
  for (const s of packSchema) {
    const no = Number(s?.no) > 0 ? Number(s.no) : 0;
    if (no < 1) continue;
    byNo.set(no, {
      no,
      name: String(s?.name || ""),
      query: String(s?.query || ""),
    });
  }
  // 要素只补充编号/名称到展示，不把要素检索词写进资料包
  for (const s of materialsSlotSchemaFromElements.value) {
    const no = Number(s?.no) > 0 ? Number(s.no) : 0;
    if (no < 1) continue;
    const prev = byNo.get(no) || { no, name: "", query: "" };
    if (!prev.name && s.name) prev.name = String(s.name);
    byNo.set(no, prev);
  }
  for (const no of fills.keys()) {
    if (no > 0 && !byNo.has(no)) byNo.set(no, { no, name: "", query: "" });
  }
  const orderRaw = Array.isArray(materialsPack.value?.slot_order)
    ? materialsPack.value.slot_order
    : [];
  const ordered = [];
  const seen = new Set();
  for (const item of orderRaw) {
    const no = Number(item) || 0;
    if (no < 1 || !byNo.has(no) || seen.has(no)) continue;
    ordered.push(byNo.get(no));
    seen.add(no);
  }
  for (const no of [...byNo.keys()].sort((a, b) => a - b)) {
    if (!seen.has(no)) ordered.push(byNo.get(no));
  }
  return ordered.map((def) => {
      const fill = fills.get(def.no);
      return {
        no: def.no,
        name: def.name,
        query: "",
        file_name: fill?.file_name || "",
        file_id: fill?.file_id || "",
        parse_status: String(fill?.parse_status || "").trim(),
        parse_error: String(fill?.parse_error || "").trim(),
      };
    });
});

function packSchemaPayloadFromRows(rows) {
  // 资料包 schema 不落检索词；顺序跟当前展示（slot_order），第一次保存不要改成按编号升序
  return (rows || [])
    .map((r) => ({
      no: Number(r?.no) > 0 ? Number(r.no) : 0,
      name: String(r?.name || "").trim(),
      query: "",
    }))
    .filter((r) => r.no > 0);
}

const selectedElementIds = computed(() => {
  const node = selectedOutlineNode.value;
  const ch = selectedChapter.value;
  // 大纲节点优先，避免文档侧空 [] 把刚关联的要素盖掉
  return pickElementIds(
    node?.element_ids,
    ch?.element_ids,
    ch?.config?.element_ids
  );
});

/** 配置完成后在基本/内容 Tab 顶部展示章节说明与要点关联概况 */
const configChapterMetaBanner = computed(() => {
  if (configDraft.structure_only) {
    const d = String(configDraft.describes || "").trim();
    return d ? [`目录说明：${d.slice(0, 200)}${d.length > 200 ? "…" : ""}`] : ["目录节点：可在下方填写章节说明"];
  }
  const lines = [];
  const describes = String(configDraft.describes || "").trim();
  if (describes) {
    lines.push(
      `章节说明（背景）：${describes.slice(0, 160)}${describes.length > 160 ? "…" : ""}`
    );
  } else {
    lines.push("章节说明（背景）：尚未填写，分析或保存配置后会在此显示");
  }
  const total = configDraft.point_items.length;
  if (total) {
    let linked = 0;
    let open = 0;
    for (const row of configDraft.point_items) {
      if (rowNeedsPointAssociate(row)) open += 1;
      else if (String(row?.element_id || "").trim()) linked += 1;
    }
    lines.push(`写作要点：共 ${total} 条，已关联要素 ${linked} 条${open ? `，待关联 ${open} 条` : ""}`);
  }
  const aux = String(configDraft.aux_prompt || "").trim();
  if (aux) {
    lines.push(`补充提示词：${aux.slice(0, 80)}${aux.length > 80 ? "…" : ""}`);
  }
  return lines;
});

const configLinkedElements = computed(() => {
  const byId = new Map(elementNodes.value.map((el) => [String(el?.id || ""), el]));
  return selectedElementIds.value.map((id) => byId.get(id)).filter(Boolean);
});

/** 分析生成的章节检索词：只读展示，供配要素/写检索时参考 */
const configReferenceKeywords = computed(() => {
  const node = selectedOutlineNode.value;
  const ch = selectedChapter.value;
  const raw =
    (Array.isArray(node?.keywords) && node.keywords.length
      ? node.keywords
      : null) ||
    (Array.isArray(ch?.keywords) && ch.keywords.length ? ch.keywords : null) ||
    (Array.isArray(ch?.config?.keywords) && ch.config.keywords.length
      ? ch.config.keywords
      : null) ||
    [];
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    const kw = String(item || "").trim();
    if (!kw || seen.has(kw)) continue;
    seen.add(kw);
    out.push(kw);
  }
  return out;
});

const elementRefTargetOptions = computed(() => {
  if (!elementDraft.value) return [];
  const selfId = String(elementDraft.value.id || "");
  return elementNodes.value
    .filter((item) => String(item?.id || "") !== selfId)
    .map((item) => ({
      value: item.id,
      label: `${item.name || item.id}（${elementKindLabel(item.kind)}）`,
    }));
});

function isSelectedConfigChapter(ch) {
  const key = String(ch?.key || "").trim();
  return Boolean(key) && key === String(selectedKey.value || "").trim();
}

function chapterHasPoints(ch) {
  // 当前编辑草稿优先：刚取消「仅目录」或改过要点时，按钮立刻按草稿态刷新
  if (isSelectedConfigChapter(ch) && !configDraft.structure_only) {
    if (configDraft.point_items.some((row) => String(row?.text || "").trim())) return true;
  }
  const node = outlineNodes.value.find((n) => String(n?.key || "") === String(ch?.key || ""));
  // 空数组 [] 在 JS 里是 truthy，不能用 || 链，否则会挡住 config.points
  return (
    pickRicherList(node?.points, pickRicherList(ch?.points, ch?.config?.points)).length > 0
  );
}

function chapterPointItemsForState(ch) {
  if (isSelectedConfigChapter(ch) && !configDraft.structure_only) {
    return Array.isArray(configDraft.point_items) ? configDraft.point_items : [];
  }
  const node = outlineNodes.value.find((n) => String(n?.key || "") === String(ch?.key || ""));
  return pointItemsFromChapter(node, ch, ch?.config);
}

/** 仍有待补且未关联有效要素的要点行 */
function chapterHasUnlinkedSupplementPoints(ch) {
  if (!ch || chapterIsStructure(ch) || !chapterHasPoints(ch)) return false;
  for (const row of chapterPointItemsForState(ch)) {
    if (rowNeedsPointAssociate(row)) return true;
  }
  return false;
}

/** 与后端 is_chapter_analyzed 对齐：要点/检索词/简述/参考正文任一齐了即算已分析 */
function chapterIsStudied(ch) {
  if (!ch || chapterIsStructure(ch)) return true;
  if (chapterHasPoints(ch)) return true;
  const node = outlineNodes.value.find((n) => String(n?.key || "") === String(ch?.key || ""));
  if (isSelectedConfigChapter(ch) && !configDraft.structure_only) {
    if (String(configDraft.describes || "").trim()) return true;
    if (String(configDraft.reference_text || "").trim()) return true;
  }
  const keywords = pickRicherList(
    node?.keywords,
    pickRicherList(ch?.keywords, ch?.config?.keywords)
  );
  if (keywords.length) return true;
  const describes = pickRicherText(
    node?.describes,
    pickRicherText(ch?.describes, ch?.config?.describes)
  );
  if (String(describes || "").trim()) return true;
  const reference = pickRicherText(
    node?.reference_text,
    pickRicherText(ch?.reference_text, ch?.config?.reference_text)
  );
  if (String(reference || "").trim()) return true;
  const blocks = node?.content_blocks || ch?.content_blocks || ch?.config?.content_blocks;
  return Array.isArray(blocks) && blocks.length > 0;
}

function chapterIsStructure(ch) {
  // 勾选「仅目录节点」以草稿为准，取消后立刻露出分析/关联要素，不必先点保存
  if (isSelectedConfigChapter(ch)) {
    return Boolean(configDraft.structure_only);
  }
  const node = outlineNodes.value.find((n) => String(n?.key || "") === String(ch?.key || ""));
  // 勿用 ||：structure_only=false 会被旧 true 盖回
  return Boolean(node?.structure_only ?? ch?.structure_only ?? ch?.config?.structure_only);
}

function chapterNeedsStudy(ch) {
  if (!ch || chapterIsStructure(ch)) return false;
  return !chapterIsStudied(ch);
}

function chapterHasDraft(ch) {
  if (!ch) return false;
  if (ch.has_draft || ch.draft_text || ch.draft_preview) return true;
  const st = String(ch.status || "").trim();
  if (st === "confirmed" || st === "awaiting_confirm") return true;
  // needs_human 但已有正文（旧数据）也算已写作
  if (st === "needs_human" && (ch.has_draft || ch.draft_text || ch.draft_preview)) return true;
  // 当前选中章：编辑区已有正文也算已写作（进度卡可能已滑走）
  if (String(ch.key || "") === String(selectedKey.value || "")) {
    const html = String(props.draftText || "");
    if (html.replace(/<[^>]+>/g, "").trim()) return true;
  }
  return false;
}

function chapterWriteStatusLabel(ch) {
  if (!ch) return "";
  const st = String(ch.status || "").trim();
  if (["searching", "writing", "checking", "running"].includes(st)) {
    return statusLabel(st) || "写作中";
  }
  if (st === "awaiting_confirm" || st === "confirmed" || st === "needs_human") {
    // 有正文或已落库：统一「已写作」，不再展示待确认/待人工
    if (st === "confirmed" || st === "awaiting_confirm" || chapterHasDraft(ch)) return "已写作";
    return "待重写";
  }
  if (st === "skipped") return statusLabel(st) || "已跳过";
  // 有正文一律「已写作」，优先于「已分析」
  if (chapterHasDraft(ch)) return "已写作";
  if (ch.status_label && st && st !== "pending") return ch.status_label;
  return "";
}

function chapterIsAnalyzing(ch) {
  const key = String(ch?.key || "").trim();
  if (!key || !props.opsLocked) return false;
  if (analyzingKey.value && analyzingKey.value === key) return true;
  return Boolean(ch?.is_running) && String(props.opsLockLabel || "").includes("分析");
}

/** 资料包已填充且有文件的槽位编号 */
function materialsFilledSlotNos() {
  const fills = materialsPack.value?.slots_fill || [];
  const nos = new Set();
  for (const f of fills) {
    const no = Number(f?.slot_no) || 0;
    if (no < 1) continue;
    if (String(f?.file_id || f?.source_path || f?.file_name || "").trim()) nos.add(no);
  }
  return nos;
}

/** 资料包已规划的槽位编号（schema ∪ 已有填充记录） */
function materialsSchemaSlotNos() {
  const nos = new Set();
  for (const s of materialsPack.value?.slot_schema || []) {
    const no = Number(s?.no) || 0;
    if (no > 0) nos.add(no);
  }
  for (const f of materialsPack.value?.slots_fill || []) {
    const no = Number(f?.slot_no) || 0;
    if (no > 0) nos.add(no);
  }
  return nos;
}

function chapterLinkedFileSlotElements(ch) {
  const key = String(ch?.key || "").trim();
  const node = key
    ? outlineNodes.value.find((n) => String(n?.key || "") === key)
    : null;
  const ids = pickElementIds(
    node?.element_ids,
    ch?.element_ids,
    ch?.config?.element_ids,
    key ? pendingChapterElementIds.get(key) : null
  );
  if (!ids.length) return [];
  const byId = new Map(
    (elementNodes.value || []).map((el) => [String(el?.id || ""), el])
  );
  return ids
    .map((id) => byId.get(String(id)))
    .filter((el) => el && String(el.kind || "").trim() === "file_slot");
}

/** 本章引用了资料包中不存在的槽位（仅编号不存在；未上传不报警） */
function chapterSlotGaps(ch) {
  const els = chapterLinkedFileSlotElements(ch);
  if (!els.length) return [];
  const packId = String(materialsPackIdLocal.value || materialsPack.value?.id || "").trim();
  const schema = materialsSchemaSlotNos();
  const gaps = [];
  const seen = new Set();
  for (const el of els) {
    const slots = Array.isArray(el.file_slots) ? el.file_slots : [];
    for (const slot of slots) {
      const no = Number(slot?.no) || 0;
      if (no < 1) continue;
      // 未绑包：引用的槽位视为在资料包侧不存在
      const missing = !packId || !schema.has(no);
      if (!missing) continue;
      const tag = `槽${no}·资料包无此槽位`;
      if (seen.has(tag)) continue;
      seen.add(tag);
      gaps.push(tag);
    }
  }
  return gaps;
}

function chapterSlotMisaligned(ch) {
  return chapterSlotGaps(ch).length > 0;
}

function chapterSlotWarnTitle(ch) {
  const gaps = chapterSlotGaps(ch);
  if (!gaps.length) return "";
  return `引用的槽位在资料包中不存在：${gaps.join("；")}`;
}

function chapterTreeTitle(ch) {
  const base = chapterLabel(ch);
  const warn = chapterSlotWarnTitle(ch);
  return warn ? `${base}\n${warn}` : base;
}

function chapterStateLabel(ch) {
  if (!ch) return "";
  if (chapterIsStructure(ch)) return "目录节点";
  if (chapterIsAnalyzing(ch)) return "分析中";
  if (!outlineConfirmed.value && (isSampleStudyDoc.value || isFreeformDoc.value)) return "待确认目录";
  // 已写 / 写作中优先于「已配要点」「已分析」
  const writeLabel = chapterWriteStatusLabel(ch);
  if (writeLabel) return writeLabel;
  if (ch.is_next && !chapterHasDraft(ch)) return "待开写";
  if (isFreeformDoc.value) {
    if (chapterNeedsStudy(ch)) return "待配要点";
    if (chapterHasPoints(ch)) {
      return chapterHasUnlinkedSupplementPoints(ch) ? "待关联" : "已配要点";
    }
  }
  if (chapterNeedsStudy(ch) && isSampleStudyDoc.value) return "待分析";
  if (chapterHasPoints(ch) && isSampleStudyDoc.value) return "已分析";
  return "";
}

function chapterStateTone(ch) {
  if (!ch) return "";
  if (chapterIsStructure(ch)) return "muted";
  if (chapterIsAnalyzing(ch)) return "run";
  if (!outlineConfirmed.value && (isSampleStudyDoc.value || isFreeformDoc.value)) return "ready";
  const st = String(ch.status || "").trim();
  if (["searching", "writing", "checking", "running"].includes(st) || ch.is_running) {
    return statusTone(st || "writing");
  }
  if (
    st === "awaiting_confirm" ||
    st === "confirmed" ||
    st === "needs_human" ||
    chapterHasDraft(ch)
  ) {
    return "ok";
  }
  if (ch.is_next && !chapterHasDraft(ch)) return "ready";
  if (isFreeformDoc.value) {
    if (chapterNeedsStudy(ch)) return "study";
    if (chapterHasPoints(ch)) return "ok";
  }
  if (chapterNeedsStudy(ch) && isSampleStudyDoc.value) return "study";
  if (chapterHasPoints(ch) && isSampleStudyDoc.value) return "ok";
  return statusTone(st);
}

function appendChapterWriteActions(acts, ch) {
  if (!ch || chapterIsStructure(ch) || chapterNeedsStudy(ch)) return acts;
  const st = String(ch.status || "");
  const written = chapterHasDraft(ch);
  if (["searching", "writing", "checking", "running"].includes(st)) {
    acts.push({ id: "pause_writing", label: "暂停写作", primary: true });
  } else if (written) {
    acts.push({ id: "retry", label: "重写本章", primary: true, writeMode: "rewrite" });
  } else {
    acts.push({ id: "retry", label: "开始写本章", primary: true, writeMode: "start" });
  }
  return acts;
}

const unstudiedCount = computed(
  () => outlineNodes.value.filter((ch) => chapterNeedsStudy(ch)).length
);

/** 尚无章节时不展示确认/重提目录等阶段按钮 */
const hasOutlineChapters = computed(
  () => outlineNodes.value.length > 0 || chapters.value.length > 0
);

/** 文档级：按蓝本/写作阶段显示 */
const phaseActions = computed(() => {
  const acts = [];
  if (!taskId.value) return acts;
  if (isSampleStudyDoc.value) {
    const allowSampleRollback =
      isFreeformSampleDoc.value ||
      String(activeDoc.value?.wizardMode || activeDoc.value?.mode || "").trim() !== "selected";
    if (!hasOutlineChapters.value) {
      if (allowSampleRollback && String(activeDoc.value?.wizardStep || "").trim()) {
        acts.push({ id: "re_upload", label: "重新上传样板" });
      }
      return acts;
    }
    if (!outlineConfirmed.value) {
      acts.push({ id: "confirm_outline", label: "确认目录", primary: true });
      if (allowSampleRollback) {
        acts.push({ id: "re_upload", label: "重新上传样板" });
        acts.push({ id: "re_extract", label: "重新提取目录" });
      }
    } else if (unstudiedCount.value > 0) {
      acts.push({
        id: "study_all",
        label: `继续分析未完成 · ${unstudiedCount.value}章`,
        primary: true,
      });
      const studied = outlineNodes.value.filter(
        (ch) => !chapterNeedsStudy(ch) && !chapterIsStructure(ch)
      ).length;
      if (studied > 0) {
        acts.push({ id: "write_analyzed", label: `先写已分析 · ${studied}章` });
      }
      if (allowSampleRollback) {
        acts.push({ id: "re_upload", label: "重新上传样板" });
        acts.push({ id: "re_extract", label: "重新提取目录" });
      }
    } else {
      acts.push({ id: "write_analyzed", label: "开始写作（已分析章节）", primary: true });
      acts.push({ id: "start_writing", label: "开始写作" });
      acts.push({ id: "study_all", label: "重新分析全部", primary: false });
      if (allowSampleRollback) {
        acts.push({ id: "re_upload", label: "重新上传样板" });
        acts.push({ id: "re_extract", label: "重新提取目录" });
      }
    }
    appendOutlineChapterStructureActions(acts);
    return acts;
  }
  if (isFreeformDoc.value) {
    if (!hasOutlineChapters.value) {
      return acts;
    }
    if (!outlineConfirmed.value) {
      // 与蓝本「确认目录」对齐话术：确认后可生成要点 / 开写
      acts.push({ id: "confirm_outline", label: "确认大纲并继续", primary: true });
      acts.push({ id: "regenerate_outline", label: "重新生成章节目录" });
    } else if (unstudiedCount.value > 0) {
      acts.push({
        id: "generate_points",
        label: `生成写作要点 · ${unstudiedCount.value}章`,
        primary: true,
        scope: "bulk",
      });
      // 对齐蓝本「先写已分析」：已有要点的章可先开写
      const ready = outlineNodes.value.filter(
        (ch) => !chapterNeedsStudy(ch) && !chapterIsStructure(ch)
      ).length;
      if (ready > 0) {
        acts.push({ id: "write_points_ready", label: `先写已配要点 · ${ready}章` });
      }
      acts.push({ id: "start_writing", label: "开始写作（全部）" });
    } else {
      acts.push({ id: "start_writing", label: "开始写作", primary: true });
      acts.push({ id: "generate_points", label: "重新生成写作要点", scope: "bulk" });
      acts.push({ id: "pause_writing", label: "暂停写作" });
    }
    // 「收录到蓝本库」只保留顶栏，避免与阶段操作条重复
    appendOutlineChapterStructureActions(acts);
    return acts;
  }
  if (isWritingDoc.value) {
    acts.push({ id: "start_writing", label: "开始写作", primary: true });
    acts.push({ id: "pause_writing", label: "暂停写作" });
  }
  appendOutlineChapterStructureActions(acts);
  return acts;
});

function appendOutlineChapterStructureActions(acts) {
  if (!taskId.value || !hasOutlineChapters.value) return;
  acts.push({ id: "add_chapter", label: "新增章节", compact: true });
  acts.push({ id: "remove_chapters", label: "删除章节", danger: true, compact: true });
}

/** 选中章：只显示当前状态需要的按钮（配置区操作统一在此，底部不再重复） */
const chapterActions = computed(() => {
  const acts = [];
  const ch = selectedChapter.value;
  if (!taskId.value) return acts;

  if (selectedKey.value && ch && !chapterIsStructure(ch)) {
    const st = String(ch.status || "");
    if (["searching", "writing", "checking", "running"].includes(st) || ch.is_running) {
      acts.push({ id: "pause_writing", label: "暂停写作", primary: true });
    }
  }
  if (selectedKey.value) {
    acts.push({ id: "save_config", label: "保存配置", primary: true });
  }
  if (!ch) return acts;

  if (isSampleStudyDoc.value) {
    if (!outlineConfirmed.value) {
      acts.push({ id: "confirm_outline", label: "确认目录", primary: true });
    } else if (chapterNeedsStudy(ch)) {
      acts.push({ id: "study_chapter", label: "分析本章", primary: true });
      if (!chapterIsStructure(ch)) {
        acts.push({ id: "associate_elements", label: "关联要素" });
      }
    } else if (!chapterIsStructure(ch)) {
      appendChapterWriteActions(acts, ch);
      acts.push({ id: "study_chapter", label: "重新分析本章" });
      acts.push({ id: "associate_elements", label: "关联要素" });
    }
    return acts;
  }

  if (isFreeformDoc.value && !chapterIsStructure(ch)) {
    if (!outlineConfirmed.value) {
      acts.push({ id: "confirm_outline", label: "确认大纲并继续", primary: true });
      return acts;
    }
    // 与蓝本对齐：未配要点也可先关联要素；配要点后再开写本章
    if (chapterNeedsStudy(ch)) {
      acts.push({
        id: "study_chapter",
        label: "分析本章",
        primary: true,
      });
      acts.push({ id: "associate_elements", label: "关联要素" });
    } else {
      acts.push({
        id: "study_chapter",
        label: "重新分析本章",
      });
      appendChapterWriteActions(acts, ch);
      acts.push({ id: "associate_elements", label: "关联要素" });
    }
    return acts;
  }

  if (isWritingDoc.value && !chapterIsStructure(ch)) {
    appendChapterWriteActions(acts, ch);
    if (!chapterNeedsStudy(ch) && !ch.has_draft && !ch.status) {
      acts.push({ id: "retry_search", label: "重试要素解析" });
    }
    acts.push({ id: "associate_elements", label: "关联要素" });
  }
  return acts;
});

const boxStyle = computed(() => ({
  left: `${box.value.x}px`,
  top: `${box.value.y}px`,
  width: `${box.value.w}px`,
  height: `${box.value.h}px`,
}));

function chaptersFromDocPayload(data) {
  if (!data || typeof data !== "object") return [];
  if (Array.isArray(data.chapters)) return data.chapters;
  const outline = data.outline;
  if (!outline || typeof outline !== "object") return [];
  return Array.isArray(outline.chapters) ? outline.chapters : [];
}

function ensureChapterParents(list) {
  const chapters = (list || [])
    .filter((ch) => ch && typeof ch === "object")
    .map((ch) => ({ ...ch }));
  const bySeq = new Map();
  const byKey = new Map();
  for (const ch of chapters) {
    const seq = String(ch.sequence || "").trim();
    const key = String(ch.key || "").trim();
    if (seq) bySeq.set(seq, ch);
    if (key) byKey.set(key, ch);
  }
  for (const ch of chapters) {
    const key = String(ch.key || "").trim();
    const seq = String(ch.sequence || "").trim();
    const parentKey = String(ch.parent || "").trim();
    // 顶层序号（2、3…）不得挂在上一章 key 下；仅 2.1 这类才允许 parent
    if (seq && !seq.includes(".")) {
      ch.parent = "";
      ch.parent_sequence = "";
      continue;
    }
    // 已有有效 parent（能在本树找到）则保留；无效/自指则按序号重推
    if (parentKey && parentKey !== key && byKey.has(parentKey)) {
      const p = byKey.get(parentKey);
      ch.parent_sequence =
        String(ch.parent_sequence || "").trim() || String(p?.sequence || "").trim();
      continue;
    }
    const parentSeq = seq.split(".").slice(0, -1).join(".");
    const parent = bySeq.get(parentSeq);
    if (parent?.key && String(parent.key) !== key) {
      ch.parent = parent.key;
      ch.parent_sequence = parentSeq;
      ch.level = parentSeq.split(".").length + 1;
    } else {
      ch.parent = "";
      ch.parent_sequence = "";
    }
  }
  return chapters;
}

function applyChapterPreview(preview, { switchTopology = false, allowShrink = false } = {}) {
  const list = ensureChapterParents(preview || []);
  if (!list.length) return;
  const prev = outlineNodes.value || [];
  // 向导前 8 章、写作进度焦点章、当前分析子树都可能比全量目录短：
  // 只合入同 key 的要点/状态，禁止把 200 章换成 2 章。
  const keepMissing = !allowShrink && prev.length > list.length;
  outlineNodes.value = mergeOutlinePreserveElementIds(prev, list, { keepMissing });
  extraChapters.value = outlineNodes.value.map((ch) => ({
    key: ch.key,
    sequence: ch.sequence,
    title: ch.title,
    parent: ch.parent,
    parent_sequence: ch.parent_sequence,
    level: ch.level,
    structure_only: Boolean(ch.structure_only),
    points: ch.points || [],
    element_ids: Array.isArray(ch.element_ids) ? [...ch.element_ids] : [],
    status: ch.status || "",
  }));
  if (!selectedKey.value) selectedKey.value = list[0]?.key || "";
  if (switchTopology && isSampleStudyDoc.value) {
    // 未确认目录时偏拓扑核对；已确认后默认章节详情（不 remount，避免大目录卡顿）
    mainView.value = outlineConfirmed.value ? "chapter" : "topology";
  }
}

function scheduleLoadFullOutline({ force = false } = {}) {
  clearTimeout(outlineLoadTimer);
  // 预约拉取时立刻遮罩，避免打开后空白可点
  loadLabel.value = force ? "正在拉取目录…" : "正在刷新目录…";
  loading.value = true;
  outlineLoadTimer = setTimeout(() => {
    loadFullOutline({ force }).catch(() => {
      loading.value = false;
    });
  }, force ? 80 : 280);
}

function onSyncClick() {
  if (maskVisible.value) return;
  emit("sync-request");
}

function requestClose() {
  if (closing.value) return;
  closing.value = true;
  loadLabel.value = "正在关闭文档窗口…";
  emit("close");
}

function snapshotOutlineCache(tid) {
  const id = String(tid || "").trim();
  if (!id || !outlineFullyLoaded.value || !outlineNodes.value.length) return;
  outlineCacheByTask.set(id, {
    outlineNodes: outlineNodes.value.map((ch) => ({ ...ch })),
    extraChapters: extraChapters.value.map((ch) => ({ ...ch })),
    elementNodes: Array.isArray(elementNodes.value) ? elementNodes.value.map((n) => ({ ...n })) : [],
    selectedKey: String(selectedKey.value || ""),
    outlineConfirmed: Boolean(outlineConfirmedLocal.value),
  });
}

function restoreOutlineCache(tid) {
  const id = String(tid || "").trim();
  const hit = id ? outlineCacheByTask.get(id) : null;
  if (!hit?.outlineNodes?.length) return false;
  outlineNodes.value = hit.outlineNodes.map((ch) => ({ ...ch }));
  extraChapters.value = (hit.extraChapters || []).map((ch) => ({ ...ch }));
  elementNodes.value = Array.isArray(hit.elementNodes) ? hit.elementNodes.map((n) => ({ ...n })) : [];
  selectedKey.value = String(hit.selectedKey || hit.outlineNodes[0]?.key || "");
  outlineConfirmedLocal.value = Boolean(hit.outlineConfirmed);
  outlineFullyLoaded.value = true;
  loadedOutlineTaskId = id;
  hydrateConfigDraft();
  return true;
}

function clearLocalOutlineState() {
  outlineNodes.value = [];
  extraChapters.value = [];
  elementNodes.value = [];
  selectedKey.value = "";
  checkedKeys.value = [];
  applyCheckedKeysToTree([]);
  outlineFullyLoaded.value = false;
  Object.assign(configDraft, emptyConfigDraft());
  loadedOutlineTaskId = "";
  pendingChapterElementIds.clear();
}

function applyPreviewPayload(tid, data) {
  previewLoadedTid.value = tid;
  previewHtml.value = String(data?.html || "");
  previewWrittenCount.value = Number(data?.written_count || 0);
  previewChapterCount.value = Number(data?.chapter_count || 0);
  previewError.value = "";
  previewCacheByTask.set(tid, {
    html: previewHtml.value,
    written_count: previewWrittenCount.value,
    chapter_count: previewChapterCount.value,
  });
}

function clearPreviewState() {
  previewHtml.value = "";
  previewWrittenCount.value = 0;
  previewChapterCount.value = 0;
  previewLoadedTid.value = "";
  previewError.value = "";
}

function beginTabSwitch(label = "正在切换视图…") {
  clearTimeout(tabSwitchTimer);
  tabSwitchLabel.value = label;
  tabSwitching.value = true;
}

function endTabSwitch(delayMs = 240) {
  clearTimeout(tabSwitchTimer);
  tabSwitchTimer = setTimeout(() => {
    tabSwitching.value = false;
  }, Math.max(80, Number(delayMs) || 240));
}

function switchMainView(view) {
  const next = String(view || "").trim();
  if (!next || next === mainView.value) return;
  if (tabControlsDisabled.value) return;
  if (next === "topology") topologyEverOpened.value = true;
  mainView.value = next;
  // 预览后台拉，不挡切 tab
  if (next === "preview") void loadDraftPreview();
}

function switchDetailTab(tab) {
  const next = normalizeDetailTab(tab);
  if (next === detailTab.value) return;
  if (tabControlsDisabled.value) return;
  detailTab.value = next;
}

function openDraftPreview() {
  switchMainView("preview");
}

function refreshCurrentDoc() {
  if (refreshDisabled.value) {
    if (props.opsLocked) {
      ElMessage.warning(docSwitchBlockReason.value || "操作进行中，请稍后再刷新");
    }
    return;
  }
  beginTabSwitch("正在刷新文档…");
  loadLabel.value = "正在刷新文档…";
  scheduleLoadFullOutline({ force: true });
  const finish = () => endTabSwitch(180);
  if (mainView.value === "preview") {
    loadDraftPreview({ force: true }).finally(finish);
  } else {
    // loading 遮罩会接棒；短暂保留切页遮罩再交给拉取态
    finish();
  }
}

async function loadDraftPreview({ force = false } = {}) {
  const tid = previewTaskId.value;
  if (!tid) {
    clearPreviewState();
    previewError.value = "当前文档还没有可预览的写作任务";
    return;
  }
  if (!force && previewLoadedTid.value === tid && previewHtml.value) {
    return;
  }
  if (!force) {
    const cached = previewCacheByTask.get(tid);
    if (cached?.html) {
      applyPreviewPayload(tid, cached);
      // 后台静默刷新
    }
  }
  previewLoading.value = true;
  previewError.value = "";
  try {
    const data = await request(
      `/api/writing/tasks/${encodeURIComponent(tid)}/draft-preview?written_only=true&confirmed_only=false`
    );
    // 切文档后丢弃过期响应
    if (previewTaskId.value !== tid) return;
    applyPreviewPayload(tid, data || {});
    // force 刷新且真无正文时才清空；非 force 保留已有展示，避免错误 tid 误伤缓存
    if (force && (!previewHtml.value || !previewWrittenCount.value)) {
      previewHtml.value = "";
    } else if (!force && !previewHtml.value && !previewWrittenCount.value) {
      previewHtml.value = "";
    }
  } catch (error) {
    if (previewTaskId.value !== tid) return;
    previewError.value = error?.message || "预览加载失败";
    if (!previewCacheByTask.has(tid)) clearPreviewState();
  } finally {
    if (previewTaskId.value === tid) previewLoading.value = false;
  }
}

let lastDocIdentity = "";
watch(
  () =>
    [
      props.activeId,
      activeDoc.value?.taskId,
      activeDoc.value?.blueprintId,
      activeDoc.value?.writingTaskId,
    ].join("|"),
  (identity) => {
    if (!identity || identity === lastDocIdentity) {
      // 同一文档仅写作进度字段变化：不重拉全量目录，避免卡顿
      return;
    }
    const nextTid = outlineSourceTaskId(activeDoc.value);
    const prevTid = loadedOutlineTaskId;
    // 跨蓝本/任务：先缓存上一份；优先恢复目标缓存，避免先清空导致下拉后空白卡几秒
    if (prevTid && prevTid !== nextTid) {
      snapshotOutlineCache(prevTid);
      fetchToken += 1;
      outlineInflight = null;
      outlineInflightTid = "";
    }
    lastDocIdentity = identity;
    const preview = Array.isArray(activeDoc.value?.chapters) ? activeDoc.value.chapters : [];
    const restored = restoreOutlineCache(nextTid);
    if (!restored) {
      clearLocalOutlineState();
      if (preview.length) {
        applyChapterPreview(preview, {
          switchTopology: true,
          allowShrink: true,
        });
        if (preview[0]?.key) selectedKey.value = preview[0].key;
      }
    }
    saveHint.value = "";
    // 切文档：立刻摘掉上一份资料包，避免空目录/未拉完大纲时仍显示旧绑
    void resetMaterialsPackForActiveDoc();
    // 切文档时按文档项对齐；确认态只升不降见下方 outlineConfirmed watch
    if (!restored) {
      outlineConfirmedLocal.value = Boolean(activeDoc.value?.outlineConfirmed);
    } else if (activeDoc.value?.outlineConfirmed) {
      outlineConfirmedLocal.value = true;
    }
    // 有缓存先展示再软刷新；无缓存才 force。
    // 若文档项已确认而缓存仍是待确认，必须 force，避免确认态不同步
    const needConfirmSync =
      Boolean(activeDoc.value?.outlineConfirmed) && !outlineConfirmedLocal.value;
    if (nextTid) {
      scheduleLoadFullOutline({ force: !restored || needConfirmSync });
    } else {
      loading.value = false;
    }
    // 全文预览按文档隔离：切文档后清掉上一份展示，若停在预览页则重载本份
    const nextPreviewTid = resolveWritingTaskId(activeDoc.value);
    if (previewLoadedTid.value && previewLoadedTid.value !== nextPreviewTid) {
      clearPreviewState();
      const cached = nextPreviewTid ? previewCacheByTask.get(nextPreviewTid) : null;
      if (cached?.html) applyPreviewPayload(nextPreviewTid, cached);
    }
    if (mainView.value === "preview") {
      const hasPreviewCache = Boolean(nextPreviewTid && previewCacheByTask.get(nextPreviewTid)?.html);
      loadDraftPreview({ force: !hasPreviewCache });
    }
  },
  { immediate: true }
);

// writingTaskId 晚到时：若停在预览页则强制重载（identity 已含 writingTaskId，此处兜底同 id 字段补齐）
watch(
  () => String(activeDoc.value?.writingTaskId || "").trim(),
  (wid, prev) => {
    if (!wid || wid === prev) return;
    if (mainView.value === "preview") {
      loadDraftPreview({ force: true });
    } else if (prev) {
      previewCacheByTask.delete(prev);
    }
  }
);

// 文档项已确认：只升不降，避免向导卡短暂缺 flag 时打回「待确认目录」
watch(
  () => Boolean(activeDoc.value?.outlineConfirmed),
  (confirmed) => {
    if (confirmed) outlineConfirmedLocal.value = true;
  }
);

// 向导推进到学习/完成：立刻按步对齐确认态，并补拉一次真值
watch(
  () => String(activeDoc.value?.wizardStep || ""),
  (step, prev) => {
    if (!step || step === prev) return;
    if (step === "study" || step === "study_done" || step === "done") {
      outlineConfirmedLocal.value = true;
    }
    if (!["outline_confirm", "study", "study_done", "done"].includes(step)) return;
    scheduleLoadFullOutline({ force: true });
  }
);

// 学习完成计数变化：节流软刷新，避免每章学完都全量重绘
watch(
  () =>
    [
      Number(props.outlineReloadToken || 0),
      Number(activeDoc.value?.study_analyzed ?? -1),
      Number(activeDoc.value?.study_unanalyzed ?? -1),
    ].join("|"),
  (sig, prev) => {
    if (!sig || sig === prev) return;
    if (!taskId.value) return;
    scheduleLoadFullOutline({ force: false });
  }
);

// 操作完成后强制直读库，避免文档窗仍显示旧章节状态/配置
watch(
  () => Number(props.outlineForceReloadToken || 0),
  (token, prev) => {
    if (!token || token === prev) return;
    if (!taskId.value && !outlineSourceTaskId()) return;
    const tid = outlineSourceTaskId();
    const bp = String(taskId.value || "").trim();
    if (tid) outlineCacheByTask.delete(tid);
    if (bp && bp !== tid) outlineCacheByTask.delete(bp);
    // 作废进行中的软刷新，确保本次 force 走 ?fresh=1
    fetchToken += 1;
    outlineInflight = null;
    outlineInflightTid = "";
    scheduleLoadFullOutline({ force: true });
    if (mainView.value === "preview") {
      loadDraftPreview({ force: true });
    } else if (previewTaskId.value) {
      // 写作后失效本任务预览缓存，下次打开拉新稿
      previewCacheByTask.delete(previewTaskId.value);
      if (previewLoadedTid.value === previewTaskId.value) clearPreviewState();
    }
  }
);

// 会话预建资料包变化：无写作任务时立刻展示；切到无预建时也要清空，勿留旧包
watch(
  () => String(props.pendingMaterialsPackId || "").trim(),
  (pid, prev) => {
    if (pid === prev) return;
    // 已有写作副本绑定时，正式包由目录拉取对齐；预建变化仅在无副本时驱动展示
    if (materialsPackTargetTid.value) {
      if (!pid && !materialsPackIdLocal.value) {
        materialsPack.value = null;
      }
      return;
    }
    void loadMaterialsPack(pid);
  }
);

// live/卡片章节变化时同步要点与状态；目录变短时不得替换已加载的全量树
watch(
  () => {
    const ch = activeDoc.value?.chapters || [];
    return ch
      .map((c) => {
        const pts = Array.isArray(c?.points) && c.points.length
          ? c.points.length
          : Array.isArray(c?.config?.points)
            ? c.config.points.length
            : 0;
        return `${c?.key || ""}:${pts}:${c?.structure_only ? 1 : 0}`;
      })
      .join("|");
  },
  (sig, prev) => {
    if (!sig || sig === prev) return;
    const preview = activeDoc.value?.chapters || [];
    if (!preview.length) return;
    // 只同步章节预览，不强制切 tab / remount 拓扑（否则 live 更新会卡顿）
    applyChapterPreview(preview, { switchTopology: false, allowShrink: false });
  }
);

watch(
  mainView,
  (view) => {
    if (view === "topology") topologyEverOpened.value = true;
  },
  { immediate: true }
);

watch(
  () => Boolean(props.opsLocked),
  (locked) => {
    if (!locked) analyzingKey.value = "";
  }
);

watch(
  () => [selectedKey.value, selectedOutlineNode.value],
  () => {
    hydrateConfigDraft();
  }
);

watch(
  () => props.documents.map((item) => item.id).join("|"),
  () => {
    // 会话清空文档列表时必须丢掉本地大纲缓存，禁止展示上一会话残留
    if (!props.documents.length) {
      clearLocalOutlineState();
      lastDocIdentity = "";
      return;
    }
    if (!props.documents.some((item) => item.id === props.activeId)) {
      const rawActive = String(props.activeId || "").trim();
      const activeNorm = /^bp:(w.+)$/i.test(rawActive)
        ? `task:${rawActive.slice(3)}`
        : rawActive;
      const remapped = props.documents.find((item) => item.id === activeNorm);
      if (remapped?.id) {
        emit("update:activeId", remapped.id);
      } else if (props.opsLocked) {
        // 写作/分析锁定中：勿硬切到列表第一项
        return;
      } else {
        const prefer =
          props.documents.find((d) => (d.chapters || []).length > 0 && d.kind === "outline") ||
          props.documents[props.documents.length - 1];
        if (prefer?.id) emit("update:activeId", prefer.id);
      }
    }
  }
);

function chapterLabel(ch) {
  const seq = String(ch?.sequence || "").trim();
  const title = String(ch?.title || ch?.key || "未命名章节").trim();
  return seq ? `${seq} ${title}` : title;
}

function onPickDocument(id) {
  const next = String(id || "").trim();
  if (!next || next === String(props.activeId || "").trim()) return;
  if (docSwitchBlocked.value) {
    ElMessage.warning(docSwitchBlockReason.value);
    return;
  }
  // 立刻通知父级改 activeId（下拉名称马上变）；勿本地再挡一层遮罩
  emit("update:activeId", next);
}

function scrollTreeToKey(key) {
  const target = String(key || "").trim();
  if (!target) return;
  nextTick(() => {
    chapterOutlineTreeRef.value?.setCurrentKey?.(target);
    const root = document.querySelector(".doc-win-tree");
    if (!root) return;
    const active =
      root.querySelector(".el-tree-node.is-current > .el-tree-node__content") ||
      root.querySelector(".tree-node-row.active");
    if (active && typeof active.scrollIntoView === "function") {
      active.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });
}

function normalizeDetailTab(tab) {
  const raw = String(tab || "").trim();
  if (raw === "content" || raw === "writing" || raw === "prep") return "content";
  if (raw === "draft" || raw === "process" || raw === "text" || raw === "preview") return "content";
  if (raw === "config") return "config";
  return "config";
}

function selectChapter(key, tab = "config", opts = {}) {
  selectedKey.value = key;
  const nextTab = normalizeDetailTab(tab);
  detailTab.value = nextTab;
  const mv = String(opts.mainView || "").trim();
  if (mv) {
    mainView.value = mv;
  } else if (opts.skipMainView !== true) {
    mainView.value = "chapter";
  }
  const doc = activeDoc.value;
  // 正文真值在写作 clone：focus 优先 writingTaskId，避免蓝本 id 对不上草稿
  const focusTid = String(
    doc?.writingTaskId || doc?.taskId || taskId.value || ""
  ).trim();
  emit("focus-chapter", {
    documentId: doc?.id || "",
    taskId: focusTid,
    chapterKey: key,
    openConfig: nextTab === "config",
    tab: nextTab,
    mainView: mv || mainView.value,
  });
  scrollTreeToKey(key);
}

function firstStudyTargetKey() {
  const list = Array.isArray(chapters.value) ? chapters.value : [];
  const hit = list.find((ch) => chapterNeedsStudy(ch));
  return String(hit?.key || "").trim();
}

function firstWriteTargetKey() {
  const list = Array.isArray(chapters.value) ? chapters.value : [];
  const running = list.find((ch) => ch?.is_running);
  if (running?.key) return String(running.key).trim();
  const next = list.find((ch) => ch?.is_next);
  if (next?.key) return String(next.key).trim();
  const pending = list.find(
    (ch) =>
      !chapterIsStructure(ch) &&
      !chapterNeedsStudy(ch) &&
      !["confirmed", "awaiting_confirm"].includes(String(ch?.status || ""))
  );
  if (pending?.key) return String(pending.key).trim();
  return String(selectedKey.value || "").trim();
}

function applyLocate(cmd) {
  const key = String(cmd?.chapterKey || "").trim();
  const reason = String(cmd?.reason || "").trim();
  let tab = String(cmd?.tab || "config").trim() || "config";
  const mainViewHint = String(cmd?.mainView || "").trim();
  const writeLocate =
    reason === "write_chapter_done" ||
    reason === "write_awaiting_confirm" ||
    reason === "write_running" ||
    reason === "write_batch_advance";
  if (writeLocate) {
    tab = "content";
    mainView.value = mainViewHint || "preview";
    if (key) {
      selectChapter(key, tab, { mainView: "preview" });
    }
    void loadDraftPreview({ force: true });
  } else if (reason === "analyze") {
    tab = "config";
    if (key) analyzingKey.value = key;
    // 无蓝本要点流：事件里带 points 时立刻合入，避免右侧仍显示「待配要点」
    if (key && (Array.isArray(cmd?.points) || Array.isArray(cmd?.keywords) || cmd?.describes)) {
      applyChapterPointsLocal(key, {
        points: cmd.points,
        keywords: cmd.keywords,
        describes: cmd.describes,
        structure_only: cmd.structure_only,
      });
      outlineConfirmedLocal.value = true;
    }
  }
  // 有具体章节时优先进详情树，避免停在拓扑图看不到对应段
  if (key && (tab === "topology" || tab === "preview") && !writeLocate) {
    if (tab === "topology") {
      tab = "config";
    }
  }
  const tid = String(cmd?.taskId || "").trim();
  const elementId = String(cmd?.elementId || "").trim();
  const wantKnowledge = Boolean(cmd?.associateKnowledge) || reason === "associate_element_knowledge";
  // 关联确认后乐观合并要素库与本章 element_ids，标签立刻可见
  if (
    reason === "elements_associated" ||
    (reason === "config_saved" && Array.isArray(cmd?.elementIds))
  ) {
    const incoming = Array.isArray(cmd?.elementNodes) ? cmd.elementNodes : [];
    if (key && Array.isArray(cmd?.elementIds)) {
      applyChapterElementIdsLocal(key, cmd.elementIds, incoming, {
        newElementIds: cmd.newElementIds,
        pointItemLink: cmd.pointItemLink,
        pointItems: cmd.pointItems,
        associateScope: cmd.associateScope,
      });
    } else if (incoming.length) {
      elementNodes.value = mergeElementNodesLocal(elementNodes.value, incoming);
    }
  }
  if (reason === "library_pending") {
    openLibraryPendingAfterRefresh = true;
    if (collectElementsCount.value > 0) {
      openLibraryPendingAfterRefresh = false;
      nextTick(() => openLibraryPendingDialog(true));
    }
  }
  // 写作完成：立刻把该章标成已写作，避免目录仍显示「已分析」要靠点击才变
  if (
    key &&
    (reason === "write_chapter_done" || reason === "write_awaiting_confirm")
  ) {
    markChapterWrittenLocal(key);
  }
  // 写作任务 id 常挂在蓝本项的 writingTaskId 上：当前文档已匹配则不要切走
  if (tid && taskId.value && tid !== taskId.value) {
    const cur = activeDoc.value;
    const currentMatches =
      String(cur?.writingTaskId || "") === tid ||
      String(cur?.taskId || "") === tid ||
      String(cur?.blueprintId || "") === tid;
    if (!currentMatches) {
      const doc = (props.documents || []).find(
        (d) =>
          String(d.taskId || "") === tid ||
          String(d.blueprintId || "") === tid ||
          String(d.writingTaskId || "") === tid
      );
      // 定位目标不在当前会话文档列表 → 忽略，禁止串到无关蓝本
      if (!doc?.id) return;
      emit("update:activeId", doc.id);
    }
  } else if (tid && !(props.documents || []).length) {
    // 无文档登记时不因 locate 残留 taskId 拉旧大纲
    return;
  }
  if (writeLocate) {
    if (mainViewHint === "chapter" && key) {
      selectChapter(key, tab);
    } else if (!key && (mainViewHint || tab === "preview")) {
      mainView.value = mainViewHint || "preview";
      void loadDraftPreview({ force: true });
    }
  } else if (key) {
    selectChapter(key, tab);
  } else if (tab === "topology") {
    mainView.value = "topology";
  } else if (tab === "preview") {
    mainView.value = "preview";
    void loadDraftPreview({ force: true });
  } else if (tab) {
    mainView.value = "chapter";
    detailTab.value = normalizeDetailTab(tab);
  }
  if (elementId) {
    nextTick(() => {
      const hit = elementNodes.value.find((el) => String(el?.id || "") === elementId);
      if (hit) openElementEditor(hit, { preferFile: true });
    });
  } else if (wantKnowledge) {
    ElMessage.info("请把要素类型设为「文件」并上传、解析；项目资料用「资料库」资料包");
  }
}

watch(
  () => props.locate?.token,
  (token, prev) => {
    if (!token || token === prev) return;
    nextTick(() => applyLocate(props.locate || {}));
  }
);

function onDraftInput(event) {
  emit("update:draftText", event?.target?.value || "");
}

function onDraftHtml(html) {
  emit("update:draftText", html || "");
}

function emitWrite(action, extra = {}) {
  const tid = String(taskId.value || "").trim();
  const bid = String(activeDoc.value?.blueprintId || "").trim();
  const writingTid = String(activeDoc.value?.writingTaskId || "").trim();
  const projectIds = (projectFolderIdsLocal.value || [])
    .map((x) => String(x || "").trim())
    .filter(Boolean);
  emit("write-action", {
    action,
    // 一律当前文档 id，禁止父级用过期 selectedBlueprint 兜底串蓝本
    taskId: tid,
    blueprintId: bid || tid,
    writingTaskId: writingTid || tid,
    chapterKey: selectedKey.value,
    chapter: selectedChapter.value,
    project_folder_ids: projectIds,
    project_folder_label: projectFolderLabel.value || folderLabelFromTree(projectIds),
    ...extra,
  });
}

function emitWizard(payload) {
  // 向导身份用库蓝本 id；学习/落盘必须打文档副本 w*
  const bid = String(
    activeDoc.value?.blueprintId ||
      (String(activeDoc.value?.kind || "") === "blueprint" ? activeDoc.value?.taskId : "") ||
      ""
  ).trim();
  const wid = String(activeDoc.value?.writingTaskId || outlineSourceTaskId() || "").trim();
  const tid = wid || String(taskId.value || "").trim();
  emit("wizard-action", {
    blueprint_id: bid || tid,
    task_id: tid,
    working_task_id: wid || undefined,
    writingTaskId: wid || undefined,
    ...payload,
  });
}

function openRestudyDialog({ keys, sequences, label, key } = {}) {
  const chapterKeys = (keys || []).map((k) => String(k || "").trim()).filter(Boolean);
  if (!chapterKeys.length) {
    ElMessage.warning("请先选择章节");
    return;
  }
  restudyTarget.value = {
    keys: chapterKeys,
    sequences: (sequences || []).map((s) => String(s || "").trim()).filter(Boolean),
    label: String(label || "").trim() || "当前章节",
    key: String(key || chapterKeys[0] || "").trim(),
  };
  restudyMethod.value = boundRestudyMethod.value;
  restudyPasteText.value = "";
  restudyDialogVisible.value = true;
  nextTick(() => {
    if (restudyPasteBox.value) restudyPasteBox.value.innerHTML = "";
  });
}

function onRestudyDialogClosed() {
  restudyPasteText.value = "";
  if (restudyPasteBox.value) restudyPasteBox.value.innerHTML = "";
}

function onRestudyPasteInput(event) {
  const el = event?.target;
  restudyPasteText.value = String(el?.innerText || el?.textContent || "");
}

function onRestudyRichPaste(event) {
  const recovered = recoverClipboardPlain(event);
  if (!recovered.trim()) return;
  event.preventDefault();
  const box = restudyPasteBox.value;
  if (!box) return;
  const selection = window.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  if (range && box.contains(range.commonAncestorContainer)) {
    range.deleteContents();
    const node = document.createTextNode(recovered);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
  } else {
    box.innerText = recovered;
  }
  restudyPasteText.value = box.innerText || recovered;
}

async function submitRestudyDialog() {
  const target = restudyTarget.value || {};
  const keys = (target.keys || []).map((k) => String(k || "").trim()).filter(Boolean);
  const method = String(restudyMethod.value || boundRestudyMethod.value).trim();
  if (!keys.length) {
    restudyDialogVisible.value = false;
    return;
  }
  if (method === "source" && !String(restudyPasteText.value || "").trim()) {
    ElMessage.warning("请先粘贴本章原文");
    return;
  }
  if (!(await flushConfigBeforeWrite())?.ok) return;
  const focusKey = String(target.key || keys[0] || "").trim();
  if (focusKey) selectChapter(focusKey, "config");
  restudyDialogVisible.value = false;
  if (method === "auto") {
    emitWrite("generate_freeform_points", {
      chapterKeys: keys,
      chapterKey: focusKey || undefined,
    });
    return;
  }
  emitWizard({
    step: "study",
    wizard_command: "re_study_chapters",
    chapter_keys: keys,
    chapter_sequences: target.sequences || [],
    study_message: target.label || "",
    study_scope: keys.length === 1 ? "self" : "selected",
    study_method: method,
    study_text: method === "source" ? String(restudyPasteText.value || "").trim() : "",
    locate_chapter_key: focusKey,
  });
}

async function confirmOutlineFromDoc() {
  const tid = String(taskId.value || "").trim();
  if (!tid || saving.value) return;
  saving.value = true;
  saveHint.value = "正在确认目录…";
  try {
    // 先直连任务确认，避免向导步已到 study 时被幂等短路、任务仍未确认
    const task = await request(`/api/writing/tasks/${encodeURIComponent(tid)}/outline/confirm`, {
      method: "POST",
    });
    outlineConfirmedLocal.value = Boolean(task?.outline_confirmed ?? true);
    // 确认与学习分开：确认后进入学习步，但不自动开跑
    emitWizard({
      step: "study",
      outline_confirmed: true,
      trigger_begin_study: false,
    });
    await loadFullOutline({ force: true });
    // 防止并发软刷新用旧缓存盖掉确认态
    outlineConfirmedLocal.value = true;
    ElMessage.success("目录已确认，可再决定全部分析或指定章节");
    saveHint.value = "";
  } catch (error) {
    saveHint.value = "";
    ElMessage.error(error?.message || "确认目录失败");
  } finally {
    saving.value = false;
  }
}

function isOutlinePhaseAction(act) {
  const id = typeof act === "string" ? act : String(act?.id || "");
  return (
    id === "confirm_outline" ||
    id === "re_extract" ||
    id === "re_upload" ||
    id === "go_outline_confirm" ||
    id === "add_chapter" ||
    id === "remove_chapter" ||
    id === "study_all" ||
    id === "add_chapter" ||
    id === "remove_chapters" ||
    id === "study_chapter" ||
    id === "write_analyzed" ||
    id === "write_points_ready" ||
    id === "generate_points" ||
    id === "regenerate_outline" ||
    id === "save_as_blueprint" ||
    id === "save_and_retry" ||
    id === "start_writing" ||
    id === "retry" ||
    id === "associate_elements"
  );
}

function isSelectedWizardDoc() {
  return String(activeDoc.value?.wizardMode || activeDoc.value?.mode || "").trim() === "selected";
}

function onWizardStepperClick(row) {
  if (!row?.done || row.current || blockingLoad.value) return;
  const id = String(row.id || "").trim();
  if (id === "meta") {
    ElMessage.info("名称可在文档窗口标题处修改");
    return;
  }
  if (isSelectedWizardDoc()) {
    if (id === "outline_confirm") {
      emitWizard({ step: "outline_confirm", wizard_command: "go_outline_confirm" });
    }
    return;
  }
  if (id === "upload") {
    outlineConfirmedLocal.value = false;
    emitWizard({
      wizard_command: "re_upload_sample",
      step: "upload",
      outline_confirmed: false,
    });
    return;
  }
  if (id === "outline") {
    outlineConfirmedLocal.value = false;
    emitWizard({
      wizard_command: "re_extract_outline",
      step: "outline",
      outline_confirmed: false,
    });
    return;
  }
  if (id === "outline_confirm") {
    emitWizard({ step: "outline_confirm", wizard_command: "go_outline_confirm" });
  }
}

async function runPhaseAction(act) {
  // 确认/改目录不跟对话 sending 绑死：提取刚结束时常还在落库，按钮必须能点
  const id = String(act?.id || "");
  if (blockingLoad.value) return;
  const flushFirst =
    id === "write_analyzed" ||
    id === "start_writing" ||
    id === "save_and_retry" ||
    id === "retry" ||
    id === "study_all" ||
    id === "study_chapter";
  if (saving.value && !flushFirst) return;
  if (props.busy && !isOutlinePhaseAction(id)) return;
  if (id === "confirm_outline") {
    if (isFreeformDoc.value) {
      // 无蓝本：确认大纲并自动生成写作信息；树有勾选则只起草勾选章要点
      const keys = orderedCheckedKeys();
      emitWrite("confirm_freeform_outline", {
        ...(keys.length ? { chapterKeys: keys } : {}),
      });
      return;
    }
    confirmOutlineFromDoc();
    return;
  }
  if (id === "regenerate_outline") {
    if (!isFreeformDoc.value) return;
    emitWrite("regenerate_freeform_outline", {
      taskId: String(taskId.value || "").trim(),
    });
    return;
  }
  if (id === "generate_points") {
    const scope = String(act?.scope || "").trim() || "bulk";
    const missingKeys = outlineNodes.value
      .filter((ch) => chapterNeedsStudy(ch) && !chapterIsStructure(ch))
      .map((ch) => String(ch.key || "").trim())
      .filter(Boolean);
    const writableKeys = outlineNodes.value
      .filter((ch) => !chapterIsStructure(ch))
      .map((ch) => String(ch.key || "").trim())
      .filter(Boolean);
    const selected = String(selectedKey.value || "").trim();
    let chapterKeys;
    let focusKey;
    if (scope === "chapter") {
      focusKey = selected;
      if (!focusKey) {
        ElMessage.warning("请先选择章节");
        return;
      }
      chapterKeys = [focusKey];
    } else {
      // 文档级：有勾选优先勾选；否则缺要点章 / 全量可写章
      const fallback = missingKeys.length ? missingKeys : writableKeys;
      chapterKeys = scopedOrFallbackKeys(fallback);
      focusKey =
        (selected && chapterNeedsStudy(selectedChapter.value) ? selected : "") ||
        chapterKeys[0] ||
        selected;
    }
    if (focusKey) selectChapter(focusKey, "config");
    emitWrite("generate_freeform_points", {
      chapterKeys: chapterKeys.length ? chapterKeys : undefined,
      chapterKey: focusKey || undefined,
    });
    return;
  }
  if (id === "write_points_ready") {
    if (!(await flushConfigBeforeWrite())?.ok) return;
    const readyKeys = outlineNodes.value
      .filter((ch) => !chapterNeedsStudy(ch) && !chapterIsStructure(ch))
      .map((ch) => String(ch.key || "").trim())
      .filter(Boolean);
    const chapterKeys = scopedOrFallbackKeys(readyKeys).filter((k) => {
      const ch = chapters.value.find((c) => String(c?.key || "") === k);
      return ch && !chapterNeedsStudy(ch) && !chapterIsStructure(ch);
    });
    const focusKey = chapterKeys[0] || String(selectedKey.value || "").trim();
    if (!chapterKeys.length) {
      ElMessage.info("请先勾选已配要点的章节，或先生成写作要点");
      return;
    }
    if (focusKey) selectChapter(focusKey, "content");
    emitWrite("start_writing", buildWriteActionPayload(chapterKeys, focusKey));
    return;
  }
  if (id === "save_as_blueprint") {
    if (!showSaveAsBlueprint.value) {
      ElMessage.info(
        showSyncToBlueprintLibrary.value
          ? "当前已有源蓝本，请用「同步到蓝本库」回写改动"
          : "请先确认大纲后再收录到蓝本库"
      );
      return;
    }
    emitWrite("save_freeform_as_blueprint", {
      taskId: String(taskId.value || "").trim(),
    });
    return;
  }
  if (id === "sync_to_blueprint_library") {
    if (!showSyncToBlueprintLibrary.value) {
      ElMessage.info("当前没有可回写的源蓝本；尚未入库请先「收录到蓝本库」");
      return;
    }
    const syncTid = String(
      activeDoc.value?.writingTaskId ||
        activeDoc.value?.taskId ||
        taskId.value ||
        ""
    ).trim();
    emitWrite("sync_document_to_blueprint_library", { taskId: syncTid });
    return;
  }
  if (id === "re_upload") {
    if (isSelectedWizardDoc()) {
      ElMessage.info("选用已有蓝本没有上传样板步骤");
      return;
    }
    outlineConfirmedLocal.value = false;
    emitWizard({
      wizard_command: "re_upload_sample",
      outline_confirmed: false,
      step: "upload",
    });
    return;
  }
  if (id === "re_extract") {
    if (isSelectedWizardDoc()) {
      ElMessage.info("选用已有蓝本请直接改目录或重新分析");
      return;
    }
    outlineConfirmedLocal.value = false;
    emitWizard({
      wizard_command: "re_extract_outline",
      outline_confirmed: false,
      step: "outline",
    });
    return;
  }
  if (id === "go_outline_confirm") {
    emitWizard({
      wizard_command: "go_outline_confirm",
      step: "outline_confirm",
    });
    return;
  }
  if (id === "study_all") {
    if (!outlineConfirmed.value) {
      // 只打开确认目录，不自动确认
      ElMessage.info("请先确认目录后再分析");
      emitWizard({ step: "outline_confirm", wizard_command: "go_outline_confirm" });
      return;
    }
    if (!(await flushConfigBeforeWrite())?.ok) return;
    const focusKey = firstStudyTargetKey() || String(selectedKey.value || "").trim();
    if (focusKey) selectChapter(focusKey, "config");
    emitWizard({
      step: "study",
      wizard_command: "go_study",
      // 仅用于小窗定位，不限制实际分析范围
      ...(focusKey ? { locate_chapter_key: focusKey } : {}),
    });
    return;
  }
  if (id === "write_analyzed") {
    if (!(await flushConfigBeforeWrite())?.ok) return;
    const analyzedKeys = outlineNodes.value
      .filter((ch) => !chapterNeedsStudy(ch) && !chapterIsStructure(ch))
      .map((ch) => String(ch.key || "").trim())
      .filter(Boolean);
    const chapterKeys = scopedOrFallbackKeys(analyzedKeys).filter((k) => {
      const ch = chapters.value.find((c) => String(c?.key || "") === k);
      return ch && !chapterNeedsStudy(ch) && !chapterIsStructure(ch);
    });
    const focusKey = chapterKeys[0] || firstWriteTargetKey();
    if (!chapterKeys.length) {
      ElMessage.info("请先勾选已分析章节，或先完成分析");
      return;
    }
    if (focusKey) selectChapter(focusKey, "preview", { mainView: "preview" });
    emitWrite("write_analyzed_only", buildWriteActionPayload(chapterKeys, focusKey || selectedKey.value));
    return;
  }
  if (id === "study_chapter") {
    const key = selectedKey.value;
    if (!key) return;
    if (!outlineConfirmed.value) {
      ElMessage.info("请先确认目录后再分析");
      emitWizard({ step: "outline_confirm", wizard_command: "go_outline_confirm" });
      return;
    }
    openRestudyDialog({
      keys: [key],
      sequences: [
        String(selectedChapter.value?.sequence || selectedOutlineNode.value?.sequence || "").trim(),
      ].filter(Boolean),
      label: chapterLabel(selectedChapter.value || selectedOutlineNode.value),
      key,
    });
    return;
  }
  if (id === "save_config") {
    await saveChapterConfig();
    return;
  }
  if (id === "add_chapter") {
    addChapter();
    return;
  }
  if (id === "remove_chapters") {
    await removeSelectedChapters();
    return;
  }
  if (id === "start_writing") {
    const flushed = await flushConfigBeforeWrite();
    if (!flushed?.ok) return;
    const readyKeys = outlineNodes.value
      .filter((ch) => !chapterIsStructure(ch) && !chapterNeedsStudy(ch))
      .map((ch) => String(ch.key || "").trim())
      .filter(Boolean);
    // 有勾选：只写勾选；无勾选且按钮标「全部」时写全部可写章；否则写当前/首个目标章
    const checked = orderedCheckedKeys();
    let chapterKeys;
    if (checked.length) {
      chapterKeys = checked.filter((k) => {
        const ch = chapters.value.find((c) => String(c?.key || "") === k);
        return ch && !chapterIsStructure(ch) && !chapterNeedsStudy(ch);
      });
    } else if (String(act?.label || "").includes("全部")) {
      chapterKeys = readyKeys;
    } else {
      const focus = firstWriteTargetKey() || String(selectedKey.value || "").trim();
      chapterKeys = focus ? [focus] : readyKeys;
    }
    if (!chapterKeys.length) {
      ElMessage.info("请先勾选要写作的章节，或先为章节配置要点");
      return;
    }
    const focusKey = chapterKeys[0];
    if (focusKey) selectChapter(focusKey, "preview", { mainView: "preview" });
    emitWrite(
      "start_writing",
      buildWriteActionPayload(chapterKeys, focusKey, {
        already_persisted: Boolean(flushed.already_persisted),
        from_doc_window: true,
      })
    );
    return;
  }
  if (id === "pause_writing") {
    emitWrite("pause_writing");
    return;
  }
  if (id === "save_and_retry" || id === "retry") {
    const flushed = await flushConfigBeforeWrite();
    if (!flushed?.ok) return;
    const written = chapterHasDraft(selectedChapter.value);
    const writeMode =
      act?.writeMode ||
      (id === "save_and_retry" || written ? "rewrite" : "start");
    emitWrite(id === "save_and_retry" ? "save_and_retry" : "retry", {
      writeMode,
      already_persisted: Boolean(flushed.already_persisted),
      from_doc_window: true,
    });
    return;
  }
  if (id === "confirm" || id === "retry_search" || id === "associate_elements") {
    if (id === "associate_elements") {
      pendingPointItemLink.value = null;
      emitWrite("associate_elements", { associateScope: "chapter" });
      return;
    }
    emitWrite(id);
  }
}

async function persistFromTopology(chapters) {
  if (!Array.isArray(chapters)) return taskId.value ? { outline: { chapters: outlineNodes.value } } : null;
  return persistOutlineNodes(chapters, { replaceTree: true });
}

function onTopologySelect(index) {
  topologyFocus.value = index;
  const node = outlineNodes.value[index];
  if (node?.key) selectChapter(node.key);
}

function onTopologyAdd(index) {
  const list = outlineNodes.value.map((item) => ({ ...item }));
  const anchor = list[index] || { level: 1 };
  const key = `ch-new-${Date.now().toString(36)}`;
  list.splice(index + 1, 0, {
    key,
    sequence: "",
    title: "新章节",
    level: anchor.level || 1,
    parent: "",
    parent_sequence: "",
    points: [],
    keywords: [],
    describes: "",
    aux_prompt: "",
    reference_text: "",
    fixed_output: false,
    structure_only: false,
    element_ids: [],
    material_files: [],
    knowledge_folder_ids: [],
  });
  persistOutlineNodes(list, { selectKey: key, replaceTree: true });
}

function onTopologyRemove(index) {
  const list = outlineNodes.value.map((item) => ({ ...item }));
  if (index < 0 || index >= list.length) return;
  list.splice(index, 1);
  persistOutlineNodes(list, { selectKey: list[Math.max(0, index - 1)]?.key || "", replaceTree: true });
}

async function saveElementNodes(elements) {
  const targets = configPersistTaskIds();
  if (!targets.length) return;
  saving.value = true;
  saveHint.value = "正在保存要素…";
  try {
    let latest = elements || [];
    for (const tid of targets) {
      const updated = await request(`/api/writing/tasks/${encodeURIComponent(tid)}/elements`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ elements }),
      });
      if (tid === targets[0]) {
        latest = updated?.element_nodes || elements || [];
      }
    }
    elementNodes.value = latest;
    saveHint.value = "";
    // 父级按 taskId 索引要素：写作 clone 与蓝本都登记，避免重试取空
    for (const tid of targets) {
      emit("elements-saved", { taskId: tid, elementNodes: elementNodes.value });
    }
  } catch (error) {
    saveHint.value = error?.message || "保存要素失败";
    throw error;
  } finally {
    saving.value = false;
  }
}

function openElementEditor(node, { preferFile = false } = {}) {
  if (!node?.id) return;
  let kind = node.kind === "rag_search" ? "file" : node.kind;
  if (preferFile && kind !== "file") {
    kind = "file";
    ElMessage.info("已切换为「文件」要素；请上传文件并手动解析后再保存");
  }
  elementDraft.value = {
    ...node,
    kind,
    material_files: [...(node.material_files || [])],
    files: Array.isArray(node.files) ? node.files.map((f) => ({ ...f })) : [],
    kv_pairs: kvPairsFromElement(node).map((p) => ({ ...p })),
    file_slots: Array.isArray(node.file_slots) && node.file_slots.length
      ? node.file_slots.map((s) => ({
          no: Number(s?.no) > 0 ? Number(s.no) : 0,
          name: String(s?.name || ""),
          query: String(s?.query || ""),
        }))
      : [{ no: 0, name: "", query: "" }],
    query: node.query || "",
    ref_key: node.ref_key || "",
    ref_kind: node.kind === "node_ref" ? "element" : node.ref_kind || "chapter",
    ref_field:
      node.kind === "node_ref"
        ? "result"
        : node.ref_field || (node.ref_kind === "element" ? "result" : "points"),
  };
  elementDialogVisible.value = true;
}

async function saveProjectFolders() {
  const tid = String(projectFoldersTargetTid.value || "").trim();
  if (!tid || saving.value) return;
  const ids = (projectFolderIdsLocal.value || []).map((x) => String(x || "").trim()).filter(Boolean);
  const label = projectFolderLabel.value || (ids.length ? `已选 ${ids.length} 项` : "");
  saving.value = true;
  saveHint.value = "正在保存项目资料…";
  try {
    // 写作任务：直接落库；蓝本预选：只回传父级写入会话，开写时带入 clone
    if (/^w/i.test(tid) || String(activeDoc.value?.writingTaskId || "") === tid) {
      await request(`/api/writing/tasks/${encodeURIComponent(tid)}/project-folders`, {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ project_folder_ids: ids, project_folder_label: label }),
      });
    }
    projectFolderLabel.value = label;
    emit("project-folders-saved", {
      taskId: tid,
      writingTaskId: String(activeDoc.value?.writingTaskId || tid).trim(),
      project_folder_ids: ids,
      project_folder_label: label,
    });
    saveHint.value = "项目资料已保存";
    ElMessage.success(ids.length ? "项目资料根已更新" : "已清空项目资料根");
  } catch (error) {
    saveHint.value = error?.message || "保存项目资料失败";
    ElMessage.error(saveHint.value);
  } finally {
    saving.value = false;
  }
}

async function persistMaterialsPackSchema(schema, { prune = true } = {}) {
  const pid = String(materialsPackIdLocal.value || "").trim();
  if (!pid) return null;
  const bid = String(activeDoc.value?.blueprintId || activeDoc.value?.taskId || "").trim();
  try {
    const updated = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pid)}/sync-schema`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          slot_schema: schema,
          blueprint_id: bid,
          prune: Boolean(prune),
        }),
      }
    );
    if (updated) materialsPack.value = updated;
    return updated;
  } catch (error) {
    ElMessage.error(error?.message || "保存资料包槽位失败");
    return null;
  }
}

/** 要素引用槽位 → 合并进资料包（不 prune，避免冲掉资料包已配槽位） */
async function syncMaterialsPackSchema(packId) {
  const pid = String(packId || materialsPackIdLocal.value || "").trim();
  if (!pid) return null;
  const fromEls = materialsSlotSchemaFromElements.value;
  if (!fromEls.length) return null;
  const packSchema = Array.isArray(materialsPack.value?.slot_schema)
    ? materialsPack.value.slot_schema
    : [];
  const byNo = new Map();
  for (const s of [...packSchema, ...fromEls]) {
    const no = Number(s?.no) > 0 ? Number(s.no) : 0;
    if (no < 1) continue;
    const prev = byNo.get(no) || { no, name: "", query: "" };
    if (s?.name) prev.name = String(s.name);
    if (s?.query) prev.query = String(s.query);
    byNo.set(no, prev);
  }
  const ordered = [];
  const seen = new Set();
  for (const row of materialsSlotRows.value || []) {
    const no = Number(row?.no) > 0 ? Number(row.no) : 0;
    if (no < 1 || !byNo.has(no) || seen.has(no)) continue;
    ordered.push(byNo.get(no));
    seen.add(no);
  }
  for (const [no, slot] of byNo) {
    if (seen.has(no)) continue;
    ordered.push(slot);
  }
  return persistMaterialsPackSchema(ordered, { prune: false });
}

async function loadMaterialsPack(packId) {
  const pid = String(packId || "").trim();
  materialsPackIdLocal.value = pid;
  if (!pid) {
    materialsPack.value = null;
    return;
  }
  try {
    let pack = await request(`/api/writing/materials-packs/${encodeURIComponent(pid)}`);
    materialsPack.value = pack;
    // 仅把要素新增的槽位号合并进包，不删资料包已有槽位
    if (materialsSlotSchemaFromElements.value.length) {
      const synced = await syncMaterialsPackSchema(pid);
      if (synced) pack = synced;
    }
    materialsPack.value = pack;
  } catch {
    materialsPack.value = { id: pid, name: pid, slots_fill: [], slot_schema: [], in_library: false };
  }
}

/** 切文档时先清掉旧包展示；有预建包则立刻挂上，正式绑定等目录拉取后再对齐 */
async function resetMaterialsPackForActiveDoc() {
  const pending = String(pendingMaterialsPackIdLocal.value || "").trim();
  const targetTid = String(materialsPackTargetTid.value || "").trim();
  // 无写作副本：只展示本档预建包（可为空）
  if (!targetTid) {
    await loadMaterialsPack(pending);
    return;
  }
  // 有写作副本：先清空，避免短暂串旧档；目录拉取成功后再 bind
  materialsPackIdLocal.value = "";
  materialsPack.value = null;
  if (pending) {
    // 预建可先露出来，目录返回后若任务已绑包会覆盖
    await loadMaterialsPack(pending);
  }
}

async function addMaterialsSlot() {
  if (!materialsPackIdLocal.value) {
    ElMessage.warning("请先绑定资料包");
    return;
  }
  const rows = packSchemaPayloadFromRows(materialsSlotRows.value);
  const maxNo = Math.max(0, ...rows.map((r) => r.no));
  rows.push({ no: maxNo + 1, name: "", query: "" });
  await persistMaterialsPackSchema(rows, { prune: true });
}

async function removeMaterialsSlot(slotNo) {
  const no = Number(slotNo) || 0;
  if (no < 1) return;
  const rows = packSchemaPayloadFromRows(materialsSlotRows.value).filter((r) => r.no !== no);
  await persistMaterialsPackSchema(rows, { prune: true });
}

async function moveMaterialsSlot(slotNo, direction) {
  const pid = String(materialsPackIdLocal.value || "").trim();
  const no = Number(slotNo) || 0;
  const dir = Number(direction) || 0;
  if (!pid || no < 1 || !dir) return;
  const nos = (materialsSlotRows.value || [])
    .map((r) => Number(r?.no) || 0)
    .filter((n) => n >= 1);
  const idx = nos.indexOf(no);
  const j = idx + dir;
  if (idx < 0 || j < 0 || j >= nos.length) return;
  const next = [...nos];
  const tmp = next[idx];
  next[idx] = next[j];
  next[j] = tmp;
  try {
    const updated = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pid)}/slots/reorder`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ order: next }),
      }
    );
    if (updated) materialsPack.value = updated;
  } catch (error) {
    ElMessage.error(error?.message || "调整槽位顺序失败");
  }
}

let packSlotUpdateTimer = 0;
let packSlotUpdatePending = false;
function flushMaterialsSlotSchemaPersist() {
  if (!packSlotUpdatePending) return;
  packSlotUpdatePending = false;
  clearTimeout(packSlotUpdateTimer);
  packSlotUpdateTimer = 0;
  if (!materialsPackIdLocal.value || !materialsPack.value) return;
  void persistMaterialsPackSchema(packSchemaPayloadFromRows(materialsSlotRows.value), {
    prune: true,
  });
}
function updateMaterialsSlot(slotNo, patch) {
  const no = Number(slotNo) || 0;
  if (no < 1 || !materialsPack.value) return;
  const schema = Array.isArray(materialsPack.value.slot_schema)
    ? materialsPack.value.slot_schema.map((s) => ({ ...s }))
    : [];
  let hit = schema.find((s) => Number(s?.no) === no);
  if (!hit) {
    hit = { no, name: "", query: "" };
    schema.push(hit);
  }
  if (Object.prototype.hasOwnProperty.call(patch || {}, "name")) {
    hit.name = String(patch.name || "");
  }
  hit.query = "";
  materialsPack.value = {
    ...materialsPack.value,
    slot_schema: schema.sort((a, b) => Number(a.no) - Number(b.no)),
  };
  packSlotUpdatePending = true;
  clearTimeout(packSlotUpdateTimer);
  packSlotUpdateTimer = setTimeout(() => {
    flushMaterialsSlotSchemaPersist();
  }, 450);
}

async function refreshLibraryPackOptions() {
  try {
    const rid = robotId.value;
    const url = rid
      ? `/api/writing/robots/${encodeURIComponent(rid)}/materials-packs?in_library=true`
      : "/api/writing/materials-packs?in_library=true";
    const data = await request(url);
    libraryPackOptions.value = Array.isArray(data?.items) ? data.items : [];
  } catch {
    libraryPackOptions.value = [];
  }
}

async function putTaskMaterialsPack(packId, { skip = false } = {}) {
  const tid = String(materialsPackTargetTid.value || "").trim();
  if (!tid) {
    // 开写前：资料包记到会话，开写时再绑写作文档
    const pid = String(packId || "").trim();
    emit("materials-pack-pending", {
      packId: skip ? "" : pid,
      skip: Boolean(skip),
      blueprintId: String(activeDoc.value?.blueprintId || activeDoc.value?.taskId || "").trim(),
    });
    if (!skip && pid) await loadMaterialsPack(pid);
    else await loadMaterialsPack("");
    ElMessage.success(skip || !pid ? "已取消预选资料包" : "资料包已预选，开写时自动绑定");
    return;
  }
  saving.value = true;
  try {
    const task = await request(`/api/writing/tasks/${encodeURIComponent(tid)}/materials-pack`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(skip ? { skip: true } : { pack_id: packId || "" }),
    });
    await loadMaterialsPack(String(task?.materials_pack_id || "").trim());
    ElMessage.success(skip || !packId ? "已解绑资料包" : "资料包已绑定");
  } catch (error) {
    ElMessage.error(error?.message || "绑定资料包失败");
  } finally {
    saving.value = false;
  }
}

async function createAndBindMaterialsPack() {
  let name = "";
  try {
    const { value } = await ElMessageBox.prompt("给资料包起个名字（默认待入库）", "新建资料包", {
      confirmButtonText: "创建并绑定",
      cancelButtonText: "取消",
      inputPlaceholder: "如：某某项目资料",
    });
    name = String(value || "").trim() || "未命名资料包";
  } catch {
    return;
  }
  saving.value = true;
  try {
    const bid = String(activeDoc.value?.blueprintId || activeDoc.value?.taskId || "").trim();
    // 新建时至少带一个空槽，便于立刻上传；也可再点「添加槽位」
    const initialSchema = materialsSlotSchemaFromElements.value.length
      ? materialsSlotSchemaFromElements.value
      : [{ no: 1, name: "", query: "" }];
    const pack = await request("/api/writing/materials-packs", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        name,
        in_library: false,
        slot_schema: initialSchema,
        blueprint_id: bid,
      }),
    });
    await putTaskMaterialsPack(pack?.id);
  } catch (error) {
    ElMessage.error(error?.message || "新建资料包失败");
    saving.value = false;
  }
}

async function bindExistingMaterialsPack() {
  await refreshLibraryPackOptions();
  const options = libraryPackOptions.value || [];
  if (!options.length) {
    ElMessage.warning("资料库还没有已入库的资料包，请先新建或到「资料库」确认入库");
    return;
  }
  try {
    const { value } = await ElMessageBox.prompt(
      `输入资料包 id（已入库：${options.map((p) => `${p.name}=${p.id}`).join("；")}）`,
      "换绑资料包",
      {
        confirmButtonText: "绑定",
        cancelButtonText: "取消",
        inputPlaceholder: options[0]?.id || "mp-…",
        inputValue: options[0]?.id || "",
      }
    );
    const packId = String(value || "").trim();
    if (!packId) return;
    await putTaskMaterialsPack(packId);
  } catch {
    /* cancel */
  }
}

async function unbindMaterialsPack() {
  await putTaskMaterialsPack("", { skip: true });
}

async function confirmMaterialsPackLibrary() {
  const pid = String(materialsPackIdLocal.value || "").trim();
  if (!pid) return;
  saving.value = true;
  try {
    await request(`/api/writing/materials-packs/${encodeURIComponent(pid)}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ in_library: true }),
    });
    await loadMaterialsPack(pid);
    ElMessage.success("已确认入库，可在资料库选用");
  } catch (error) {
    ElMessage.error(error?.message || "入库失败");
  } finally {
    saving.value = false;
  }
}

async function uploadMaterialsSlot(slotNo, event) {
  const pid = String(materialsPackIdLocal.value || "").trim();
  const file = event?.target?.files?.[0];
  if (event?.target) event.target.value = "";
  if (!pid || !file) return;
  saving.value = true;
  saveHint.value = `正在上传到槽位 ${slotNo}…`;
  try {
    const form = new FormData();
    form.append("file", file);
    materialsPack.value = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pid)}/slots/${encodeURIComponent(slotNo)}/files`,
      {
        method: "POST",
        headers: authHeaders(),
        body: form,
      }
    );
    saveHint.value = "";
    ElMessage.success(`槽位 ${slotNo} 已更新`);
  } catch (error) {
    saveHint.value = error?.message || "上传失败";
    ElMessage.error(saveHint.value);
  } finally {
    saving.value = false;
  }
}

async function clearMaterialsSlot(slotNo) {
  const pid = String(materialsPackIdLocal.value || "").trim();
  if (!pid) return;
  saving.value = true;
  try {
    materialsPack.value = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pid)}/slots/${encodeURIComponent(slotNo)}/files`,
      { method: "DELETE", headers: authHeaders() }
    );
    ElMessage.success(`已清除槽位 ${slotNo}`);
  } catch (error) {
    ElMessage.error(error?.message || "清除失败");
  } finally {
    saving.value = false;
  }
}

async function parseMaterialsSlot(slotNo) {
  const pid = String(materialsPackIdLocal.value || "").trim();
  const no = Number(slotNo) || 0;
  if (!pid || no < 1) return;
  saving.value = true;
  saveHint.value = `正在解析槽位 ${no}…`;
  try {
    materialsPack.value = await request(
      `/api/writing/materials-packs/${encodeURIComponent(pid)}/slots/${encodeURIComponent(no)}/parse`,
      { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: "{}" }
    );
    saveHint.value = "";
    ElMessage.success(`槽位 ${no} 已解析`);
  } catch (error) {
    saveHint.value = error?.message || "解析失败";
    ElMessage.error(saveHint.value);
    await loadMaterialsPack(pid);
  } finally {
    saving.value = false;
  }
}

function onElementDialogClosed() {
  elementDraft.value = null;
}

async function saveElementDraft() {
  if (!elementDraft.value) return;
  let saved = { ...elementDraft.value };
  if (saved.kind === "rag_search") saved.kind = "file";
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
  if (saved.kind === "file_slot") {
    // 深拷贝槽位，避免与表单行共享引用被后续重置冲掉
    const slots = (elementDraft.value?.file_slots || saved.file_slots || [])
      .map((s) => ({
        no: Number(s?.no) > 0 ? Number(s.no) : 0,
        name: String(s?.name || "").trim(),
        query: String(s?.query || "").trim(),
      }))
      .filter((s) => s.no > 0);
    if (!slots.length) {
      ElMessage.warning("请从资料包槽位中至少选择一项引用");
      return;
    }
    const slotOrder = (materialsSlotRows.value || [])
      .map((r) => Number(r?.no) || 0)
      .filter((n) => n > 0);
    if (slotOrder.length > 1) {
      const rank = new Map(slotOrder.map((no, index) => [no, index]));
      slots.sort((a, b) => (rank.get(a.no) ?? 9999) - (rank.get(b.no) ?? 9999));
    }
    saved.file_slots = slots;
  }
  if (saved.kind === "file") {
    saved.files = Array.isArray(saved.files) ? saved.files.map((f) => ({ ...f })) : [];
    if (!saved.files.length) {
      ElMessage.warning("文件类型必须上传至少一个对应文件");
      return;
    }
    const pending = saved.files.filter((f) => String(f?.parse_status || "") !== "parsed");
    if (pending.length) {
      ElMessage.warning(
        `有 ${pending.length} 个文件尚未解析。文件需要解析后才能读取相关内容；可先保存，稍后再解析。`
      );
    }
  }
  if (!(saved.name || "").trim()) {
    if (saved.kind === "kv" && saved.kv_pairs?.some((p) => p.key?.trim())) {
      saved.name = saved.kv_pairs.find((p) => p.key?.trim())?.key || "未命名要素";
    } else {
      ElMessage.warning("请填写要素名称");
      return;
    }
  }
  const wasNew = !elementNodes.value.some((item) => item.id === saved.id);
  const elements = elementNodes.value.map((item) =>
    item.id === saved.id ? { ...saved } : { ...item }
  );
  if (!elements.some((item) => item.id === saved.id)) {
    elements.push(saved);
  }
  try {
    await saveElementNodes(elements);
    // 已关联本章时静默落盘；勿 syncSession，避免对话只刷「保存章节配置」并占住发送锁
    const linked = selectedElementIds.value.includes(String(saved.id || ""));
    if (linked) {
      await saveChapterConfig({ syncSession: false });
    }
    elementDialogVisible.value = false;
    ElMessage.success("要素已保存");
    // 新建且尚未入库：入队待收录并提示
    if (
      wasNew &&
      linked &&
      !String(saved.library_item_id || "").trim() &&
      String(props.sessionId || "").trim() &&
      String(taskId.value || "").trim()
    ) {
      try {
        const data = await request("/api/writing/chat/element-library/queue", {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            session_id: String(props.sessionId || "").trim(),
            task_id: String(taskId.value || "").trim(),
            chapter_key: String(selectedKey.value || "").trim(),
            chapter_title: chapterLabel(selectedChapter.value),
            element_ids: [String(saved.id)],
            elements: [saved],
          }),
        });
        if (Number(data?.queued) > 0) {
          ElMessage.info("新要素可收入个人库，请点「待收录要素」");
          openLibraryPendingAfterRefresh = true;
          emit("library-pending-changed");
        }
      } catch {
        /* 入队失败不挡保存 */
      }
    }
  } catch (error) {
    ElMessage.error(error?.message || "保存要素失败");
  }
}

async function unlinkCurrentElement() {
  const id = String(elementDraft.value?.id || "").trim();
  const key = String(selectedKey.value || "").trim();
  const persistTid = outlineSourceTaskId();
  if (!id || !key || !persistTid) return;
  let nodes = outlineNodes.value.length
    ? outlineNodes.value.map((item) => ({ ...item }))
    : [];
  if (!nodes.length) {
    try {
      const slim = await request(`/api/writing/tasks/${encodeURIComponent(persistTid)}/doc-outline`);
      nodes = (slim?.chapters || []).map((item) => ({ ...item }));
    } catch (error) {
      ElMessage.error(error?.message || "读取目录失败");
      return;
    }
  }
  const idx = nodes.findIndex((item) => String(item.key || "") === key);
  if (idx < 0) {
    ElMessage.warning("未找到该章节");
    return;
  }
  const prevIds = Array.isArray(nodes[idx].element_ids) ? nodes[idx].element_ids : [];
  nodes[idx] = {
    ...nodes[idx],
    element_ids: prevIds.filter((item) => String(item || "") !== id),
  };
  try {
    await persistOutlineNodes(nodes, { selectKey: key });
    elementDialogVisible.value = false;
    emit("config-saved", {
      taskId: persistTid,
      chapterKey: key,
      chapter: nodes[idx],
      elementNodes: elementNodes.value,
      syncSession: false,
    });
    ElMessage.success("已取消本章关联");
  } catch (error) {
    ElMessage.error(error?.message || "取消关联失败");
  }
}

async function persistOutlineNodes(nodes, { selectKey = "", replaceTree = false } = {}) {
  const tid = outlineSourceTaskId();
  if (!tid) return null;
  const ownedBusy = !saving.value;
  if (ownedBusy) {
    saving.value = true;
    saveHint.value = "正在保存目录…";
  }
  try {
    const updated = await request(`/api/writing/tasks/${encodeURIComponent(tid)}/outline`, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ chapters: nodes, replace_tree: Boolean(replaceTree) }),
    });
    outlineNodes.value = (updated?.outline?.chapters || nodes).map((item) => ({ ...item }));
    extraChapters.value = outlineNodes.value.map((ch) => ({
      key: ch.key,
      sequence: ch.sequence,
      title: ch.title,
      parent: ch.parent,
      structure_only: Boolean(ch.structure_only),
      status: chapters.value.find((row) => row.key === ch.key)?.status || "",
    }));
    if (selectKey) selectedKey.value = selectKey;
    hydrateConfigDraft();
    if (ownedBusy) saveHint.value = "";
    return updated;
  } catch (error) {
    if (ownedBusy) saveHint.value = error?.message || "保存目录失败";
    throw error;
  } finally {
    if (ownedBusy) saving.value = false;
  }
}

async function addChapter() {
  const persistTid = outlineSourceTaskId();
  if (!persistTid || saving.value) return;
  saving.value = true;
  saveHint.value = "";
  try {
    let nodes = outlineNodes.value.length
      ? outlineNodes.value.map((item) => ({ ...item }))
      : [];
    if (!nodes.length) {
      const slim = await request(`/api/writing/tasks/${encodeURIComponent(persistTid)}/doc-outline`);
      nodes = (slim?.chapters || []).map((item) => ({ ...item }));
    }
    const anchor = nodes.find((item) => item.key === selectedKey.value) || nodes[nodes.length - 1] || {};
    const key = `ch-new-${Date.now().toString(36)}`;
    nodes.push({
      key,
      sequence: "",
      title: "新章节",
      level: anchor.level || 1,
      parent: anchor.parent || "",
      parent_sequence: "",
      points: [],
      keywords: [],
      describes: "",
      aux_prompt: "",
      reference_text: "",
      fixed_output: false,
      structure_only: false,
      element_ids: [],
      material_files: [],
      knowledge_folder_ids: [],
    });
    await persistOutlineNodes(nodes, { selectKey: key, replaceTree: true });
    detailTab.value = "config";
    saveHint.value = "已新增章节";
    emit("config-saved", {
      taskId: persistTid,
      chapterKey: key,
      chapter: nodes.find((item) => item.key === key),
      elementNodes: elementNodes.value,
    });
  } catch (error) {
    saveHint.value = error?.message || "新增失败";
  } finally {
    saving.value = false;
  }
}

async function removeSelectedChapters() {
  const keys = orderedCheckedKeys();
  if (!keys.length) {
    ElMessage.info("请先勾选要删除的章节");
    return;
  }
  const persistTid = outlineSourceTaskId();
  if (!persistTid || saving.value) return;
  try {
    await ElMessageBox.confirm(
      `确定从目录中删除已选的 ${keys.length} 章？删除后不可恢复。`,
      "删除章节",
      {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消",
      }
    );
  } catch {
    return;
  }
  const drop = new Set(keys.map((k) => String(k || "").trim()).filter(Boolean));
  saving.value = true;
  saveHint.value = "";
  try {
    let nodes = outlineNodes.value.length
      ? outlineNodes.value.map((item) => ({ ...item }))
      : [];
    if (!nodes.length) {
      const slim = await request(`/api/writing/tasks/${encodeURIComponent(persistTid)}/doc-outline`);
      nodes = (slim?.chapters || []).map((item) => ({ ...item }));
    }
    const next = nodes.filter((item) => !drop.has(String(item.key || "").trim()));
    if (next.length === nodes.length) throw new Error("未找到所选章节");
    const remain = next[0]?.key || "";
    await persistOutlineNodes(next, { selectKey: remain, replaceTree: true });
    checkedKeys.value = [];
    saveHint.value = `已删除 ${drop.size} 章`;
    emit("config-saved", {
      taskId: persistTid,
      chapterKey: remain,
      chapter: next[0] || { key: "", points: [] },
      elementNodes: elementNodes.value,
    });
  } catch (error) {
    saveHint.value = error?.message || "删除失败";
    ElMessage.error(saveHint.value);
  } finally {
    saving.value = false;
  }
}

async function removeChapter() {
  const key = String(selectedKey.value || "").trim();
  const persistTid = outlineSourceTaskId();
  if (!persistTid || !key || saving.value) return;
  if (!window.confirm("确定从目录中删除本章？")) return;
  saving.value = true;
  saveHint.value = "";
  try {
    let nodes = outlineNodes.value.length
      ? outlineNodes.value.map((item) => ({ ...item }))
      : [];
    if (!nodes.length) {
      const slim = await request(`/api/writing/tasks/${encodeURIComponent(persistTid)}/doc-outline`);
      nodes = (slim?.chapters || []).map((item) => ({ ...item }));
    }
    const next = nodes.filter((item) => String(item.key || "") !== key);
    if (next.length === nodes.length) throw new Error("未找到该章节");
    await persistOutlineNodes(next, { selectKey: next[0]?.key || "", replaceTree: true });
    saveHint.value = "已删除";
    emit("config-saved", {
      taskId: persistTid,
      chapterKey: next[0]?.key || "",
      chapter: next[0] || { key: "", points: [] },
      elementNodes: elementNodes.value,
    });
  } catch (error) {
    saveHint.value = error?.message || "删除失败";
  } finally {
    saving.value = false;
  }
}

const hydratingConfig = ref(false);
let configAutosaveTimer = 0; // 仅用于取消防抖残留；配置不再自动保存

function hydrateConfigDraft() {
  hydratingConfig.value = true;
  const node = selectedOutlineNode.value;
  const ch = selectedChapter.value;
  const cfg = ch?.config && typeof ch.config === "object" ? ch.config : {};
  const src = node || cfg || ch || {};
  const points = pickRicherList(
    node?.points,
    pickRicherList(ch?.points, cfg?.points)
  );
  Object.assign(configDraft, {
    sequence: String(src.sequence || ch?.sequence || "").trim(),
    title: String(src.title || ch?.title || ch?.key || "").trim(),
    parent: String(src.parent || ch?.parent || cfg?.parent || "").trim(),
    describes: pickRicherText(src.describes, ch?.describes || cfg?.describes),
    aux_prompt: pickRicherText(src.aux_prompt, ch?.aux_prompt || cfg?.aux_prompt),
    reference_text: pickRicherText(src.reference_text, ch?.reference_text || cfg?.reference_text),
    fixed_output: Boolean(src.fixed_output ?? ch?.fixed_output ?? cfg?.fixed_output),
    // 勿用 ||：取消「仅目录」后 false 会被章节旧 true 盖回
    structure_only: Boolean(
      node?.structure_only ?? ch?.structure_only ?? cfg?.structure_only ?? false
    ),
  });
  const loadedItems = pointItemsFromChapter(node, ch, cfg);
  let rows =
    !loadedItems.length && points.length
      ? syncPointItemsFromPoints(points, [])
      : loadedItems;
  const linkIds = pickElementIds(
    node?.element_ids,
    ch?.element_ids,
    cfg?.element_ids
  );
  if (rows.length && linkIds.length) {
    rows = linkPointItemsToElements(rows, elementNodes.value, { elementIds: linkIds });
  }
  configDraft.point_items.splice(
    0,
    configDraft.point_items.length,
    ...rows.map((row) => ({ ...row }))
  );
  saveHint.value = "";
  nextTick(() => {
    hydratingConfig.value = false;
  });
}

function pointsFromText(text) {
  return String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function pointItemElementLabel(row) {
  if (!row || typeof row !== "object") return "";
  const eid = String(row.element_id || "").trim();
  if (!eid) return "";
  const el = elementNodes.value.find((item) => String(item?.id || "") === eid);
  if (!el) return "";
  const name = humanLabel(el.name, row.element_name, row.slot);
  if (!name) return "";
  return `${name}（${elementKindLabel(el.kind)}）`;
}

function onConfigPointRowEdit(row) {
  if (!row || typeof row !== "object") return;
  const text = String(row.text || "");
  row.needs_supplement = textNeedsSupplement(text);
  if (String(row.element_id || "").trim()) {
    row.needs_supplement = false;
  }
  if (!String(row.slot || "").trim()) {
    const slot = parseSlotFromText(text);
    if (slot) row.slot = slot;
  }
}

const pointItemDialogVisible = ref(false);
const pointItemEditIndex = ref(-1);
const pointItemEditDraft = reactive({ text: "", slot: "" });

function onPointItemDialogClosed() {
  pointItemEditIndex.value = -1;
  pointItemEditDraft.text = "";
  pointItemEditDraft.slot = "";
}

function openPointItemEditor(index) {
  const row = configDraft.point_items[index];
  if (!row) return;
  pointItemEditIndex.value = index;
  pointItemEditDraft.text = String(row.text || "");
  pointItemEditDraft.slot = String(row.slot || "");
  pointItemDialogVisible.value = true;
}

function savePointItemEditor() {
  const index = pointItemEditIndex.value;
  const row = configDraft.point_items[index];
  if (!row) {
    pointItemDialogVisible.value = false;
    return;
  }
  row.text = String(pointItemEditDraft.text || "").trim();
  row.slot = String(pointItemEditDraft.slot || "").trim();
  onConfigPointRowEdit(row);
  pointItemDialogVisible.value = false;
  syncConfigPointItemsToLocalOutline();
  scheduleChapterMetaFromPoints();
  saveHint.value = "未保存";
}

function rowNeedsPointAssociate(row) {
  if (!row || typeof row !== "object") return false;
  const eid = String(row.element_id || "").trim();
  if (eid && elementNodes.value.some((el) => String(el?.id || "") === eid)) {
    return false;
  }
  return Boolean(row.needs_supplement) || textNeedsSupplement(String(row.text || ""));
}

const pendingPointItemLink = ref(null);

function associatePointItemElement(row, index) {
  pendingPointItemLink.value = {
    index: Number(index),
    id: String(row?.id || "").trim(),
    slot: String(row?.slot || "").trim(),
  };
  if (row && String(row.slot || "").trim()) {
    saveHint.value = `请为待补项「${String(row.slot).trim()}」关联要素`;
  }
  emitWrite("associate_elements", {
    associateScope: "point",
    pointItemId: pendingPointItemLink.value.id,
    pointIndex: pendingPointItemLink.value.index,
    pointSlot: pendingPointItemLink.value.slot,
    pointItems: configDraft.point_items.map((row) => ({ ...row })),
  });
}

function openPointLinkedElement(row) {
  const eid = String(row?.element_id || "").trim();
  if (!eid) return;
  const hit = elementNodes.value.find((item) => String(item?.id || "") === eid);
  if (hit) openElementEditor(hit);
}

function addConfigPointRow() {
  const i = configDraft.point_items.length;
  configDraft.point_items.push({
    id: `p${i}`,
    text: "",
    slot: "",
    needs_supplement: false,
  });
  nextTick(() => openPointItemEditor(i));
}

function syncConfigPointItemsToLocalOutline() {
  const key = String(selectedKey.value || "").trim();
  if (!key) return;
  const items = configDraft.point_items.map((row) => ({ ...row }));
  const points = pointsFromPointItems(items);
  const describes = String(configDraft.describes || "").trim();
  const aux_prompt = String(configDraft.aux_prompt || "").trim();
  const patchChapter = (ch) => {
    if (String(ch?.key || "") !== key) return ch;
    const cfg = ch?.config && typeof ch.config === "object" ? ch.config : {};
    return {
      ...ch,
      points: [...points],
      point_items: items.map((row) => ({ ...row })),
      describes,
      aux_prompt,
      config: {
        ...cfg,
        points: [...points],
        point_items: items.map((row) => ({ ...row })),
        describes,
        aux_prompt,
      },
    };
  };
  outlineNodes.value = outlineNodes.value.map(patchChapter);
}

let chapterMetaRegenTimer = 0;
const chapterMetaRegenBusy = ref(false);

async function refreshChapterMetaFromPoints() {
  if (configDraft.structure_only) return;
  const tid = configPersistTaskIds()[0] || String(taskId.value || "").trim();
  const key = String(selectedKey.value || "").trim();
  if (!tid || !key || chapterMetaRegenBusy.value || blockingLoad.value) return;
  const point_items = draftPointItemsForSave();
  if (!point_items.length) {
    configDraft.describes = "";
    configDraft.aux_prompt = "";
    syncConfigPointItemsToLocalOutline();
    saveHint.value = "未保存";
    return;
  }
  chapterMetaRegenBusy.value = true;
  try {
    const data = await request(
      `/api/writing/tasks/${encodeURIComponent(tid)}/outline/meta-from-points`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          chapter_key: key,
          title: configDraft.title || "",
          sequence: configDraft.sequence || "",
          point_items,
        }),
      }
    );
    configDraft.describes = String(data?.describes || "").trim();
    configDraft.aux_prompt = String(data?.aux_prompt || "").trim();
    syncConfigPointItemsToLocalOutline();
    saveHint.value = "未保存";
  } catch (error) {
    ElMessage.warning(error?.message || "未能根据要点更新章节说明");
  } finally {
    chapterMetaRegenBusy.value = false;
  }
}

function scheduleChapterMetaFromPoints() {
  if (configDraft.structure_only) return;
  if (chapterMetaRegenTimer) clearTimeout(chapterMetaRegenTimer);
  chapterMetaRegenTimer = window.setTimeout(() => {
    chapterMetaRegenTimer = 0;
    refreshChapterMetaFromPoints();
  }, 450);
}

function removeConfigPointRow(index) {
  configDraft.point_items.splice(index, 1);
  syncConfigPointItemsToLocalOutline();
  scheduleChapterMetaFromPoints();
  saveHint.value = "未保存";
}

function draftPointItemsForSave() {
  return configDraft.point_items
    .map((row, i) => {
      const text = String(row?.text || "").trim();
      const id = String(row?.id || `p${i}`).trim() || `p${i}`;
      const out = { id, text };
      const slot = humanLabel(row?.slot);
      if (slot) out.slot = slot;
      if (textNeedsSupplement(text) || row?.needs_supplement) out.needs_supplement = true;
      const eid = String(row?.element_id || "").trim();
      if (eid) {
        out.element_id = eid;
        delete out.needs_supplement;
        const ename = humanLabel(row?.element_name, slot);
        if (ename) out.element_name = ename;
      }
      return out;
    })
    .filter((row) => row.text);
}

/**
 * @param {{ syncSession?: boolean }} [opts]
 *   syncSession=false：学习/重写前静默落盘，不在会话再发「保存章节配置」
 * @returns {Promise<boolean|null>} true 成功 / false 失败 / null 跳过
 */
async function patchChapterConfigOnTask(tid, key, chapterFields) {
  const slim = await request(`/api/writing/tasks/${encodeURIComponent(tid)}/doc-outline?fresh=1`);
  const nodes = (slim?.chapters || []).map((item) => ({ ...item }));
  if (!nodes.length) throw new Error("未找到该章节");
  const idx = nodes.findIndex((item) => String(item.key || "") === key);
  if (idx < 0) throw new Error("未找到该章节");
  const prev = nodes[idx] || {};
  let point_items = [];
  let points = [];
  if (chapterFields.structure_only) {
    point_items = [];
    points = [];
  } else if (Array.isArray(chapterFields.point_items)) {
    point_items = chapterFields.point_items.map((row, i) => {
      const text = String(row?.text || "").trim();
      const id = String(row?.id || `p${i}`).trim() || `p${i}`;
      const out = { id, text };
      const slot = humanLabel(row?.slot);
      if (slot) out.slot = slot;
      if (textNeedsSupplement(text) || row?.needs_supplement) out.needs_supplement = true;
      const eid = String(row?.element_id || "").trim();
      if (eid) {
        out.element_id = eid;
        delete out.needs_supplement;
        const ename = humanLabel(row?.element_name, slot);
        if (ename) out.element_name = ename;
      }
      return out;
    });
    points = pointsFromPointItems(point_items);
  } else {
    const draftPoints = pointsFromText(chapterFields.pointsText);
    const prevPoints = Array.isArray(prev.points) ? prev.points : [];
    points =
      !draftPoints.length && prevPoints.length ? [...prevPoints] : draftPoints;
    point_items = syncPointItemsFromPoints(points, prev.point_items);
  }
  // 调用方带了本章关联就以它为准。服务端 element_ids 常是空数组，
  // 若优先用服务端，界面上已挂的「建设规模」不会进大纲，写作检索就当没关联。
  let prevIds = Array.isArray(chapterFields.element_ids)
    ? chapterFields.element_ids.map((id) => String(id || "").trim()).filter(Boolean)
    : Array.isArray(prev.element_ids)
      ? prev.element_ids.map((id) => String(id || "").trim()).filter(Boolean)
      : [];
  const idSet = new Set(prevIds);
  for (const row of point_items) {
    const eid = String(row?.element_id || "").trim();
    if (eid) idSet.add(eid);
  }
  prevIds = [...idSet];
  const parentKey = String(chapterFields.parent || "").trim();
  let parentSeq = "";
  let level = Number(prev.level) > 0 ? Number(prev.level) : 1;
  if (parentKey) {
    const parentNode = nodes.find((item) => String(item.key || "") === parentKey);
    if (parentNode) {
      parentSeq = String(parentNode.sequence || "").trim();
      level = Math.min(8, (Number(parentNode.level) > 0 ? Number(parentNode.level) : 1) + 1);
    }
  } else {
    level = 1;
  }
  nodes[idx] = {
    ...prev,
    key,
    sequence: chapterFields.sequence || prev.sequence,
    title: chapterFields.title || prev.title || key,
    parent: parentKey,
    parent_sequence: parentSeq,
    level,
    points,
    point_items,
    describes: chapterFields.describes,
    aux_prompt: chapterFields.structure_only ? "" : chapterFields.aux_prompt,
    reference_text: chapterFields.reference_text,
    fixed_output: Boolean(chapterFields.fixed_output) && !chapterFields.structure_only,
    structure_only: Boolean(chapterFields.structure_only),
    element_ids: prevIds,
  };
  const updated = await request(`/api/writing/tasks/${encodeURIComponent(tid)}/outline`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ chapters: nodes }),
  });
  return { updated, chapter: nodes[idx], nodes };
}

async function saveChapterConfig(opts = {}) {
  const syncSession = opts.syncSession !== false;
  const targets = configPersistTaskIds();
  const primaryTid = targets[0] || "";
  const key = String(selectedKey.value || "").trim();
  if (!primaryTid || !key || saving.value) return null;
  if (configAutosaveTimer) {
    clearTimeout(configAutosaveTimer);
    configAutosaveTimer = 0;
  }
  saving.value = true;
  saveHint.value = "";
  try {
    if (chapterMetaRegenTimer) {
      clearTimeout(chapterMetaRegenTimer);
      chapterMetaRegenTimer = 0;
      await refreshChapterMetaFromPoints();
    }
    const local = outlineNodes.value.find((item) => String(item.key || "") === key) || {};
    const draftItems = configDraft.structure_only ? [] : draftPointItemsForSave();
    const idsFromPoints = elementIdsFromPointItems(draftItems);
    const idsFromSelect = pickElementIds(
      selectedElementIds.value,
      local.element_ids,
      local.config?.element_ids
    );
    const fields = {
      sequence: configDraft.sequence,
      title: configDraft.title,
      parent: String(configDraft.parent || "").trim(),
      point_items: draftItems,
      describes: configDraft.describes,
      aux_prompt: configDraft.aux_prompt,
      reference_text: configDraft.reference_text,
      fixed_output: configDraft.fixed_output,
      structure_only: configDraft.structure_only,
      element_ids: [...new Set([...idsFromSelect, ...idsFromPoints])],
    };
    let primaryChapter = null;
    let primaryNodes = null;
    for (const tid of targets) {
      // 只写本章配置。要素体由要素对话框单独保存；这里再提交全部要素，
      // 会把其它章尚未配槽位的要素（如「变电站站址」）拿来拦住「建设规模」。
      const { chapter, nodes } = await patchChapterConfigOnTask(tid, key, fields);
      if (tid === primaryTid) {
        // 以本次写入的 nodes 为准，避免响应里缺字段时 merge 又把旧 structure_only 盖回
        primaryChapter = chapter;
        primaryNodes = nodes.map((item) => ({ ...item }));
      }
    }
    if (primaryNodes?.length) {
      outlineNodes.value = mergeOutlinePreserveElementIds(outlineNodes.value, primaryNodes);
      extraChapters.value = outlineNodes.value.map((ch) => ({
        key: ch.key,
        sequence: ch.sequence,
        title: ch.title,
        parent: ch.parent,
        structure_only: Boolean(ch.structure_only),
        points: Array.isArray(ch.points) ? [...ch.points] : [],
        element_ids: Array.isArray(ch.element_ids) ? [...ch.element_ids] : [],
        status: chapters.value.find((row) => row.key === ch.key)?.status || "",
      }));
    }
    outlineFullyLoaded.value = true;
    hydrateConfigDraft();
    saveHint.value = "已保存";
    snapshotOutlineCache(outlineSourceTaskId() || primaryTid);
    // 写作 clone 与蓝本都通知父级，保证 retry 取得到 element_ids / 要素体
    for (const tid of targets) {
      emit("config-saved", {
        taskId: tid,
        chapterKey: key,
        chapter: primaryChapter,
        elementNodes: elementNodes.value,
        // 小窗已 PUT 落盘到文档副本，父级勿再 configure_chapter / 强制重拉以免旧进度盖回
        syncSession: false,
        alreadyPersisted: true,
      });
    }
    // 本地 outline + snapshot 已是刚写入真值；勿立刻软刷新（无 fresh 易吃到缓存/旧稿再 hydrate）
    return true;
  } catch (error) {
    saveHint.value = error?.message || "保存失败";
    ElMessage.error(saveHint.value);
    return false;
  } finally {
    saving.value = false;
  }
}

/** 当前章配置草稿是否相对大纲有未落盘改动（含取消「仅目录」） */
function isConfigDraftDirty() {
  const key = String(selectedKey.value || "").trim();
  if (!key) return false;
  const node = selectedOutlineNode.value || selectedChapter.value || {};
  const draftSig = pointItemsSignature(draftPointItemsForSave());
  const nodeItems = pointItemsFromChapter(node, node, node?.config);
  const nodeSig = pointItemsSignature(nodeItems);
  if (Boolean(configDraft.structure_only) !== Boolean(node.structure_only)) return true;
  if (String(configDraft.sequence || "").trim() !== String(node.sequence || "").trim()) return true;
  if (String(configDraft.title || "").trim() !== String(node.title || "").trim()) return true;
  if (String(configDraft.parent || "").trim() !== String(node.parent || "").trim()) return true;
  if (draftSig !== nodeSig) return true;
  if (String(configDraft.describes || "").trim() !== String(node.describes || "").trim()) return true;
  if (String(configDraft.aux_prompt || "").trim() !== String(node.aux_prompt || "").trim()) return true;
  if (String(configDraft.reference_text || "").trim() !== String(node.reference_text || "").trim()) return true;
  if (Boolean(configDraft.fixed_output) !== Boolean(node.fixed_output)) return true;
  return false;
}

/**
 * 学习本章 / 重写本章前：草稿有改动则先静默保存再开跑（保存与学习合并）。
 * 纯「保存配置」、关联要素不走这里，不会触发学习。
 */
/** 开写/重试前静默落盘；返回 { ok, already_persisted, chapterKey } */
async function flushConfigBeforeWrite() {
  const key = String(selectedKey.value || "").trim();
  if (!key) return { ok: true, already_persisted: false, chapterKey: "" };
  if (!isConfigDraftDirty()) {
    return { ok: true, already_persisted: true, chapterKey: key };
  }
  const result = await saveChapterConfig({ syncSession: false });
  if (result === false) return { ok: false, already_persisted: false, chapterKey: key };
  if (result == null && isConfigDraftDirty()) {
    ElMessage.warning("配置尚未保存成功，请稍后再试分析");
    return { ok: false, already_persisted: false, chapterKey: key };
  }
  return { ok: true, already_persisted: true, chapterKey: key };
}

/** 保存正文：落库 official_text（写完自动保存后，此按钮用于改稿再存） */
async function saveChapterDraft() {
  // 正文真值在写作 clone；无 clone 时落到当前任务（蓝本会按大纲补建章节状态）
  const tid = resolveWritingTaskId(activeDoc.value) || String(taskId.value || "").trim();
  const key = String(selectedKey.value || "").trim();
  if (!tid || !key || saving.value || blockingLoad.value) return false;
  // 优先编辑器当前 HTML（与正文 tab 一致），再回落 outline 字段
  const html = String(
    props.draftText ||
      selectedChapter.value?.draft_text ||
      selectedChapter.value?.draft_preview ||
      selectedChapter.value?.source_html ||
      ""
  ).trim();
  if (!html) {
    ElMessage.warning("正文为空，请先编辑后再保存");
    return false;
  }
  saving.value = true;
  saveHint.value = "正在保存正文…";
  try {
    await request(
      `/api/writing/tasks/${encodeURIComponent(tid)}/chapters/${encodeURIComponent(key)}/confirm`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          decision: "approve",
          text: html,
          comment: "用户保存正文",
        }),
      }
    );
    // 本地同步状态，立刻显示「已写作」
    const patch = (list) =>
      (list || []).map((ch) => {
        if (String(ch?.key || "") !== key) return ch;
        return {
          ...ch,
          status: "confirmed",
          status_label: "已写作",
          has_draft: true,
          draft_text: html,
          draft_preview: html.slice(0, 400),
        };
      });
    outlineNodes.value = patch(outlineNodes.value);
    extraChapters.value = patch(extraChapters.value);
    emit("update:draftText", html);
    saveHint.value = "正文已保存";
    ElMessage.success("正文已保存");
    emit("config-saved", {
      taskId: tid,
      chapterKey: key,
      tab: "draft",
    });
    return true;
  } catch (error) {
    const detail = error?.message || "保存正文失败";
    saveHint.value = `${detail}（任务 ${tid} / 章节 ${key}）`;
    ElMessage.error(saveHint.value);
    return false;
  } finally {
    saving.value = false;
    setTimeout(() => {
      if (String(saveHint.value || "").includes("已保存")) saveHint.value = "";
    }, 1800);
  }
}

function nudgeZoom(delta) {
  zoom.value = Math.min(1.6, Math.max(0.75, Math.round((zoom.value + delta) * 10) / 10));
}

function startDrag(event) {
  if (props.docked || event.button !== 0) return;
  drag = {
    mode: "move",
    x: event.clientX,
    y: event.clientY,
    ox: box.value.x,
    oy: box.value.y,
  };
  window.addEventListener("mousemove", onPointerMove);
  window.addEventListener("mouseup", stopPointer);
}

function startResize(event) {
  if (props.docked || event.button !== 0) return;
  drag = {
    mode: "resize",
    x: event.clientX,
    y: event.clientY,
    ow: box.value.w,
    oh: box.value.h,
  };
  window.addEventListener("mousemove", onPointerMove);
  window.addEventListener("mouseup", stopPointer);
}

function onPointerMove(event) {
  if (!drag) return;
  if (drag.mode === "move") {
    const nx = drag.ox + event.clientX - drag.x;
    const ny = drag.oy + event.clientY - drag.y;
    box.value = {
      ...box.value,
      x: clamp(nx, 8, window.innerWidth - 120),
      y: clamp(ny, 8, window.innerHeight - 48),
    };
    return;
  }
  box.value = {
    ...box.value,
    w: clamp(drag.ow + event.clientX - drag.x, 480, window.innerWidth - box.value.x - 8),
    h: clamp(drag.oh + event.clientY - drag.y, 420, window.innerHeight - box.value.y - 8),
  };
}

function stopPointer() {
  drag = null;
  window.removeEventListener("mousemove", onPointerMove);
  window.removeEventListener("mouseup", stopPointer);
}

function clamp(n, min, max) {
  return Math.min(max, Math.max(min, n));
}

function buildTree(list) {
  // 先按序号补全 parent，避免脏 parent / 仅有 parent_sequence 时整树摊平
  const nodes = ensureChapterParents(list || []).map((item) => ({ ...item, children: [] }));
  const byKey = new Map(
    nodes.filter((item) => String(item.key || "").trim()).map((item) => [String(item.key), item])
  );
  const bySeq = new Map(
    nodes
      .filter((item) => String(item.sequence || "").trim())
      .map((item) => [String(item.sequence).trim(), item])
  );
  const roots = [];
  for (const item of nodes) {
    const key = String(item.key || "").trim();
    let parent = String(item.parent || "").trim();
    if ((!parent || !byKey.has(parent)) && item.parent_sequence) {
      const viaSeq = bySeq.get(String(item.parent_sequence).trim());
      parent = viaSeq?.key ? String(viaSeq.key) : "";
      if (parent) item.parent = parent;
    }
    if (parent && byKey.has(parent) && parent !== key) {
      byKey.get(parent).children.push(item);
    } else {
      roots.push(item);
    }
  }
  return roots.length ? roots : nodes;
}

function flattenTree(nodes, depth = 0) {
  const out = [];
  for (const node of nodes || []) {
    out.push({ ...node, depth });
    if (node.children?.length) out.push(...flattenTree(node.children, depth + 1));
  }
  return out;
}

async function resolveMaterialsPackIdFromOutline(data, tid, writingTid) {
  let packId = "";
  if (/^w/i.test(tid) || (writingTid && writingTid === tid)) {
    packId = String(data?.materials_pack_id || "").trim();
  } else if (writingTid && writingTid !== tid) {
    try {
      const wData = await request(
        `/api/writing/tasks/${encodeURIComponent(writingTid)}/doc-outline`
      );
      if (wData && Object.prototype.hasOwnProperty.call(wData, "materials_pack_id")) {
        packId = String(wData.materials_pack_id || "").trim();
      }
    } catch {
      /* 写作任务可选 */
    }
  }
  if (!packId) {
    packId = String(pendingMaterialsPackIdLocal.value || "").trim();
  }
  return packId;
}

async function loadFullOutline({ force = false } = {}) {
  // 有文档副本时以 w* 为目录真源，避免保存后从库 bp* 刷新把改动盖回
  const tid = outlineSourceTaskId(activeDoc.value);
  if (!tid) {
    // 尚无任务 id：按当前文档预建包对齐（可为空），勿保留上一份
    void resetMaterialsPackForActiveDoc();
    loading.value = false;
    return;
  }
  // 同蓝本并发合并，避免反复 ++token 导致永远对不上、确认态写不进去
  // 强制刷新不得复用软刷新：软请求可能落在要点写入前，会把空 points 再写回 UI
  if (outlineInflight && outlineInflightTid === tid) {
    if (!force) return outlineInflight;
    fetchToken += 1;
    outlineInflight = null;
    outlineInflightTid = "";
  }
  if (force) {
    outlineCacheByTask.delete(tid);
    const bp = String(activeDoc.value?.taskId || activeDoc.value?.blueprintId || "").trim();
    if (bp && bp !== tid) outlineCacheByTask.delete(bp);
  }
  // 已有全量且非强制：少重挂拓扑，但仍显示加载遮罩
  const soft = !force && outlineFullyLoaded.value && outlineNodes.value.length > 0;
  const token = ++fetchToken;
  loadLabel.value = soft ? "正在刷新目录…" : "正在拉取目录…";
  loading.value = true;
  outlineInflightTid = tid;
  outlineInflight = (async () => {
    try {
      const qs = "?fresh=1";
      const data = await request(
        `/api/writing/tasks/${encodeURIComponent(tid)}/doc-outline${qs}`
      );
      // 确认态：force/fresh 可对齐真值；软刷新只允许 false→true，避免缓存把已确认打回
      if (data && Object.prototype.hasOwnProperty.call(data, "outline_confirmed")) {
        const confirmed = Boolean(data.outline_confirmed);
        if (force || confirmed) {
          outlineConfirmedLocal.value = confirmed;
        }
      }
      if (data && Object.prototype.hasOwnProperty.call(data, "sample_ready")) {
        sampleReadyLocal.value = Boolean(data.sample_ready);
        sampleNameLocal.value = String(data.sample_name || "").trim();
      } else if (isSampleStudyDoc.value) {
        sampleReadyLocal.value = true;
      } else {
        sampleReadyLocal.value = false;
        sampleNameLocal.value = "";
      }
      if (token !== fetchToken) return;
      // 切文档后丢弃过期响应（副本 id 或库蓝本 id 都算当前文档）
      const activeTid = outlineSourceTaskId(activeDoc.value);
      if (activeTid && activeTid !== tid) return;
      let list = ensureChapterParents(chaptersFromDocPayload(data));
      const writingTid = String(activeDoc.value?.writingTaskId || "").trim();
      // 空目录也要刷新资料包，否则会残留上一份文档的绑定
      if (!list.length) {
        const emptyPackId = await resolveMaterialsPackIdFromOutline(data, tid, writingTid);
        if (token !== fetchToken) return;
        if (outlineSourceTaskId(activeDoc.value) !== tid) return;
        await loadMaterialsPack(emptyPackId);
        outlineFullyLoaded.value = true;
        loadedOutlineTaskId = tid;
        return;
      }
      let projectIds = Array.isArray(data?.project_folder_ids) ? [...data.project_folder_ids] : [];
      let projectLabel = String(data?.project_folder_label || "").trim();
      // 资料包只跟写作文档；纯蓝本 outline 不带包
      let packId = "";
      let writingElements = [];
      if (/^w/i.test(tid) || (writingTid && writingTid === tid)) {
        // 已直接拉副本：目录/要点/结构以副本为准
        packId = String(data?.materials_pack_id || "").trim();
        if (Array.isArray(data?.element_nodes) && data.element_nodes.length) {
          writingElements = data.element_nodes;
        }
      } else if (writingTid && writingTid !== tid) {
        // 兼容：无 writingTaskId 挂在文档上时仍可能只拿到库蓝本 id
        try {
          const wData = await request(
            `/api/writing/tasks/${encodeURIComponent(writingTid)}/doc-outline${qs}`
          );
          const wByKey = new Map(
            (wData?.chapters || [])
              .filter((ch) => ch?.key)
              .map((ch) => [String(ch.key), ch])
          );
          if (wByKey.size) {
            list = list.map((ch) => {
              const w = wByKey.get(String(ch.key || ""));
              if (!w) return ch;
              // 副本上的结构/要点/关联整段覆盖库蓝本，避免改动被盖回
              const next = {
                ...ch,
                ...w,
                key: ch.key,
                sequence: w.sequence ?? ch.sequence,
                title: w.title ?? ch.title,
                parent: w.parent ?? ch.parent,
                parent_sequence: w.parent_sequence ?? ch.parent_sequence,
                level: w.level ?? ch.level,
                points: Array.isArray(w.points) ? [...w.points] : ch.points,
                point_items: pickPointItemsAuthoritative(
                  [w.point_items, w.config?.point_items],
                  ch.point_items,
                  ch.config?.point_items
                ),
                structure_only: Boolean(w.structure_only ?? ch.structure_only),
                describes: w.describes ?? ch.describes,
                aux_prompt: w.aux_prompt ?? ch.aux_prompt,
                reference_text: w.reference_text ?? ch.reference_text,
                fixed_output: Boolean(w.fixed_output ?? ch.fixed_output),
                element_ids: pickElementIds(w.element_ids, w.config?.element_ids, ch.element_ids),
              };
              const written =
                Boolean(w.has_draft) ||
                ["confirmed", "awaiting_confirm", "needs_human"].includes(String(w.status || ""));
              if (written) {
                next.status = "confirmed";
                next.status_label = "已写作";
                next.has_draft = true;
              } else if (w.status) {
                next.status = w.status;
              }
              return next;
            });
          }
          if (Array.isArray(wData?.project_folder_ids)) {
            projectIds = [...wData.project_folder_ids];
            projectLabel = String(wData.project_folder_label || "").trim();
          }
          if (wData && Object.prototype.hasOwnProperty.call(wData, "materials_pack_id")) {
            packId = String(wData.materials_pack_id || "").trim();
          }
          if (Array.isArray(wData?.element_nodes) && wData.element_nodes.length) {
            writingElements = wData.element_nodes;
          }
        } catch {
          /* 写作任务目录可选 */
        }
      } else if (/^w/i.test(tid)) {
        packId = String(data?.materials_pack_id || "").trim();
      }
      // 尚无写作 clone：展示会话预建资料包
      if (!packId) {
        packId = String(pendingMaterialsPackIdLocal.value || "").trim();
      }
      const prevKeys = outlineNodes.value.map((ch) => String(ch.key || "")).join("|");
      const prevOutline = outlineNodes.value;
      const prevElements = elementNodes.value;
      outlineNodes.value = mergeOutlinePreserveElementIds(prevOutline, list);
      // 服务端要素与本地合并：写作 clone 优先（检索词/槽位真源）
      const serverElements = writingElements.length
        ? writingElements
        : Array.isArray(data?.element_nodes)
          ? data.element_nodes
          : [];
      elementNodes.value = mergeElementNodesLocal(prevElements, serverElements);
      projectFolderIdsLocal.value = projectIds.map((x) => String(x || "").trim()).filter(Boolean);
      projectFolderLabel.value = projectLabel || folderLabelFromTree(projectFolderIdsLocal.value);
      await loadMaterialsPack(packId);
      // 服务端已带回非空关联时，清掉对应 pending
      for (const ch of outlineNodes.value) {
        const key = String(ch?.key || "").trim();
        const serverIds = pickElementIds(
          list.find((row) => String(row?.key || "") === key)?.element_ids,
          list.find((row) => String(row?.key || "") === key)?.config?.element_ids
        );
        if (key && serverIds.length) pendingChapterElementIds.delete(key);
      }
      emit("elements-saved", { taskId: tid, elementNodes: elementNodes.value });
      if (writingTid && writingTid !== tid) {
        emit("elements-saved", { taskId: writingTid, elementNodes: elementNodes.value });
      }
      extraChapters.value = outlineNodes.value.map((ch) => ({
        key: ch.key,
        sequence: ch.sequence,
        title: ch.title,
        parent: ch.parent,
        parent_sequence: ch.parent_sequence,
        level: ch.level,
        structure_only: Boolean(ch.structure_only),
        points: ch.points || [],
        element_ids: Array.isArray(ch.element_ids) ? [...ch.element_ids] : [],
        status: chapters.value.find((row) => row.key === ch.key)?.status || "",
      }));
      outlineFullyLoaded.value = true;
      loadedOutlineTaskId = tid;
      // 若选中章不在本蓝本目录中（切蓝本残留），改到第一章
      if (
        !selectedKey.value ||
        !list.some((ch) => String(ch.key || "") === String(selectedKey.value || ""))
      ) {
        selectedKey.value = extraChapters.value[0]?.key || "";
      }
      const idx = list.findIndex((ch) => String(ch.key || "") === String(selectedKey.value || ""));
      topologyFocus.value = idx >= 0 ? idx : 0;
      if (!soft && isSampleStudyDoc.value) {
        mainView.value = outlineConfirmedLocal.value ? "chapter" : "topology";
      }
      const nextKeys = list.map((ch) => String(ch.key || "")).join("|");
      // 仅目录结构变化时重挂拓扑，写作状态刷新不重挂（卡顿主因）
      if (!soft && prevKeys !== nextKeys) {
        nextTick(() => {
          topologyRenderKey.value += 1;
        });
      }
      // 软刷新也要回填配置，否则保存清空后仍显示缓存里的旧补充提示词/参考正文
      hydrateConfigDraft();
      snapshotOutlineCache(tid);
    } catch {
      if (token !== fetchToken) return;
      if (!soft) elementNodes.value = [];
    } finally {
      // 无论是否被更新的请求取代，只要本轮结束就清掉 inflight，避免 loading 卡死
      if (outlineInflightTid === tid) {
        outlineInflight = null;
        outlineInflightTid = "";
      }
      if (token === fetchToken) {
        loading.value = false;
      } else if (!outlineInflight) {
        // 后发请求已结束且没有更新请求：确保 loading 不会残留
        loading.value = false;
      }
    }
  })();
  return outlineInflight;
}

watch(
  () => props.robotId,
  () => {
    refreshLibraryPackOptions();
  },
);

onMounted(() => {
  window.addEventListener("pagehide", flushMaterialsSlotSchemaPersist);
  window.addEventListener("beforeunload", flushMaterialsSlotSchemaPersist);
});

onUnmounted(() => {
  flushMaterialsSlotSchemaPersist();
  window.removeEventListener("pagehide", flushMaterialsSlotSchemaPersist);
  window.removeEventListener("beforeunload", flushMaterialsSlotSchemaPersist);
  stopPointer();
  if (configAutosaveTimer) {
    clearTimeout(configAutosaveTimer);
    configAutosaveTimer = 0;
  }
  if (tabSwitchTimer) {
    clearTimeout(tabSwitchTimer);
    tabSwitchTimer = 0;
  }
  if (outlineLoadTimer) {
    clearTimeout(outlineLoadTimer);
    outlineLoadTimer = 0;
  }
});
</script>

<style scoped>
.doc-win {
  position: fixed;
  z-index: 1400;
  display: flex;
  flex-direction: column;
  min-width: 480px;
  min-height: 420px;
  border: 1px solid #d5deee;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.16);
  overflow: hidden;
}
.doc-win.docked {
  position: relative;
  inset: auto;
  z-index: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  border: 0;
  border-radius: 0;
  box-shadow: none;
}
.doc-win-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  background: #f8fafc;
  border-bottom: 1px solid #e8eef6;
  cursor: move;
  user-select: none;
  flex: 0 0 auto;
}
.doc-win.docked .doc-win-bar {
  cursor: default;
}
.doc-win-bar-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.doc-win-bar-main strong {
  font-size: 13px;
  color: #1e293b;
  white-space: nowrap;
}
.doc-win-select {
  width: min(220px, 52vw);
}
.doc-win-rename {
  height: 24px;
  padding: 0 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #475569;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  flex: 0 0 auto;
}
.doc-win-rename:hover:not(:disabled) {
  border-color: #bfdbfe;
  color: #1d4ed8;
  background: #eff6ff;
}
.doc-win-rename:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.doc-win-ops {
  display: flex;
  align-items: center;
  gap: 4px;
}
.doc-win-sync {
  height: 24px;
  padding: 0 8px;
  border: 1px solid #bfdbfe;
  border-radius: 7px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.doc-win-sync:hover {
  background: #dbeafe;
}
.doc-win-pending-lib {
  position: relative;
  height: 24px;
  padding: 0 10px 0 8px;
  border: 1px solid #fcd34d;
  border-radius: 7px;
  background: #fffbeb;
  color: #b45309;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.doc-win-pending-lib:hover {
  background: #fef3c7;
}
.doc-win-pending-lib:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.doc-win-pending-badge {
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ea580c;
  color: #fff;
  font-size: 11px;
  font-weight: 650;
  line-height: 16px;
  text-align: center;
}
.draft-html-pane {
  min-height: 220px;
}
.draft-html-pane :deep(.chapter-composer) {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}
.draft-html-pane :deep(.chapter-html-editor) {
  min-height: 200px;
  max-height: min(48vh, 420px);
  overflow: auto;
  padding: 10px 12px;
}
.doc-win-icon {
  width: 24px;
  height: 24px;
  border: 1px solid #dbe3f0;
  border-radius: 7px;
  background: #fff;
  color: #475569;
  cursor: pointer;
  line-height: 1;
}
.doc-win-icon:hover {
  background: #eef2ff;
  color: #4e6ef2;
}
.doc-win-zoom {
  min-width: 40px;
  text-align: center;
  font-size: 11px;
  color: #64748b;
}
.doc-win-body {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 10px 12px 16px;
  overflow: hidden;
}
.doc-win-mask {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(248, 250, 252, 0.78);
  backdrop-filter: blur(1px);
  cursor: wait;
  user-select: none;
}
.doc-win-mask-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  min-width: 180px;
  padding: 18px 22px;
  border: 1px solid #dbe4f3;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.1);
  color: #334155;
  text-align: center;
}
.doc-win-mask-card strong {
  font-size: 14px;
  color: #1e293b;
}
.doc-win-mask-card p {
  margin: 0;
  font-size: 12px;
  color: #64748b;
}
.doc-win-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid #dbe4f3;
  border-top-color: #4e6ef2;
  border-radius: 50%;
  animation: doc-win-spin 0.7s linear infinite;
}
@keyframes doc-win-spin {
  to {
    transform: rotate(360deg);
  }
}
.doc-win-meta {
  margin-bottom: 6px;
  color: #64748b;
  font-size: 0.92em;
}
.doc-win-meta .ops-live {
  color: #2563eb;
  font-weight: 600;
}
.doc-win-project-pack {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}
.project-pack-label {
  flex: 0 0 auto;
  font-size: 0.88em;
  color: #475569;
  font-weight: 600;
}
.project-pack-select {
  flex: 1 1 220px;
  min-width: 180px;
}
.materials-pack-panel {
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  flex-wrap: nowrap;
}
.materials-pack-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.materials-pack-name {
  font-size: 0.9em;
  color: #0f172a;
}
.materials-pack-name.muted,
.materials-pack-empty.muted,
.materials-slot-meta .muted {
  color: #94a3b8;
}
.materials-slot-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
}
.materials-slot-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #fff;
  border: 1px solid #e2e8f0;
}
.materials-slot-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  font-size: 0.88em;
}
.materials-slot-meta .slot-file {
  color: #2563eb;
}
.materials-slot-actions {
  display: flex;
  gap: 6px;
}
.slot-upload {
  cursor: pointer;
}
.slot-upload.disabled {
  opacity: 0.5;
  pointer-events: none;
}
.doc-win-ops-banner {
  margin: 0 0 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
  font-size: 0.92em;
  line-height: 1.4;
}
.doc-win-wizard-steps {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}
.doc-win-wizard-step {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #94a3b8;
}
.doc-win-wizard-step .step-index {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  background: #e2e8f0;
  color: #64748b;
}
.doc-win-wizard-step.done {
  color: #15803d;
}
.doc-win-wizard-step.done .step-index {
  background: #bbf7d0;
  color: #166534;
}
.doc-win-wizard-step.current {
  color: #1d4ed8;
  font-weight: 600;
}
.doc-win-wizard-step.current .step-index {
  background: #dbeafe;
  color: #1d4ed8;
}
.doc-win-wizard-step.clickable {
  cursor: pointer;
  border-radius: 6px;
  padding: 2px 4px;
}
.doc-win-wizard-step.clickable:hover {
  background: #ecfdf5;
  color: #047857;
}
.phase-actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  scrollbar-color: #cbd5e1 transparent;
}
.phase-actions::-webkit-scrollbar {
  height: 6px;
}
.phase-actions::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 999px;
}
.phase-actions .cfg-save {
  flex: 0 0 auto;
  white-space: nowrap;
  font-size: 11px;
  font-weight: 600;
  padding: 6px 12px;
  line-height: 1.25;
  min-height: 28px;
  box-sizing: border-box;
}
.view-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 10px;
  border-bottom: 1px solid #e8eef6;
}
.view-tab {
  border: 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  border-radius: 0;
  background: transparent;
  color: #64748b;
  font-size: 13px;
  font-weight: 500;
  padding: 8px 14px 9px;
  cursor: pointer;
}
.view-tab:hover:not(:disabled) {
  color: #334155;
}
.view-tab.active {
  color: #4338ca;
  font-weight: 650;
  border-bottom-color: #4e6ef2;
}
.view-tab:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.doc-win-topology {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid #eef2f7;
  border-radius: 10px;
  background: #fbfdff;
  padding: 6px;
}
.doc-win-topology :deep(.outline-shell) {
  min-height: 420px;
}
.doc-win-topology :deep(.canvas) {
  min-height: 360px;
}
.doc-win-preview {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid #eef2f7;
  border-radius: 10px;
  background: #fbfdff;
  padding: 10px 12px;
}
.preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-shrink: 0;
}
.preview-meta {
  color: #5b6b7c;
  font-size: 0.92em;
}
.preview-article {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 18px 28px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #e8eef6;
  line-height: 1.7;
  color: #1f2a37;
}
.preview-article :deep(h1) {
  margin: 0 0 1em;
  font-size: 1.45em;
  font-weight: 700;
}
.preview-article :deep(h2),
.preview-article :deep(h3),
.preview-article :deep(h4) {
  margin: 1.2em 0 0.55em;
  font-weight: 650;
}
.preview-article :deep(.rw-preview-body) {
  margin: 0 0 0.4em;
}
.preview-article :deep(.rw-preview-body p) {
  margin: 0.45em 0;
}
.preview-article :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.6em 0 1em;
  font-size: 0.95em;
}
.preview-article :deep(th),
.preview-article :deep(td) {
  border: 1px solid #cbd5e1;
  padding: 6px 8px;
  vertical-align: top;
  text-align: left;
}
.preview-article :deep(th) {
  background: #f1f5f9;
  font-weight: 650;
}
.preview-article :deep(.rw-preview-empty) {
  color: #8a97a8;
  font-style: italic;
}
.doc-win-split {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(220px, 38%) 1fr;
  gap: 10px;
}
.doc-win-tree,
.doc-win-detail {
  min-height: 0;
  overflow: auto;
  border: 1px solid #eef2f7;
  border-radius: 10px;
  background: #fbfdff;
}
.doc-win-detail {
  padding: 10px 12px;
}
.doc-outline-tree {
  padding: 2px 4px 8px;
  background: transparent;
  --el-tree-node-content-height: 28px;
  --el-tree-node-hover-bg-color: #eef2ff;
}
.doc-outline-tree :deep(.el-tree-node__content) {
  border-radius: 6px;
  /* 不要写 padding-left：el-tree 用 inline paddingLeft 做父子缩进 */
  padding-right: 4px;
  overflow: hidden;
}
.doc-outline-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: #eef2ff;
}
.doc-outline-tree :deep(.el-tree-node__expand-icon) {
  color: #94a3b8;
  font-size: 12px;
  width: 14px;
  padding: 2px;
  margin-right: 0;
}
.doc-outline-tree :deep(.el-tree-node__expand-icon.is-leaf) {
  width: 14px;
  padding: 2px;
}
.doc-outline-tree :deep(.el-checkbox) {
  height: 16px;
  margin-right: 4px;
}
.doc-outline-tree :deep(.el-checkbox__inner) {
  width: 14px;
  height: 14px;
}
.tree-bulk {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 6px 8px 8px;
  border-bottom: 1px solid #e2e8f0;
}
.tree-bulk-all {
  height: auto;
  margin-right: 0;
  font-size: 0.85em;
  color: #64748b;
  user-select: none;
}
.tree-bulk-all :deep(.el-checkbox__label) {
  font-size: 0.85em;
  color: #64748b;
  padding-left: 6px;
}
.tree-bulk-count {
  font-size: 0.85em;
  color: #64748b;
}
.tree-bulk-actions {
  display: inline-flex;
  gap: 6px;
  margin-left: auto;
}
.tree-bulk-actions .cfg-save {
  padding: 2px 10px;
  font-size: 0.85em;
  min-height: 28px;
}
.tree-node-row {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: #334155;
  font-size: 12px;
  line-height: 1.3;
}
.tree-node-row.run .label {
  color: #1d4ed8;
}
.tree-node-row .label {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-node-row .tone {
  font-size: 11px;
  max-width: 3.2em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.slot-warn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fef3c7;
  color: #b45309;
  border: 1px solid #f59e0b;
  font-style: normal;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  cursor: help;
}
.detail-head-main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.detail-head-main strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.detail-head .slot-warn {
  margin: 0;
}
.cfg-grid.is-ops-locked {
  opacity: 0.72;
}
.tone {
  font-style: normal;
  font-size: 0.85em;
  color: #64748b;
  flex-shrink: 0;
}
.tone.ok {
  color: #047857;
}
.tone.study {
  color: #b45309;
}
.tone.confirm {
  color: #6d28d9;
}
.tone.ready {
  color: #0369a1;
}
.tone.warn {
  color: #c2410c;
}
.tone.run {
  color: #2563eb;
}
.tone.muted {
  color: #94a3b8;
}
.detail-sticky {
  position: sticky;
  top: 0;
  z-index: 6;
  margin: -10px -12px 10px;
  padding: 10px 12px 0;
  background: #fbfdff;
  border-bottom: 1px solid #e8eef6;
  box-shadow: 0 6px 12px -8px rgba(15, 23, 42, 0.18);
}
.detail-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.detail-head strong {
  color: #0f172a;
}
.detail-tabs {
  display: flex;
  gap: 0;
  margin: 0 0 10px;
  border-bottom: 1px solid #e8eef6;
}
.detail-tab {
  border: 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  border-radius: 0;
  background: transparent;
  color: #64748b;
  font-size: 13px;
  font-weight: 500;
  padding: 7px 14px 8px;
  cursor: pointer;
}
.detail-tab:hover {
  color: #334155;
}
.detail-tab.active {
  color: #4338ca;
  font-weight: 650;
  border-bottom-color: #4e6ef2;
  background: transparent;
}
.cfg-pane,
.process-pane {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cfg-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 10px;
}
.cfg-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #475569;
}
.cfg-grid .span-2 {
  grid-column: 1 / -1;
}
/* el-checkbox 根节点也是 label，勿继承上面的 column，否则圆点与文字会换行 */
.cfg-grid .cfg-switch.el-checkbox,
.cfg-grid label.cfg-switch {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  height: auto;
  margin-right: 0;
  white-space: nowrap;
}
.cfg-switch :deep(.el-checkbox__input) {
  flex-shrink: 0;
}
.cfg-switch :deep(.el-checkbox__label) {
  font-size: inherit;
  color: inherit;
  line-height: 1.4;
  white-space: nowrap;
  padding-left: 8px;
}
.cfg-input {
  width: 100%;
  border: 1px solid #dbe3f0;
  border-radius: 8px;
  padding: 6px 8px;
  font: inherit;
  color: #0f172a;
  background: #fff;
  resize: vertical;
}
.cfg-parent-select {
  width: 100%;
}
.cfg-save {
  border: 0;
  border-radius: 999px;
  background: #4e6ef2;
  color: #fff;
  font-size: 12px;
  font-weight: 650;
  padding: 7px 14px;
  cursor: pointer;
}
.cfg-save:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.cfg-save.ghost {
  background: #fff;
  color: #4338ca;
  border: 1px solid #c7d2fe;
}
.cfg-save.ghost.danger {
  color: #b91c1c;
  border-color: #fecaca;
}
.cfg-save.compact {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
}
.write-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 0 0 10px;
}
.draft-area {
  min-height: 180px;
  font-size: 13px;
  line-height: 1.55;
}
.cfg-meta-banner {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  margin-bottom: 10px;
  border-radius: 8px;
  background: #f0f4ff;
  border: 1px solid #dbeafe;
}
.cfg-meta-line {
  font-size: 12px;
  line-height: 1.45;
  color: #334155;
}
.cfg-hint {
  font-size: 12px;
  color: #047857;
}
.cfg-point-items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cfg-point-items-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.cfg-point-items-head > span {
  font-size: 12px;
  font-weight: 650;
  color: #334155;
}
.cfg-point-table-wrap {
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
}
.cfg-point-table {
  width: 100%;
  min-width: 420px;
  border-collapse: collapse;
  font-size: 11px;
}
.cfg-point-table th,
.cfg-point-table td {
  padding: 4px 6px;
  border-bottom: 1px solid #eef2f7;
  vertical-align: middle;
  text-align: left;
}
.cfg-point-table th {
  background: #f8fafc;
  color: #64748b;
  font-weight: 600;
  white-space: nowrap;
}
.cfg-point-table tbody tr:last-child td {
  border-bottom: 0;
}
.cfg-point-table .col-idx {
  width: 28px;
  color: #94a3b8;
  text-align: center;
  font-size: 10px;
}
.cfg-point-table .col-slot {
  width: 88px;
  max-width: 100px;
}
.cfg-point-table .col-el {
  width: 96px;
  max-width: 120px;
}
.cfg-point-table .col-state {
  width: 52px;
  white-space: nowrap;
}
.cfg-point-table .col-op {
  width: 88px;
  min-width: 88px;
  text-align: right;
  white-space: nowrap;
  position: sticky;
  right: 0;
  background: #fff;
  box-shadow: -4px 0 6px -4px rgba(15, 23, 42, 0.12);
}
.cfg-point-table th.col-op {
  background: #f8fafc;
}
.cfg-point-text,
.cfg-point-slot {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.35;
  color: #334155;
  word-break: break-word;
}
.cfg-point-slot {
  color: #64748b;
}
.cfg-point-link-el {
  border: 1px solid #c7d2fe;
  border-radius: 999px;
  background: #fff;
  color: #4338ca;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  cursor: pointer;
  white-space: nowrap;
}
.cfg-point-link-el:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.cfg-point-el-btn {
  border: 0;
  background: transparent;
  color: #4338ca;
  font-size: 10px;
  padding: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  text-align: left;
}
.cfg-point-edit {
  border: 0;
  background: transparent;
  color: #4338ca;
  font-size: 10px;
  cursor: pointer;
  padding: 0 2px;
  margin-right: 4px;
}
.cfg-point-edit:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.cfg-point-cell {
  width: 100%;
  min-height: 32px;
  padding: 6px 8px;
  font-size: 12px;
  resize: none;
}
.cfg-point-el {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #4338ca;
}
.cfg-point-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
}
.cfg-point-badge.warn {
  background: #fff7ed;
  color: #c2410c;
}
.cfg-point-table .muted {
  color: #94a3b8;
}
.cfg-point-del {
  border: 0;
  background: transparent;
  color: #b91c1c;
  font-size: 10px;
  cursor: pointer;
  padding: 0 2px;
}
.cfg-point-del:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.cfg-ref-keywords {
  margin: 0;
  padding: 8px 10px;
  border: 1px dashed #dbe4f0;
  border-radius: 10px;
  background: #f8fafc;
}
.cfg-ref-keywords-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 650;
  color: #475569;
}
.cfg-ref-keywords-hint {
  font-size: 11px;
  font-weight: 500;
  color: #94a3b8;
}
.cfg-ref-keywords-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.cfg-ref-kw {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid #e2e8f0;
  background: #fff;
  color: #334155;
  font-size: 12px;
  line-height: 1.4;
  word-break: break-word;
}
.cfg-elements {
  margin-top: 2px;
  padding: 10px 12px;
  border: 1px solid #e8eef6;
  border-radius: 10px;
  background: #f8fafc;
}
.cfg-elements-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 650;
  color: #475569;
}
.cfg-el-manage {
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
}
.cfg-el-manage:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.cfg-element-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.cfg-el-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  color: #1e3a5f;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  line-height: 1.3;
  cursor: pointer;
  text-align: left;
}
.cfg-el-tag:hover:not(:disabled) {
  border-color: #60a5fa;
  background: #dbeafe;
}
.cfg-el-tag:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.cfg-el-tag .kind {
  flex: none;
  padding: 0 6px;
  border-radius: 999px;
  background: #fff;
  color: #1d4ed8;
  font-size: 11px;
}
.cfg-el-tag .name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cfg-el-empty {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.45;
}
.detail-note,
.detail-block {
  margin: 0 0 10px;
  font-size: 0.95em;
  color: #334155;
}
.detail-title {
  margin-bottom: 4px;
  font-weight: 650;
  color: #475569;
}
.detail-steps,
.detail-block ul {
  margin: 0;
  padding-left: 18px;
}
.detail-steps li,
.detail-block li {
  margin: 4px 0;
}
.detail-steps em {
  margin-left: 6px;
  color: #64748b;
  font-style: normal;
}
.doc-ev-excerpt {
  margin-top: 4px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}
.write-collect-panel {
  margin: 0 0 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.write-collect-block {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  padding: 10px 12px;
}
.write-collect-head {
  font-size: 12px;
  font-weight: 650;
  color: #475569;
  margin-bottom: 8px;
}
.write-collect-steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.write-collect-steps li strong {
  font-size: 13px;
  color: #0f172a;
}
.write-collect-steps li .st {
  margin-left: 8px;
  font-size: 12px;
  color: #64748b;
}
.write-collect-steps li.running .st,
.write-collect-steps li.searching .st {
  color: #2563eb;
}
.write-collect-steps li.done .st {
  color: #059669;
}
.write-collect-notes {
  margin: 6px 0 0;
  padding-left: 18px;
  color: #334155;
  font-size: 12px;
  line-height: 1.45;
}
.write-collect-note {
  margin: 6px 0 0;
  font-size: 12px;
  color: #475569;
  line-height: 1.45;
}
.write-collect-evidence {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.write-collect-evidence .ev-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.write-collect-evidence .ev-name {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}
.write-collect-evidence .ev-kind {
  font-size: 11px;
  color: #64748b;
  background: #e2e8f0;
  border-radius: 999px;
  padding: 1px 8px;
}
.write-collect-evidence .ev-query {
  margin: 4px 0 0;
  font-size: 12px;
  color: #475569;
}
.write-collect-evidence .ev-excerpt {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fff;
  border: 1px solid #e2e8f0;
  font-size: 12px;
  line-height: 1.45;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 160px;
  overflow: auto;
  font-family: inherit;
}
.doc-win-empty {
  margin: 16px 8px;
  color: #64748b;
  line-height: 1.55;
}
.doc-win-empty.sm {
  margin: 12px;
  font-size: 0.95em;
}
.doc-win-resize {
  position: absolute;
  right: 2px;
  bottom: 2px;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  background:
    linear-gradient(135deg, transparent 50%, #94a3b8 50%) no-repeat,
    linear-gradient(135deg, transparent 60%, #cbd5e1 60%) no-repeat;
  background-position: 4px 4px, 1px 1px;
  background-size: 10px 10px, 10px 10px;
}
.restudy-hero {
  margin-bottom: 14px;
}
.restudy-hero strong {
  display: block;
  font-size: 15px;
  color: #0f172a;
}
.restudy-hero p {
  margin: 6px 0 0;
  font-size: 13px;
  color: #64748b;
  line-height: 1.5;
}
.restudy-modes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 12px;
}
.restudy-mode {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  padding: 12px 14px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
  cursor: pointer;
}
.restudy-mode.active {
  border-color: #4e6ef2;
  background: #eef2ff;
  box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.12);
}
.restudy-mode-title {
  font-size: 14px;
  font-weight: 650;
  color: #0f172a;
}
.restudy-mode-desc {
  font-size: 12px;
  color: #64748b;
  line-height: 1.45;
}
.restudy-rich-paste {
  min-height: 180px;
  max-height: 320px;
  overflow: auto;
  padding: 12px 14px;
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  background: #fff;
  line-height: 1.6;
  font-size: 13px;
  outline: none;
}
.restudy-rich-paste:empty::before {
  content: attr(data-placeholder);
  color: #94a3b8;
}
.restudy-rich-paste:focus {
  border-color: #4e6ef2;
  box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.12);
}
</style>
