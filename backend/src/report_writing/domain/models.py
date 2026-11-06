"""书写任务的实体。接口、落盘和阶段之间都使用这些对象。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class ChapterStatus(str, Enum):
    PENDING = "pending"
    SEARCHING = "searching"
    WRITING = "writing"
    CHECKING = "checking"
    AWAITING_CONFIRM = "awaiting_confirm"
    CONFIRMED = "confirmed"
    NEEDS_HUMAN = "needs_human"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    STUDYING = "studying"
    RUNNING = "running"
    AWAITING_CONFIRM = "awaiting_confirm"
    COMPLETED = "completed"


class CheckAction(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"
    NEEDS_HUMAN = "needs_human"


class HumanDecision(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    RETURN = "return"
    SKIP = "skip"


ADOPT_SCORE = 5
MAX_ADOPTED = 8
MAX_SEARCH_TERMS = 6  # 每章最多用几个检索词
SEARCH_TOP_K = 4  # 每个检索词 RAG 返回条数
MAX_EVIDENCE_CARDS = 20  # 每章最终保留的证据卡上限（含未采纳）
MAX_REVISIONS = 2
MAX_AGENT_STEPS = 8


class Entity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        return cls.model_validate(data)


class ExtractItem(Entity):
    """一章需要从项目资料里取出的一项。"""

    name: str
    kind: str = "文本"
    source: str = ""
    hint: str = ""
    required: bool = True
    style: str = "归纳"


class ContentBlock(Entity):
    """章节内容块：文字、表格结构或图片占位（图片暂不生成）。"""

    kind: str = "text"  # text | table | image
    note: str = ""
    rows: int = 0
    cols: int = 0
    headers: list[str] = Field(default_factory=list)
    merges: list[dict[str, Any]] = Field(default_factory=list)
    cells_sample: list[list[str]] = Field(default_factory=list)
    # 表格列分析：header / role(fixed|variable|mixed) / note
    column_roles: list[dict[str, Any]] = Field(default_factory=list)
    # 相对固定的表头/栏目名/单位行等（跨项目可复用）
    fixed_items: list[str] = Field(default_factory=list)
    # 随项目变化的单元格/行项（写作时需从资料填充）
    variable_items: list[str] = Field(default_factory=list)

    @field_validator("cells_sample", mode="before")
    @classmethod
    def _coerce_cells_sample(cls, value: Any) -> list[list[str]]:
        # 模型常返回一维字符串列表；入库/校验前统一成二维
        from report_writing.domain.content_blocks import normalize_cells_sample

        return normalize_cells_sample(value)


class ChapterRequirement(Entity):
    key: str
    title: str
    sequence: str = ""
    level: int = 1
    parent: str = ""
    describes: str = ""
    generate: str = ""
    keywords: list[str] = Field(default_factory=list)
    extracts: list[ExtractItem] = Field(default_factory=list)
    points: list[str] = Field(default_factory=list)
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    reference_text: str = ""  # 参考正文：学写法/格式；fixed_output 时作为必须照抄的固定正文
    allowed_fact_types: list[str] = Field(default_factory=lambda: ["名称", "数值", "日期", "单位"])
    boundary: str = ""
    depends_on: list[str] = Field(default_factory=list)
    aux_prompt: str = ""
    material_files: list[str] = Field(default_factory=list)
    knowledge_folder_ids: list[str] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)
    element_results: list[dict[str, Any]] = Field(default_factory=list)
    structure_only: bool = False  # 纯目录节点：不写正文、不检索
    fixed_output: bool = False  # 固定内容：跳过检索，按 reference_text 原文输出
    search_terms_separate: bool = False  # True=每个检索词单独查；默认 False=合并一次查

    def search_terms(self) -> list[str]:
        """章节级检索词已废弃；资料由关联要素解析（见 element_results）。"""
        if self.structure_only or self.fixed_output:
            return []
        hints = [item.hint.strip() or item.name.strip() for item in self.extracts]
        hints = [item for item in hints if item]
        if hints:
            return _unique_terms(hints)[:MAX_SEARCH_TERMS]
        return []


def _unique_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in terms:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


class ChapterElement(Entity):
    """兼容旧版：章节内嵌要素。新路径请用独立 ElementNode + element_ids。"""

    id: str = ""
    name: str = ""
    kind: str = "fixed"  # fixed | kv | file（旧 rag_search）| file_slot | llm_generate | node_ref
    value: str = ""
    query: str = ""
    prompt: str = ""
    ref_key: str = ""
    ref_field: str = "points"  # points | describes | extracts | generate
    required: bool = True


class KeyValueEntry(Entity):
    """键值对一行（要素库 / 任务要素可多条）。"""

    key: str = ""
    value: str = ""


class FileSlotDef(Entity):
    """蓝本/要素上预设的文件插槽：编号 + 名称 + 检索词。"""

    no: int = 1
    name: str = ""
    query: str = ""


class ElementAttachedFile(Entity):
    """要素「文件」类型上挂载的附件；上传后需手动解析才能读取。"""

    file_id: str = ""
    file_name: str = ""
    source_path: str = ""
    parse_status: str = "pending"  # pending | parsed | error
    parse_error: str = ""
    file_size: int = 0
    created_at: str = ""
    parsed_at: str = ""
    rag_task_id: str = ""  # RAG 向量任务 id，检索时限定 taskIdIn
    rag_biz_id: str = ""  # RAG bizId，与 task 成对限定检索范围


class MaterialsSlotFill(Entity):
    """资料包内某一槽位的文件填充。"""

    slot_no: int = 1
    file_id: str = ""
    file_name: str = ""
    source_path: str = ""  # 相对用户工作区或绝对路径
    # pending | parsed | error；资料包负责上传/解析，检索词在要素上
    parse_status: str = ""
    parse_error: str = ""
    parsed_at: str = ""
    rag_task_id: str = ""
    rag_biz_id: str = ""


class MaterialsPack(Entity):
    """资料包：项目文件按槽位填充；可被多文档引用；入库后出现在资料库。"""

    id: str = ""
    name: str = ""
    user_id: str = ""
    in_library: bool = False  # false=待入库收集；true=资料库可选
    # 槽位定义（编号/名称）；检索词不在资料包上，由要素引用时配置
    slot_schema: list[FileSlotDef] = Field(default_factory=list)
    # 展示顺序：slot_no 列表；空则按编号升序。编号本身仍是稳定身份（文件目录 / 要素引用）
    slot_order: list[int] = Field(default_factory=list)
    # 曾与之同步过的蓝本 id，便于蓝本改槽位时回写资料库中的包
    blueprint_ids: list[str] = Field(default_factory=list)
    slots_fill: list[MaterialsSlotFill] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class ElementNode(Entity):
    """独立要素节点：可单独新建，再通过连线挂到章节。"""

    id: str = ""
    name: str = ""
    # fixed | kv | file（旧 rag_search）| llm_generate | node_ref | file_slot | image
    kind: str = "file"
    value: str = ""
    kv_pairs: list[KeyValueEntry] = Field(default_factory=list)
    query: str = ""
    prompt: str = ""
    ref_key: str = ""
    ref_kind: str = "chapter"  # chapter | element
    ref_field: str = "points"  # points | describes | generate | result
    material_files: list[str] = Field(default_factory=list)
    # 兼容旧数据；知识库已下线，新流程勿再写入
    knowledge_folder_ids: list[str] = Field(default_factory=list)
    knowledge_scope: str = "both"
    # 文件插槽：蓝本预设编号槽位 + 检索词；写作时从绑定资料包取文件
    file_slots: list[FileSlotDef] = Field(default_factory=list)
    # 文件要素：多附件；需手动解析后才能读取
    files: list[ElementAttachedFile] = Field(default_factory=list)
    result: str = ""
    required: bool = True
    library_item_id: str = ""  # 从个人要素库克隆时记录来源 lib-el-*
    # 库收录元数据：未收入要素库时也保留在任务要素上，避免「暂不收录」后分类/标签/作用丢失
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    purpose: str = ""
    # 图片要素：upload=上传固定图；llm=文生图（prompt）
    image_source: str = "upload"
    image_path: str = ""


class ElementLibraryItem(Entity):
    """个人要素库条目（跨任务复用）；拓扑上通过 link_ids 互相关联。"""

    id: str = ""
    name: str = ""
    kind: str = "kv"
    value: str = ""
    kv_pairs: list[KeyValueEntry] = Field(default_factory=list)
    query: str = ""
    prompt: str = ""
    ref_key: str = ""
    ref_kind: str = "element"
    ref_field: str = "result"
    material_files: list[str] = Field(default_factory=list)
    knowledge_folder_ids: list[str] = Field(default_factory=list)
    knowledge_scope: str = "both"
    file_slots: list[FileSlotDef] = Field(default_factory=list)
    files: list[ElementAttachedFile] = Field(default_factory=list)
    required: bool = True
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    purpose: str = ""  # 作用说明
    image_source: str = "upload"
    image_path: str = ""
    link_ids: list[str] = Field(default_factory=list)  # 关联的其它库内要素 id
    created_at: str = ""
    updated_at: str = ""


class OutlineNode(Entity):
    """目录树的一个节点。确认后作为学习与写作的章节骨架。"""

    key: str = ""
    sequence: str = ""
    title: str
    level: int = 1
    parent: str = ""
    parent_sequence: str = ""
    points: list[str] = Field(default_factory=list)
    point_items: list[dict] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    describes: str = ""
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    reference_text: str = ""  # 参考正文；fixed_output 时为固定正文
    aux_prompt: str = ""
    material_files: list[str] = Field(default_factory=list)
    knowledge_folder_ids: list[str] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)
    elements: list[ChapterElement] = Field(default_factory=list)
    structure_only: bool = False  # 仅目录标题，不需要写作要点/检索词
    fixed_output: bool = False  # 固定内容输出：写作时照抄 reference_text
    search_terms_separate: bool = False  # True=检索词分开查；默认合并一次查


class AutoStudyDraft(Entity):
    """依据文档学习后待人工确认的章节要点草案；可含资料中新发现的子章节。"""

    chapter_key: str = ""
    sequence: str = ""
    title: str = ""
    points: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    describes: str = ""
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    matched: bool = False
    sample_excerpt: str = ""
    is_new: bool = False
    parent_key: str = ""
    level: int = 1
    accepted: bool = True
    structure_only: bool = False
    project_slots: list[dict[str, Any]] = Field(default_factory=list)


class OutlineDiffItem(Entity):
    """Agent tool：学习时发现的目录差异项。"""

    action: str = "add"  # add
    sequence: str = ""
    title: str = ""
    parent_sequence: str = ""
    chapter_key: str = ""


class OutlineExtractDraft(Entity):
    """extract_outline_from_document 待确认的新目录章节。"""

    chapter_key: str = ""
    sequence: str = ""
    title: str = ""
    parent_key: str = ""
    parent_sequence: str = ""
    level: int = 1
    accepted: bool = True


class ExtractOutlineToolResult(Entity):
    """extract_outline_from_document tool 的结构化结果。"""

    tool: str = "extract_outline_from_document"
    needs_confirmation: bool = True
    outline_mismatch: bool = False
    message: str = ""
    new_chapter_count: int = 0
    outline_diff: list[OutlineDiffItem] = Field(default_factory=list)
    selected_chapter_keys: list[str] = Field(default_factory=list)
    drafts: list[OutlineExtractDraft] = Field(default_factory=list)


class StudyDocumentToolResult(Entity):
    """study_from_document tool 的结构化结果（仅写作要点，不含目录 diff）。"""

    tool: str = "study_from_document"
    needs_confirmation: bool = True
    outline_mismatch: bool = False
    message: str = ""
    new_chapter_count: int = 0
    outline_diff: list[OutlineDiffItem] = Field(default_factory=list)
    drafts: list[AutoStudyDraft] = Field(default_factory=list)


class SpecMetaRequest(Entity):
    """写作规格的分类与标签（供后续 Agent 选用）。"""

    title: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""


class ProjectFoldersRequest(Entity):
    """写作任务绑定的项目资料根（知识库文件夹）。"""

    project_folder_ids: list[str] = Field(default_factory=list)
    project_folder_label: str = ""


class CreateBlueprintRequest(Entity):
    title: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    biz_type: str = "002"


class BlueprintSummary(Entity):
    id: str
    title: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    chapter_count: int = 0
    element_count: int = 0
    outline_confirmed: bool = False
    enabled: bool = True
    updated_at: str = ""


class BlueprintEnabledRequest(Entity):
    enabled: bool = True


class AutoStudyConfirmRequest(Entity):
    drafts: list[AutoStudyDraft] = Field(default_factory=list)


class AutoStudyTextRequest(Entity):
    chapter_keys: list[str] = Field(default_factory=list)
    text: str = ""
    use_sample: bool = False  # 使用蓝本已上传并 RAG 解析的样本
    use_llm: bool = False  # 是否用模型整理目录（默认关，避免覆盖目录页规则结果）
    re_extract: bool = False  # 用户主动整本重提：先解开 outline_confirmed 再 generate


class ConfirmOutlineExtractRequest(Entity):
    drafts: list[OutlineExtractDraft] = Field(default_factory=list)
    apply_outline: bool = True
    continue_study: bool = True


class OutlineTree(Entity):
    chapters: list[OutlineNode] = Field(default_factory=list)


class ParsePointsRequest(Entity):
    """粘贴原文，让模型单独解析本章写作要点。"""

    text: str = ""
    title: str = ""
    sequence: str = ""


class MetaFromPointsRequest(Entity):
    """按当前写作要点重写章节说明与补充提示词。"""

    chapter_key: str = ""
    title: str = ""
    sequence: str = ""
    point_items: list[dict] = Field(default_factory=list)


class ElementDraftFromPointRequest(Entity):
    """要点待补项 → 要素配置草稿。"""

    chapter_key: str = ""
    title: str = ""
    sequence: str = ""
    describes: str = ""
    point_text: str = ""
    slot: str = ""
    point_id: str = ""


class WritingBrief(Entity):
    template_id: str
    template_version: str
    analyzed_by: str = ""
    voice: str = ""
    layout: str = ""
    paragraph_format: str = ""
    chapters: list[ChapterRequirement] = Field(default_factory=list)

    def chapter(self, key: str) -> ChapterRequirement:
        for item in self.chapters:
            if item.key == key:
                return item
        raise KeyError(key)


class EvidenceCard(Entity):
    id: str
    point: str = ""
    source_file: str = ""
    location: str = ""
    excerpt: str = ""
    query: str = ""
    relevance: int = 0
    specificity: int = 0
    source_score: int = 0
    consistency: int = 0
    adopted: bool = False
    reject_reason: str | None = None

    @computed_field
    @property
    def total(self) -> int:
        return self.relevance + self.specificity + self.source_score + self.consistency


class Paragraph(Entity):
    index: int
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class ChapterDraft(Entity):
    title: str = ""
    paragraphs: list[Paragraph] = Field(default_factory=list)
    uncovered_points: list[str] = Field(default_factory=list)


class ParagraphComment(Entity):
    index: int
    message: str = ""


class CheckResult(Entity):
    action: CheckAction
    comments: list[ParagraphComment] = Field(default_factory=list)
    reason: str = ""


class ChapterState(Entity):
    key: str
    title: str = ""
    status: ChapterStatus = ChapterStatus.PENDING
    evidence: list[EvidenceCard] = Field(default_factory=list)
    draft: ChapterDraft | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    revision_count: int = 0
    official_text: str = ""
    human_note: str = ""

    def adopted_evidence(self) -> list[EvidenceCard]:
        return [card for card in self.evidence if card.adopted]


class StepEvent(Entity):
    """流水线一步的执行状态和结果。"""

    step: str
    status: str
    chapter: str = ""
    title: str = ""
    result: dict[str, Any] = Field(default_factory=dict)


class WritingTask(Entity):
    id: str
    template_id: str = ""
    template_version: str = ""
    dataset_name: str = ""
    template_path: str = ""
    source_path: str = ""
    # 蓝本绑定的学习/大纲样本（上传后 RAG 解析，缓存旁路 .rag-parse.md）
    sample_path: str = ""
    sample_name: str = ""
    sample_chars: int = 0
    sample_ready: bool = False
    dataset_dir: str = ""
    biz_type: str = ""
    title: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    # 对话开写时从蓝本 clone 出来的写作任务，指向源蓝本 id
    source_blueprint_id: str = ""
    # 本写作任务的项目资料根（知识库文件夹 id）；兼容旧路径，主路径改用资料包
    project_folder_ids: list[str] = Field(default_factory=list)
    project_folder_label: str = ""  # 展示用，如「项目A」
    # 资料包按写作文档绑定（非蓝本级）；空=未绑/不用，检索时跳过资料包槽位
    materials_pack_id: str = ""
    enabled: bool = True  # 蓝本启用/停用；停用后不参与对话匹配
    status: TaskStatus = TaskStatus.STUDYING
    outline: OutlineTree | None = None
    outline_confirmed: bool = False
    brief: WritingBrief | None = None
    brief_from_cache: bool = False
    brief_confirmed: bool = True
    study_runs: int = 0
    chapters: list[ChapterState] = Field(default_factory=list)
    steps: list[StepEvent] = Field(default_factory=list)
    element_nodes: list[ElementNode] = Field(default_factory=list)
    pending_outline: list[OutlineExtractDraft] = Field(default_factory=list)
    pending_outline_tool: ExtractOutlineToolResult | None = None
    pending_study: list[AutoStudyDraft] = Field(default_factory=list)
    pending_study_tool: StudyDocumentToolResult | None = None
    study_chapter_keys: list[str] = Field(default_factory=list)
    # 本轮开写仅处理这些章 key；完整 outline 仍用于文档窗展示
    write_chapter_scope: list[str] = Field(default_factory=list)
    # 开写前/写后待补充项登记（与对话 registry 同步）
    writing_supplement_gaps: list[dict[str, Any]] = Field(default_factory=list)
    user_id: str = ""

    def chapter(self, key: str) -> ChapterState:
        for item in self.chapters:
            if item.key == key:
                return item
        raise KeyError(key)

    def refresh_status(self) -> None:
        if self.outline is not None and not self.outline_confirmed:
            self.status = TaskStatus.STUDYING
            return
        if self.brief is not None and not self.brief_confirmed:
            self.status = TaskStatus.STUDYING
            return
        statuses = [item.status for item in self.chapters]
        if statuses and all(item in {ChapterStatus.CONFIRMED, ChapterStatus.SKIPPED} for item in statuses):
            self.status = TaskStatus.COMPLETED
        elif any(item == ChapterStatus.AWAITING_CONFIRM for item in statuses):
            self.status = TaskStatus.AWAITING_CONFIRM
        elif self.brief is not None:
            self.status = TaskStatus.RUNNING


class StudiedTemplate(Entity):
    """已经学过、可以再次选用的模版。"""

    sample_id: str
    sample_version: str
    title: str
    biz_type: str
    chapter_count: int = 0


class StudyRecord(Entity):
    """从已写样板学到的书写要求，按样板编号和版本落盘。"""

    sample_id: str
    sample_version: str
    brief: WritingBrief


class SampleChapter(Entity):
    key: str
    title: str
    level: int = 1
    paragraphs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class SampleDocument(Entity):
    """之前写过的报告样板。"""

    id: str
    version: str
    title: str = ""
    chapters: list[SampleChapter] = Field(default_factory=list)

    def chapter(self, key: str) -> SampleChapter:
        for item in self.chapters:
            if item.key == key:
                return item
        raise KeyError(key)


class ConfirmStudyRequest(Entity):
    voice: str = ""
    layout: str = ""
    paragraph_format: str = ""
    chapters: list[ChapterRequirement] = Field(default_factory=list)


class SaveOutlineRequest(Entity):
    chapters: list[OutlineNode] = Field(default_factory=list)
    # 增删改结构时为 True；默认 False：已确认大纲若被短列表子集覆盖则按 key 合并，避免分析/写作预览裁掉整棵树
    replace_tree: bool = False


class SaveElementsRequest(Entity):
    elements: list[ElementNode] = Field(default_factory=list)


class RunChaptersRequest(Entity):
    chapter_key: str = ""
    include_children: bool = False
    chapter_keys: list[str] = Field(default_factory=list)
    robot_id: str = ""


class Robot(Entity):
    """书写机器人：提示词 + 蓝本/渲染/要素库/资料库绑定。"""

    id: str = ""
    name: str = ""
    description: str = ""
    chat_prompt: str = ""
    study_prompt: str = ""
    write_prompt: str = ""
    blueprint_ids: list[str] = Field(default_factory=list)
    render_template_ids: list[str] = Field(default_factory=list)
    word_style_ids: list[str] = Field(default_factory=list)
    knowledge_folder_ids: list[str] = Field(default_factory=list)
    element_library_ids: list[str] = Field(default_factory=list)
    materials_pack_ids: list[str] = Field(default_factory=list)
    user_id: str = ""
    created_at: str = ""
    updated_at: str = ""


class RobotSummary(Entity):
    id: str
    name: str = ""
    description: str = ""
    blueprint_count: int = 0
    render_template_count: int = 0
    word_style_count: int = 0
    knowledge_folder_count: int = 0
    element_library_count: int = 0
    materials_pack_count: int = 0
    updated_at: str = ""


class RenderTemplateSummary(Entity):
    id: str
    name: str = ""
    desc: str = ""
    format: str = ""
    updated: str = ""
    tone: str = ""
    margin: str = ""
    font: str = ""
    heading: str = ""
    header: str = ""


class WritingPace(str, Enum):
    BATCH = "batch"
    STEP = "step"


class SessionGoal(Entity, Mapping[str, Any]):
    """会话级写作目标（类型 / 标题 / 项目 / 备注）。"""

    summary: str = ""
    doc_category: str = ""
    doc_title: str = ""
    project: str = ""
    notes: str = ""
    updated_at: str = ""

    def get(self, key: str, default: Any = None) -> Any:
        if key in type(self).model_fields:
            value = getattr(self, key)
            return default if value in (None, "") and default is not None else value
        return default

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key in data:
            return data[key]
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class ChatActionPayload(Entity, Mapping[str, Any]):
    """对话 action / Tool 入参。点选、口语 Tool、调度层共用，避免散落的 dict[str]。"""

    model_config = ConfigDict(extra="allow")

    q: str = ""
    message: str = ""
    text: str = ""
    notes: str = ""
    user_message: str = ""
    task_id: str = ""
    blueprint_id: str = ""
    card_key: str = ""
    chapter_key: str = ""
    chapter_key_hint: str = ""
    focus_chapter_key: str = ""
    chapter_keys: list[str] = Field(default_factory=list)
    write_chapter_keys: list[str] = Field(default_factory=list)
    chapter_sequences: list[str] = Field(default_factory=list)
    writing_pace: str = ""
    decision: str = ""
    comment: str = ""
    pause_after: bool = False
    skip_empty: bool = True
    confirm: bool = False
    regenerate: bool = False
    reopen_edit: bool = False
    outline_text: str = ""
    revision_instructions: str = ""
    title: str = ""
    category: str = ""
    gap_id: str = ""
    hint: str = ""
    user_note: str = ""
    slot: str = ""
    slot_label: str = ""
    name: str = ""
    already_persisted: bool = False
    from_doc_window: bool = False
    skip_supplement_check: bool = False
    aux_prompt: str = ""
    describes: str = ""
    reference_text: str = ""
    points_text: str = ""
    keywords_text: str = ""
    pack_id: str = ""
    option_id: str = ""
    option_label: str = ""
    explicit_create: bool = False
    force_new: bool = False
    dismiss: bool = False
    project_folder_label: str = ""
    project_folder_ids: list[str] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    writing_task_id: str = ""
    sequence: str = ""
    chapter_title: str = ""
    kind: str = ""
    element_id: str = ""
    element_name: str = ""
    points: Any = None
    keywords: Any = None
    point_items: list[Any] = Field(default_factory=list)
    elements: list[Any] = Field(default_factory=list)
    chapters: list[Any] = Field(default_factory=list)
    point_index: Any = None
    fixed_output: bool = False
    search_terms_separate: bool = False
    structure_only: bool = False
    preserve_chapter_meta: bool = False

    @classmethod
    def parse(cls, raw: Any = None) -> ChatActionPayload:
        if isinstance(raw, cls):
            return raw
        if raw is None or raw == "":
            return cls()
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return cls()
        if not isinstance(raw, dict):
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            else:
                return cls()
        data = dict(raw)
        if data.get("chapter_keys_json") and not data.get("chapter_keys"):
            data["chapter_keys"] = data.get("chapter_keys_json")
        for key in ("chapter_keys", "write_chapter_keys", "chapter_sequences", "element_ids", "tags", "project_folder_ids"):
            if key in data:
                data[key] = _as_str_list(data.get(key))
        if data.get("_skip_supplement_check") and not data.get("skip_supplement_check"):
            data["skip_supplement_check"] = bool(data.get("_skip_supplement_check"))
        return cls.model_validate(data)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)

    def with_updates(self, **kwargs: Any) -> ChatActionPayload:
        data = self.as_dict()
        data.update(kwargs)
        return ChatActionPayload.parse(data)

    def __getitem__(self, key: str) -> Any:
        extra = self.model_extra or {}
        if key in extra:
            return extra[key]
        if key in self.model_fields_set:
            return getattr(self, key)
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())

    def get(self, key: str, default: Any = None) -> Any:
        extra = self.model_extra or {}
        if key in extra:
            return extra[key]
        if key in self.model_fields_set:
            return getattr(self, key)
        return default

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key in self.model_fields_set or key in (self.model_extra or {})

    def pop(self, key: str, default: Any = None) -> Any:
        extra = self.model_extra
        if extra is not None and key in extra:
            return extra.pop(key, default)
        if key in self.model_fields_set:
            value = getattr(self, key)
            field = type(self).model_fields[key]
            if field.default_factory is not None:
                setattr(self, key, field.default_factory())
            else:
                setattr(self, key, field.default)
            self.model_fields_set.discard(key)
            return value
        return default


def _as_str_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = [text]
        else:
            value = [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def action_payload_dict(payload: Any = None) -> dict[str, Any]:
    """把 Tool/点选入参收成普通 dict（仅已填字段），给仍走 dict 的旧调用。"""
    return ChatActionPayload.parse(payload).as_dict()


class ChatPhase(str, Enum):
    INTENT = "intent"
    BLUEPRINT_SELECT = "blueprint_select"
    CREATE_HANDOFF = "create_handoff"
    FREEFORM_ASK = "freeform_ask"
    FREEFORM_OUTLINE = "freeform_outline"
    FREEFORM_POINTS = "freeform_points"
    READY_TO_WRITE = "ready_to_write"  # 已选蓝本/大纲，待点击开始写作
    WRITING = "writing"
    CONFIRM_EXPORT = "confirm_export"


class ChatCard(Entity):
    """对话内可交互卡片（蓝本选择、跳转、进度、章节确认、导出）。"""

    card_id: str = ""
    used: bool = False
    type: str = ""
    title: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(Entity):
    role: str = "assistant"
    text: str = ""
    cards: list[ChatCard] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    # 同会话多文档：本条所属焦点文档（bp:… / task:…）；闲聊可空
    document_key: str = ""
    # 与 document_key 同值，显式给前端 / LLM 当「文档 id」用
    document_id: str = ""


class ChatUiStateRequest(Entity):
    """同步对话 UI：已点卡片、过程日志、蓝本向导进度等。"""

    used_card_keys: list[str] = Field(default_factory=list)
    message_patches: list[dict[str, Any]] = Field(default_factory=list)
    freeform_patch: dict[str, Any] = Field(default_factory=dict)
    chapter_edits: dict[str, Any] = Field(default_factory=dict)
    # 文档小窗切换焦点（同会话多文档）
    focus_document_key: str = ""
    focus_task_id: str = ""
    focus_blueprint_id: str = ""


class ChatSessionRuntime(Entity):
    """会话内可恢复交互状态（由服务端 reconcile 与任务真值对齐）。"""

    version: int = 1
    workflows: dict[str, Any] = Field(default_factory=dict)
    blueprint_wizard: dict[str, Any] = Field(default_factory=dict)


class ChatSession(Entity):
    id: str = ""
    phase: str = ChatPhase.INTENT.value
    robot_id: str = ""
    user_id: str = ""
    blueprint_id: str = ""
    task_id: str = ""
    messages: list[ChatMessage] = Field(default_factory=list)
    pending_actions: list[dict[str, Any]] = Field(default_factory=list)
    freeform: dict[str, Any] = Field(default_factory=dict)
    runtime: ChatSessionRuntime | dict[str, Any] | None = None
    # Redis/MySQL 乐观版本；每次 save 递增，刷库 CAS 防旧覆盖新
    revision: int = 0
    created_at: str = ""
    updated_at: str = ""


class ChatSessionSummary(Entity):
    """会话列表项（不含完整 messages）。"""

    id: str = ""
    title: str = ""
    preview: str = ""
    robot_id: str = ""
    phase: str = ""
    message_count: int = 0
    updated_at: str = ""
    created_at: str = ""


class ChatRequest(Entity):
    message: str = ""
    robot_id: str = ""
    session_id: str = ""
    action: str = ""  # select_blueprint | skip_blueprint | resume | confirm_chapter | start_writing | ...
    action_payload: ChatActionPayload = Field(default_factory=ChatActionPayload)
    client_sync: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(Entity):
    reply: str = ""
    robot_id: str = ""
    blueprint_ids: list[str] = Field(default_factory=list)
    render_template_ids: list[str] = Field(default_factory=list)
    word_style_ids: list[str] = Field(default_factory=list)
    session_id: str = ""
    phase: str = ""
    cards: list[ChatCard] = Field(default_factory=list)
    task_id: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)
    runtime: dict[str, Any] = Field(default_factory=dict)
    blueprint_wizard: dict[str, Any] = Field(default_factory=dict)


class GenerateRobotPromptsRequest(Entity):
    """根据用户描述生成机器人提示词。"""

    text: str = ""
    kind: str = "all"  # all | chat | study | write
    name: str = ""
    description: str = ""


class GenerateRobotPromptsResponse(Entity):
    chat_prompt: str = ""
    study_prompt: str = ""
    write_prompt: str = ""


class OutlineTextRequest(Entity):
    text: str = ""
    use_llm: bool = False
    biz_type: str = ""


class CreateTaskRequest(Entity):
    template_path: str
    dataset_dir: str
    task_id: str
    biz_type: str
    force_study: bool = False


class UseStudyRequest(Entity):
    task_id: str
    biz_type: str
    sample_id: str
    sample_version: str


class UpdatePointsRequest(Entity):
    points: list[str]
    save_cache: bool = False


class ConfirmChapterRequest(Entity):
    decision: HumanDecision
    text: str = ""
    comment: str = ""


class ConfirmedExport(Entity):
    template_id: str
    template_version: str
    chapters: dict[str, str]


class RenderRequest(Entity):
    """任务渲染：有 template_id 走占位填充；否则走 LLM（或 plain）。"""

    template_id: str = ""
    mode: str = "auto"  # auto | template | llm | plain
    confirmed_only: bool = True


class RenderTemplateUpsert(Entity):
    id: str = ""
    name: str = ""
    desc: str = ""
    format: str = "DOCX"
    tone: str = ""
    margin: str = ""
    font: str = ""
    heading: str = ""
    header: str = ""
    html: str = ""


class DatabaseSettings(Entity):
    """与 Java `application-local.yml` 里 `spring.datasource.druid.master` 相同的业务库。"""

    host: str
    port: int = 3306
    database: str
    user: str
    password: str = ""
    ssl: bool = False


class EmbeddingModel(Entity):
    """书写服务自己的嵌入模型，不使用 t_ai_llm_model。"""

    code: str
    provider: str
    model_name: str
    base_url: str
    dimensions: int
    active: bool = True


class BailianSettings(Entity):
    """与 Java `bailian.deepseek` 相同的百炼兼容接口配置。"""

    api_key: str
    base_url: str
    completions_path: str = "/chat/completions"
    model_name: str
    timeout: int = 60
    temperature: float = 0
    max_tokens: int = 4096
    thinking_level: str = "NONE"
    chat_template_kwargs_enabled: bool = False

    def request_extras(self) -> dict[str, Any]:
        thinking = self.thinking_level.upper() in {"MEDIUM", "HIGH"}
        if self.chat_template_kwargs_enabled:
            kwargs: dict[str, Any] = {"thinking": thinking}
            if self.thinking_level.upper() == "MEDIUM":
                kwargs["reasoning_effort"] = "medium"
            elif self.thinking_level.upper() == "HIGH":
                kwargs["reasoning_effort"] = "high"
            return {"chat_template_kwargs": kwargs}
        return {"enable_thinking": thinking}
