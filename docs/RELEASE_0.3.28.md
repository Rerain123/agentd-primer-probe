# OligoSmith 0.3.28

## 输入表单

- 为 `prepare_wf01_design_input` 增加服务端重复调用保护。同一调用方在同一轮次内短时间重复提交相同参数时，仅第一次返回可渲染表单，后续调用返回 `DUPLICATE_SUPPRESSED` 控制结果。
- 支持通过 MCP 请求 `_meta.turnId` 及兼容的消息、会话、线程标识划分去重范围；没有显式轮次标识时使用短时间窗口兜底。
- `prepare_wf01_design_input` 要求传入 `file_names`。单文件显示上传时的原始文件名；一次上传多个文件时显示“多个文件”。
- 移除 `uploaded.fasta` 伪文件名兜底；旧 REST 请求没有文件名时显示“未获取文件名”。

## 最终输出表单

- 修复 M6 完成结果被包装为 `{"content":[],"structuredContent":...}` 后无法被 HiMat 渲染的问题。
- 进程内调用直接返回裸 `primer_design_output`；标准 MCP 响应继续将同一业务对象写入顶层 `structuredContent`，并在 `content[0].text` 提供 JSON 镜像。
- 最终回复约束为唯一的助手裸 JSON 输出，不允许附加进度说明、代码围栏或 MCP 包装字段。

## 流程稳定性

- 用户确认后的下载、预检、建批和 M2-M6 执行改为静默工具链，禁止在中间工具调用之间重复输出历史进度。
- 预检兼容 HiMat 转发的 `confirmed=true` 表单快照；输入错误不再向模型返回完整表单示例，并明确禁止在当前轮次自行重建输入或重试。

## 部署

- 默认镜像标签、OCI 镜像版本、发布清单及安装完成提示同步升级为 `0.3.28`。
- 部署包继续采用 Docker Compose 源码包格式，包含 SHA-256 文件清单和外部校验文件。
