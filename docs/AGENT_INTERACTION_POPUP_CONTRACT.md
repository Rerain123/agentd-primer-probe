# 智能体用户决策弹窗契约

本契约仅保留给文件上传兼容任务、旧任务恢复和内部 REST 调用。直接粘贴/手动输入序列的主 MCP
链路使用 `NO_POPUPS`：预检不合格时返回普通工具错误；候选不足、0 候选、BLAST/数据库未就绪或
阶段异常时继续生成最终报告，不创建 `agent_interaction`。

当兼容任务已经创建，但因输入待确认或可恢复执行错误而不能继续时，Agent D
在 API/MCP 结果的 `ui_message` 中生成 `agent_interaction`。MCP 边界将该对象放入
`structuredContent`，同时在 `content[0].text` 中放入内容完全相同的紧凑裸 JSON。宿主直接使用
`structuredContent` 生成 `block_data` 并打开弹窗；对于仍把 MCP 文本结果交给模型的兼容链路，
模型必须逐字输出 `content[0].text`，不得改写。自然语言问题或文字版选择不是弹窗数据源。

任何输入模式下，候选少于 3 个都不属于交互阻塞。系统继续生成 M6 报告，并在结果、报告与输出 `issues`
中标记“候选不足”；0 个候选同样正常生成空结果报告，并记录 Primer3、硬规则或特异性
阶段能够确定的无候选原因。

`agent_interaction.flowControl` 固定为 `action=WAIT_FOR_USER`、`stopCurrentTurn=true` 和
`resumeMessageType=agent_interaction_response`。宿主收到该 JSON 后必须立即结束当前助手轮次，
不得添加 `block_content` 包装、Markdown、说明文字或前后缀，不得继续采样模型或调用任何阶段工具。
只有用户完成填写或选择并点击确定、前端产生新的 role=user 回复后，智能体才恢复流程。

## 载荷

单题使用扁平对象，与 HiMat 当前解析格式一致：

```json
{
  "type": "agent_interaction",
  "task": "PRIMER_DESIGN",
  "flowControl": {
    "action": "WAIT_FOR_USER",
    "stopCurrentTurn": true,
    "resumeMessageType": "agent_interaction_response"
  },
  "interactionId": "interaction_001",
  "reason": "当前仅获得6套有效候选，少于期望的10套。",
  "question": "下一步如何处理？",
  "inputType": "SINGLE_SELECT",
  "options": [
    {"value": "ACCEPT_CURRENT", "label": "接受当前结果"},
    {"value": "ADJUST_PARAMETERS", "label": "修改设计参数"},
    {"value": "CHANGE_TARGET", "label": "更换标靶"},
    {"value": "CANCEL_TASK", "label": "取消任务"}
  ],
  "answer": null
}
```

同时存在两题或更多题时才使用有序 `interactions` 数组，前端按数组下标显示 `1/N`、`2/N`
导航。每个数组元素的字段与上面的扁平题目相同。

## 前端提交

用户完成所有题目后，前端提交 `agent_interaction_response`。单题回复可以同样使用扁平的
`interactionId`/`answer`；多题回复数组必须逐一包含本次弹窗中每个 `interactionId`。`answer` 为所选 option
的 `value`；选择需要补充文字的 `OTHER` 时，同时携带 `customInstruction`。表单题把可编辑字段
放入 `fieldValues`。

```json
{
  "type": "agent_interaction_response",
  "task": "PRIMER_DESIGN",
  "interactionId": "interaction_001",
  "answer": "ACCEPT_CURRENT",
  "fieldValues": {},
  "customInstruction": null
}
```

前端只负责收集答案，不得修改 `reason`、`question`、`options` 或 `interactionId`。HiMat 将完整
回复提交到 `POST /v1/wf01/tasks/{batch_id}/interactions/response`，或由编排器调用
`submit_wf01_interaction_response`。Agent D 在动作路由成功后保存答案并标记对应的持久化
`primer_agent_interaction` 为已解决；所有 M1 确认解决后批次进入 `QUEUED`。

机器可读 schema 通过 MCP resources
`oligosmith://contracts/agent-interaction` 和
`oligosmith://contracts/agent-interaction-response` 发布。
