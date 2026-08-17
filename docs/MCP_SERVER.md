# OligoSmith MCP 服务器部署

OligoSmith 在 `/mcp` 提供无状态的 MCP 可流式传输 HTTP 接口。同一服务进程还保留
`/health`、`/ready`、REST 兼容接口和报告下载接口。健康检查与就绪检查公开访问；
MCP 与工作流写操作需要 Bearer JWT。启用 `MCP_PUBLIC_WF01_ARTIFACTS=true` 后，
WF-01 报告、导出文件和科学过程产物允许匿名只读访问。

## 必需环境变量

将 `deploy/.env.example` 复制为 `deploy/.env`，并至少设置以下变量：

```dotenv
AGENTD_PORT=8020
AGENTD_REQUIRE_DATABASES=false
MCP_PUBLIC_BASE_URL=https://oligosmith.ne.scqcyz.cn
MCP_ALLOWED_HOSTS=oligosmith.ne.scqcyz.cn,127.0.0.1:8000,127.0.0.1:*
MCP_ALLOWED_ORIGINS=https://oligosmith.ne.scqcyz.cn
JWT_SECRET=replace-with-a-long-random-secret
MCP_PUBLIC_WF01_ARTIFACTS=true
HIMAT_FILE_DOWNLOAD_BASE_URL=https://himat.ne.scqcyz.cn/api/files/download
HIMAT_FILE_DOWNLOAD_TIMEOUT_SECONDS=300
AGENTD_INPUT_FILE_MAX_BYTES=1073741824
```

`JWT_SECRET` 用于签发令牌，它不是客户端 `Authorization` 请求头中填写的令牌。
禁止提交 `deploy/.env`，也禁止在日志或终端中输出该密钥。

测试服务器没有 BLAST/FASTA 数据库时，可设置 `AGENTD_REQUIRE_DATABASES=false` 以允许服务
进入就绪状态；所有依赖不可用数据库的结果仍必须保持为 `NOT_EVALUATED`。正式发布前应准备带版本的
数据库，并将该变量设置为 `true`。

## 构建与启动

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml config --quiet
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build --pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
bash deploy/scripts/verify.sh --wait 180
```

1Panel 反向代理应将整个域名转发到 `http://127.0.0.1:8020`。`/mcp` 不需要单独配置端口或子应用。

## 创建客户端令牌

使用以下命令生成有效期为 30 天的 HS256 令牌；该命令不会在命令行参数中暴露签名密钥：

```bash
bash deploy/scripts/generate-jwt.sh 30 oligosmith-backend
```

只将命令返回的令牌复制到平台 MCP 配置中：

```json
{
  "name": "OligoSmith",
  "type": "streamableHttp",
  "description": "引物与探针设计、验证、优化和报告生成",
  "baseUrl": "https://oligosmith.ne.scqcyz.cn/mcp",
  "headers": {
    "Content-Type": "application/json",
    "Authorization": "Bearer PASTE_GENERATED_JWT_HERE"
  }
}
```

轮换 `JWT_SECRET` 后，之前签发的全部令牌会立即失效。

`MCP_PUBLIC_WF01_ARTIFACTS=true` 只会匿名开放 `/data/*`、WF-01 报告与导出路由、
科学产物列表与下载路由的 `GET`/`HEAD` 请求。MCP、任务创建、交互回复、重新设计以及其他写操作
仍然需要 JWT。将该变量设置为 `false` 后，这些文件会重新受 JWT 保护。

## 对外工作流覆盖范围

服务器面向主引物智能体提供 11 个 MCP 工具，仅覆盖 WF-01 的文件上传兼容、创建并自动运行、
问题修正、任务恢复、M7 版本、紧凑报告与候选视图以及导出。服务状态、审计、独立 Primer3、
预检、科学原始产物和 WF-02 至 WF-07 仍可由后台代码或 REST 使用，但不暴露给模型。
BLAST 特异性只通过工作流调用服务器本地版本化数据库。旧版 WF-01 单次执行入口不通过 MCP 对外开放。WF-01 任务结果与规范报告
也以 MCP 资源模板形式提供，只用于明确的非对话式检查；智能体不得将完整资源读入模型上下文。

## 智能体工具审计

后台 REST 接口 `GET /v1/audit/agent` 提供只追加、已脱敏的可观察执行记录，包括工具
开始、完成与失败事件，提交字段来源，合并后的有效设计输入，工作流状态，数据库就绪情况和错误详情。
可按 `trace_id`、`batch_id` 或 `operation` 筛选；`limit` 的取值范围为 1～500。

审计记录不包含模型的隐藏推理。FASTA 或序列内容会替换为长度、SHA-256 和记录数；授权令牌、密码和
密钥会被移除。详见 [智能体工具审计日志](AGENT_AUDIT_LOG.md)。

## WF-01 UI 与输入边界

`prepare_wf01_design_input` 仅用于暂未改造的文件上传兼容链路。调用后，宿主必须直接渲染其结构化结果；模型不得在助手文本中复现、
引用、概括或包装该 JSON。返回的业务对象顶层严格只有 `type` 和 `data` 两个字段；流程控制字段和
交互编号不属于该公开 JSON。MCP 服务器指令将此工具规定为当前助手轮次的终止边界。对于一次明确的
用户设计请求，模型最多调用一次 `prepare_wf01_design_input`；取得任何结果后均不得重试、改换字段名、
调用其他工具或输出可见助手正文。后续 `primer_design_input_update` 只包含用户可编辑字段的值。
上传文件模式下，`prepare_wf01_design_input.file_names` 必须逐一复制当前用户消息中所有上传附件的原始显示
文件名。单文件表单显示原始文件名；两个或更多文件统一显示“多个文件”，不得使用 `uploaded.fasta`
占位。直接粘贴或手动输入序列不得调用本工具，也不创建输入 artifact 或确认表单；模型应直接调用
`create_wf01_batch(sequence=..., target_organism=..., host=...)`。
两个 WF-01 入口工具都要求同时提供原始表单和真实的 `role=user` 更新。宿主提供 HiMat `file_id` 后，
模型调用 `download_himat_file`；该工具只返回 `input_artifact_id`、文件名、大小、类型和 SHA-256，
不返回文件正文、下载地址或服务器路径。模型将 `payload.input_artifact_id` 原样传给
`create_wf01_batch`。上传模式的 FASTA 仅在服务端内部解析。编排器不得调用通用文件读取工具，
也不得编造或自动确认更新。直接序列走明确参数入口，不经过该上传确认链路。

用户确认后的 `download_himat_file → create_wf01_batch` 是上传文件的静默工具链。直接序列则只调用一次
`create_wf01_batch`。`create_wf01_batch`
在输入合格时自动执行 M2-M6；相关助手采样只能包含工具调用，助手
`content` 必须为空；不得输出或在后续采样中复述下载、预检、建批、M2-M6 进度。只有两类终止输出
允许可见：旧任务或上传兼容流程确需用户决策时的 `agent_interaction`，以及 M6 完成后的
路线说明文字与 `primer_design_output` 报告弹窗。直接序列流程不产生中间弹窗。HiMat 若把同一轮此前的助手文字作为前缀再次提供，模型也不得
复制、续写或拼接该前缀。

表单提交兼容 `data.confirmed=false/true` 两种布尔快照，确认事实仍以独立的
`primer_design_input_update` 为准。用户原样确认时，update 的 `data` 可以为空，或 `data.designInput`
可以为空，并从原始表单继承全部字段。第一次预检若只是工具参数选择了错误的历史对象，智能体复用当前
上下文里的原始表单与结构化更新，修正参数后重试一次；错误结果不提供完整示例，避免模型自行编造对象。

完整序列只供后端预检、建批和科学计算使用，不会进入模型上下文。输入 artifact 保存在
`AGENTD_STATE_DIR/input-artifacts/`，因此建批可跨请求复用同一个引用。

`开始设计`、`确认开始设计` 等可见按钮文字不是有效的 `primer_design_input_update`。Agent D
无法查询 HiMat 的私有业务数据库。用户确认后，即使 HiMat 后端已经保存相关字段，也必须将结构化字段
作为新的 `role=user` 消息或工具参数转发。

持久化的批次或阶段结果会提供权威 `ui_message`，其值为待处理交互、最终
`primer_design_output` 或 `null`。对于待处理的 `agent_interaction` 和最终
`primer_design_output`，MCP 工具会将 `result.ui_message` 提升为顶层结构化结果，宿主再将该对象
作为 `block_data` 发出。`agent_interaction` 的权威对象写入 `structuredContent`，同时在
`content[0].text` 提供内容完全相同的紧凑裸 JSON，兼容旧任务交互链路；
模型必须逐字输出该 JSON，禁止自然语言改写、Markdown、说明文字或任何前后缀，并立即结束轮次。
`primer_design_input` 继续把同一业务 JSON 镜像到 `content[0].text`，兼容当前只向模型传递 MCP 文本
结果的 HiMat 客户端。`primer_design_output` 顶层严格保持 `data/type`，并在 `structuredContent` 与
`content[0].text` 中返回完全相同的结构化对象；模型必须逐字输出裸 JSON，不得改写成 Markdown 或
URL 列表。M6 完成后，以及用户再次查看报告、请求下载地址或生成 CSV/PDF 后，HiMat 都据此展示
同一输出卡片并提供报告预览和附件下载。
待处理交互还会设置 `next_action=WAIT_FOR_USER`；在对应用户回复提交并解决前，所有 M2-M6 端点都会
拒绝继续执行。

`create_wf01_batch` 在输入合格时直接自动执行 M2-M6，模型不得随后重复调用运行工具。M2-M6 由
Python 包和持久化状态机执行，模型不检查每个候选，也不对每个候选进行推理。当 `ui_message` 要求
用户输入，或 M6 已生成报告时，执行会自动停止。直接输入模式把阶段异常、候选不足和特异性未完成写入
最终报告，不创建中间交互；只有序列少于 80 nt、非法、歧义或设计参数不可执行时才在建批前返回普通错误。
`run_wf01_until_blocked` 仅用于问题修正、超时中断或
旧任务恢复；`submit_wf01_interaction_response` 持久化用户答案后默认恢复自动执行。

`status=RUNNING` 是执行中的硬边界。其紧凑响应使用
`next_action=WAIT_FOR_STAGE_COMPLETION` 和 `flowControl.stopCurrentTurn=true`；模型与宿主
必须结束当前轮次，不得轮询、刷新或重试该阶段。兼容函数 `run_wf01_stage` 仍可供内部 Python
调用方使用，但不会出现在 MCP 工具列表中，因此普通模型客户端无法绕过自动执行器并反复进入 M4。
确认后若缺少原始表单、更新对象或 HiMat 文件引用，模型必须要求宿主恢复结构化状态，不得通过试探性调用
自行重建。

MCP 批次与阶段工具返回 `wf01-agent-view-v1` 摘要。完整候选池保留在服务端批次或报告产物中。
`get_wf01_report` 默认返回报告摘要；提供 `target_id` 时，每页最多返回 25 个紧凑候选。
`get_wf01_candidate` 只展开一个明确选中的候选。`MCP_MAX_TOOL_RESULT_BYTES` 提供最终响应大小保护，
默认值为 65536 字节。

每个完成的 WF-01 科学阶段都会持久化一份面向专家、不可变的原始 JSON 快照。模型使用
`get_wf01_export(format_name=scientific_data)` 下载科学工具数据包；后台客户端可通过
`GET /v1/wf01/tasks/{batch_id}/scientific-artifacts` 查询受限清单，再使用对应下载端点读取原始
MAFFT、Primer3、BLAST 和阶段证据，不把这些大体量内容写入模型上下文。

M6 默认生成 Top10 HTML、中文科学过程 HTML 和便于阅读的科学数据 ZIP。ZIP 中包含中文说明和
CSV 表格，不包含内部 JSON 或命令输出。CSV 与 PDF 是按需格式，仅在用户明确请求后生成。返回
`primer_design_output` 前必须完成所有默认附件的发布。若 HTML 不可用，智能体视图通过
`text_fallback.default_results` 返回排名前 10 的候选。智能体默认不得加载或展示完整候选池。
只有用户明确要求查看全部候选后，智能体才能遵循 `all_candidates_on_request`，逐页调用
`get_wf01_report` 并设置 `candidate_scope="deliverable"`，最后以文本返回完整的紧凑候选结果。

## 完成后的连续对话

`COMPLETE` 结束的是当前批次的科学执行，不会自动开始下一次设计。批次摘要中的
`conversation_context.activeBatchId` 是会话侧应跨轮保留的活动任务句柄。HiMat 应保存该字段，
并在后续模型请求中继续提供，直到用户明确开始新设计或切换任务。

- “文字输出前 10 个候选”、解释或查看结果：调用 `get_wf01_report`；
- 查看单个候选：调用 `get_wf01_candidate`；
- 请求 CSV/PDF：调用 `get_wf01_export`；
- 修改当前设计约束：调用 `create_wf01_version`；
- 明确开始新设计或提供新靶标：才调用 `prepare_wf01_design_input`。

如果活动 `batch_id` 未进入模型上下文，模型不得声称“尚未设计”或重新打开输入表单；应提示宿主
恢复或重新选择已完成任务。正式 `primer_design_output` UI JSON 不增加批次控制字段，任务句柄保留在
外层 MCP 摘要中。

详见 [WF-01 首次设计输入对话契约](PRIMER_DESIGN_INPUT_CONTRACT.md)。机器可读结构约束通过
`oligosmith://contracts/primer-design-input` 和
`oligosmith://contracts/primer-design-input-update` 对外提供。
