# Agent D 0.3.7 发布说明

本版本增加可查询的脱敏智能体工具审计日志，并明确区分 HiMat 业务数据库、Agent D 自身批次存储和科学数据库。

## 主要变更

- 新增 MCP 工具 `get_agent_audit_log` 和 REST 接口 `GET /v1/audit/agent`。
- 审计日志记录工具开始/完成/失败、输入信封、字段来源、有效合并值、工作流状态、数据库就绪状态和稳定错误信息。
- `mcp_input_mapping` 新增 `input_mode`、`field_sources` 和 `effective_design_input`，可直接判断字段来自原表单还是用户确认更新。
- FASTA/序列只记录长度、SHA-256 和记录数；JWT、Authorization、密码、密钥和 token 全部脱敏。
- 明确不记录模型内部思维链；日志只保存可观察、可验证的执行事实和决策依据。
- MCP 指令明确：`开始设计`/`确认开始设计`按钮文字不是结构化表单更新，Agent D 不能据此继续，也不能直接读取 HiMat 私有业务数据库。

## 本次问题的结论

截图中的表单已经包含宿主“鸡”和覆盖要求“单一毒株”，但确认后的新用户消息只显示“确认开始设计”。如果 HiMat 没有把 `primer_design_input_update.data.designInput` 一并传回智能体，Agent D 无法从 HiMat 后端自动补读这些值。

部署后可查询 `operation=wf01_preflight`：没有记录表示智能体根本没有调用预检；`TOOL_FAILED` 表示调用但消息结构或字段不完整；`TOOL_COMPLETED` 中的 `field_sources` 和 `effective_design_input` 表示最终真实使用的数据。
