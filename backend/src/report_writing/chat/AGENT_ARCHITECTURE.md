# 对话 Agent 架构（对齐 LangChain 1.x）

官方模式见 [LangChain Agents / create_agent](https://docs.langchain.com/oss/python/langchain/agents)：

- **一个** `create_agent(model, tools, system_prompt=…)` 图：模型 ↔ `tool_calls` ↔ `ToolMessage` 循环，直到不再调用工具。
- **工具语义**在 `@tool(..., description=)` 和参数 `Field(description=)`，不在 system prompt 里堆工具表。
- **system_prompt**只写角色、边界、安全策略；动态上下文放在 **user 消息**（`build_agent_user_text` 的 JSON）。

## 没有「意图层」

产品对话里 **没有** 单独的「先猜用户意图再选 Tool」模块。

历史上 `chat/intent_*` 做过第二套大脑：便宜模型先吐 `tools[]` JSON，再串行执行（`intent_plan`）。这和官方单 Agent 冲突，**口语主路径已删除该短路**。

对外契约仍用历史名（卡片 `intent_clarify`、Tool `ask_intent`、配置 `intent.max_tokens`），模块已按职责改名：

| 模块 | 实际职责 | 口语要不要它 |
|------|----------|----------------|
| **`clarify_flow`** | **选择卡**：选项、pending、点选后续跑（卡片 type 仍为 `intent_clarify`） | 要（UX） |
| **`tool_dispatch`** | **按 tool 名执行**（卡片/按钮/单测已经决定了 tool+args） | 要（执行器） |
| `session_state` / `entry_intent` | 会话标志：写作路径、是否跳过蓝本；入口辅助 | 要（状态） |
| `clarify_invoke` | `invoke_clarify_llm` / `clarify_invoke_fn`；配置键仍叫 `intent.max_tokens` | 要（省成本） |
| `clarify_llm` | 窄 JSON：澄清卡回复、写/分析范围槽、向导 command、要素 payload。**不**做整句选 Tool / 入口 / 定名 / 无蓝本动作 | 要（解卡补槽） |
| `classifier_context` | 分类器共用对话/焦点上下文 | 要 |
| `chapter_resolve` | 从原话/大纲解析章节 key | 要 |
| `classifier_action_chain` | 分类器 JSON 多动作排序 | 要 |
| `ask_intent`（Tool 名） | 挂一张选择卡，不是分类整句口语 | 要 |

整轮 JSON 口语计划模块与 `features.intent_plan` 配置已移除。

## 本项目分层

| 层 | 模块 | 是否 LangChain Agent | 说明 |
|----|------|----------------------|------|
| **口语闭环** | `agents/react.run_react` → `run_langchain_chat_control` | **是** | `create_agent` + `chat_tools.build_chat_tools`（对位 StudioTools） |
| **卡片/按钮** | `action` 写入 Agent user JSON | **是** | 点选当一轮对话；Agent 调同名 Tool；未调到才兜底 |
| **选择卡 UX** | `clarify_flow` pending + `resolve_intent_clarify` Tool | 否（执行）/是（决策） | 挂卡仍是 UX；点选由总控分析 |
| **调度执行** | `tool_dispatch.execute_dispatch_tool` | 否 | tool+args 已确定后的执行 |
| **只读观察** | `session_gaps` + `suggested_next_tools` | 否 | 写入 Agent user JSON；口语不自动执行队列 |
| **子域** | `subagents/runner.invoke_subagent` | **是** | [官方 Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)：总控把子 Agent 当 Tool；`create_agent(...).invoke({"messages":[user]})`，最后结果回总控 |
| **章流水线** | `agents/supervisor` | **是** | `call_study/search/write/check` |

## 代码入口（口语默认路径）

```
handle_chat → run_chat_control
  → ensure_write_onboarding_started（只记账）
  → run_langchain_chat_control（create_agent）
  → 若点了按钮且 Agent 未调 Tool：run_ui_action_shortcut 兜底（保持现有点选结果）
```

## 配置

| 键 | 默认 | 含义 |
|----|------|------|
| `features.intent_tool_catalog_in_prompt` | 代码恒 `false` | 已废弃 |

正则 vs 口语见 `INTENT_REGEX_AUDIT.md`。

---

## 深化路线图

目标：口语只有一条 LangChain Agent 闭环；`intent_*` 只当澄清/调度/窄 JSON；Tool 语义单源。

### 已落地

- **A**：registry 审计、`chat_prompt` lint、`oral_route` 日志
- **C1/C3 部分**：主路径去掉 JSON 计划；删除「唯一大脑」超长 catalog prompt；分类器不再贴 Tool 表

### Phase B（已完成）

| 项 | 做什么 | 状态 |
|----|--------|------|
| B1 | `clarify_llm.py` 作为窄 JSON 分类器唯一实现 | 已做 |
| B2 | Tool 内缺参 → `missing_slots` observation，少调分类器选 Tool | `submit_points` / 写作控制误调改为 `suggested_next_tool`；写作 Tool 仍可补本 Tool 槽 |
| B3 | 口语主路径不再 `dispatch_suggested_onboarding_tools`；Agent 读 `suggested_next_tools` | 已做（函数仍供 force_new 同轮续跑） |
| B4 | `entry_intent` / 入职 path 有 invoke 时不静默 lexical | 已做 |
| B5 | 资料包 / 待补要素按钮绑成同名 Tool | 已做 |
| B6 | `match_blueprints` 不再代选；明确选用走 `select_blueprint` | 已做 |
| B7 | 无匹配不再在 match 内代开模版向导；总控再调 `begin_blueprint_wizard` | 已做 |
| B8 | 无大纲时 `submit_points` 不再 skip_blueprint / 代生成目录；回流 `submit_outline` | 已做 |
| B9 | 要点收集器不再用 `wants_skip/generate_points` 改道；跳过/生成走对应 Tool | 已做 |
| B10 | 目录收集器只认显式 `confirm` / `regenerate`；口语补槽在 `submit_outline` | 已做 |
| B11 | 定名收集器 / 入职观察不再用 `wants_generate_outline` 分类改道；只认生成目录短语 | 已做 |
| B12 | `skip_blueprint` 遇「再写一份」另开文档；定名已推断类型｜标题则确认；已确认目录的向导不再拽回确认步 | 已做 |
| B13 | `ask_entry_path` 只观察/挂卡（`apply_plan=False`）；链式 skip/大纲/向导留给总控读 `suggested_next_tools` | 已做 |

### 完整链路修复（P0–P6）

目标链：口语或点选 → `handle_chat` 组装 user JSON（`action` / `message` / `session_gaps` / `suggested_next_tools` / `pending_clarify`）→ `create_agent` → 控制 Tool 返回 `ok` 或 `missing_slots` → Agent 按 `suggested_next_tool(s)` 再跳或收尾。未出现 ToolMessage 且 `action` 在白名单 → `run_ui_action_shortcut`。

澄清卡仍是 UX：点选把 `action=resolve_intent_clarify` 写入 user JSON；该 Tool **只落槽 / 改 session 标志**，不得调用 skip / outline / match / wizard 执行体。

| 阶段 | 做什么 | 状态 |
|------|--------|------|
| P0 | 契约：控制 Tool 只改本 Tool 的 session；缺槽 `missing_slots`；下一跳只写 observation。禁止 Tool 内 `execute_dispatch_tool` 另一个总控 Tool | 已做（入职澄清 / 新建文档） |
| P1 | 入职链：`execute_clarify_spec` 去掉 skip+outline 实跑；`continue_after_new_document` 只 prepare；生产路径禁止 `apply_plan=True` | 已做 |
| P2 | 无蓝本链：`skip_blueprint` 不再调 `_collect_freeform_meta`；定名走 `set_session_meta`；目录 / 要点 / 开写各一跳 | 已做 |
| P3 | 蓝本 / 向导：子代理去掉 `is_create_blueprint_intent` 自动 begin；`resolve_wizard_action` 只补本 Tool 槽 | 已做 |
| P4 | 写作中：`add_elements` / associate / retry 可填本 Tool 槽，不得在本函数改调另一个总控 Tool | 已做 |
| P5 | 黄金路径从 `handle_chat` 断言 Tool 名与阶段；离线脚本只跟 `suggested_next_tools`；JSON 计划夹具不动 | 已做 |
| P6 | `wants_*` 仅测试别名；`intent_*` 文件改名放到行为稳定之后 | 已做（新入口 `clarify_flow` / `tool_dispatch`；实现文件改名见 Phase D） |

P1 必须覆盖：口语「写一份…」→ 生成目录；点选自动大纲 / 用模版 / 创建蓝本；「再写一份」隔离后由 Agent hop；shortcut 在零 ToolMessage 时仍保 UX。

P0–P6 与 Phase D/E 已落地。`wants_*` 只作测试别名；选择卡/执行器在 `clarify_flow` / `tool_dispatch`。`run_react` 经 LangChain middleware 写链路日志并限制模型调用次数。整轮 JSON 口语计划与 `intent_multi_plan` 已删除。

### Phase C

| 项 | 做什么 | 状态 |
|----|--------|------|
| C2 | ~~`classify_turn_plan_llm` / `classify_chat_control_plan_llm`~~ | **已删除** |
| C4 | 从 yml 彻底移除整轮计划相关键 | 已做 |

### Phase D / E

| 项 | 做什么 | 状态 |
|----|--------|------|
| D1 | 选择卡实现迁到 `clarify_flow.py` | 已做 |
| D2 | 执行器实现迁到 `tool_dispatch.py` | 已做 |
| E | `create_agent` middleware（链路日志 + 模型调用上限）；删除 `intent_clarify` / `intent_dispatch` shim；oral 多计划已删 | 已做 |
| F1 | `associate_elements` 未点名章节时不落到大纲第一章，挂 `intent_clarify`（source=action_slots） | 已做 |
| F2 | 生产 `clarify_flow` 不调用 oral JSON 计划；`apply_turn_plan` / `intent_multi_plan` 已移除 | 已做 |

## 不做的事

- 不删选择卡状态机（卡片 UX）。
- 不把章级 supervisor 合并进对话总控。
