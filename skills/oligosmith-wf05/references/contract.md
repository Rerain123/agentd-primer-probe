# WF-05 工具契约

## 服务边界

- 专属 MCP 地址：`/mcp/wf05/`。
- 专属运行时：`wf05-runtime-snapshot-v1`。
- 专属状态目录由服务端选择；Skill 不构造本地路径。
- WF-05 不调用其他工作流。后端返回的交接建议仅供用户决定下一步。
- 专家规则文档与既有工具实现是只读边界；WF-05 只编排经固定摘要校验的既有工具快照。
- 未提供的可选工具必须记录为 `NOT_EVALUATED`，不得自行实现或使用近似替代。

## MCP 编排入口

下列入口只负责附件接入、建单、不可变重试和终态交付，不是新的计算工具。

| 工具 | 用途 |
|---|---|
| `download_himat_file` | 将附件转换为不透明的内部文件引用 |
| `run_wf05_variation` | 创建并运行一个新的 WF-05 任务 |
| `retry_wf05_variation` | 从可重试父任务创建不可变子任务 |
| `get_wf05_report` | 重新获取已持久化任务的终态卡片 |

## 入口约束

- `forward_primer` 和 `reverse_primer` 必填。
- 序列集必须通过 `input_artifact_id` 或 `sequences` 提供，且二者互斥。
- `probe`、`reference_sequence_id`、`target_id`、`target_species` 和 `assay_id` 均为可选字段；不得猜测。
- 附件存在时必须先调用下载工具；模型不得读取、解析或转写附件正文。
- 不传递契约外字段、本地文件路径或内部控制字段。
- 不拆分请求来绕过后端资源上限；上限错误应直接转告用户。
- 工具完整性校验失败时必须停止 WF-05，不得请求模型或其他工作流代算。

## 状态与重试

- `execution_status` 表示后端执行是否完整，`decision` 表示本次评估结论；两者不得混用。
- 输入错误由工具直接返回；运行依赖错误以可查询的 `FAILED / INCONCLUSIVE` 任务返回。
- 仅当 `retry.allowed` 为 `true` 时重试。重试创建新 `job_id`，父任务保持不变。
- 父任务原始输入不可用时停止，不从报告摘要重建输入。
- `next_action = RETRY` 时等待用户补充输入或依赖修复。
- `next_action = HANDOFF_WF01` 只说明存在交接建议，不授权自动调用 WF-01。
- 终态工具结果由 `primer_design_output` 直接交付，模型不重建卡片或附件链接。
