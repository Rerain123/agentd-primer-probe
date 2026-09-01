# OligoSmith MCP 服务器部署

OligoSmith 在 `/mcp` 保留兼容的全量无状态 MCP 可流式传输 HTTP 接口，并新增
`/mcp/wf01/` 至 `/mcp/wf05/` 五个按工作流限制工具目录的入口。同一服务进程还保留
`/health`、`/ready`、REST 兼容接口和报告下载接口。健康检查与就绪检查公开访问；
除健康与就绪探针外，MCP 和 REST/API 默认都需要 Bearer JWT。启用
`MCP_PUBLIC_WF01_ARTIFACTS=true` 后，仅 WF-01 报告、导出文件和科学过程产物允许匿名只读访问。
WF-02 canonical report、面板详情、预览及标准下载路由仍受 Bearer JWT 保护。启用 RustFS 后，
WF-02 终态默认 HTML 会先发布到配置的 bucket/prefix；CSV/PDF 仅在明确请求对应格式时生成并发布。
附件卡片直接使用对象的完整公网 URL；
未启用 RustFS 时才回退到由 Agent D 签发的短期只读 URL。

MCP 初始化说明、工具说明、参数说明和资源说明统一使用中文。JSON Schema 中由框架自动生成的
英文展示标题会在服务启动时移除；工具名、参数字段名、枚举值、URL 以及 JSON/MCP/JWT 等协议标识
保持原值，避免破坏客户端调用契约。

## 必需环境变量

将 `deploy/.env.example` 复制为 `deploy/.env`，并至少设置以下变量：

```dotenv
AGENTD_PORT=8020
AGENTD_REQUIRE_DATABASES=false
MCP_PUBLIC_BASE_URL=https://oligosmith.ne.scqcyz.cn
WF02_ATTACHMENT_URL_TTL_SECONDS=86400
MCP_ALLOWED_HOSTS=oligosmith.ne.scqcyz.cn,127.0.0.1:8000,127.0.0.1:*
MCP_ALLOWED_ORIGINS=https://oligosmith.ne.scqcyz.cn
JWT_SECRET=replace-with-a-long-random-secret
MCP_PUBLIC_WF01_ARTIFACTS=true
HIMAT_FILE_DOWNLOAD_BASE_URL=https://himat.ws.scqcyz.cn/api/files/download
HIMAT_FILE_DOWNLOAD_TIMEOUT_SECONDS=300
AGENTD_INPUT_FILE_MAX_BYTES=1073741824
RUSTFS_ENABLED=true
RUSTFS_ENDPOINT_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_BUCKET=wens
RUSTFS_OBJECT_PREFIX=data
RUSTFS_PUBLIC_BASE_URL=https://rustfs.ne.scqcyz.cn
```

`JWT_SECRET` 用于签发令牌，它不是客户端 `Authorization` 请求头中填写的令牌。
禁止提交 `deploy/.env`，也禁止在日志或终端中输出该密钥。

MCP HTTP 响应层会固定转换终态 `primer_design_output`：将内部
`CallToolResult.structuredContent` 提升为 JSON-RPC `result`，使 `result` 严格只包含
`data/type`。初始化、工具列表、错误、`agent_interaction` 和非终态结果不受影响。
该服务仅用于 HiMat，因此没有开关或标准 MCP 外壳回退路径。

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

1Panel 反向代理应将整个域名转发到 `http://127.0.0.1:8020`。全量和按工作流 MCP 地址都不需要单独配置端口或子应用。

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

### 按工作流 Skill 与 MCP 入口

生产对话入口优先使用以下受限地址；原 `/mcp` 仅作为兼容与回退入口保留：

| Skill | MCP 地址 | 工具数 |
|---|---|---:|
| `oligosmith-wf01` | `https://oligosmith.ne.scqcyz.cn/mcp/wf01/` | 10 |
| `oligosmith-wf02` | `https://oligosmith.ne.scqcyz.cn/mcp/wf02/` | 11 |
| `oligosmith-wf03` | `https://oligosmith.ne.scqcyz.cn/mcp/wf03/` | 4 |
| `oligosmith-wf04` | `https://oligosmith.ne.scqcyz.cn/mcp/wf04/` | 4 |
| `oligosmith-wf05` | `https://oligosmith.ne.scqcyz.cn/mcp/wf05/` | 4 |

五份 Skill 位于 `skills/oligosmith-wf01` 至 `skills/oligosmith-wf05`。平时只需要暴露 Skill
名称和简短描述；命中后加载对应 `SKILL.md`，并连接其 `agents/openai.yaml` 声明的唯一 MCP 依赖。
五个入口重新注册现有工具函数，继续调用同一 `AgentDService`；不会复制、替换或迁移工作流后端。
所有入口使用同一 Bearer JWT。`tools/list` 必须严格等于该入口的工具白名单，不能出现其他工作流工具。

`MCP_PUBLIC_WF01_ARTIFACTS=true` 只会匿名开放 `/data/*`、WF-01 报告与导出路由、
科学产物列表与下载路由的 `GET`/`HEAD` 请求。MCP、任务创建、交互回复、重新设计以及其他写操作
仍然需要 JWT。RustFS 启用时，WF-02 附件卡片直接引用 RustFS 公网对象，Agent D 的报告 JSON、
面板详情、预览和标准下载路由继续需要 JWT。RustFS 未启用时，Agent D 仅对签名匹配且未过期的
具体 WF-02 附件放行 `GET`/`HEAD`。`WF02_ATTACHMENT_URL_TTL_SECONDS` 默认 86400 秒，可配置范围为
300–604800 秒；轮换 JWT 密钥会立即使本地兜底地址失效。将 `MCP_PUBLIC_WF01_ARTIFACTS` 设置为
`false` 后，WF-01 文件会重新受 JWT 保护。

## 对外工作流覆盖范围

兼容入口 `/mcp` 提供 29 个 MCP 工具：1 个文件传输入口；9 个覆盖 WF-01 的创建、问题修正、
任务恢复、M7 版本、紧凑报告、候选视图与导出；10 个覆盖 WF-02 N01–N32 的建单、输入确认、
任务查询、推进、两类交互回复、不可变新版本、canonical report、面板详情和附件卡片；3 个覆盖
WF-03 的运行、Step 1 重入和终态交付；3 个覆盖 WF-04 v0.1 的运行、不可变重试和终态交付；
3 个覆盖 WF-05 v0.1 的运行、不可变重试和终态交付。服务状态、审计、独立工具、预检和原始产物不暴露给模型。WF-06、WF-07 的第二阶段任务已完成，当前暂不继续，也不加入现行公开工具集。
WF-01 的特异性步骤必须调用 P2；M4 只提供工作流输入映射和结果排序。P2 对目标优先使用本地
版本化资源，缺失时使用任务内不可变输入快照；综合背景优先使用本地资源，缺失时使用部署配置的
远端服务；宿主仍要求本地版本化资源。所有回退均由后端执行并保存来源，资源不可用时结果为 `NOT_EVALUATED`。旧版
WF-01 单次执行入口不通过 MCP 对外开放。WF-01 任务结果与规范报告
也以 MCP 资源模板形式提供，只用于明确的非对话式检查；智能体不得将完整资源读入模型上下文。

## WF-03 内标设计边界

宿主内标、内参或管家基因检测体系统一调用 `run_wf03_internal_control`。模型只识别用户意图并
原样传递宿主、已有靶标寡核苷酸以及用户明确提供的自定义基因/RefSeq/同源序列；不得自行选择
管家基因、计算保守性或热力学、调用底层 P1/P2/P6，亦不得补写阈值结论。

后端 `WF03InternalControl` 只直接调用既有 `P5.design_internal_control()`。NCBI Entrez、C5、P1、
P2 和 P6 均由 P5 负责；P3/MGB 和 P4/最终通道兼容性在独立 WF-03 中明确为不适用。P5 返回后，
WF-03 只做候选数量、证据等级、P6 `NOT_EVALUATED`、靶标兼容性声明和失败类型的确定性映射。

若失败，报告固定给出 `next_action=WAIT_FOR_USER`、备选基因需 RefSeq 的说明及外源性异源内标建议。
只有用户补充证据或管理员修复依赖后，模型才调用 `retry_wf03_internal_control`。后端从父 `job_id`
恢复原始输入、应用白名单补丁并创建新的不可变 `job_id`；不会覆盖父结果。完整结果保存在任务
存储中；MCP 的 `run/retry/get` 都从持久化结果生成 HTML 终态附件，再返回与 WF-01 相同的
`primer_design_output`。终态对象由 MCP 工具确定性生成并直接交付；
模型不生成助手文本，也不重建该对象。

HiMat 路由必须先区分“有附件”和“无附件”。有附件/file_id 时固定先调用 `download_himat_file`，
再把返回对象顶层的 `input_artifact_id` 原样传给 `run_wf03_internal_control`；只有无附件且参数齐全时
才直接运行。缺少必填字段时只询问该字段并结束当前轮次；全程不得先输出分析或用文本模拟工作流。
该输入链与 WF-01 完全一致，不增加 WF-03 专用下载字段。附件正文、内部路径和字段映射均由
`AgentDService` 在后端处理，模型只看见不透明引用。
服务端同时把该约束写入
MCP initialize instructions 和工具 description，兼容忽略其中任意一种提示的客户端。服务升级后必须
刷新 HiMat 的 MCP `tools/list` 缓存；若客户端支持 `tool_choice`，附件请求应依次指定下载和运行工具，
而不是使用 `auto`。部署验收脚本会同时校验工具名称、initialize instructions 和工具 description，避免只更新服务代码而
HiMat 继续使用旧工具元数据。

## WF-04 v0.1 编排边界

模型只调用 `run_wf04_validation`、`retry_wf04_validation` 和 `get_wf04_report` 三个粗粒度工具。
后端负责输入规范化，并严格按 Step 1、Step 2、Step 3、Step 4、Step 5、Step 6 顺序执行；Step 5 的 P3/P6 与配合性判定只在三个 P2 步骤完成后启动，Step 6 再做确定性汇总与持久化；
MCP 层不包含节点实现，也不允许模型重新计算结果。

WF-04 与 WF-01 使用同一个文件接入边界。有附件/file_id 时固定执行
`download_himat_file → 顶层 input_artifact_id → run_wf04_validation`，不增加 WF-04 专用下载字段。
后端按固定记录名或固定位置规则映射输入，映射不唯一时直接返回
输入错误，不允许模型读取正文后猜测。无附件且运行参数齐全时保留直接调用兼容路径。

完整结果同时返回 `execution_status` 和 `decision`。前者只描述工作流是否完整执行，后者只描述最终判定；
因此完整执行可以得到失败判定，依赖不完整则返回 `PARTIAL/FAILED + INCONCLUSIVE`，两者不得互相覆盖。
当 `next_action=RETRY` 时，调用方只能提交报告列出的 `retryable_steps` 和白名单补丁。后端恢复父任务输入，
创建带 `parent_job_id` 的新任务，并复用未受影响的成功 P2 节点，不覆盖父结果。

MCP 的 `run/retry/get` 均从持久化结果生成 HTML 终态附件，并复用 WF-01 的
`primer_design_output` 输出结构和 `outputSchema` 约束。HTTP 响应层将该业务对象
直接作为 JSON-RPC `result` 交付给 HiMat，不生成终态助手文本。输出卡片中的
`status=COMPLETED` 只表示终态附件已经生成并可交付；工作流的 `execution_status` 与 `decision`
继续作为两个独立字段保存在报告中。

REST 专用入口为 `POST /v1/wf04/validate`、`POST /v1/wf04/validate/{job_id}/retry` 和
`GET /v1/wf04/validate/{job_id}/report`。`POST /v1/workflows/WF-04/run` 保留为兼容别名，但与专用入口
调用同一个 `WF04V01Validation` 后端，不拥有独立实现。

## WF-02 N01–N32 编排与交付边界

同一样本、同一 qPCR 管的 2–5 个目标靶标由模型先调用 `create_wf02_batch`；“重数”只统计目标靶标，
内标不计入重数但额外占 1 个检测通道。任务停在 N05 后，
调用 `run_wf02_until_blocked` 推进 N06–N32。后端为每个靶标建立独立的已完成 WF-01 阶段子任务，
候选序列只从其报告中取得，并且仅保留 BLAST 证据、specificity 和 R19 均通过的 Top 5。随后由
WF-02 通过 `AgentDService` 的 P6 服务操作，对每个保留候选执行 ViennaRNA MFE 与次优结构评估；
P6 是软性规则，缺少扩增子序列、工具不可用或评估异常时明确写入 `not_evaluated`，不淘汰候选。
WF-02 不直接调用 Primer3 或 BLAST，模型也不得自行生成候选。

MCP 的 `run_wf02_until_blocked` 现在是持久化后台任务入口：后端立即接管执行，首个请求最多
同步等待 235 秒。若任务在此期间完成，当前请求直接交付最终结果；若仍在运行，则返回
`WAIT_FOR_BACKGROUND` 和一条 `user_message`，当前 HiMat 轮次必须结束，而后台任务继续执行。
235 秒到达后，响应读取最近一次已原子提交的任务快照，不等待后台执行锁，因此后台正在处理一个
长执行单元也不能延长前台请求。`create_wf02_batch` 在任务已持久化但响应链路失败时，禁止模型在
同一轮自动重试；用户后续明确继续后，可以用完全相同的参数再次调用。后端按已认证调用方隔离，
在 15 分钟窗口内接回最近的同请求任务，不创建重复任务，也不在持久化记录中保存调用方明文。
模型不得轮询、不得连续调用运行工具，也不得重复输出阶段过程文字。用户后续明确查询时只调用一次
`get_wf02_batch`；运行中返回当前状态并结束，完成后交付最终结果。服务重启后，持久化状态为
`QUEUED` 或 `RUNNING` 的任务会自动恢复。陈旧 `resume_token` 由后端基于已持久化状态吸收，
不要求模型发起恢复循环。

WF-02 上传文件采用 `wf02-one-file-per-target-v1`：一个文件只能绑定一个检测靶标；文件内允许包含
该靶标不同毒株、分离株或基因型的多条同源序列。模型必须对每个附件分别调用
`download_himat_file`，再把返回的 `input_artifact_id` 放入对应的
`targets[].input_artifact_id`。后端固定把该文件作为一个 `alignment_target` 交给 WF-01，因此多条
FASTA 记录进入同一靶标的 MAFFT/保守性流程，不按记录拆成多个靶标。同一文件绑定多个靶标、一个
靶标绑定多个文件，或使用 `independent_targets`/`target_manifest` 请求混合文件拆分，都会在 N03
作为阻塞输入拒绝；靶标与文件对应关系不清楚时必须请求用户确认，不得根据标题静默猜测。

如果核心建单输入缺失，使用 `submit_wf02_input_response` 回复 N04；如果某靶标缺少模板或其 WF-01
子任务产生交互，工具会原样返回 `agent_interaction`，使用 `submit_wf02_child_response` 回复后恢复。
单靶标 0 候选或子任务失败只标记该靶标并继续循环。N08 持久化失败 ID、完整失败对象、当前覆盖数
和面板不完整文案，并将失败 ID 同步到 `multiplex.failed_targets[]`。全部靶标完成后，若用户需要内标，
新 WF-02 直接调用完整 P5；0 套候选或 P5 FAIL 时记录失败并自动继续不含内标面板，1–2 套以
CAUTION 继续，≥3 套以 PASS 继续。P5 只提供 Cy5 默认建议；N13 由 WF-02 按
“Cy5 → 最远可用远红通道 → 其他不冲突通道”生成候选指派，并禁止使用与已占通道
`Δλ_em < 15 nm` 的替代。仪器或通道条件无法容纳内标时同样记录 FAIL 并自动继续目标面板，
当前不再等待用户交互。随后 N15–N20 分批穷举完整候选组合；WF-02
按输入顺序生成候选通道指派，再通过服务网关调用完整 P4。P4 原样负责交叉二聚体、
光谱、淬灭基团与仪器检查。R-17 长度规则只形成项目 CAUTION，不改写 P4 verdict。每批最多 500 个
组合并保存游标，必须穷举完成才判断是否存在严格完整面板。无严格方案时，N21 使用现有 P4 的
`relaxed=True` 将交叉二聚体阈值放宽到 ΔG > −7.0；仍无结果时，N22 继续通过 P4 将引物和探针
Tm 极差分别放宽到 4°C。两轮都必须保存 WARNING、阈值和游标，P4 未评估时停在原组合重试。
若完整面板仍不可用，N23 按最大靶标覆盖优先穷举部分组合；N25 结果固定标记 `PARTIAL` 并排除在
完整 Top 3 之外。没有至少二靶标的部分面板时，N26 输出分管、减少靶标和 SYBR Green 文字降级建议。
存在完整严格或风险面板时，N27 最多复用两次 WF-01 不可变版本完成 Tm/GC 定向均衡；N28–N30
按版本化公式评分、按序列成员去重并分层选择最多 Top 3。N31 的 canonical report 与 HTML
完全由后端证据确定性生成，模型不编写报告；N32 默认只通过 `primer_design_output` 交付 HTML。
CSV/PDF 仅在用户明确调用 `get_wf02_export` 请求对应格式时生成并返回；同时支持
`get_wf02_report`、`get_wf02_panel` 和 `create_wf02_version`。下载与预览请求
必须携带与 MCP 调用相同的 Bearer JWT。

## 智能体工具审计

后台 REST 接口 `GET /v1/audit/agent` 提供只追加、已脱敏的可观察执行记录，包括工具
开始、完成与失败事件，提交字段来源，合并后的有效设计输入，工作流状态，数据库就绪情况和错误详情。
可按 `trace_id`、`batch_id` 或 `operation` 筛选；`limit` 的取值范围为 1～500。

审计记录不包含模型的隐藏推理。FASTA 或序列内容会替换为长度、SHA-256 和记录数；授权令牌、密码和
密钥会被移除。详见 [智能体工具审计日志](AGENT_AUDIT_LOG.md)。

## WF-01 UI 与输入边界

`prepare_wf01_design_input`、`primer_design_input` 和 `primer_design_input_update` 已从当前 MCP 链路删除，
HiMat 不再展示设计前确认表单。新任务只有两种输入方式：

- 直接序列：`create_wf01_batch(sequence=..., target_organism=..., host=...)`；
- 上传附件：`download_himat_file(file_id=...) → create_wf01_batch(input_artifact_id=..., target_organism=..., host=...)`。

`sequence` 与 `input_artifact_id` 必须二选一，`target_organism` 必填，`host` 可选。若当前对话没有靶标
物种或病原名称，模型必须先用普通文本询问用户；不得猜测，也不得以 `DESIGN_ONLY` 自动完成报告。
`download_himat_file` 只返回内部 artifact 引用和文件元数据，不返回 FASTA 正文、下载地址或服务器路径；
上传 FASTA 仅在 Agent D 后端内部解析。

`create_wf01_batch` 在输入合格时自动执行 M2-M6，并在建批后验证所有靶标均为
`FULL_SPECIFICITY`。M4 必须通过 P2 执行靶标命中、综合背景检索及可选宿主排除。模型不得调用独立
Primer3 工具代替 WF-01，也不得把 M2、M3 或 `DESIGN_ONLY` 报告称作完整路线。

完整序列只供后端预检、建批和科学计算使用，不会进入模型上下文。输入 artifact 保存在
`AGENTD_STATE_DIR/input-artifacts/`，因此建批可跨请求复用同一个引用。

持久化的批次或阶段结果会提供权威 `ui_message`，其值为待处理交互、最终
`primer_design_output` 或 `null`。对于待处理的 `agent_interaction` 和最终
`primer_design_output`，MCP 工具会将 `result.ui_message` 提升为顶层结构化结果，宿主再将该对象
作为 `block_data` 发出。`agent_interaction` 的权威对象写入 `structuredContent`，同时在
`content[0].text` 提供内容相同的紧凑 JSON，兼容旧任务交互链路。

`primer_design_output` 业务对象顶层严格保持 `data/type`。FastMCP 内部先构造
`CallToolResult`，HTTP 兼容层仅从其 `structuredContent` 读取权威对象，然后将 JSON-RPC
`result` 替换为该对象。线上响应中不再存在 `content`、`structuredContent` 或
`isError` 外壳，也不会读取可能被 Markdown 化的文本通道。所有可能产生终态结果的工具
仍通过 MCP `outputSchema` 声明 `primer_design_output` 分支，服务端在返回前按同一
Schema 校验实际对象。

HiMat 直接解析 JSON-RPC `result` 中的 `{data,type}` 业务 JSON 并生成卡片。
收到终态后模型不得生成助手文本、解释、Markdown 或重建 JSON，也不得再次调用工具。
WF-01 M6 或 WF-02 N32 完成后，以及用户再次查看报告或请求附件后，HiMat 据此展示
对应输出卡片并提供报告预览和附件下载；WF-02 卡片中的 `fileUrl` 优先为 RustFS 完整公网 URL，
未启用 RustFS 时使用短期签名只读地址。标准 REST 路由和其他报告接口仍需 Bearer JWT。
待处理交互还会设置 `next_action=WAIT_FOR_USER`；在对应用户回复提交并解决前，所有 M2-M6 端点都会
拒绝继续执行。

`create_wf01_batch` 在输入合格时直接自动执行 M2-M6，模型不得随后重复调用运行工具。M2-M6 由
Python 包和持久化状态机执行，模型不检查每个候选，也不对每个候选进行推理。当 `ui_message` 要求
用户输入，或 M6 已生成报告时，执行会自动停止。缺少靶标、序列少于 80 nt、非法、歧义或设计参数
不可执行时，在建批前返回普通错误，不生成 `DESIGN_ONLY` 报告。
`run_wf01_until_blocked` 仅用于问题修正、超时中断或
旧任务恢复；`submit_wf01_interaction_response` 持久化用户答案后默认恢复自动执行。

`status=RUNNING` 是执行中的硬边界。其紧凑响应使用
`next_action=WAIT_FOR_STAGE_COMPLETION` 和 `flowControl.stopCurrentTurn=true`；模型与宿主
必须结束当前轮次，不得轮询、刷新或重试该阶段。兼容函数 `run_wf01_stage` 仍可供内部 Python
调用方使用，但不会出现在 MCP 工具列表中，因此普通模型客户端无法绕过自动执行器并反复进入 M4。
上传任务若缺少 HiMat artifact 引用，模型应重新调用 `download_himat_file`，不得自行重建 FASTA 内容。

MCP 批次与阶段工具返回 `wf01-agent-view-v1` 摘要。完整候选池保留在服务端批次或报告产物中。
`get_wf01_report` 默认返回报告摘要；提供 `target_id` 时，每页最多返回 25 个紧凑候选。
`get_wf01_candidate` 只展开一个明确选中的候选。`MCP_MAX_TOOL_RESULT_BYTES` 提供最终响应大小保护，
默认值为 65536 字节。WF-02 首次同步等待通过 `MCP_WF02_SYNC_WAIT_SECONDS` 配置，默认且上限为
235 秒；单进程后台工作线程数通过 `WF02_BACKGROUND_WORKERS` 配置，默认为 1，允许范围为 1–4。

每个完成的 WF-01 科学阶段都会持久化一份面向专家、不可变的原始 JSON 快照。模型使用
`get_wf01_export(format_name=scientific_data)` 下载科学工具数据包；后台客户端可通过
`GET /v1/wf01/tasks/{batch_id}/scientific-artifacts` 查询受限清单，再使用对应下载端点读取原始
MAFFT、Primer3、BLAST 和阶段证据，不把这些大体量内容写入模型上下文。

WF-01 `alignment_target` 的多模板靶标在 M2 中由后端完成 MAFFT 和 gap-aware Shannon 保守性分析，模型不串联或重做该科学计算。真实 MAFFT 证据会进入 R16、候选结合位点明细以及四维综合分；单模板、工具未完成或开发降级只能返回 `NOT_EVALUATED`，不能由模型补写保守性分数。特异性仍由必需的 P2 统一检索和判定，M4 只消费 P2 结果。

M6 默认生成 Top10 HTML、中文科学过程 HTML 和便于阅读的科学数据 ZIP。ZIP 中包含 21 条规则验证 HTML、中文说明和
CSV 表格，不重复收纳已独立交付的科学过程 HTML，也不包含内部 JSON 或命令输出。CSV 与 PDF 是按需格式，仅在用户明确请求后生成。返回
`primer_design_output` 前必须完成所有默认附件的发布。若 HTML 不可用，智能体视图通过
`text_fallback.default_results` 返回排名前 10 的候选。智能体默认不得加载或展示完整候选池。
只有用户明确要求查看全部候选后，智能体才能遵循 `all_candidates_on_request`，逐页调用
`get_wf01_report` 并设置 `candidate_scope="deliverable"`，最后以文本返回完整的紧凑候选结果。

## 完成后的连续对话

`COMPLETE` 结束的是当前批次的科学执行，不会自动开始下一次设计。批次摘要中的
`conversation_context.activeBatchId` 是会话侧应跨轮保留的活动任务句柄。HiMat 应保存该字段，
并在后续模型请求中继续提供，直到用户明确开始新设计或切换任务。
为兼容只保留了报告卡片的客户端，所有模型可见 WF-01 后续工具的 `batch_id` 参数也接受卡片中的
`WF01-YYMMDD-HHMM-XXXX` 任务编号；服务端会先解析为唯一内部 UUID。若编号不存在或发生碰撞，
服务端拒绝猜测并要求使用内部 UUID。

- “文字输出前 10 个候选”、解释或查看结果：调用 `get_wf01_report`；
- 查看单个候选：调用 `get_wf01_candidate`；
- 请求 CSV/PDF：调用 `get_wf01_export`；
- 修改当前设计约束：调用 `create_wf01_version`；
- 新设计：直接序列调用 `create_wf01_batch`；上传文件先调用 `download_himat_file`，再调用 `create_wf01_batch`。

如果活动 `batch_id` 未进入模型上下文，模型不得声称“尚未设计”或重新打开输入表单；应提示宿主
恢复或重新选择已完成任务。正式 `primer_design_output` UI JSON 不增加批次控制字段，任务句柄保留在
外层 MCP 摘要中。

完整路线、前端消息和交互边界见 [WF-01 当前实现与运行路线](../workflows/wf01/WF01_CURRENT_IMPLEMENTATION.md#wf01-input-and-ui)。
