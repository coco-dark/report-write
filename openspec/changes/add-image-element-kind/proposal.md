# 图片要素（image）

## Why

报告写作需要在正文中插入插图：既支持上传固定图片，也支持按描述文生图；写作阶段用占位符，预览与 Word 导出再解析为真实图片。

## What

- 新要素类型 `image`，子模式 `image_source`: `upload` | `llm`
- 文生图：配置时按 `prompt` 调用 DashScope 文生图 API，图片落盘到任务目录
- 写作：要素解析结果为占位符 `{{img:<element_id>}}`，模型在 HTML 中原样输出
- 预览 / Word：按路径或 API 替换为 `<img>` 并写入 docx

## Scope

- 本变更不含蓝本拓扑大图编辑，仅要素库与章节关联要素
- 文生图依赖 `bailian.deepseek.api-key`（与对话共用 DashScope Key）；未配置时生成接口返回明确错误
