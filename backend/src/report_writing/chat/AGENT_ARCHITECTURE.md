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
