# Agent D 0.3.6 发布说明

本版本为 WF-01 输入确认和询问弹窗增加硬性用户轮次边界，避免智能体返回 UI JSON 后在同一轮继续思考、输出或执行后续阶段。

## 主要变更

- `primer_design_input` 和 `agent_interaction` 新增统一的 `flowControl`：
  `action=WAIT_FOR_USER`、`stopCurrentTurn=true`，并声明恢复所需的用户消息类型。
- MCP 系统指令和工具描述明确规定：等待 JSON 是当前助手轮次的终点；必须原样返回 UI JSON，且不得继续调用工具或自行构造用户确认。
- MCP `preflight_wf01_batch` 与 `create_wf01_batch` 现在要求同时提供原始 `design_input_form` 和真实的 role=user `user_field_updates`，不再暴露跳过首次确认的直接入口。
- 所有 M2-M6 阶段统一检查未解决交互。只要存在 `WAITING_USER`、`USER_SUBMITTED` 或 `RUNNING` 交互，后端就以 `next_action=WAIT_FOR_USER` 拒绝继续。
- 所有含等待弹窗的 WF-01 API/MCP 结果统一返回 `next_action=WAIT_FOR_USER`；用户提交回复并完成确定性路由后，才返回 `RUN_M2` 或 `RUN_M6`。

## 前后端时序

1. 智能体返回输入表单或询问弹窗 JSON，并结束当前轮次。
2. 前端渲染 JSON，等待用户填写或选择。
3. 用户点击确认后，前端发送新的 role=user 更新/回复消息。
4. 智能体在新轮次提交回复，根据后端返回的 `next_action` 恢复工作流。

内部 REST/service 仍保留 canonical payload 适配能力，供非对话式兼容调用使用；MCP 对话入口不能借此绕过用户确认。
