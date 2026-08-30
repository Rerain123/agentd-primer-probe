# 智能体工具审计日志

Agent D 持久化记录可观察的执行事实，用于排查前端、编排器和科学工作流之间的数据传递问题。
日志不是模型内部思维链，也不会尝试提取或保存隐藏推理。

## 可记录内容

- 调用了哪个 MCP 工具，以及开始、完成或失败时间；
- 传入的是原始 `payload`、`design_input_form` 还是 `user_field_updates`；
- `targetOrganism`、`host`、`coverageRequirement` 的字段来源和最终有效值；
- `batch_id`、阶段、状态、`next_action`、UI 消息类型；
- `mcp_input_mapping`、预检问题数量、数据库就绪状态；
- 稳定错误码、错误信息、阻塞项和恢复动作。

完整 FASTA/序列不写入日志，只记录长度、SHA-256 和 FASTA 记录数量。JWT、Authorization、
密码、密钥和 token 会被替换为 `[REDACTED]`。普通长文本也会截断并附带哈希。
每条 `TOOL_COMPLETED` 事件同时保存 `output_source` 和脱敏后的 `output`：终态或交互工具
优先记录实际交付给客户端的 UI 业务对象，普通工具记录经过响应大小控制后的返回结果。
因此完成报告可直接在日志的 `output` 中核对严格的 `{data,type}`，无需从状态摘要反推。

## 查询方式

审计查询不暴露给主 MCP 智能体，使用后台 REST：

```text
GET /v1/audit/agent?operation=wf01_preflight&limit=100
GET /v1/audit/agent?batch_id=<batch_id>&limit=100
GET /v1/audit/agent?trace_id=<trace_id>&limit=100
```

日志保存于 `AGENTD_STATE_DIR/audit/agent-tools.jsonl`，跟随 Agent D 任务持久化卷保存。

## 本次“只读到开始设计”问题的判定方法

1. 查询 `operation=wf01_prepare_design_input`，确认首次表单工具确实执行。
2. 查询 `operation=wf01_preflight`：
   - 没有记录：确认后的智能体没有调用预检工具，通常是前端只发送了按钮文字；
   - `TOOL_FAILED`：查看缺少的是原表单、用户更新还是 `input_artifact_id`；
   - `TOOL_COMPLETED`：检查 `mcp_input_mapping.field_sources` 和
     `effective_design_input`，即可确认字段在合并前后是否正确。
3. 数据库 `NOT_EVALUATED` 或 readiness 缺库属于后续科学数据库问题，不会导致 M1 丢失
   `host` 或 `coverageRequirement`，两者应分开排查。

## 数据读取边界

Agent D 当前可以读取：

- 自身持久化的 WF-01 批次 JSON（`get_wf01_batch`）；
- 自身任务结果（`get_job`）；
- `AGENTD_DATABASE_CATALOG` 配置的版本化科学数据库和 FASTA 资源。

Agent D 当前不能直接读取 HiMat 的业务数据库、聊天消息表或前端表单状态。因此 HiMat 在用户
点击确认时，必须把完整的 `primer_design_input_update` 作为新的 role=user 消息传给智能体；
“数据库已经保存字段”本身不会让智能体自动获得这些值。
