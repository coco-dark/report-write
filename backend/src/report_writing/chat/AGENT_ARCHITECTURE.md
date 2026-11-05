# 对话 Agent 架构（对齐 LangChain 1.x）

官方模式见 [LangChain Agents / create_agent](https://docs.langchain.com/oss/python/langchain/agents)：

- **一个** `create_agent(model, tools, system_prompt=…)` 图：模型 ↔ `tool_calls` ↔ `ToolMessage` 循环，直到不再调用工具。
- **工具语义**在 `@tool` / `Tool.description`（docstring），不在 system prompt 里堆工具表。
- **system_prompt**只写角色、边界、安全策略；动态上下文放在 **user 消息**（本项目为 `build_agent_user_text` 的 JSON）。

## 本项目分层（不应再有两个「大脑」）

| 层 | 模块 | 是否 LangChain Agent | 说明 |
|----|------|----------------------|------|
| **口语闭环** | `agents/react.run_react` → `run_langchain_chat_control` | **是** | 理解用户说什么 + 选 Tool + 读 observation，全在此循环 |
| **确定性入口** | UI `action`、`_try_ui_action_shortcut` | 否 | 点按钮 = 直接调同名 Tool，不经过分类器 |
| **澄清状态机** | `intent_clarify` + pending 卡 | 否 | 结构化选项/续跑；可用小模型解析「用户点了哪一项」，**不是**提前替 Agent 选 Tool |
| **调度执行** | `intent_dispatch.execute_dispatch_tool` | 否 | 澄清卡 / 计划 JSON **已产出** tool+args 后的执行层，供 UI·单测复用 |
| **只读观察** | `session_gaps` | 否 | 写入 user JSON 的 `session_gaps`，供 Agent 读；不强制改 Tool 选择 |
| **可选遗留** | `intent_turn_plan` / `try_run_oral_multi_intent` | 否 | `features.intent_plan=true` 时口语 JSON 计划短路；**默认 false**，与官方单 Agent 一致 |
| **子域** | `subagents/runner.run_domain_subagent` | **是** | 总控 Tool 委派；内层同样 `create_agent` |
| **章流水线** | `agents/supervisor` | **是** | `call_study/search/write/check` 专员 Tool |

## 为何历史上「意图」被单独拿出来？

早期为了：

1. 用便宜模型先输出 `tools[]` JSON，减少 ReAct 步数；
2. 在 prompt 里写满工具路由表（与 LangChain 推荐相反）；
3. 用正则短切口语（已逐步删除，见 `INTENT_REGEX_AUDIT.md`）。

这与 **「一个 Agent 闭环完成理解+行动」** 冲突。当前默认已改为 **仅 ReAct**；`chat/intent_*` 目录名保留兼容，职责应理解为 **澄清 + 调度 + 可选遗留计划**，而不是第二个总控。

## `intent_invoke` / `intent_llm` 还应存在吗？

**可以，但角色要收窄：**

- **不应**：在 `run_chat_control` 主路径上再跑一遍「整轮意图分类 → 替 Agent 选 Tool」（除非 `intent_plan=true`）。
- **可以**：澄清卡回复解析、向导 command 分类、scope/doc_name 等 **窄任务 JSON**（输入输出固定，不是开放域路由）。
- Tool 内部若需补槽（如要素名解析），应优先读 **Tool 返回的 observation**，其次才调窄分类器。

## 代码入口（口语默认路径）

```
handle_chat → run_chat_control
  → pending clarify / UI shortcut / 寒暄 / onboarding dispatch（确定性）
  → [可选] try_run_oral_multi_intent（intent_plan）
  → run_langchain_chat_control（create_agent + build_chat_tools）
```

## 配置

| 键 | 默认 | 含义 |
|----|------|------|
| `features.intent_plan` | `false` | 是否启用口语 JSON 计划（双 LLM） |
| `features.intent_tool_catalog_in_prompt` | `false` | 计划分类器 prompt 是否贴工具表（不推荐） |

恢复旧行为：`REPORT_WRITING_INTENT_PLAN=1` 或 local yml 设 `intent_plan: true`。

---

## 深化路线图（不影响现功能的分阶段）

目标：**口语只有一条 LangChain Agent 闭环**；`intent_*` 收敛为「澄清 + 调度 + 窄分类」；Tool 语义 100% 单源；可观测、可回滚。

### Phase A — 收口与可观测（低风险，优先）

| 项 | 做什么 | 验收 |
|----|--------|------|
| A1 | `tool_registry` 补全剩余总控 Tool（`materials_pack_*` 若在 dispatch 无 @tool 则文档化「仅 UI/dispatch」） | `test_tool_registry` 抽检每个 `build_chat_tools` 名称有 doc 或显式豁免列表 |
| A2 | 机器人 `generate_prompts` + 存量 `chat_prompt`  lint：拒绝含 Tool 枚举 / `match_blueprints` 清单 | 单测或脚本扫描 |
| A3 | 指标：`agent_chain_log` 区分 `turn_plan` vs `create_agent` 步数；配置项变更打 `session` event | 对话一轮可在日志看到仅 `chat_control` agent |
| A4 | 文档：`INTENT_REGEX_AUDIT.md` 与路线图交叉链接；运维 runbook（何时开 `intent_plan`） | 新人只读 AGENT_ARCHITECTURE 能改对话 |

**回滚**：仅配置，无删代码。

### Phase B — 窄分类器归位（中风险，行为等价）

原则：**不再新增「整句口语 → tool id」分类器**；现有 `classify_*_llm` 只允许固定 schema。

| 项 | 做什么 | 验收 |
|----|--------|------|
| B1 | 新建 `chat/clarify_llm.py`（或 `narrow_classifiers.py`），从 `intent_llm.py` **搬迁**仅 clarify/wizard/scope/doc_name 等函数；`intent_llm.py` 变薄或 re-export | import 路径兼容 1 个版本 |
| B2 | Tool 内调用分类器：统一经 `tool_capability` + observation 优先；列出 `chat_control.py` 内仍调 `intent_invoke` 的点，逐个改为「缺参 → missing_slots」 | `test_chat_observe_loop` + 要素/改名用例 |
| B3 | `dispatch_suggested_onboarding_tools`：评估是否改为 Agent 读 `session_gaps.suggested_tools`（保留 deterministic 开关） | onboarding 用例不回归 |
| B4 | `session_goal_intent` / `entry_intent`：有 `invoke` 时禁止静默走 lexical；与 Agent 路径一致 | `test_flexible_onboarding` |

**回滚**：B3/B4 用 feature flag `chat.onboarding_deterministic_dispatch`（默认 true 直到验完）。

### Phase C — 废弃 intent_plan 路径（中高风险，需对照期）

| 项 | 做什么 | 验收 |
|----|--------|------|
| C1 | 内网/Staging 默认 `intent_plan=false` 已满 1 个发布周期且无回归 → **删除** `try_run_oral_multi_intent` 在 `run_chat_control` 的调用块（非删模块） | 默认配置下代码路径无 plan |
| C2 | `classify_turn_plan_llm` / `_invoke_control_plan_json` 标记 `@deprecated`；仅 `intent_clarify` 的 `intent_plan` kind 保留 | `test_intent_multi_plan` 迁到 `@pytest.mark.legacy_plan` 或删 |
| C3 | 删除 `intent_tool_catalog_in_prompt` 与 legacy 超长 plan system（减维护面） | grep 无 `唯一大脑` 类 prompt |
| C4 | 配置项移除 `intent_plan`（或硬编码 false + 日志 warn） | migration note in CHANGELOG |

**回滚**：C1–C4 独立 PR，保留 git tag；紧急时 revert 单 PR。

### Phase D — LangChain 1.x 对齐增强（可选）

| 项 | 做什么 | 验收 |
|----|--------|------|
| D1 | 评估 `create_agent` **middleware**（动态 system、截断 dialogue）替代手写 `build_system_prompt` 拼接 | 同 token 下行为一致 |
| D2 | Supervisor / 子 Agent 共用 `run_react` 配置（recursion_limit、name、logging） | 一处常量 |
| D3 | 结构化输出：仅 clarify 窄任务用 `response_format`，总控仍 tool-calling | 无双通道抢路由 |
| D4 | 单测默认 `ScriptedChatModel` 走 Tool 闭环，减少对 `classify_*` mock 的依赖 | CI 更快、更贴 Agent |

### Phase E — 目录与命名（最后做，避免大爆炸）

| 项 | 做什么 |
|----|--------|
| E1 | `intent_clarify` → `clarify_flow`；`intent_dispatch` → `tool_dispatch`（兼容 shim） |
| E2 | `intent_invoke` → `narrow_llm_invoke` |
| E3 | 删除空壳 `freeform_intent` / `writing_control_route` 若已无引用 |

---

## 建议 PR 切分（与 Phase 对应）

1. **PR-2**（A1–A2）：registry 全覆盖 + prompt lint  
2. **PR-3**（B1–B2）：窄分类器搬迁 + Tool 内 observation 优先  
3. **PR-4**（B3–B4）：onboarding dispatch 可切换  
4. **PR-5**（C1–C2）：去掉主路径 plan 调用 + legacy 测试隔离  
5. **PR-6**（C3–C4）：删 legacy prompt / 配置  
6. **PR-7+**（D/E）：按需

每 PR：**全量 chat 相关 pytest + 抽 5 条人工对话脚本**（开场 / 改名 / 开写 / 要素 / 资料包）。

## 不做的事（避免破坏现功能）

- 不一次性删掉 `intent_clarify` 状态机（卡片 UX 依赖）。  
- 不把 UI `action` 强行改成 Agent 解析（确定性更快、更稳）。  
- 不在未验完前默认打开 `intent_plan`。  
- 不把章级 supervisor 合并进对话总控（职责边界清晰）。

