# Agent D 0.3.5 发布说明

本版本以《引物智能体 WF-01 PRD v0.2》为基线，修复 HiMat 中 WF-01 用户交互后无法继续运行的问题。

## 主要变更

- 新增由 Agent D 确定性生成的 `primer_design_input`、`agent_interaction` 和
  `primer_design_output` UI 消息，流程不再依赖大模型能否输出严格 JSON。
- REST 和 MCP 均支持从 canonical payload 或 `designInput` 直接归一化输入。
- 新增交互回复提交接口与 MCP 工具，支持单题扁平结构和多题数组结构。
- 用户回复会持久化为 `USER_SUBMITTED`/`RESOLVED`，并返回明确的 `next_action`。
- M1 确认可将批次推进到 `QUEUED`；M5 接受当前结果可进入 M6；调整约束可基于
  显式 `target_changes` 创建不可变的 M7 新版本。
- 存在待处理交互时禁止进入 M6。
- M6 完成消息以实际生成的 HTML 为主附件，并仅公布真实存在的 HTML、XLSX、PDF 文件。
- 部署脚本现在遵循 `AGENTD_REQUIRE_DATABASES`：严格生产模式会阻止缺库上线，流程联调
  模式允许启动但必须保留 `NOT_EVALUATED`。

## HiMat 接入要求

HiMat 必须直接消费 REST/MCP 结构化结果中的 `ui_message` 和 `next_action`，不能从大模型
自然语言回复中重建流程状态。大模型输出非 JSON、Markdown 或额外文字时，工作流仍应继续。

## 兼容性

- PRD 基线保持 WF-01 v0.2。
- canonical report schema 保持 `3.0.0-phase3`。
- 原 0.3.4 任务记录不原地修改；M7 重设计继续创建新版本。
