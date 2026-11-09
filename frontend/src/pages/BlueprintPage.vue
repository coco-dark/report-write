<template>
  <div v-if="!editing" class="blueprint-list ay-page ay-page-scroll">
    <div v-if="fromChat" class="chat-handoff-banner">
      <span>配置完成后返回对话继续写作。</span>
      <a-button @click="returnToChat" type="link">返回对话</a-button>
    </div>
    <header class="ay-hero list-hero">
      <div>
        <h1>书写蓝本</h1>
        <p>选择一份蓝本进入配置；不同蓝本互不影响。</p>
      </div>
      <div class="list-hero-actions">
        <a-button v-if="fromChat" @click="returnToChat" shape="round">返回对话</a-button>
        <a-button type="primary" @click="openCreate" shape="round">新建蓝本</a-button>
      </div>
    </header>

    <div class="list-grid">
      <article
        v-for="item in blueprints"
        :key="item.id"
        class="bp-card"
        :class="{ disabled: item.enabled === false }"
      >
        <div class="bp-top" @click="enterBlueprint(item.id)">
          <strong>{{ item.title || item.id }}</strong>
          <div class="bp-pills">
            <span class="bp-pill" :class="item.enabled === false ? 'off' : 'on'">
              {{ item.enabled === false ? "已停用" : "启用中" }}
            </span>
            <span class="bp-pill" :class="item.outline_confirmed ? 'ok' : ''">
              {{ item.outline_confirmed ? "目录已确认" : "未确认" }}
            </span>
          </div>
        </div>
        <p class="bp-desc" @click="enterBlueprint(item.id)">{{ item.description || item.category || "暂无说明" }}</p>
        <div class="bp-tags" v-if="(item.tags || []).length" @click="enterBlueprint(item.id)">
          <span v-for="tag in item.tags" :key="tag" class="bp-tag">{{ tag }}</span>
        </div>
        <div class="bp-meta">
          <span @click="enterBlueprint(item.id)">{{ item.chapter_count || 0 }} 章 · {{ item.element_count || 0 }} 要素</span>
          <span>{{ item.updated_at }}</span>
        </div>
        <div class="bp-actions" @click.stop>
          <a-switch
 :checked="item.enabled !== false"
 inline-prompt
 checked-children="启用"
 inchecked-children="停用"
 :loading="statusBusyId === item.id"
 @change="(val) => toggleEnabled(item, val)"
          />
          <a-button size="small" :loading="deleteBusyId === item.id" @click="removeBlueprint(item)" danger>
            删除
          </a-button>
        </div>
      </article>
    </div>
    <a-empty v-if="!blueprints.length && !listLoading" description="还没有蓝本，先新建一份" :image-style="{ height: 80 }"  />

    <a-modal v-model:open="createDialog" title="新建书写蓝本" width="480px" destroy-on-close centered>
      <a-form layout="vertical">
        <a-form-item label="名称">
          <a-input v-model:value="createForm.title" placeholder="如：安全预评价正文蓝本" />
        </a-form-item>
        <a-form-item label="分类">
          <a-input v-model:value="createForm.category" placeholder="如：安全评价" />
        </a-form-item>
        <a-form-item label="标签">
          <a-input v-model:value="createForm.tagsText" placeholder="逗号或空格分隔" />
        </a-form-item>
        <a-form-item label="用途说明">
          <a-textarea v-model:value="createForm.description" :rows="3" placeholder="何时选用这份蓝本" />
        </a-form-item>
      </a-form>
      <template #footer>
        <a-button @click="createDialog = false">取消</a-button>
        <a-button type="primary" :loading="busy" @click="submitCreate">创建</a-button>
      </template>
    </a-modal>
  </div>

  <div v-else class="blueprint">
    <aside class="rail">
      <div class="brand">
        <button type="button" class="back" @click="backToList">←</button>
        <div>
          <strong>{{ specForm.title || "书写蓝本" }}</strong>
          <p>写作格式与物料</p>
        </div>
      </div>

      <a-tabs v-model:activeKey="railTab" class="rail-tabs">
        <a-tab-pane tab="蓝本" key="work">
          <div class="rail-block">
            <div class="block-title">蓝本信息</div>
            <a-input v-model:value="specForm.title" size="small" placeholder="蓝本名称，如：安全预评价正文规格" />
            <a-input v-model:value="specForm.category" size="small" placeholder="分类，如：安全评价 / 环评" />
            <a-input
              v-model:value="tagsText"
              size="small"
              placeholder="标签，用逗号或空格分隔，如：预评价 目录 要点"
            />
            <a-textarea
              v-model:value="specForm.description"
              :rows="2"
              size="small"
              placeholder="用途说明：何时选用这份蓝本"
            />
            <a-button class="full" type="primary" :loading="busy" @click="saveSpec()">保存蓝本信息</a-button>
            <div class="meta-tags" v-if="currentTask">
              <a-tag size="small" :color="statusTagType(currentTask.status)">
                {{ STATUS_LABELS[currentTask.status] || currentTask.status }}
              </a-tag>
              <a-tag size="small" color="default">{{ outlineConfirmed ? "目录已确认" : "目录未确认" }}</a-tag>
            </div>
            <p class="hint tiny">无匹配蓝本时，Agent 将按模型能力直接生成。</p>
          </div>

          <div class="rail-block">
            <div class="block-title">蓝本样本</div>
            <p class="hint tiny">上传一份样例报告，系统会 RAG 解析；生成大纲与依据文档分析可选用该样本，并按章节序号匹配正文。</p>
            <div v-if="currentTask?.sample_ready" class="sample-card">
              <div class="sample-name" :title="currentTask.sample_name">{{ currentTask.sample_name }}</div>
              <div class="sample-meta">已解析 {{ formatChars(currentTask.sample_chars) }}</div>
              <div class="sample-actions">
                <a-upload
 :before-upload="preventUpload"
 :show-file-list="false"
 accept=".pdf,.doc,.docx"
 :disabled="busy"
 @change="onBlueprintSample"
 >
                  <a-button size="small" :loading="sampleUploading">更换</a-button>
                </a-upload>
                <a-button size="small" :disabled="busy" @click="removeBlueprintSample" danger>
                  清除
                </a-button>
              </div>
            </div>
            <a-upload-dragger
 v-else
 :before-upload="preventUpload"
 :show-file-list="false"
 accept=".pdf,.doc,.docx"
 :disabled="busy"
 @change="onBlueprintSample"
 class="sample-upload"
 >
              <div class="ant-upload-text">拖拽或点击上传样本</div>
              <template #tip>
                <div class="ant-upload-hint">PDF / Word，上传后自动 RAG 解析</div>
              </template>
            </a-upload-dragger>
          </div>

          <div class="rail-block" v-if="outlineConfirmed">
            <div class="block-title">编写</div>
            <a-button class="full" type="primary" :loading="busy" :disabled="!confirmed" @click="writeReport()">
              开始编写
            </a-button>
          </div>
        </a-tab-pane>

        <a-tab-pane :disabled="!hasOutline" tab="节点" key="nodes">
          <a-radio-group v-model:value="nodeKind" size="small" class="node-kind">
            <a-radio-button value="chapter">章节节点</a-radio-button>
            <a-radio-button value="element">要素节点</a-radio-button>
          </a-radio-group>

          <template v-if="nodeKind === 'chapter'">
            <div class="chapter-toolbar">
              <div class="chapter-toolbar-row">
                <a-button
 class="toolbar-half"
 size="small"
 :disabled="outlineConfirmed || busy"
 @click="addOutlineNode(outlineFocus)"
 >
                  新增章节
                </a-button>
                <a-button
 class="toolbar-half"
 size="small"
 :disabled="outlineConfirmed || busy || !checkedKeys.length"
 @click="batchRemoveChapters"
 danger>
                  批量删除{{ checkedKeys.length ? ` ${checkedKeys.length}` : "" }}
                </a-button>
              </div>
              <a-button
 class="toolbar-primary"
 size="small"
 type="primary"
 :disabled="busy || !checkedKeys.length"
 @click="openAutoStudy"
 >
                依据文档分析{{ checkedKeys.length ? ` · ${checkedKeys.length} 章` : "" }}
              </a-button>
              <button
                v-if="(currentTask?.pending_study || []).length"
                type="button"
                class="pending-chip"
                @click="reopenPendingStudy"
              >
                待确认分析结果 {{ currentTask.pending_study.length }}
              </button>
              <p class="hint tiny toolbar-hint">
                {{
                  outlineConfirmed
                    ? "目录已确认，结构不可删改；勾选后可分析。"
                    : "勾选章节后删除或分析；树内「运行」默认只跑本章。"
                }}
              </p>
            </div>
            <UiTree
 ref="chapterTreeRef"
 class="chapter-tree"
 :data="chapterTree"
 node-key="key"
 show-checkbox
 default-expand-all
 highlight-current
 :expand-on-click-node="false"
 :current-node-key="chapterNodes[outlineFocus]?.key"
 @check="onTreeCheck"
 @node-click="onTreeNodeClick"
 >
              <template #default="{ data }">
                <div class="tree-row">
                  <span class="seq">{{ data.sequence || "—" }}</span>
                  <span class="name">{{ data.title }}</span>
                  <a-tag v-if="isPendingStudy(data.key)" size="small" color="warning">
                    待确认
                  </a-tag>
                  <a-tag v-else-if="hasChapterRunResult(data.key)" size="small" color="blue">
                    已写
                  </a-tag>
                  <a-tag v-else-if="hasChapterStudyResult(data)" size="small" color="success">
                    已分析
                  </a-tag>
                  <a-button
 v-if="hasChapterRunResult(data.key)"
 size="small"
 @click.stop="viewChapterRun(data.key)"
 type="link">
                    正文
                  </a-button>
                  <a-tag v-if="(data.element_ids || []).length" size="small">
                    {{ data.element_ids.length }}
                  </a-tag>
                  <a-button
 v-if="hasChapterStudyResult(data)"
 size="small"
 @click.stop="viewChapterResult(data.key)"
 type="text">
                    结果
                  </a-button>
                  <a-button
 v-if="canRunChapters"
 size="small"
 :loading="busy"
 title="仅运行本章（不含子章节）"
 @click.stop="runChapter(data.key, false)"
 type="link">
                    运行
                  </a-button>
                </div>
              </template>
            </UiTree>
          </template>

          <template v-else>
            <a-button class="full" size="small" :disabled="busy" @click="outlineWorkspaceRef?.startNewElement?.()">新增要素</a-button>
            <p class="hint tiny">独立新建后，在拓扑图连线到章节。</p>
            <button
              v-for="element in elementNodes"
              :key="element.id"
              type="button"
              class="node-row element"
              @click="openElementNode(element.id)"
            >
              <a-tag size="small" color="default">{{ elementKindLabel(element.kind) }}</a-tag>
              <span class="name">{{ element.name || "未命名要素" }}</span>
            </button>
            <a-empty v-if="!elementNodes.length" description="暂无要素节点" :image-style="{ height: 48 }"  />
          </template>
        </a-tab-pane>
      </a-tabs>
    </aside>

    <main class="main">
      <div v-if="fromChat" class="chat-handoff-banner">
        <span>从对话进入：完善蓝本后返回对话继续。</span>
        <a-button @click="returnToChat" type="link">返回对话</a-button>
      </div>
      <header class="ay-hero main-hero">
        <div>
          <h1>{{ hasOutline ? "章节拓扑" : "书写蓝本" }}</h1>
          <p>
            {{
              hasOutline
                ? "配置章节格式与要素物料；可勾选章节后依据文档分析要点。"
                : "配置目录大纲、分类标签与要素节点，供后续 Agent 编写时选用。"
            }}
          </p>
        </div>
        <div class="head-actions">
          <a-button v-if="fromChat" @click="returnToChat" shape="round">返回对话</a-button>
          <template v-if="hasOutline">
            <a-button v-if="outlineConfirmed" @click="revokeOutline()" shape="round">撤回确认</a-button>
            <a-button type="primary" :loading="busy" :disabled="outlineConfirmed" @click="confirmOutline()" shape="round">
              确认目录
            </a-button>
          </template>
        </div>
      </header>

      <section v-if="!hasOutline" class="empty-stage">
        <a-card shadow="never" class="empty-card">
          <a-empty description="还没有目录大纲">
            <div class="empty-actions">
              <a-button type="primary" @click="openOutlineDialog('sample')">配置 / 生成大纲</a-button>
            </div>
          </a-empty>
        </a-card>
      </section>

      <OutlineWorkspace
        v-else
        ref="outlineWorkspaceRef"
        :outline="outline"
        :confirmed="outlineConfirmed"
        :busy="busy"
        :focus="outlineFocus"
        :task-id="taskId"
        :locate-key="locateNodeKey"
        :open-key="openNodeKey"
        :open-element-id="openElementId"
        :element-nodes="elementNodes"
        :material-names="materialNames"
        :chapter-states="currentTask?.chapters || []"
        :prefer-tab="openChapterTab"
        :can-run="canRunChapters"
        canvas-only
        @confirm="confirmOutline()"
        @revoke="revokeOutline()"
        @copy="copyOutline()"
        @import="importOutlineFile"
        @from-text="outlineFromText"
        @select="(index) => (outlineFocus = index)"
        :persist-chapters="saveOutlineNodes"
        @change="saveOutlineNodes"
        @add="addOutlineNode"
        @remove="removeOutlineNode"
        @indent="indentOutlineNode"
        @dialog-closed="clearOpenKeys"
        @change-elements="saveElementNodes"
        @remove-element="removeElementNode"
        @run-chapter="onRunChapter"
      />
    </main>

    <a-modal v-model:open="outlineDialog" title="配置 / 生成目录大纲" width="680px" destroy-on-close centered>
      <a-radio-group v-model:value="outlineMode" class="outline-mode">
        <a-radio-button value="sample">蓝本样本</a-radio-button>
        <a-radio-button value="upload">上传文件</a-radio-button>
        <a-radio-button value="paste">粘贴文本</a-radio-button>
      </a-radio-group>

      <div v-if="outlineMode === 'sample'" class="outline-upload">
        <p class="hint">使用本蓝本已上传并 RAG 解析的样本，按文内标题/序号抽取目录。</p>
        <div v-if="currentTask?.sample_ready" class="sample-card inline">
          <div class="sample-name">{{ currentTask.sample_name }}</div>
          <div class="sample-meta">已解析 {{ formatChars(currentTask.sample_chars) }}</div>
        </div>
        <p v-else class="hint">尚未上传样本，请先在左侧「蓝本样本」上传，或改用上传文件 / 粘贴文本。</p>
        <a-checkbox v-model:checked="outlineUseLlm" class="check">交由模型整理层级</a-checkbox>
      </div>

      <div v-else-if="outlineMode === 'upload'" class="outline-upload">
        <p class="hint">上传 Word / PDF，经 RAG 解析后自动抽取目录结构。</p>
        <a-upload-dragger
 :before-upload="preventUpload"
 :max-count="1"
 :file-list="outlineDocList"
 accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
 @change="onOutlineDoc"
 @remove="clearOutlineDoc"
 >
          <div class="ant-upload-text">将文件拖到此处，或<em>点击上传</em></div>
          <template #tip>
            <div class="ant-upload-hint">支持 .doc / .docx / .pdf</div>
          </template>
        </a-upload-dragger>
        <a-checkbox v-model:checked="outlineUseLlm" class="check">交由模型整理层级</a-checkbox>
      </div>

      <div v-else>
        <p class="hint">粘贴 Word 目录或大纲文本，也可导入 JSON。</p>
        <a-textarea
          v-model:value="outlinePaste"
          :rows="12"
          placeholder="1 概述&#10;1.1 安全预评价目的&#10;2 工程概况"
        />
        <a-checkbox v-model:checked="outlineUseLlm" class="check">交由模型整理层级</a-checkbox>
      </div>

      <template #footer>
        <a-upload
 v-if="outlineMode === 'paste'"
 :before-upload="preventUpload"
 :show-file-list="false"
 accept="application/json,.json"
 @change="onOutlineJson"
 >
          <a-button>导入 JSON</a-button>
        </a-upload>
        <a-button @click="outlineDialog = false">取消</a-button>
        <a-button
 v-if="outlineMode === 'sample'"
 type="primary"
 :loading="busy"
 :disabled="!currentTask?.sample_ready"
 @click="submitOutlineSample"
 >
          依据样本生成
        </a-button>
        <a-button
 v-else-if="outlineMode === 'upload'"
 type="primary"
 :loading="busy"
 :disabled="!outlineDocFile"
 @click="submitOutlineFile"
 >
          上传并分析
        </a-button>
        <a-button
 v-else
 type="primary"
 :loading="busy"
 :disabled="!outlinePaste.trim()"
 @click="submitOutlinePaste"
 >
          生成大纲
        </a-button>
      </template>
    </a-modal>

    <a-modal v-model:open="exportVisible" title="已确认正文" width="720px" destroy-on-close centered :footer="null">
      <pre class="draft export">{{ exportText }}</pre>
    </a-modal>

    <a-modal
 v-model:open="autoStudyDialog"
 title="依据文档分析"
 width="680px"
 class="study-dialog"
 destroy-on-close centered>
      <div class="study-hero">
        <div class="study-hero-mark">析</div>
        <div>
          <strong>已勾选 {{ checkedKeys.length }} 个章节</strong>
          <p>模型会按蓝本目录在文档中定位对应内容，并提炼写作要点，确认后写入。</p>
        </div>
      </div>

      <div class="study-modes">
        <button type="button" class="mode-card" :class="{ active: studyMode === 'sample' }" @click="studyMode = 'sample'">
          <span class="mode-ico">样</span>
          <span class="mode-title">蓝本样本</span>
          <span class="mode-desc">按章节序号匹配已解析样本正文</span>
        </button>
        <button type="button" class="mode-card" :class="{ active: studyMode === 'paste' }" @click="studyMode = 'paste'">
          <span class="mode-ico">文</span>
          <span class="mode-title">粘贴富文本</span>
          <span class="mode-desc">从 Word / 网页复制后粘贴</span>
        </button>
        <button type="button" class="mode-card" :class="{ active: studyMode === 'file' }" @click="studyMode = 'file'">
          <span class="mode-ico">档</span>
          <span class="mode-title">上传文件</span>
          <span class="mode-desc">上传 PDF / Word 临时分析</span>
        </button>
      </div>

      <div v-if="studyMode === 'sample'" class="study-pane">
        <div v-if="currentTask?.sample_ready" class="sample-card inline">
          <div class="sample-name">{{ currentTask.sample_name }}</div>
          <div class="sample-meta">已解析 {{ formatChars(currentTask.sample_chars) }} · 将按勾选章节序号定位正文</div>
        </div>
        <p v-else class="hint">请先在左侧上传蓝本样本并完成 RAG 解析。</p>
      </div>

      <div v-else-if="studyMode === 'paste'" class="study-pane">
        <div
          ref="pasteBox"
          class="rich-paste"
          contenteditable="true"
          data-placeholder="在此粘贴文档内容（从 Word 复制即可，将保留原文序号如 2.3.1）…"
          @input="onPasteBoxInput"
          @paste="onRichPaste"
        />
        <p class="hint tiny">
          从 Word 粘贴时会读取 HTML 中的真实序号（如 2. / 2.3.1）。若只要纯文本也可直接粘贴。
        </p>
      </div>

      <div v-else class="study-pane">
        <a-upload-dragger
 :before-upload="preventUpload"
 :max-count="1"
 accept=".pdf,.doc,.docx"
 @change="onAutoStudyFile"
 @remove="clearAutoStudyFile"
 :file-list="autoStudyFileList"
 class="study-upload"
 >
          <div class="upload-inner">
            <div class="upload-ico">↑</div>
            <div class="upload-title">拖拽文件到此处，或点击选择</div>
            <div class="upload-desc">支持 PDF、DOC、DOCX</div>
          </div>
        </a-upload-dragger>
      </div>

      <template #footer>
        <a-button @click="autoStudyDialog = false">取消</a-button>
        <a-button type="primary" :loading="busy" :disabled="!canSubmitStudy" @click="submitAutoStudy">
          开始分析
        </a-button>
      </template>
    </a-modal>

    <a-modal
 v-model:open="outlinePromptDialog"
 title="目录不一致"
 width="520px"
 :mask-closable="false"
 destroy-on-close centered>
      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-badge">Tool</span>
          <code>extract_outline_from_document</code>
        </div>
        <p class="tool-msg">{{ outlineToolMessage }}</p>
        <ul class="diff-preview">
          <li v-for="item in pendingOutlineDrafts" :key="item.chapter_key">
            <span class="seq">{{ item.sequence || "—" }}</span>
            <span>{{ item.title }}</span>
          </li>
        </ul>
      </div>
      <template #footer>
        <a-button :loading="busy" @click="skipOutlineAndStudy">不改目录，直接分析要点</a-button>
        <a-button type="primary" @click="openOutlineEdit">编辑新目录并写入</a-button>
      </template>
    </a-modal>

    <a-modal
 v-model:open="outlineEditDialog"
 title="编辑新目录章节"
 width="720px"
 :mask-closable="false"
 destroy-on-close centered>
      <div class="tool-card compact">
        <div class="tool-card-head">
          <span class="tool-badge">Tool</span>
          <code>extract_outline_from_document</code>
          <a-tag size="small" color="warning">确认后写入目录，再学要点</a-tag>
        </div>
        <p class="tool-msg">{{ outlineToolMessage }}</p>
      </div>
      <p class="hint">可修改序号与标题；取消勾选的章节不会写入大纲。</p>
      <div v-for="(item, index) in pendingOutlineDrafts" :key="item.chapter_key" class="study-draft">
        <div class="study-draft-head">
          <div class="study-draft-title">
            <a-checkbox v-model:checked="pendingOutlineDrafts[index].accepted"  />
            <a-tag size="small" color="warning">新增</a-tag>
            <strong>{{ item.sequence || "—" }} {{ item.title }}</strong>
          </div>
        </div>
        <div class="outline-edit-row" v-if="item.accepted !== false">
          <a-input v-model:value="pendingOutlineDrafts[index].sequence" placeholder="序号 如 2.3.1" style="width: 120px" />
          <a-input v-model:value="pendingOutlineDrafts[index].title" placeholder="章节标题" />
        </div>
      </div>
      <template #footer>
        <a-button @click="outlineEditDialog = false">稍后</a-button>
        <a-button type="primary" :loading="busy" @click="confirmOutlineThenStudy">
          确认目录并继续分析
        </a-button>
      </template>
    </a-modal>

    <a-modal
 v-model:open="runProgressVisible"
 title="正在运行章节"
 width="520px"
 :mask-closable="false"
 :close-on-press-escape="false"
 :show-close="false"
 class="study-progress-dialog"
 destroy-on-close centered :footer="null">
      <div class="study-progress">
        <p class="study-progress-phase">{{ runProgress.phase || "处理中" }}</p>
        <p class="study-progress-current" v-if="runProgress.current">
          当前：<strong>{{ runProgress.current }}</strong>
          <span v-if="runProgress.step" class="study-progress-count"> · {{ runStepLabel(runProgress.step) }}</span>
        </p>
        <ul class="study-progress-list">
          <li
            v-for="item in runProgress.items"
            :key="item.key"
            :class="item.status"
          >
            <span class="dot" />
            <span class="name">{{ item.title || item.chapter }}</span>
            <span class="st">
              {{ item.stepLabel || runStepLabel(item.step) }}
              {{ item.status === "done" ? "完成" : item.status === "skipped" ? "跳过" : item.status === "running" ? "进行中…" : "" }}
            </span>
          </li>
        </ul>
        <p class="hint tiny">单章按 检索 → 写作 → 检查 推进，请勿刷新页面。</p>
      </div>
    </a-modal>

    <a-modal
 v-model:open="studyProgressVisible"
 title="正在分析"
 width="520px"
 :mask-closable="false"
 :close-on-press-escape="false"
 :show-close="false"
 class="study-progress-dialog"
 destroy-on-close centered :footer="null">
      <div class="study-progress">
        <p class="study-progress-phase">{{ studyProgress.phase || "处理中" }}</p>
        <p class="study-progress-current" v-if="studyProgress.current">
          当前：<strong>{{ studyProgress.current }}</strong>
          <span v-if="studyProgress.total" class="study-progress-count">
            （{{ studyProgress.index || studyProgress.items.filter((i) => i.status === 'done').length }}/{{ studyProgress.total }}）
          </span>
        </p>
        <a-progress
 v-if="studyProgress.total"
 :percent="studyProgressPercent"
 :stroke-width="10"
 style="margin: 12px 0 16px"
  />
        <ul class="study-progress-list">
          <li
            v-for="item in studyProgress.items"
            :key="item.key"
            :class="item.status"
          >
            <span class="dot" />
            <span class="seq">{{ item.sequence || "—" }}</span>
            <span class="name">{{ item.title }}</span>
            <span class="st">{{ item.status === "done" ? "完成" : item.status === "running" ? "分析中…" : "" }}</span>
          </li>
        </ul>
        <p class="hint tiny">多章会逐个调用模型，请勿刷新页面。</p>
      </div>
    </a-modal>

    <a-modal
 v-model:open="studyConfirmDialog"
 title="确认写作要点"
 width="780px"
 :mask-closable="false"
 destroy-on-close centered>
      <div class="tool-card compact">
        <div class="tool-card-head">
          <span class="tool-badge">Tool</span>
          <code>study_from_document</code>
          <a-tag size="small" color="default">仅写入要点</a-tag>
        </div>
        <p class="tool-msg">{{ studyToolMessage || "请确认写作要点后写入蓝本。" }}</p>
      </div>
      <p class="hint">每章各自确认后写入该节点；也可稍后统一确认。未确认的仍保留在待确认列表。</p>
      <div v-for="(item, index) in pendingStudyDrafts" :key="item.chapter_key" class="study-draft">
        <div class="study-draft-head">
          <div class="study-draft-title">
            <strong>{{ item.sequence || "—" }} {{ item.title }}</strong>
          </div>
          <a-tag size="small" :color="item.matched ? 'success' : 'default'">
            {{ item.matched ? "已匹配正文" : "未匹配到正文" }}
          </a-tag>
          <a-button
 size="small"
 type="primary"
 :loading="busy"
 @click="confirmOneStudy(index)"
 >
            确认本章
          </a-button>
        </div>
        <a-textarea
          v-if="item.describes"
          v-model:value="pendingStudyDrafts[index].describes"
          :rows="2"
          class="study-describes"
          placeholder="本章简述"
        />
        <a-textarea
          v-model:value="pendingStudyDrafts[index].pointsText"
          :rows="4"
          placeholder="写作要点，一行一条"
        />
      </div>
      <template #footer>
        <a-button @click="deferStudyConfirm">稍后确认</a-button>
        <a-button type="primary" :loading="busy" :disabled="!pendingStudyDrafts.length" @click="applyAutoStudy">
          全部确认并保存
        </a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from "../ui.js";
import { computed, inject, nextTick, onMounted, ref, watch } from "vue";

import OutlineWorkspace from "../components/OutlineWorkspace.vue";
import { request } from "../api";
import { STATUS_LABELS, statusTagType } from "../constants";
import { useWriting } from "../useWriting";

const props = defineProps({
  fromChat: { type: Boolean, default: false },
  chatSessionId: { type: String, default: "" },
  initialBlueprintId: { type: String, default: "" },
});

const navigateTo = inject("navigateTo", null);

const writing = useWriting();
const {
  taskId,
  busy,
  studyProgress,
  resetStudyProgress,
  runProgress,
  resetRunProgress,
  currentTask,
  outlineFocus,
  exportText,
  exportVisible,
  confirmed,
  outlineConfirmed,
  outline,
  confirmOutline,
  revokeOutline,
  copyOutline,
  importOutlineFile,
  outlineFromText,
  generateOutline,
  uploadBlueprintSample,
  clearBlueprintSample,
  saveOutlineNodes,
  addOutlineNode,
  removeOutlineNode,
  removeOutlineNodes,
  indentOutlineNode,
  saveElementNodes,
  removeElementNode,
  runChapter,
  saveSpecMeta,
  extractOutlineFromDocument,
  confirmOutlineExtract,
  studyPointsFromDocument,
  confirmAutoStudy,
  writeReport,
  loadBlueprints,
  openBlueprint,
  createBlueprint,
  leaveBlueprint,
} = writing;

const editing = ref(false);
const blueprints = ref([]);
const listLoading = ref(false);
const statusBusyId = ref("");
const deleteBusyId = ref("");
const createDialog = ref(false);
const createForm = ref({ title: "", category: "", tagsText: "", description: "" });
const railTab = ref("work");
const nodeKind = ref("chapter");
const templateList = ref([]);
const materialList = ref([]);
const outlineDialog = ref(false);
const outlineMode = ref("sample");
const outlinePaste = ref("");
const outlineUseLlm = ref(false);
const outlineDocFile = ref(null);
const outlineDocList = ref([]);
const sampleUploading = ref(false);
const openNodeKey = ref("");
const locateNodeKey = ref("");
const openChapterTab = ref("");
const openElementId = ref("");
const checkedKeys = ref([]);
const chapterTreeRef = ref(null);
const outlineWorkspaceRef = ref(null);
const autoStudyDialog = ref(false);
const autoStudyFile = ref(null);
const autoStudyFileList = ref([]);
const studyMode = ref("sample");
const studyPasteHtml = ref("");
const studyPasteText = ref("");
const pasteBox = ref(null);
const studyConfirmDialog = ref(false);
const outlinePromptDialog = ref(false);
const outlineEditDialog = ref(false);
const outlineToolMessage = ref("");
const studyToolMessage = ref("");
const pendingOutlineDrafts = ref([]);
const pendingStudyDrafts = ref([]);
const canSubmitStudy = computed(() => {
  if (studyMode.value === "sample") return Boolean(currentTask.value?.sample_ready);
  if (studyMode.value === "file") return Boolean(autoStudyFile.value);
  return Boolean(studyPasteText.value.trim());
});
const studyProgressVisible = computed(
  () => Boolean(busy.value && studyProgress.value?.active && !studyConfirmDialog.value && !outlineEditDialog.value)
);
const runProgressVisible = computed(
  () => Boolean(busy.value && runProgress.value?.active && !studyProgressVisible.value)
);

function runStepLabel(step) {
  return { search: "检索", write: "写作", check: "检查" }[step] || step || "";
}

function onRunChapter(payload) {
  if (payload && typeof payload === "object") {
    return runChapter(payload.key, Boolean(payload.includeChildren));
  }
  return runChapter(payload, false);
}
const studyProgressPercent = computed(() => {
  const total = studyProgress.value?.total || 0;
  if (!total) return 0;
  const done = (studyProgress.value.items || []).filter((item) => item.status === "done").length;
  const index = studyProgress.value.index || done;
  return Math.min(100, Math.round((done / total) * 100) || Math.round(((index - 1) / total) * 100));
});
const specForm = ref({ title: "", category: "", tags: [], description: "" });
const tagsText = ref("");

onMounted(async () => {
  await refreshBlueprints();
  if (props.initialBlueprintId) {
    try {
      await enterBlueprint(props.initialBlueprintId);
    } catch (error) {
      ElMessage.error(error.message || "打开蓝本失败");
    }
  }
});

function returnToChat() {
  const sessionId = props.chatSessionId || "";
  const blueprintId = taskId.value || props.initialBlueprintId || "";
  if (typeof navigateTo === "function") {
    navigateTo("chat", {
      from: "blueprint",
      session_id: sessionId,
      blueprint_id: blueprintId,
    });
    return;
  }
  const params = new URLSearchParams({ from: "blueprint" });
  if (sessionId) params.set("session_id", sessionId);
  if (blueprintId) params.set("blueprint_id", blueprintId);
  window.location.search = params.toString();
}

async function refreshBlueprints() {
  listLoading.value = true;
  try {
    blueprints.value = await loadBlueprints();
  } catch (error) {
    blueprints.value = [];
  } finally {
    listLoading.value = false;
  }
}

function openCreate() {
  createForm.value = { title: "", category: "", tagsText: "", description: "" };
  createDialog.value = true;
}

async function submitCreate() {
  await createBlueprint({
    title: createForm.value.title || "未命名蓝本",
    category: createForm.value.category,
    tags: parseTags(createForm.value.tagsText),
    description: createForm.value.description,
  });
  createDialog.value = false;
  editing.value = true;
  railTab.value = "work";
  await refreshBlueprints();
}

async function enterBlueprint(id) {
  await openBlueprint(id);
  editing.value = true;
  railTab.value = "work";
  templateList.value = [];
  materialList.value = [];
  checkedKeys.value = [];
  clearOpenKeys();
}

async function toggleEnabled(item, enabled) {
  if (!item?.id) return;
  statusBusyId.value = item.id;
  try {
    await request(`/api/writing/blueprints/${encodeURIComponent(item.id)}/enabled`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: Boolean(enabled) }),
    });
    item.enabled = Boolean(enabled);
    ElMessage.success(enabled ? "已启用" : "已停用（对话匹配将忽略）");
  } catch (error) {
    ElMessage.error(error.message || "更新状态失败");
  } finally {
    statusBusyId.value = "";
  }
}

async function removeBlueprint(item) {
  if (!item?.id) return;
  try {
    await ElMessageBox.confirm(
      `确定删除蓝本「${item.title || item.id}」？删除后不可恢复。`,
      "删除蓝本",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  deleteBusyId.value = item.id;
  try {
    await request(`/api/writing/blueprints/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    blueprints.value = blueprints.value.filter((row) => row.id !== item.id);
    ElMessage.success("蓝本已删除");
  } catch (error) {
    ElMessage.error(error.message || "删除失败");
  } finally {
    deleteBusyId.value = "";
  }
}

async function backToList() {
  leaveBlueprint();
  editing.value = false;
  templateList.value = [];
  materialList.value = [];
  checkedKeys.value = [];
  clearOpenKeys();
  await refreshBlueprints();
}

const hasOutline = computed(() => Boolean(outline.value?.chapters?.length));
const chapterNodes = computed(() => outline.value?.chapters || []);
const elementNodes = computed(() => currentTask.value?.element_nodes || []);
const materialNames = computed(() => []);

function formatChars(n) {
  const num = Number(n) || 0;
  if (num >= 10000) return `${(num / 10000).toFixed(1)} 万字`;
  if (num >= 1000) return `${(num / 1000).toFixed(1)}k 字`;
  return `${num} 字`;
}
const canRunChapters = computed(() =>
  Boolean(
    currentTask.value?.outline_confirmed &&
      (confirmed.value || currentTask.value?.brief || (currentTask.value?.outline?.chapters || []).length)
  )
);
const chapterTree = computed(() => buildChapterTree(chapterNodes.value));

watch(hasOutline, (value) => {
  if (value) railTab.value = "nodes";
});

watch(
  () => currentTask.value && [currentTask.value.title, currentTask.value.category, currentTask.value.description, (currentTask.value.tags || []).join("|")],
  () => {
    const task = currentTask.value;
    if (!task) return;
    specForm.value = {
      title: task.title || "",
      category: task.category || "",
      tags: [...(task.tags || [])],
      description: task.description || "",
    };
    tagsText.value = (task.tags || []).join("，");
  },
);

watch(
  () => currentTask.value?.pending_outline,
  (pending) => {
    if (!(pending || []).length) return;
    const tool = currentTask.value?.pending_outline_tool;
    pendingOutlineDrafts.value = pending.map((item) => ({
      ...item,
      accepted: item.accepted !== false,
    }));
    outlineToolMessage.value = tool?.message || "";
    outlinePromptDialog.value = true;
    outlineEditDialog.value = false;
    studyConfirmDialog.value = false;
  },
);

watch(
  () => currentTask.value?.pending_study,
  (pending) => {
    if (!(pending || []).length) return;
    const tool = currentTask.value?.pending_study_tool;
    pendingStudyDrafts.value = pending.map((item) => ({
      ...item,
      pointsText: (item.points || []).join("\n"),
    }));
    studyToolMessage.value = tool?.message || "";
    outlinePromptDialog.value = false;
    outlineEditDialog.value = false;
    studyConfirmDialog.value = true;
  },
);

watch(taskId, () => {
  templateList.value = [];
  materialList.value = [];
  checkedKeys.value = [];
  railTab.value = "work";
  clearOpenKeys();
});

function onTemplate(file, files) {
  writing.templateFile.value = file.raw;
  templateList.value = files.slice(-1);
}

function clearTemplate() {
  writing.templateFile.value = null;
  templateList.value = [];
}

function onMaterial(_file, files) {
  materialList.value = files;
  writing.materialFiles.value = files.map((item) => item.raw).filter(Boolean);
}

function onMaterialRemove(_file, files) {
  onMaterial(null, files);
}

function openOutlineDialog(mode = "sample") {
  if (mode === "paste") outlineMode.value = "paste";
  else if (mode === "upload") outlineMode.value = "upload";
  else outlineMode.value = "sample";
  outlineDialog.value = true;
}

function preventUpload() {
  return false;
}

function uploadRaw(info) {
  const file = info?.file || info;
  return file?.originFileObj || file?.raw || (file instanceof File ? file : null);
}

function uploadList(info, fallback) {
  const list = info?.fileList;
  if (Array.isArray(list) && list.length) return list.slice(-1);
  return fallback || [];
}

async function onBlueprintSample(info) {
  const file = info?.file || info;
  if (file?.status === "removed") return;
  const raw = uploadRaw(info);
  if (!raw) return;
  sampleUploading.value = true;
  try {
    await uploadBlueprintSample(raw);
  } finally {
    sampleUploading.value = false;
  }
}

async function removeBlueprintSample() {
  try {
    await ElMessageBox.confirm("确认清除蓝本样本？大纲与分析将无法再选用该样本。", "清除样本", {
      type: "warning",
    });
  } catch {
    return;
  }
  await clearBlueprintSample();
}

function onOutlineJson(info) {
  const file = info?.file || info;
  if (file?.status === "removed") return;
  const raw = uploadRaw(info);
  if (raw) {
    importOutlineFile(raw);
    outlineDialog.value = false;
  }
}

function onOutlineDoc(info) {
  const file = info?.file || info;
  if (file?.status === "removed") {
    clearOutlineDoc();
    return;
  }
  outlineDocFile.value = uploadRaw(info);
  outlineDocList.value = uploadList(info, info?.fileList).slice(-1);
  if (outlineDocFile.value) {
    writing.templateFile.value = outlineDocFile.value;
  }
}

function clearOutlineDoc() {
  outlineDocFile.value = null;
  outlineDocList.value = [];
}

async function submitOutlineFile() {
  if (!outlineDocFile.value) {
    ElMessage.warning("请先选择 Word 或 PDF 文件");
    return;
  }
  writing.templateFile.value = outlineDocFile.value;
  await generateOutline({ use_llm: outlineUseLlm.value, use_sample: false });
  if (writing.currentTask.value?.outline?.chapters?.length) {
    outlineDialog.value = false;
    clearOutlineDoc();
  }
}

async function submitOutlineSample() {
  if (!currentTask.value?.sample_ready) {
    ElMessage.warning("请先上传并解析蓝本样本");
    return;
  }
  await generateOutline({ use_llm: outlineUseLlm.value, use_sample: true });
  if (writing.currentTask.value?.outline?.chapters?.length) {
    outlineDialog.value = false;
  }
}

function submitOutlinePaste() {
  outlineFromText({ text: outlinePaste.value, use_llm: outlineUseLlm.value }).then(() => {
    outlineDialog.value = false;
    outlinePaste.value = "";
  });
}

function selectChapterNode(index) {
  outlineFocus.value = index;
  openElementId.value = "";
  openNodeKey.value = chapterNodes.value[index]?.key || "";
}

function hasChapterStudyResult(node) {
  if (!node) return false;
  return Boolean(
    (node.describes || "").trim() ||
      (node.points || []).length ||
      (node.keywords || []).length ||
      (node.content_blocks || []).length
  );
}

function isPendingStudy(key) {
  return (currentTask.value?.pending_study || []).some((item) => item.chapter_key === key);
}

function viewChapterResult(key) {
  const index = chapterNodes.value.findIndex((item) => item.key === key);
  if (index < 0) return;
  openChapterTab.value = "result";
  selectChapterNode(index);
}

function hasChapterRunResult(key) {
  const state = (currentTask.value?.chapters || []).find((item) => item.key === key);
  if (!state) return false;
  return Boolean(
    (state.official_text || "").trim() ||
      (state.draft?.paragraphs || []).length ||
      (state.evidence || []).length
  );
}

function viewChapterRun(key) {
  const index = chapterNodes.value.findIndex((item) => item.key === key);
  if (index < 0) return;
  openChapterTab.value = "run";
  selectChapterNode(index);
}

function onTreeNodeClick(data) {
  const index = chapterNodes.value.findIndex((item) => item.key === data.key);
  if (index < 0) return;
  // 点击左侧树：聚焦并让右侧拓扑定位；有分析结果时打开结果弹窗
  outlineFocus.value = index;
  openElementId.value = "";
  locateNodeKey.value = "";
  nextTick(() => {
    locateNodeKey.value = data.key || "";
  });
  if (hasChapterStudyResult(data)) {
    openNodeKey.value = data.key || "";
  } else {
    openNodeKey.value = "";
  }
}

function onTreeCheck(_data, state) {
  checkedKeys.value = state?.checkedKeys || [];
}

async function batchRemoveChapters() {
  const keys = [...checkedKeys.value];
  if (!keys.length) {
    ElMessage.warning("请先勾选要删除的章节");
    return;
  }
  if (outlineConfirmed.value) {
    ElMessage.warning("目录已确认，请先撤回确认再删除");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确认删除勾选的 ${keys.length} 个章节？父级删除时其下子章节也会一并删除，且至少保留一章。`,
      "批量删除章节",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  await removeOutlineNodes(keys);
  checkedKeys.value = [];
  nextTick(() => {
    chapterTreeRef.value?.setCheckedKeys?.([]);
  });
}

function openElementNode(id) {
  openNodeKey.value = "";
  openElementId.value = id;
}

function clearOpenKeys() {
  openNodeKey.value = "";
  openElementId.value = "";
  openChapterTab.value = "";
}

watch(
  () => runProgress.value?.active,
  (active, wasActive) => {
    if (wasActive && !active && runProgress.value?.chapter) {
      const key = runProgress.value.chapter;
      openChapterTab.value = "run";
      openElementId.value = "";
      openNodeKey.value = "";
      nextTick(() => {
        openNodeKey.value = key;
        const index = chapterNodes.value.findIndex((item) => item.key === key);
        if (index >= 0) outlineFocus.value = index;
      });
    }
  }
);

function parseTags(text) {
  return String(text || "")
    .split(/[,，、\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function saveSpec() {
  const tags = parseTags(tagsText.value);
  specForm.value.tags = tags;
  return saveSpecMeta({
    title: specForm.value.title,
    category: specForm.value.category,
    tags,
    description: specForm.value.description,
  }).then(() => refreshBlueprints());
}

function openAutoStudy() {
  if (!checkedKeys.value.length) return;
  studyMode.value = currentTask.value?.sample_ready ? "sample" : "paste";
  autoStudyFile.value = null;
  autoStudyFileList.value = [];
  studyPasteHtml.value = "";
  studyPasteText.value = "";
  autoStudyDialog.value = true;
  requestAnimationFrame(() => {
    if (pasteBox.value) pasteBox.value.innerHTML = "";
  });
}

function onAutoStudyFile(info) {
  const file = info?.file || info;
  if (file?.status === "removed") {
    clearAutoStudyFile();
    return;
  }
  autoStudyFile.value = uploadRaw(info);
  autoStudyFileList.value = uploadList(info, info?.fileList).slice(-1);
}

function clearAutoStudyFile() {
  autoStudyFile.value = null;
  autoStudyFileList.value = [];
}

function onPasteBoxInput(event) {
  const el = event.target;
  studyPasteHtml.value = el.innerHTML || "";
  studyPasteText.value = el.innerText || el.textContent || "";
}

/** Word 复制片段时 text/plain 常从 1 重排；HTML 的 supportLists / mso-list 仍带屏幕序号。 */
function wordHtmlToPlain(html) {
  if (!html) return "";
  let text = html;
  const markerText = (inner) => {
    const tmp = document.createElement("div");
    tmp.innerHTML = inner || "";
    let plain = (tmp.textContent || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
    if (!plain) return "";
    const match = plain.match(/^(\d+(?:\.\d+)*\.?|[一二三四五六七八九十百]+[、.]?)\s*/);
    if (match) return `${match[1].replace(/\.$/, "")} `;
    return `${plain} `;
  };
  text = text.replace(/<!--\[if\s*!supportLists\]-->([\s\S]*?)<!--\[endif\]-->/gi, (_, inner) =>
    markerText(inner)
  );
  text = text.replace(/<span\b[^>]*mso-list\s*:\s*Ignore[^>]*>([\s\S]*?)<\/span>/gi, (_, inner) =>
    markerText(inner)
  );
  text = text
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|h[1-6]|li|tr)\s*>/gi, "\n")
    .replace(/<\/ol\s*>/gi, "\n")
    .replace(/<\/ul\s*>/gi, "\n");
  const box = document.createElement("div");
  box.innerHTML = text;
  return (box.innerText || box.textContent || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

function looksLikeWordHtml(html) {
  return /mso-|Mso|supportLists|xmlns:w=/i.test(html || "");
}

function firstLineSequence(text) {
  const line = (text || "").split("\n").map((s) => s.trim()).find(Boolean) || "";
  const match = line.match(/^(\d+(?:\.\d+)*)/);
  return match ? match[1] : "";
}

function recoverPasteText(event) {
  const plain = (event.clipboardData?.getData("text/plain") || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const html = event.clipboardData?.getData("text/html") || "";
  if (looksLikeWordHtml(html)) {
    const fromWord = wordHtmlToPlain(html);
    if (fromWord.trim()) {
      const plainSeq = firstLineSequence(plain);
      const wordSeq = firstLineSequence(fromWord);
      // plain 被 Word 重排成 1，而 HTML 仍是 2/2.3… → 用 HTML
      if (!plain.trim()) return fromWord;
      if (wordSeq && plainSeq === "1" && wordSeq !== "1") return fromWord;
      if (wordSeq && wordSeq.includes(".") && !plainSeq.includes(".")) return fromWord;
      if (wordSeq && Number(wordSeq.split(".")[0]) > Number((plainSeq || "0").split(".")[0])) {
        return fromWord;
      }
      // 同级也优先 Word HTML（更接近屏幕所见）
      if (wordSeq) return fromWord;
    }
  }
  if (plain.trim()) return plain.trim();
  if (html.trim()) return wordHtmlToPlain(html);
  return "";
}

function insertPlainIntoPasteBox(cleaned) {
  if (!pasteBox.value) return;
  const selection = window.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  if (range && pasteBox.value.contains(range.commonAncestorContainer)) {
    range.deleteContents();
    const node = document.createTextNode(cleaned);
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
  } else {
    pasteBox.value.innerText = cleaned;
  }
  studyPasteHtml.value = "";
  studyPasteText.value = pasteBox.value.innerText || cleaned;
}

function onRichPaste(event) {
  const recovered = recoverPasteText(event);
  if (!recovered.trim()) return;
  event.preventDefault();
  insertPlainIntoPasteBox(recovered);
}

async function submitAutoStudy() {
  resetStudyProgress();
  studyProgress.value = {
    ...studyProgress.value,
    active: true,
    phase: "提取目录",
    current: studyMode.value === "sample" ? "正在按样本匹配章节…" : "正在分析文档目录…",
    index: 0,
    total: 0,
    items: [],
  };
  // 已标为目录的节点无需解析，从勾选里剔除
  const outlineChapters = currentTask.value?.outline?.chapters || [];
  const studyKeys = checkedKeys.value.filter((key) => {
    const node = outlineChapters.find((item) => item.key === key);
    return !node?.structure_only;
  });
  if (!studyKeys.length) {
    ElMessage.warning("勾选的章节都是目录节点，无需解析");
    studyProgress.value = { ...studyProgress.value, active: false };
    return;
  }
  if (studyKeys.length < checkedKeys.value.length) {
    ElMessage.info(`已跳过 ${checkedKeys.value.length - studyKeys.length} 个目录节点`);
  }
  if (studyMode.value === "file") {
    await extractOutlineFromDocument(studyKeys, { file: autoStudyFile.value });
  } else if (studyMode.value === "sample") {
    await extractOutlineFromDocument(studyKeys, { useSample: true });
  } else {
    const text = (studyPasteText.value || "").trim() || studyPasteHtml.value;
    await extractOutlineFromDocument(studyKeys, { text });
  }
  autoStudyDialog.value = false;
  const tool = currentTask.value?.pending_outline_tool;
  const keys = (currentTask.value?.study_chapter_keys || studyKeys).filter((key) => {
    const node = (currentTask.value?.outline?.chapters || []).find((item) => item.key === key);
    return !node?.structure_only;
  });
  // 无新章：跳过目录确认，直接分析要点（样本同样按序号定位正文）
  if (tool && !tool.outline_mismatch && keys.length) {
    if (studyMode.value === "sample") {
      await studyPointsFromDocument(keys, { useSample: true });
    } else if (studyMode.value === "file") {
      await studyPointsFromDocument(keys, { file: autoStudyFile.value });
    } else {
      await studyPointsFromDocument(keys);
    }
  }
}

function openOutlineEdit() {
  outlinePromptDialog.value = false;
  outlineEditDialog.value = true;
  pendingOutlineDrafts.value.forEach((item) => {
    item.accepted = true;
  });
}

function outlineDraftPayload() {
  return pendingOutlineDrafts.value.map((item) => ({
    chapter_key: item.chapter_key,
    sequence: item.sequence,
    title: item.title,
    parent_key: item.parent_key || "",
    parent_sequence: item.parent_sequence || "",
    level: item.level || 1,
    accepted: item.accepted !== false,
  }));
}

async function confirmOutlineThenStudy() {
  const drafts = outlineDraftPayload();
  const accepted = drafts.filter((item) => item.accepted !== false);
  outlineEditDialog.value = false;
  outlinePromptDialog.value = false;
  resetStudyProgress();
  studyProgress.value = {
    active: true,
    phase: "分析要点",
    current: "目录已确认，准备逐章分析…",
    index: 0,
    total: accepted.length + (checkedKeys.value?.length || 1),
    items: accepted.map((item) => ({
      key: item.chapter_key,
      title: `${item.sequence || ""} ${item.title || ""}`.trim(),
      sequence: item.sequence || "",
      status: "pending",
      note: "",
    })),
  };
  await confirmOutlineExtract(drafts, { applyOutline: true, continueStudy: true });
  pendingOutlineDrafts.value = [];
}

async function skipOutlineAndStudy() {
  outlinePromptDialog.value = false;
  outlineEditDialog.value = false;
  resetStudyProgress();
  studyProgress.value = {
    active: true,
    phase: "分析要点",
    current: "准备分析勾选章节…",
    index: 0,
    total: checkedKeys.value?.length || 0,
    items: [],
  };
  await confirmOutlineExtract(
    outlineDraftPayload().map((item) => ({ ...item, accepted: false })),
    { applyOutline: false, continueStudy: true }
  );
  pendingOutlineDrafts.value = [];
}

function studyDraftPayload(item) {
  const structureOnly = Boolean(item.structure_only);
  return {
    chapter_key: item.chapter_key,
    sequence: item.sequence,
    title: item.title,
    points: structureOnly
      ? []
      : String(item.pointsText || "")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
    keywords: structureOnly ? [] : [...(item.keywords || [])],
    describes: item.describes || "",
    content_blocks: structureOnly ? [] : [...(item.content_blocks || [])],
    structure_only: structureOnly,
    matched: item.matched,
    sample_excerpt: item.sample_excerpt || "",
    is_new: false,
    parent_key: item.parent_key || "",
    level: item.level || 1,
    accepted: true,
  };
}

function syncPendingFromTask(task) {
  const pending = task?.pending_study || [];
  pendingStudyDrafts.value = pending.map((item) => ({
    ...item,
    pointsText: (item.points || []).join("\n"),
  }));
  if (!pending.length) studyConfirmDialog.value = false;
}

function confirmOneStudy(index) {
  const item = pendingStudyDrafts.value[index];
  if (!item) return Promise.resolve();
  return confirmAutoStudy([studyDraftPayload(item)]).then((updated) => {
    syncPendingFromTask(updated || currentTask.value);
  });
}

function applyAutoStudy() {
  const drafts = pendingStudyDrafts.value.map((item) => studyDraftPayload(item));
  return confirmAutoStudy(drafts).then((updated) => {
    syncPendingFromTask(updated || currentTask.value);
    studyConfirmDialog.value = false;
  });
}

function deferStudyConfirm() {
  // 草案已在服务端 pending_study，稍后可再打开确认
  studyConfirmDialog.value = false;
  ElMessage.info(`已保留 ${pendingStudyDrafts.value.length} 章待确认，可稍后统一确认`);
}

function reopenPendingStudy() {
  const pending = currentTask.value?.pending_study || [];
  if (!pending.length) {
    ElMessage.warning("没有待确认的分析结果");
    return;
  }
  pendingStudyDrafts.value = pending.map((item) => ({
    ...item,
    pointsText: (item.points || []).join("\n"),
  }));
  studyToolMessage.value = currentTask.value?.pending_study_tool?.message || "";
  studyConfirmDialog.value = true;
}

function buildChapterTree(chapters) {
  const nodes = chapters.map((item) => ({
    ...item,
    children: [],
  }));
  const byKey = new Map(nodes.map((item) => [item.key, item]));
  const roots = [];
  for (const item of nodes) {
    if (item.parent && byKey.has(item.parent)) {
      byKey.get(item.parent).children.push(item);
    } else {
      roots.push(item);
    }
  }
  return roots;
}

function elementKindLabel(kind) {
  return (
    {
      fixed: "固定",
      file: "文件",
      rag_search: "文件",
      llm_generate: "LLM",
      node_ref: "引用",
    }[kind] || kind
  );
}

</script>

<style scoped>
.blueprint-list {
  box-sizing: border-box;
}
.list-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
  padding-bottom: 8px;
}
.bp-card {
  padding: 16px 18px;
  border: 1px solid var(--ay-line);
  border-radius: var(--ay-radius-lg);
  background: var(--ay-card);
  box-shadow: 0 2px 10px rgba(31, 35, 41, 0.03);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}
.bp-card.disabled {
  opacity: 0.72;
  background: #f8fafc;
}
.bp-card:hover {
  border-color: #c9d4f5;
  box-shadow: var(--ay-shadow-hover);
  transform: translateY(-1px);
}
.bp-top {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}
.bp-top strong {
  color: var(--ay-ink);
  font-size: 15px;
}
.bp-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}
.bp-pill {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f3f7;
  color: var(--ay-muted);
  font-size: 11px;
  font-weight: 650;
}
.bp-pill.ok {
  background: #e8f8ef;
  color: #1a7f4b;
}
.bp-pill.on {
  background: #eef2ff;
  color: #4e6ef2;
}
.bp-pill.off {
  background: #f1f5f9;
  color: #64748b;
}
.bp-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--ay-line);
  cursor: default;
}
.bp-desc {
  margin: 8px 0 0;
  color: var(--ay-muted);
  font-size: 13px;
  line-height: 1.5;
  min-height: 40px;
}
.bp-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.bp-tag {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--ay-blue-soft);
  color: var(--ay-blue);
  font-size: 11px;
  font-weight: 650;
}
.bp-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  color: var(--ay-muted);
  font-size: 12px;
}
.blueprint {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--ay-bg-grad);
}
.rail {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-height: 0;
  border-right: 1px solid var(--ay-line);
  background: #fbfcfe;
  overflow: auto;
}
.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 14px 14px 8px;
}
.back {
  width: 36px;
  height: 36px;
  border: 1px solid var(--ay-line);
  border-radius: var(--ay-radius-sm);
  background: #fff;
  color: var(--ay-ink);
  font-size: 16px;
  cursor: pointer;
}
.back:hover {
  border-color: #c9d4f5;
  color: var(--ay-blue);
  background: var(--ay-blue-soft);
}
.mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--ay-blue);
  color: #fff;
  font-weight: 700;
}
.brand strong {
  display: block;
  font-size: 15px;
  color: var(--ay-ink);
}
.brand p,
.hint {
  margin: 2px 0 0;
  color: var(--ay-muted);
  font-size: 12px;
  line-height: 1.45;
}
.hint.tiny {
  margin: 4px 0 8px;
}
.rail-tabs {
  padding: 0 10px 16px;
}
.rail-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid var(--ay-line);
  border-radius: var(--ay-radius-md);
  background: var(--ay-card);
  box-shadow: 0 1px 4px rgba(31, 35, 41, 0.02);
}
.block-title {
  font-size: 13px;
  font-weight: 650;
  color: var(--ay-ink);
}
.full {
  width: 100%;
}
.meta-tags,
.head-actions,
.empty-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.outline-mode {
  margin-bottom: 14px;
}
.outline-upload {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.outline-upload :deep(.ant-upload-drag) {
  padding: 28px 16px;
}
.sample-upload :deep(.ant-upload-drag) {
  padding: 18px 12px;
}
.sample-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--ay-line, #e5e7eb);
  border-radius: 10px;
  background: #fafbfc;
}
.sample-card.inline {
  margin-bottom: 4px;
}
.sample-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ay-ink, #1f2937);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sample-meta {
  font-size: 12px;
  color: var(--ay-muted, #6b7280);
}
.sample-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.check {
  margin: 0;
}
.file-list,
.element-group {
  margin: 0;
  padding: 0;
  list-style: none;
}
.file-list li,
.node-row {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  margin-top: 6px;
  padding: 8px 10px;
  border: 1px solid var(--ay-line);
  border-radius: var(--ay-radius-sm);
  background: #fff;
  text-align: left;
  cursor: pointer;
}
.node-row.active {
  border-color: var(--ay-blue);
  background: var(--ay-blue-soft);
}
.node-row.element {
  border-style: dashed;
  border-color: #f0c674;
  border-radius: 999px;
  background: #fffbeb;
}
.node-row.element:hover,
.node-row.element:focus {
  border-color: #d97706;
  background: #fef3c7;
}
.file-name,
.name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.seq {
  min-width: 36px;
  color: var(--ay-muted);
  font-size: 12px;
}
.node-kind {
  display: flex;
  width: 100%;
  margin-bottom: 12px;
}
.node-kind :deep(.ant-radio-button-wrapper) {
  flex: 1;
}
.node-kind :deep(.ant-radio-button-wrapper__inner) {
  width: 100%;
}
.chapter-toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
  padding: 10px;
  border: 1px solid #e8ebf0;
  border-radius: 12px;
  background: #f8fafc;
}
.chapter-toolbar-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.chapter-toolbar :deep(.toolbar-half) {
  width: 100%;
  margin: 0;
}
.chapter-toolbar :deep(.toolbar-primary) {
  width: 100%;
  margin: 0;
}
.pending-chip {
  appearance: none;
  border: 1px solid #fcd34d;
  background: #fffbeb;
  color: #b45309;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  align-self: flex-start;
}
.pending-chip:hover {
  background: #fef3c7;
}
.toolbar-hint {
  margin: 0;
  line-height: 1.45;
}
.chapter-tree {
  margin-top: 2px;
  background: transparent;
  max-height: min(58vh, 640px);
  overflow: auto;
  padding: 4px 2px;
  border: 1px solid #eef1f5;
  border-radius: 10px;
}
.study-progress-phase {
  margin: 0 0 6px;
  font-size: 13px;
  color: #64748b;
}
.study-progress-current {
  margin: 0;
  font-size: 15px;
  color: #0f172a;
  line-height: 1.45;
}
.study-progress-count {
  margin-left: 6px;
  color: #64748b;
  font-size: 13px;
}
.study-progress-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 280px;
  overflow: auto;
  border: 1px solid #e8ebf0;
  border-radius: 10px;
}
.study-progress-list li {
  display: grid;
  grid-template-columns: 10px 72px 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 13px;
}
.study-progress-list li:last-child {
  border-bottom: 0;
}
.study-progress-list .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cbd5e1;
}
.study-progress-list li.running .dot {
  background: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
}
.study-progress-list li.done .dot {
  background: #22c55e;
}
.study-progress-list .seq {
  color: #64748b;
  font-variant-numeric: tabular-nums;
}
.study-progress-list .name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.study-progress-list .st {
  color: #94a3b8;
  font-size: 12px;
}
.study-progress-list li.running .st {
  color: #2563eb;
}
.study-progress-list li.done .st {
  color: #16a34a;
}
.study-draft {
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e8ebf0;
}
.study-draft-head {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.study-describes {
  margin-bottom: 8px;
}
.study-draft-title {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}
.study-draft-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.study-title-input {
  margin-bottom: 8px;
}
.tool-card {
  margin-bottom: 14px;
  padding: 14px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: linear-gradient(180deg, #f8fafc, #fff);
}
.tool-card.compact {
  padding: 12px 14px;
}
.tool-card-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.tool-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  background: #0f172a;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.tool-card-head code {
  color: #334155;
  font-size: 12px;
}
.tool-msg {
  margin: 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.55;
}
.diff-preview {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  max-height: 200px;
  overflow: auto;
}
.diff-preview li {
  display: flex;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff7ed;
  margin-top: 6px;
  font-size: 13px;
}
.diff-preview .seq {
  min-width: 52px;
  color: #c2410c;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.outline-edit-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.study-hero {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  margin-bottom: 18px;
  padding: 14px 16px;
  border-radius: var(--ay-radius-lg);
  background: var(--ay-hero);
  border: 1px solid rgba(78, 110, 242, 0.12);
}
.study-hero-mark {
  flex: 0 0 auto;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(145deg, #4e6ef2, #6b85f5);
  color: #fff;
  font-weight: 700;
  line-height: 42px;
  text-align: center;
}
.study-hero strong {
  display: block;
  font-size: 15px;
}
.study-hero p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}
.study-modes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 14px;
}
.mode-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.mode-card:hover {
  border-color: #c9d4f5;
}
.mode-card.active {
  border-color: var(--ay-blue);
  background: var(--ay-blue-soft);
  box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.12);
}
.mode-ico {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  margin-bottom: 4px;
  border-radius: 8px;
  background: #e2e8f0;
  color: #334155;
  font-size: 13px;
  font-weight: 700;
}
.mode-card.active .mode-ico {
  background: var(--ay-blue);
  color: #fff;
}
.mode-title {
  font-size: 14px;
  font-weight: 650;
  color: #0f172a;
}
.mode-desc {
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
}
.study-pane {
  min-height: 220px;
}
.rich-paste {
  min-height: 220px;
  max-height: 360px;
  overflow: auto;
  padding: 14px 16px;
  border: 1px dashed #cbd5e1;
  border-radius: 14px;
  background: #fff;
  line-height: 1.6;
  font-size: 13px;
  outline: none;
}
.rich-paste:empty::before {
  content: attr(data-placeholder);
  color: #94a3b8;
}
.rich-paste:focus {
  border-color: var(--ay-blue);
  box-shadow: 0 0 0 3px rgba(78, 110, 242, 0.12);
}
.study-upload :deep(.ant-upload-drag) {
  padding: 36px 20px;
  border-radius: 14px;
  border-color: #cbd5e1;
  background: #f8fafc;
}
.study-upload :deep(.ant-upload-drag:hover) {
  border-color: var(--ay-blue);
}
.upload-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.upload-ico {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--ay-blue-soft);
  color: var(--ay-blue);
  font-size: 22px;
  line-height: 44px;
  margin-bottom: 4px;
}
.upload-title {
  font-size: 14px;
  font-weight: 650;
  color: #0f172a;
}
.upload-desc {
  font-size: 12px;
  color: #64748b;
}
.tree-row {
  display: flex;
  gap: 6px;
  align-items: center;
  width: 100%;
  padding-right: 4px;
}
.tree-row .name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 12px 14px 14px;
  overflow: hidden;
  gap: 12px;
}
.main-hero {
  margin: 0;
}
.chat-handoff-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 10px;
  padding: 10px 14px;
  border-radius: 12px;
  background: #eef2ff;
  border: 1px solid #c5d0f5;
  color: var(--ay-ink);
  font-size: 13px;
}
.list-hero-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.main :deep(.outline-shell) {
  flex: 1 1 auto;
  min-height: 0;
}
.empty-stage {
  flex: 1;
  min-height: 0;
  display: grid;
  place-items: center;
  overflow: auto;
}
.empty-card {
  width: min(520px, 100%);
  border-radius: var(--ay-radius-lg);
  border: 1px solid var(--ay-line);
  background: var(--ay-card);
}
.draft.export {
  max-height: 480px;
  overflow: auto;
  white-space: pre-wrap;
}
@media (max-width: 960px) {
  .blueprint {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(200px, 38%) minmax(0, 1fr);
  }
  .rail {
    border-right: 0;
    border-bottom: 1px solid var(--ay-line);
  }
}
</style>
