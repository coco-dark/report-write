# 对话 Agent 架构（对齐 LangChain 1.x）

官方模式见 [LangChain Agents / create_agent](https://docs.langchain.com/oss/python/langchain/agents)：

- **一个** `create_agent(model, tools, system_prompt=…)` 图：模型 ↔ `tool_calls` ↔ `ToolMessage` 循环，直到不再调用工具。
- **工具语义**在 `@tool` / `Tool.description`（`tool_registry.py`），不在 system prompt 里堆工具表。
- **system_prompt**只写角色、边界、安全策略；动态上下文放在 **user 消息**（`build_agent_user_text` 的 JSON）。

## 没有「意图层」

产品对话里 **没有** 单独的「先猜用户意图再选 Tool」模块。

历史上 `chat/intent_*` 做过第二套大脑：便宜模型先吐 `tools[]` JSON，再串行执行（`intent_plan`）。这和官方单 Agent 冲突，**口语主路径已删除该短路**。

文件名仍带 `intent_` 只是历史包袱，职责如下：

| 名字看起来像「意图」 | 实际职责 | 口语要不要它 |
|----------------------|----------|----------------|
| `intent_clarify` | **选择卡**：选项、pending、点选后续跑 | 要（UX） |
| `intent_dispatch` | **按 tool 名执行**（卡片/按钮/单测已经决定了 tool+args） | 要（执行器） |
| `intent_router` | 会话标志：写作路径、是否跳过蓝本 | 要（状态） |
| `intent_invoke` / 配置 `intent.max_tokens` | 窄 JSON 小模型入口（解析点了哪一项、抽槽） | 要（省成本） |
| `clarify_llm` | 固定 schema：澄清回复、scope、文档名、向导 command | 要 |
| `intent_llm` | 上者的实现 + **遗留** turn-plan 分类器 | 新代码走 `clarify_llm` |
| `intent_turn_plan` / `intent_multi_plan` | 整轮 JSON 计划 | **不要**；仅单测 |
| `ask_intent`（Tool 名） | 挂一张选择卡，不是分类整句口语 | 要 |

配置 `features.intent_plan` / `REPORT_WRITING_INTENT_PLAN`：**口语忽略**。打开它也不会进 `run_chat_control`。

## 本项目分层

| 层 | 模块 | 是否 LangChain Agent | 说明 |
|----|------|----------------------|------|
| **口语闭环** | `agents/react.run_react` → `run_langchain_chat_control` | **是** | 理解用户说什么 + 选 Tool + 读 observation |
| **确定性入口** | UI `action`、`_try_ui_action_shortcut` | 否 | 点按钮 = 直接调同名 Tool |
| **选择卡** | `intent_clarify` + pending | 否 | 结构化选项/续跑 |
| **调度执行** | `intent_dispatch.execute_dispatch_tool` | 否 | tool+args 已确定后的执行 |
| **入职续跑** | `dispatch_suggested_onboarding_tools` | 否 | 执行 `suggested_next_tools` 队列，不是猜意图 |
| **只读观察** | `session_gaps` | 否 | 写入 user JSON 的 `session_gaps`，供 Agent 读 |
| **子域** | `subagents/runner.run_domain_subagent` | **是** | 总控 Tool 委派 |
| **章流水线** | `agents/supervisor` | **是** | `call_study/search/write/check` |

## 代码入口（口语默认路径）

```
handle_chat → run_chat_control
  → pending 选择卡 / UI shortcut / 寒暄 / onboarding 队列（确定性）
  → run_langchain_chat_control（create_agent + build_chat_tools）
```

## 配置

| 键 | 默认 | 含义 |
|----|------|------|
| `features.intent_plan` | `false` | 遗留。口语主路径 **不读** |
| `features.intent_tool_catalog_in_prompt` | 代码恒 `false` | 已废弃 |

正则 vs 口语见 `INTENT_REGEX_AUDIT.md`。

---

## 深化路线图

目标：口语只有一条 LangChain Agent 闭环；`intent_*` 只当澄清/调度/窄 JSON；Tool 语义单源。

### 已落地

- **A**：registry 审计、`chat_prompt` lint、`oral_route` 日志
- **C1/C3 部分**：主路径去掉 JSON 计划；删除「唯一大脑」超长 catalog prompt；分类器不再贴 Tool 表

### Phase B（进行中）

| 项 | 做什么 | 状态 |
|----|--------|------|
| B1 | `clarify_llm.py` 作为窄分类器入口；`intent_llm` 兼容 re-export | 已做入口 |
| B2 | Tool 内缺参 → `missing_slots` observation，少调分类器选 Tool | 改名等已做；写作 Tool 仍有补槽 LLM |
| B3 | `dispatch_suggested_onboarding_tools` 可切到 Agent 读 `session_gaps` | 未做（行为保留） |
| B4 | `session_goal_intent` / `entry_intent` 有 invoke 时不静默 lexical | 未做 |

### Phase C 剩余

| 项 | 做什么 |
|----|--------|
| C2 | `classify_turn_plan_llm` 标 deprecated；`test_intent_multi_plan` 打 `legacy_plan` |
| C4 | 配置项彻底移除（下一轮） |

### Phase D / E

middleware、目录改名（`intent_clarify` → `clarify_flow`）放到行为稳定之后。

## 不做的事

- 不删选择卡状态机（卡片 UX）。
- 不把 UI `action` 改成 Agent 解析。
- 不把章级 supervisor 合并进对话总控。
