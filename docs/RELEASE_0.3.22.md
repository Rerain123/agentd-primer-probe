# OligoSmith 0.3.22

本版本恢复 HiMat 最初约定的 WF-01 输入弹窗业务格式，并把模型轮次控制规则收敛到 MCP 服务。

## 输入弹窗格式

- `primer_design_input` 顶层严格只返回 `type` 和 `data`。
- 不再向 HiMat 返回 `flowControl`、`interactionId`、`deduplicationKey` 或 `onDuplicate`。
- MCP `structuredContent` 和 REST 兼容接口均使用相同格式。
- JSON Schema 使用 `additionalProperties=false` 固定该对外契约。

## MCP 调用规则

- 同一个用户新设计请求最多调用一次 `prepare_wf01_design_input`。
- 无论字段是否完整，返回后不得重试、再次调用或更换字段名调用。
- 返回后不得复述 JSON、输出助手文字或继续调用其他工具；必须立即结束当前轮次。
- 上述要求写入 MCP 服务 instructions 和工具 description，不再依赖返回 JSON 中的控制字段。

## 兼容性

- 新表单只生成 `type + data`。
- MCP 输入适配层仍可读取 0.3.21 及更早版本已经保存在对话中的扩展表单，保证滚动升级期间
  已打开表单仍能完成预检和创建批次。
- M1-M6 批次内部的 `agent_interaction` 及其交互编号不受此次调整影响。
- 0.3.21 增加的候选报告、科学过程详情报告和科学工具数据包保持不变。
