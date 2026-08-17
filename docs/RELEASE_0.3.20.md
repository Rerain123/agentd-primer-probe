# OligoSmith 0.3.20

本版本为 WF-01 增加便于人工辨认的任务编号，同时保留 UUID 作为内部主键。

## 任务编号

格式：`WF01-YYMMDD-HHMM-XXXX`

示例：`WF01-260808-1306-8EC6`

- `WF01`：工作流类型。
- `260808`：北京时间创建日期。
- `1306`：北京时间创建时刻（时、分）。
- `8EC6`：内部 UUID 的前四位。

## 兼容性

- `batch_id` 继续使用完整 UUID，API 路径、持久化目录和工具调用参数不变。
- 新增 `batch_code`，供用户、HiMat 摘要和报告展示。
- `conversation_context.activeBatchCode` 提供当前任务编号；
  `activeBatchId` 仍保留给编排器调用后续工具。
- HTML 报告首页和完成消息摘要显示任务编号。
- 历史批次在读取时根据原 `batch_id` 与 `created_at` 自动计算任务编号，无需迁移文件。
