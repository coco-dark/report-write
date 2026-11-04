# 口语意图 vs 结构解析（正则）

## 统一走意图模型 / ReAct Tool（观察闭环）

| 场景 | 模块 |
|------|------|
| 定题目 / 记住目标 | ReAct → `set_session_meta` / `remember_writing_goal` → `session_goal_actions` |
| 入口：定名 + 三选一 | ReAct → `ask_entry_path`（先 `doc_name` 再路径）→ 再 `match_blueprints` / `begin_blueprint_wizard` / `skip_blueprint` |
| 蓝本向导各步 | ReAct / subagent → `apply_wizard_action`（分类可用 `resolve_wizard_action`） |
| 向导循环防护 | `wizard_loop_guard`（sync 不回退、一次性 trigger、过渡步幂等、少叠卡片） |
| 蓝本能力执行 | `blueprint_actions.run_blueprint_action`（与 UI action 同名） |
| 无蓝本：目录/要点/托管起草 | ReAct → `submit_outline` / `generate_freeform_points` / `start_writing`（分类可用 `freeform_intent`） |
| 写作中：要素/关联/查看库/记目标 | ReAct Tool；执行复用 `apply_writing_control_classification` |
| 统一调度（非口语主路径） | `execute_intent_plan` / `execute_dispatch_tool`（UI·clarify·单测；**禁止** `message_wants_*` 改写 plan） |
| 澄清回复（全部 source） | `classify_clarify_reply_llm` / `classify_doc_name_reply_llm` → `intent_clarify.try_handle_pending_message` |

**原则**：口语「用户要做什么」禁止正则短切；LLM 失败或 `none` → clarify / 总控 ReAct，不用正则猜工具。一切澄清口语（定名 / 改名 / 资料包名 / 学习范围 / 入口路径 / 写作确认等）必须结合对话上下文走模型；精确 UI 点选与「类型｜标题」等结构行除外。禁止把短句字面或短语别名当主判决。

## 仍保留正则（非「用户动作」意图）

- `freeform_outline.looks_like_outline_directory` / `normalize_pasted_outline`：粘贴文本结构
- `session_snapshot.looks_like_freeform_meta_line`：一行「类型｜标题」结构（入口可直接 skip_blueprint；定名澄清亦可）
- `session_goal._META_SPLIT_RE`：已存 summary 字段拆分，非路由
- `helpers.is_vague_freeform_meta`：防止把路径意图词误存为文档标题（字段校验）
- `intent_chapter` / `study_chapter_resolve.parse_study_sequences_from_message` / `write_chapter_intent.parse_write_sequences_from_message`：章序号从正文抽取（分类器已选工具后补全）
- `session_snapshot.assistant_asked_for_freeform_meta`：助手上一轮是否在要 meta（语境，非用户意图路由）
- `message_wants_new_document_strong` / `quick_title_from_write_message` / `explicit_doc_title_from_message`：另开文档结构提示 / 标题候选；显式定名结构仅 **invoke=None 离线兜底**（有模型时以计划 JSON `slots.doc_name` 为准）
- `progress_continue.is_progress_continue_message`：短句「继续」结构判断（非抢路由）
- Agent 写作/检索：引用、待补标记等文本处理
- `intent_clarify._OPTION_ALIASES`：澄清选项别名（离线复述/UI；定名确认语不靠此表，有模型时走 `classify_doc_name_reply_llm`）

## 已移除的意图正则短切与认领路由

- `writing_control_route.message_wants_*` / `extract_add_element_hint`
- `intent_dispatch` 对 open/associate/add 的 plan 注入；`message_means_outline_extract_done` extract_ack
- `write_chapter_intent.message_wants_write_*`；`chat_control` 开写正则旁路
- `wizard_phrase_rules` 抢先于 LLM；蓝本向导 `message_wants_*` 跳出
- `intent_clarify.message_means_study_all` / `_CLEAR_*` 意图短路 / `message_looks_like_vague_*`
- `freeform_intent` 短语抢先（有模型时）；`lexical_freeform_intent` 仅离线兜底 / 无目录误调度恢复
- clarify 有模型后用 `looks_like_new_intent` 清 pending（仅离线兜底）
- UI `resolve_intent_clarify` 文案回退强制 `invoke=None`（现传活模型）
- entry / sample / remember / no-match 分类丢 `dialogue_tail`（现统一带上下文）
- `should_prefer_react_over_freeform_dispatch` 被 `wants_*` 抢先锁 freeform（现有模型时交 dispatch 路由）
- 改名 `_RENAME_TITLE_RE` / `extract_rename_title`（改名须结构化 `title`）
- `message_wants_collect_to_library` 改道加要素
- 弱 `message_wants_new_document` 抢 force_new（仅保留 strong / 标题冲突）
- 口语认领壳：`try_*_route` / `try_progress_continue_clarify` / `try_chat_control_plan_route` / `session_goal_route` / `blueprint_session_route`（已删除）

## 口语主路径（整段会话统一大脑）

- `run_chat_control`：pending clarify / UI 之后 → `try_run_oral_multi_intent` / `apply_turn_plan`（JSON `slots`+`tools`+`missing`+`uncertain`：确定执行 / 缺项引导 / 不确定追问）→ 未命中再 `run_react`
- 开场与写作中途同一契约；禁止再用标题/原话截取工具名当执行清单
- 单测 / Tool 执行层：`execute_intent_plan` / `execute_dispatch_tool` / `apply_*` / clarify 构建器
- Chat Tool 统一 `ok` / `reply_hint` / `observation` / `missing_slots`
- 开写硬门闩：`start_writing` / `select_write_chapters` / `write_analyzed_only` 要求 `writing_doc_named`

## Session Intent Loop（Phase 0 — 只观察，不改执行）

| 环节 | 模块 | 说明 |
|------|------|------|
| Observe | `session_gaps.observe_session_gaps` | missing **镜像** `focus_needs_*`，不 union 进 `plan.missing` |
| Snapshot | `session_gaps.refresh_session_gaps` | 写入 `freeform.session_gaps_snapshot` |
| Predict | `classify_turn_plan_llm` | user 块可选一行「规则观察」只读上下文 |
| Act | 不变 | 仍 `remaining_missing` + `hang_missing_slots` / `execute_intent_plan` |
| ReAct | 不变 | 仅 `apply_turn_plan` 返回 `None` 时进入 |
| 默认 onboarding | match_first | 产品策略；观察层 `suggest_next_tools` 仍服务 onboarding 快照 |

**禁止 Phase 0**：用 gaps 强制挂卡、与 TurnPlan 同轮并行 ReAct、或 gaps.missing 覆盖 LLM missing。

**选蓝本同轮（match_first）**：已出 `blueprint_list` / 选用澄清时，勿再叠 `doc_name` / `entry_branch` 卡。**选用成功后**若同屏已有 `doc_window_hint` / `materials_pack_gate` / 向导卡，定名只保留文案提示，定名卡留到下一轮或用户直接说「名字叫…」。见 `turn_should_defer_doc_name_card`。

## 活跃焦点 + 写题材句（`写一份××`）

| 模块 | 行为 |
|------|------|
| `focus_has_active_writing_context` | 已选蓝本 / w* 副本 / 写作·导出 phase 等（宽于 `writing_doc_named`） |
| `resolve_write_subject_vs_focus` | 写题材 vs 焦点：clarify / force_new / continue / rename_ok |
| `write_subject_on_active_doc_should_clarify` | 为 true 时禁止 `confirm_writing_document_name` 静默落盘 |
| `apply_turn_plan` | scope 门闩：`is_writing_document_named \|\| focus_has_active_writing_context` |
| `resolve_document_scope` 启发式 | 活跃焦点 + 异题 + 无强新开词 → `ask`（非直办 force_new） |

**原则**：题材 ≠ 文档名；在已有写作焦点上异题新写，默认多轮 `document_scope` 澄清，除非用户显式另开/改名。

## 配置

意图分类使用 `live_intent_model()` / `config/bailian.local.yml` 的 `intent-model-name`。
