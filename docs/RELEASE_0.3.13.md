# OligoSmith Agent D 0.3.13

发布日期：2026-08-07

## 本次更新

- MCP 服务器说明、工具描述、资源描述和面向模型的运行规则统一使用中文；英文仅保留在协议字段名、工具标识、格式名和固定枚举中，避免模型因说明语言混杂而切换输出语言。
- 客户端 MCP 配置模板中的 `description`、Python 包说明、Docker 镜像标签、API 标题和命令行说明统一改为中文。
- M6 仍只默认生成 HTML 主报告；用户明确通过文字请求科学过程信息后，智能体才按需打包此前已持久化的科学工具原始 JSON。科学过程文件的最终前端返回格式暂不写入 `primer_design_output` 契约。
- 统一用户附件地址为 `/data/<文件名>`；`get_wf01_export` 不再把内部 `/v1/wf01/tasks/.../exports/...` API 返回为用户下载或预览链接。
- `file_url`、`download_url` 和 HTML `preview_url` 均只返回 `/data/<文件名>` 相对路径，不拼接公网域名。
- 新增 `MCP_PUBLIC_WF01_ARTIFACTS`；部署默认开启后，仅匿名放行报告、HTML/CSV/PDF/科学数据 ZIP 和科学 artifact 的 `GET`/`HEAD`，MCP 与全部写接口继续要求 JWT。
- 强化 MCP 的结构化用户交互契约：需要用户选择时必须原样返回 `agent_interaction` JSON，不能改写为文字选项。
- 修正 M6 最终输出通道：完成时由 MCP 工具把 `primer_design_output` 提升为顶层结构化结果，宿主以 `block_data` 返回后端；禁止模型再将其写入 `block_content/content` 文本流。
- 为 `primer_design_input` 增加稳定 `interactionId`、去重键和重复提交保护，减少前端重复弹窗。
- 新增 WF-01 专家原始科学数据通道：M1-M7 阶段结束后由后端脚本保存不可变 JSON artifact。
- 保存 MAFFT 输入/输出、Primer3 参数和原始结果、BLAST+ 查询及 stdout/stderr，并保留工具失败和超时证据。
- 新增 MCP 工具 `get_wf01_scientific_artifacts`。
- 新增原始数据清单和下载接口：
  - `GET /v1/wf01/tasks/{batch_id}/scientific-artifacts`
  - `GET /v1/wf01/tasks/{batch_id}/scientific-artifacts/{artifact_id}`
- 新增科学 artifact 及 artifact index 的 JSON Schema、完整性 SHA-256 和自动化测试。
- 修复 WF-01 阶段处于 `RUNNING` 时仍返回 `RUN_M4` 导致智能体重复进入 M4 的状态矛盾；
  运行中阶段现在返回 `WAIT_FOR_STAGE_COMPLETION`，并形成硬性轮次边界。
- `run_wf01_stage` 不再暴露给 MCP 模型客户端，正常流程只能通过 `run_wf01_until_blocked`
  自动推进；内部 Python 兼容函数保持可用。
- M4 未预期运行时错误会持久化为 `M4_BLOCKED` 用户交互并恢复批次状态，不再遗留永久
  `RUNNING` 批次。
- 修复 M4 BLAST 原始证据回退对象引用未定义变量 `database` 的问题；回退证据现在记录当前
  数据库资源路径、检查模式和 E-value，不再触发 `NameError`。
- MCP 提示词禁止同轮轮询、刷新、超时重试、重建缺失表单以及复制此前的进度文字。

## 数据与升级

- 继续使用 `agentd-jobs` Docker 持久化卷，不应删除或重建该卷。
- 原有任务状态和报告目录保持兼容。
- 大体量科学原始数据只保存在 artifact 文件中；普通 MCP 响应仅返回清单和下载地址。
- 本版本尚未处理“智能体自行构造但服务器不存在的报告附件地址”这一后续问题。

## 验证

- Python 自动化测试：165 项通过。
- 部署包内置 `PACKAGE-MANIFEST.sha256`。
- ZIP 同目录提供独立 SHA-256 校验文件。
