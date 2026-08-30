# OligoSmith Agent D 变更记录

> 本文由原 `RELEASE_*.md` 逐版本发布说明合并而成，内容按版本从新到旧排列。原文件已从当前文档目录移除，但完整历史仍可通过 Git 恢复。

## 未发布

### WF-04 实际执行顺序对齐

- WF-04 现在严格执行 `Step1 P1 → Step2 靶标 P2 → Step3 宿主 P2 → Step4 背景 P2 → Step5 P3/P6及配合性 → Step6 判定`。
- Step 5 不再于 P2 前提前执行后补写轨迹；其耗时覆盖 P3、P6 与配合性规则汇总，评分及最终判定归入 Step 6。
- 新增真实工具调用事件回归测试；修改仅落在当前 WF-04 开发实现及其独立运行包，其他工作流快照保持冻结。

<a id="release-1-2-13"></a>

## OligoSmith 1.2.13

### WF-04 热力学单位与报告汇总修复

- 修正 WF-04 的 ΔG 单位换算。
- 修正 R-09 报告汇总，保持原有 WF-04 单一工作流，不创建 V2、支线或新工作流。

<a id="release-1-2-12"></a>

## OligoSmith 1.2.12

### WF-02 持久化后台执行与等价性能优化

- WF-02 建单或交互恢复后立即由后端持久化任务接管，首个请求最多同步等待 235 秒；超时只结束前台等待，后台继续运行并可在服务重启后恢复。
- 修复 235 秒等待结束后仍被后台执行锁阻塞的问题：前台现在直接读取最近一次原子持久化快照，按时返回 `WAIT_FOR_BACKGROUND`，不会继续占用 HiMat 请求连接。
- `create_wf02_batch` 的近期同参数重试按已认证调用方隔离并接回原任务，避免传输层失败后重复建单；模型侧同时禁止在同一轮自动重试，只有用户后续明确继续才允许重新连接。
- 移除 WF-02 的模型连续自调用协议；运行中仅返回一次普通状态文字，后续只在用户明确查询时读取状态。
- 组合游标改为直接定位，避免每批从头回放；P4 底层重复调用使用版本化持久缓存。流程节点、候选集合、判定阈值、评分、排序和报告科学字段保持原契约。

<a id="release-1-2-11"></a>

## OligoSmith 1.2.11

### HiMat MCP 终态结果直接投影

- MCP HTTP 响应层固定识别终态 `primer_design_output`，只从干净的
  `structuredContent` 取值，不读取可能被 Markdown 化的文本通道。
- JSON-RPC `result` 直接返回严格的 `{data,type}` 业务对象，不再带
  `content`、`structuredContent` 或 `isError` 外壳。
- 转换始终开启且仅影响终态卡片；初始化、工具列表、错误、
  `agent_interaction` 和非终态结果保持现有格式。

<a id="release-1-2-10"></a>

## OligoSmith 1.2.10

### HiMat 终态裸 JSON 兼容恢复

- 根据线上联调确认，HiMat 当前只识别最终助手消息中的裸
  `{data,type}` 报告对象，不能依赖 MCP 宿主直接渲染
  `structuredContent`。
- 终态工具继续确定性生成并校验 `structuredContent` 与
  `content[0].text`，但不再用 `suppressAssistantText` 阻止最终回复。
- 五个工作流和全量/受限 MCP 指令统一要求：只把
  `content[0].text` 逐字符作为唯一助手回复，不输出 MCP 外壳、解释或
  Markdown；工具输出审计日志保持不变。

<a id="release-1-2-9"></a>

## OligoSmith 1.2.9

### MCP 工具输出审计

- 每次 MCP 工具成功完成后，审计日志都会记录经过脱敏和限长处理的
  `output`，并用 `output_source` 标明输出来源。
- 终态报告卡片和交互卡片优先记录前端实际消费的 `{data,type}`
  业务对象，便于直接排查卡片无法渲染、字段污染或附件地址异常。
- 保持原有敏感字段、长序列及大文本保护规则，避免凭据或大体积结果
  被原样写入日志。

<a id="release-1-2-8"></a>

## OligoSmith 1.2.8

### 终态卡片工具直达与轮次终止

- `primer_design_output` 继续由 MCP 工具确定性生成；权威业务对象仍严格保持
  `structuredContent = {data,type}`，模型不参与生成、转发或重建卡片 JSON。
- 终态 `CallToolResult._meta.oligosmith.workflowState` 增加
  `terminal=true`、`stopCurrentTurn=true`、`continueCurrentTurn=false` 和
  `suppressAssistantText=true`，通知宿主在卡片交付后直接结束当前轮次。
- `content[0].text` 保留与 `structuredContent` 语义相同的紧凑 JSON，仅供旧宿主识别卡片；
  Workflow Skill 和 MCP 指令改为禁止模型生成任何终态助手回复或再次调用工具。
- 五个工作流、全量 MCP 与受限 MCP 入口共用相同的终态控制和业务 Schema，
  并增加协议元数据、指令及 Skill 回归测试。

<a id="release-1-2-7"></a>

## OligoSmith 1.2.7

### HiMat 终态卡片 Skill 交付修复

- 修复五个 Workflow Skill 与 MCP 终态指令冲突：Skill 不再禁止交付终态 JSON。
- 收到 `primer_design_output` 时，模型只逐字符回复工具生成的 `content[0].text`，
  不输出 `content`、`structuredContent`、`isError` 等 MCP 协议外壳。
- `content[0].text` 解析后严格等于约定的 `data/type` 业务对象；禁止解释、重建或
  Markdown 化，并在交付后立即结束当前轮次。
- Skill 契约测试扩展到 WF-01 至 WF-05，并与全量及工作流受限 MCP 入口回归测试保持一致。

<a id="release-1-2-6"></a>

## OligoSmith 1.2.6

### HiMat 终态裸 JSON 交付回归修复

- 终态 `primer_design_output` 兼容链路恢复为只将 `content[0].text`
  逐字符作为唯一助手回复，不再把 `content`、`structuredContent`、
  `isError` 等 MCP 工具结果外壳复述给 HiMat。
- 文本兼容镜像在 JSON 字符串内对 Markdown 活跃字符使用标准
  Unicode 转义，防止 RustFS URL 被自动链接化后破坏 JSON；解析结果
  与权威 `data/type` 对象完全相同。
- 全量与五个工作流受限 MCP 入口共用同一交付规则；标准 MCP
  线包仍保留 `CallToolResult` 协议外壳，其中的 `structuredContent` 仍是
  协议原生客户端的权威业务对象。

<a id="release-1-2-5"></a>

## OligoSmith 1.2.5

### WF-05 独立工作流

- 新增 WF-05 专属运行时快照、状态命名空间、Schema、Skill 和受限 MCP 工具集。
- 后端独立执行六个固定节点、持久化任务并产生终态报告；模型只负责收集入口字段和调用工具。
- 支持对话直接输入与 HiMat 附件输入，附件正文只在后端解析。
- 重试创建不可变子任务，不覆盖父任务；WF-05 不导入、不调用其他工作流。
- C0、C5、C7 和 P1 使用项目已有实现的字节级原样快照，并在测试及 WF-05 运行入口校验固定 SHA-256；不使用 WF-05 自建计算工具。
- 可选依赖未提供时固定为 `NOT_EVALUATED`，不编写或启用近似替代。

### 工作流隔离与项目整理

- 强化工作流状态隔离，根目录兼容视图改为非权威原子副本，避免回写污染专属状态。
- 从活动运行时退役旧 WF-06/WF-07，仅保留 WF-01 至 WF-05 公开入口。
- 清理过时实例依赖，测试改用内联确定性 fixture，发布包允许不包含 `examples` 目录。
- 完成全量测试、快照完整性、MCP 作用域、Schema 及部署包契约验证。

<a id="release-1-2-4"></a>

## OligoSmith 1.2.4

### HiMat 终态输出契约校正

- 终态 MCP 结果的权威业务对象严格限制为 `data/type`，并在返回前按
  `primer-design-output.schema.json` 做确定性校验。
- `content[0].text` 继续由 MCP 代码生成与 `structuredContent` 完全相同的紧凑 JSON；
  不由模型转发、拼装或改写。
- 移除无标准依据、且 HiMat 未消费的终态 `_meta.oligosmith.workflowState`；普通等待态的既有
  服务端控制数据保持不变。
- 所有可能返回终态卡片的工具通过 MCP `outputSchema` 明确声明
  `primer_design_output` 分支；全量入口与按工作流入口使用同一份 Schema。
- 文档明确 HiMat 必须在 MCP 工具结果处理器中消费 `structuredContent`、生成 `block_data` 并
  跳过后续模型回复；该客户端适配不能由 MCP 提示词代替。

<a id="release-1-2-3"></a>

## OligoSmith 1.2.3

### HiMat 终态卡片 MCP 直返回归修复

- 修复 1.2.2 将终态 `content[0].text` 替换为确认语后，HiMat 无法生成输出卡片的问题。
- WF-01、WF-02、WF-03、WF-04 统一由 MCP `tools/call` 直接返回规定的紧凑 JSON；`structuredContent` 保留同值权威对象。
- 恢复终态 `_meta.oligosmith.workflowState`，由协议字段结束当前轮次并抑制助手文字，不让模型转发、拼装或重写卡片。
- 协议测试直接解析原始 MCP HTTP 响应，并校验文本 JSON、结构化对象和终态控制字段，不再把固定确认语作为成功条件。
- 全量入口及四个工作流受限入口统一使用中文工具说明和参数说明，移除参数 Schema 自动生成的英文展示标题；工具名、字段名与枚举值继续作为协议标识保留。

<a id="release-1-2-2"></a>

## OligoSmith 1.2.2

### HiMat 结构化卡片直达修复

- WF-01、WF-02、WF-03、WF-04 的终态卡片恢复为 MCP `structuredContent` 直达 HiMat。
- 移除模型转发、复述或重建终态 JSON 的依赖；`content[0].text` 只保留固定短确认语。
- 终态响应移除额外 `_meta` 控制结构，公开业务对象继续严格保持 `data/type`。
- MCP 协议测试与部署验收改为校验单一结构化卡片通道。

### 工作流 Skill 与受限 MCP 工具目录

- 新增 `oligosmith-wf01` 至 `oligosmith-wf04` 四份按需加载 Skill，每份只声明对应工作流的 MCP 依赖。
- 新增 `/mcp/wf01/` 至 `/mcp/wf04/` 四个受限入口；各入口只暴露自己的工具白名单。
- 受限入口重新注册现有工具函数并共用同一 `AgentDService`，不修改工作流实现、状态存储或输出协议。
- 原 `/mcp` 全量入口继续保留，支持灰度切换和快速回退。

<a id="release-1-2-1"></a>

## OligoSmith 1.2.1

### HiMat 终态卡片兼容修复

- WF-01、WF-02、WF-03、WF-04 统一通过同一个终态出口返回 `primer_design_output`。
- `structuredContent` 保留权威卡片对象，`content[0].text` 恢复为 HiMat 可直接解析的语义等值裸 JSON。
- 文本序列化只在 JSON 字符串内部使用标准 Unicode 转义屏蔽 URL 与 Markdown 活跃字符，避免自动链接破坏 JSON；解析后强制校验与权威对象完全相等。
- MCP 初始化指令和全部终态工具说明统一要求逐字交付裸 JSON，禁止 Markdown、代码围栏、解释和前后缀。
- 部署验收与回归测试覆盖四条工作流的双通道等值、终态控制元数据及 Markdown 注入边界。

<a id="release-1-2-0"></a>

## OligoSmith 1.2.0

### 工作流代码隔离

- WF-02 的 WF-01 子任务切换为完整冻结版本包，并通过固定输出契约读取结果。
- WF-03 与 WF-04 分别绑定独立代码包、状态目录、报告生成器和终态附件目录。
- 服务层仅保留版本路由、公共异常转换与旧任务兼容读取。
- 增加来源归档哈希、逐文件一致性和禁止导回共享实现的自动校验。
- 发布脚本纳入全部独立版本包，并校验包内文件清单。

<a id="release-1-1-13"></a>

## OligoSmith 1.1.13

### WF-04 三层判定链修复

- 统一目标分支预筛选与最终判定的阈值语义，避免有效结果在解析阶段被提前丢弃。
- 背景分支等待目标身份解析完成，并在比较前排除目标自身，消除分支调度造成的自命中误判。
- 宿主分支保留单条命中证据并标记为需复核；仅兼容配对证据升级为失败。
- 目录解析合并等价别名并增加元数据回退，同时补充策略标识和回归测试。

<a id="release-1-1-12"></a>

## OligoSmith 1.1.12

### WF-04 资源解析与报告可读性修复

- 资源选择器改为目录驱动的规范化解析，兼容大小写、Unicode、括号组合和配置别名。
- 别名解析唯一命中后写入资源路径和匹配方式；多重命中时明确失败，避免静默选择错误资源。
- WF-04 报告主视图直接列出明确失败项与必需未完成项，不再只显示笼统的最终状态。
- 新增资源别名和报告问题展示的回归测试。

<a id="release-1-1-11"></a>

## OligoSmith 1.1.11

### WF-03 P5→P2 调用链修复

- P5 改为调用 P2 批量服务，不再直接调用底层 adapter 或维护重复阈值。
- P2 内部新增 P5 专用两分支 profile，一次执行全部候选并逐候选返回状态、证据、资源版本和原因码。
- 本地资源不可用时恢复 P2 已有远端 provider；本地与远端均不可用时保持 `NOT_EVALUATED`。
- P5 将 P2 结果写入每个候选，再执行 R-19/R-21、评分和排名；候选数量或返回结构不一致时按契约错误失败关闭。
- 版本化资源解析会跳过前置占位条目并选用后续可用资源，同时保留首个未解析资源作为失败证据。
- 新增 P5→P2、远端回退、资源缺失、逐候选映射和 WF-03 终态回归测试。

<a id="release-1-1-10"></a>

## OligoSmith 1.1.10

### WF-03 / WF-04 复用 WF-01 输入输出边界

- WF-03 与 WF-04 附件输入统一复用 WF-01 的 `download_himat_file`：存在附件时固定执行“下载 → 传递不透明 `input_artifact_id` → 运行”，无附件且必填参数齐全时才允许直接运行。
- 删除 WF-03、WF-04 专用 `downstream` 分支；两者与 WF-01 一样直接复用下载结果顶层的 `input_artifact_id`，文件正文和服务器路径只在后端解析。
- 服务层为 WF-03、WF-04 分别完成确定性字段映射，持久化原始引用供不可变重入或重试复用；MCP 不读取附件正文，也不自行拼装运行输入。
- WF-03、WF-04 的 `run/retry/get` 统一生成 HTML 终态附件，并复用 WF-01 的 `primer_design_output` 顶层结构、双通道精确 JSON 与终止当前轮次语义。
- 工作流自身的执行状态与判定字段保留在报告中；输出卡片的 `status=COMPLETED` 只表示报告附件已经完成交付。
- 终态导出支持并发幂等写入；先以本地地址生成、后启用对象存储时，会按 WF-01 语义补发布而不重新执行工作流或重渲染内容。
- 部署验收同时检查 initialize instructions、下载工具下游映射、运行工具参数、工具描述与 `primer_design_output` 终态约定，避免客户端继续使用旧调用顺序或旧输出格式。

### WF-03 / WF-04 阅读版报告

- 两条路线的 HTML 终态附件统一采用 WF-02 的白底线性版式、关键摘要、状态标签、步骤列表、结果对比、线性明细和可折叠技术记录。
- 主阅读区不再展示整页原始 JSON；完整结构化结果以安全转义的数据块保留在同一附件中，继续支持审计与后续解析。
- 渲染版本写入导出元数据，旧附件会在查询时按新版本刷新；工作流执行顺序、持久化结果以及 `primer_design_output` 输入输出协议保持不变。

<a id="release-1-1-9"></a>

## OligoSmith 1.1.9

### WF-04 v0.1 确定性后端与 MCP 编排

- 新增六节点确定性后端，复用既有工具层并由后端负责节点顺序、并行边界、状态持久化与最终汇总。
- 新增专用 REST 与 MCP `run/retry/report` 入口；通用 Workflow 入口保留为兼容别名并调用同一后端实现。
- `execution_status` 与最终 `decision` 分离，模型只消费后端结果，不在工具外重新推断状态。
- 重试创建不可变子任务，已完成节点按输入指纹复用；新增输入、报告和重试 Schema、回归测试与部署验收检查。

### WF-03 HiMat 强制工具路由

- WF-03 参数齐全时改为“立即调用工具”的硬路由契约，禁止模型在调用前输出分析、规划或模拟结果；缺少必填字段时只询问该字段并停止。
- 相同约束同时写入 MCP initialize instructions 与 `run_wf03_internal_control` description，降低客户端忽略其中一个元数据通道时的失效风险。
- 部署验收新增 WF-03 工具名称、路由指令和工具描述三层检查；HiMat 服务升级后必须刷新 `tools/list`，支持 `tool_choice` 时不得使用 `auto`。

<a id="release-1-1-8"></a>

## OligoSmith 1.1.8

### WF-02 三文件松弛阶段编排修复

- `run_wf02_until_blocked` 改为服务端执行窗口，一次模型工具调用连续推进多个已持久化单元，避免 125 个组合按每次 8 个反复消耗 LLM 工具轮次。
- `tool_control` 增加 `CALL_TOOL_NOW/STOP_CURRENT_TURN`、完整 `next_arguments`、同轮继续和中间文本抑制字段；宿主同步获得 MCP `_meta` 工作流控制。
- 查询与推进结果统一返回当前 `resume_token` 和单调 `execution_progress`；陈旧 token 在窗口内自动恢复，无进展时停止而不是重复相同文字。
- 工具等待状态优先于 `RETRY_*` 文本，消除“立即重试”与 `stopCurrentTurn=true` 的冲突。
- 增加三文件 80/125 后继续推进、陈旧 token 恢复、等待边界、查询恢复和部署参数回归测试。

### WF-01 完成态编排修复

- `primer_design_output` 恢复为当前模型轮次的硬终态；原样输出卡片 JSON 后立即结束，查询、导出、修改和恢复工具仅响应用户后续明确请求。
- 创建工具同步声明终态规则，完成卡片移除会诱导模型主动读取报告的行动式文案。
- 最终卡片继续保持严格 `data/type`，内部 UUID、展示任务编号和停止控制改由 MCP `_meta` 交给宿主保存。
- WF-01 后续工具兼容内部 UUID 与 `WF01-YYMMDD-HHMM-XXXX` 任务编号；编号必须唯一解析，发生碰撞时拒绝猜测。
- 增加终态提示、工具描述、MCP 元数据、任务编号解析和碰撞拒绝回归测试。

<a id="release-1-1-7"></a>

## OligoSmith 1.1.7

### WF-02 阅读版报告重构

- HTML 从原始 JSON、Markdown 文本和 17 列宽表改为关键数据、检查步骤、方案对比、线性明细和可折叠次要参数。
- 运行状态、方案层级、证据级别和常见注意项转换为可读标签；机器原因码仅保留在不可见追踪属性及 canonical report 中。
- 完整嵌套核验记录不再写入 HTML/PDF 主阅读区，只提取综合状态与各模式状态，显著降低文件长度和视觉噪声。
- HTML 改为白底、单一弱强调色和横线分节，移除渐变、阴影、圆角卡片及彩色信息块；参数改为紧凑表格，桌面、手机和打印样式分别适配。
- PDF 同步改为可读摘要，按方案分页，增加页脚与页码；紧凑回退同样不再输出字典文本。
- CSV 保持结构化数据导出契约不变；canonical report 的完整字段和既有计算流程不变。
- 终态阶段只生成、发布并返回 HTML；CSV/PDF 在用户明确请求对应格式后才生成、发布，并只返回该格式的附件卡片。
- HTML、PDF、CSV 元数据使用独立渲染版本；旧批次默认查询只刷新 HTML，其他格式在明确请求时按版本刷新，无需重新执行流程。
- 新增 HTML 转义、原始字段抑制、失败原因可读化、多方案 PDF 及文件大小回归测试。

### WF-01 默认附件收口

- M6 默认附件由三份 HTML 和一份 ZIP 收口为两份 HTML 和一份 ZIP。
- 21 条规则验证报告不再单独生成、发布或出现在报告卡片，改为 ZIP 内的 HTML 成员。
- ZIP 不再重复收纳已独立交付的详情 HTML；其他辅助表格保持在 ZIP 内。
- 旧批次保留的独立规则报告元数据会在新报告卡片中被过滤；CSV/PDF 的明确请求后按需生成流程不变。
- WF-01 导出契约升级为 `wf01-prd-v0.2-export-v5`。

<a id="release-1-1-6"></a>

## OligoSmith 1.1.6

### WF-02 RustFS 附件发布修复

- WF-02 终态 HTML、CSV、PDF 复用 WF-01 的 RustFS 发布器和部署配置，写入同一 bucket/prefix。
- 对象名加入 WF-02 批次 ID，避免不同批次的固定导出文件名互相覆盖；卡片仍保留友好的显示文件名。
- 上传后校验文件大小与 SHA-256 元数据，校验成功后才把 RustFS 完整 URL 写入报告卡片。
- 已由旧版本完成的 WF-02 批次在首次查询时补传现有本地导出并更新报告元数据，无需重新执行流程。
- RustFS 未启用时保留绑定批次、格式和过期时间的本地签名只读地址作为部署兜底；标准 REST 报告接口继续受 Bearer JWT 保护。
- 增加对象名隔离、完整 URL、旧批次补传、发布失败可重试及签名地址访问边界测试。

<a id="release-1-1-5"></a>

## OligoSmith 1.1.5

### WF-02 N27–N32 报告导出稳定性修复

- 修复 N27 评分完成后，PDF 表格中的超长嵌套字段触发 ReportLab `LayoutError`，使任务持久化状态反复停留在 `READY_FOR_SCORING` 的问题。
- PDF 单元格改为 CJK 可换行、行内分页和有界可见内容；完整结构化值继续保留在 HTML 与 JSON 报告中。
- 极端布局仍失败时自动生成表格无关的紧凑分页 PDF，避免可恢复的展示层异常中断 N31–N32。
- 增加从 `READY_FOR_SCORING/N27` 重入、超长字段导出、紧凑回退和完成态幂等回放测试。

<a id="release-1-1-4"></a>

## OligoSmith 1.1.4

### WF-02 Step 5 资源路由修复

- 修复 WF-02 子任务进入 WF-01/P2 后的资源断链：目标专用条目缺失时使用已冻结输入快照，`cross/nt` 本地资源缺失时恢复既有远端服务回退；本地资源仍优先，远端错误仍保持 `NOT_EVALUATED`。
- 输入快照使用 SHA-256 版本，执行时只创建任务级临时 FASTA，并在调用结束后自动清理。
- 增加本地优先、远端成功、远端失败、输入快照物化与 WF-02 子任务载荷回归测试。

<a id="release-1-1-3"></a>

## OligoSmith 1.1.3

### WF-02 流程稳定性与交付修复

- 将推进调用保持为有界、可恢复执行，保存阶段游标和恢复令牌，重复请求不会让同一步骤推进两次。
- 上传文件失败会保留逐文件状态并阻止不完整输入进入后续阶段；临时子任务异常会保存为可重试状态，继续调用时复用原任务。
- 无候选结果被收敛为单目标失败并保留原始错误码，不再让整个批次以未分类异常终止。
- 外部依赖不可用或返回 `NOT_EVALUATED` 时保留真实评估状态和诊断信息，不再用空计数覆盖失败原因。
- 大型报告在 MCP 传输层使用紧凑视图和分页指引，完整数据仍通过受控报告与导出接口交付，避免超过 64 KB 响应上限。
- 删除旧版 WF-02 编排入口，统一由 `wf02_v01` 实现、REST/MCP 薄入口和不可变批次状态驱动。

<a id="release-1-1-2"></a>

## OligoSmith 1.1.2

### WF-01 移除输入表单并强制完整路线

- 删除模型侧 `prepare_wf01_design_input` 工具、两份输入表单 MCP 资源和 REST 表单端点。
- 上传链路收敛为 `download_himat_file → create_wf01_batch(input_artifact_id=...)`，不再要求兼容表单对象。
- 新任务的 `target_organism` 改为必填；缺少时先以普通文本询问，禁止自动降级为 `DESIGN_ONLY`。
- `create_wf01_batch` 建批后校验所有靶标均进入 `FULL_SPECIFICITY`，M4 必须调用 P2/BLAST。

### WF-01 主路线补齐 MAFFT 保守性

- 真实 MAFFT 输出现在校验记录数、ID、等宽比对和去 gap 序列一致性，并保存可追溯的输入/输出证据。
- M2 在比对上计算 gap-aware Shannon profile，按 0.95/150、0.92/120、0.90/90 三轮选择保守区，并为正向引物、反向引物和探针生成结合位点证据。
- R16 进入 WF-01 实际规则与 M6 报告；N<20、放宽轮次和全长回退为低置信度。单模板或 MAFFT 未真实完成时固定为 `NOT_EVALUATED`。
- 有可追溯保守性证据时，`FULL_SPECIFICITY` 使用保守性/热力学/P2 特异性/扩增子特征四维 30/25/25/20 评分；无证据时保守性不参评分，其余三维动态重归一化。
- M6 升级为 canonical report v2 / export v4，HTML、CSV 和科学数据导出均显式展示代表序列、保守区、R16 和候选级保守性。
- P2 仍是特异性唯一策略所有者，M4 不另行定义命中阈值；IQ-TREE 保持为可选、非阻塞的后续能力。

### WF-03 确定性后端与 MCP

- WF-03 改为与 WF-01/WF-02 一致的“LLM意图路由 + MCP薄入口 + 后端程序”结构，新增 `run_wf03_internal_control`、`retry_wf03_internal_control` 和 `get_wf03_report`。
- Workflow 只调用既有 P5；Entrez、C5、P1、P2、P6 的算法与阈值继续由 P5 所有，P3/P4 在独立 WF-03 中明确为不适用。
- 新增低保守性、序列不足、依赖缺失、无候选、P2失败和交叉二聚体失败的确定性兜底分类；P6 未评估不得映射为 PASS。
- 失败任务可从私有输入附件恢复并创建不可变 Step 1 重入版本，父 `job_id` 保留；补充备选管家基因时必须提供 RefSeq 或版本化同源序列。
- 新增 WF-03 REST 重入/报告接口、三份 JSON Schema、当前实现文档及后端/MCP 回归测试。

## 版本索引

| 版本 | 原发布说明 |
|---|---|
| [1.2.6](#release-1-2-6) | OligoSmith 1.2.6 |
| [1.2.5](#release-1-2-5) | OligoSmith 1.2.5 |
| [1.2.4](#release-1-2-4) | OligoSmith 1.2.4 |
| [1.2.3](#release-1-2-3) | OligoSmith 1.2.3 |
| [1.2.2](#release-1-2-2) | OligoSmith 1.2.2 |
| [1.2.1](#release-1-2-1) | OligoSmith 1.2.1 |
| [1.2.0](#release-1-2-0) | OligoSmith 1.2.0 |
| [1.1.13](#release-1-1-13) | OligoSmith 1.1.13 |
| [1.1.12](#release-1-1-12) | OligoSmith 1.1.12 |
| [1.1.11](#release-1-1-11) | OligoSmith 1.1.11 |
| [1.1.10](#release-1-1-10) | OligoSmith 1.1.10 |
| [1.1.9](#release-1-1-9) | OligoSmith 1.1.9 |
| [1.1.8](#release-1-1-8) | OligoSmith 1.1.8 |
| [1.1.7](#release-1-1-7) | OligoSmith 1.1.7 |
| [1.1.6](#release-1-1-6) | OligoSmith 1.1.6 |
| [1.1.5](#release-1-1-5) | OligoSmith 1.1.5 |
| [1.1.4](#release-1-1-4) | OligoSmith 1.1.4 |
| [1.1.3](#release-1-1-3) | OligoSmith 1.1.3 |
| [1.1.2](#release-1-1-2) | OligoSmith 1.1.2 |
| [1.1.1](#release-1-1-1) | OligoSmith 1.1.1 |
| [1.1.0](#release-1-1-0) | OligoSmith 1.1.0 |
| [1.0.3](#release-1-0-3) | OligoSmith 1.0.3 |
| [1.0.2](#release-1-0-2) | OligoSmith 1.0.2 |
| [1.0.1](#release-1-0-1) | OligoSmith 1.0.1 |
| [1.0.0](#release-1-0-0) | OligoSmith 1.0.0 |
| [0.3.38](#release-0-3-38) | OligoSmith 0.3.38 |
| [0.3.37](#release-0-3-37) | OligoSmith 0.3.37 |
| [0.3.31](#release-0-3-31) | OligoSmith 0.3.31 |
| [0.3.30](#release-0-3-30) | OligoSmith 0.3.30 |
| [0.3.29](#release-0-3-29) | OligoSmith 0.3.29 |
| [0.3.28](#release-0-3-28) | OligoSmith 0.3.28 |
| [0.3.27](#release-0-3-27) | OligoSmith 0.3.27 |
| [0.3.26](#release-0-3-26) | OligoSmith 0.3.26 |
| [0.3.25](#release-0-3-25) | OligoSmith 0.3.25 |
| [0.3.24](#release-0-3-24) | OligoSmith 0.3.24 |
| [0.3.23](#release-0-3-23) | OligoSmith 0.3.23 |
| [0.3.22](#release-0-3-22) | OligoSmith 0.3.22 |
| [0.3.21](#release-0-3-21) | OligoSmith 0.3.21 |
| [0.3.20](#release-0-3-20) | OligoSmith 0.3.20 |
| [0.3.19](#release-0-3-19) | OligoSmith 0.3.19 |
| [0.3.18](#release-0-3-18) | OligoSmith Agent D 0.3.18 |
| [0.3.17](#release-0-3-17) | OligoSmith Agent D 0.3.17 |
| [0.3.16](#release-0-3-16) | OligoSmith Agent D 0.3.16 |
| [0.3.15](#release-0-3-15) | OligoSmith Agent D 0.3.15 |
| [0.3.14](#release-0-3-14) | OligoSmith Agent D 0.3.14 |
| [0.3.13](#release-0-3-13) | OligoSmith Agent D 0.3.13 |
| [0.3.11](#release-0-3-11) | Agent D 0.3.11 发布说明 |
| [0.3.10](#release-0-3-10) | Agent D 0.3.10 发布说明 |
| [0.3.9](#release-0-3-9) | Agent D 0.3.9 发布说明 |
| [0.3.8](#release-0-3-8) | Agent D 0.3.8 发布说明 |
| [0.3.7](#release-0-3-7) | Agent D 0.3.7 发布说明 |
| [0.3.6](#release-0-3-6) | Agent D 0.3.6 发布说明 |
| [0.3.5](#release-0-3-5) | Agent D 0.3.5 发布说明 |

<a id="release-1-1-1"></a>

## OligoSmith 1.1.1

### HiMat 与 WF-02 稳定性修复

- HiMat 附件下载默认地址切换至 `https://himat.ws.scqcyz.cn/api/files/download`，避免把错误域名返回的 PNG 页面当作 FASTA 输入。
- WF-02 MCP 推进改为持久化有界执行：每次请求最多运行一个 WF-01 子任务或一个后续阶段，并通过 `next_action` 引导继续调用。
- 多靶标任务不再在单次 MCP 请求中连续执行全部子任务，避免超过 HiMat 五分钟请求上限；每一步继续复用同一批次和已保存的子任务状态。

<a id="release-1-1-0"></a>

## OligoSmith 1.1.0

### WF-01 保守型主路线

- 多同源序列靶标通过真实 MAFFT 比对和 gap-aware Shannon profile 选择保守区，并把可追溯证据写入 R16、候选评分和 M6 报告。
- 单模板、MAFFT 未完成或仅发生开发降级时明确返回 `NOT_EVALUATED`，不伪造群体保守性结论。
- P2 保持为必需且唯一的特异性策略所有者；M4 只消费 P2 证据，不维护第二套判定阈值。

### WF-02 v0.1 主路线

- 新增 N01–N32 多重 qPCR 面板流程：接收 2–5 个检测靶标，复用 WF-01 候选和完整 P1–P6 工具链完成组合、通道分配、P4 检查、放宽、平衡、排序和报告。
- 上传契约固定为“一个文件对应一个检测靶标；文件内允许该靶标的多条同源序列”，多记录文件作为一个 `alignment_target` 进入 WF-01/MAFFT。
- 内标不计入检测靶标重数，但占用一个荧光通道；P5 失败时记录 `FAILED_CONTINUE_WITHOUT_INTERNAL` 并继续生成不含内标的目标面板。
- P5 按内置 ACTB、GAPDH、18S rRNA RefSeq 配置从 NCBI Entrez 实时获取参考及至少 5 条同物种同源序列，并记录查询时间和 accession；猪 ACTB 使用 NCBI 当前验证的 `NM_001444420.1`，用户序列仍可作为显式覆盖。
- P4 是面板光谱与热力学兼容性的唯一判定来源；P6 作为软规则，不可评估时保留证据状态并继续。
- 新增 WF-02 REST/MCP、JWT 交付边界、不可变版本、HTML/CSV/PDF 报告和结构化导出。
- WF-02 完成阶段由智能体原样交付报告附件卡片，Top 3 的完整序列和科学证据统一由 HTML/CSV/PDF 文件承载；用户明确请求解读方案时再读取对应详情。

### 部署

- 服务、Python 包、Docker 镜像、Compose 默认标签、安装脚本和发布清单统一升级至 `1.1.0`。
- 源码部署包继续沿用 1.0.3 的 ZIP、包内文件清单和外部 SHA-256 双重校验结构。
- 部署目录移除独立 ACTB FASTA 必需项；Compose 传入 `NCBI_ENTREZ_EMAIL` 和可选 API Key，P5 获取失败仍由 WF-02 按既定策略降级。

<a id="release-1-0-3"></a>

## OligoSmith 1.0.3

### 修复内容

- WF-01 完整特异性路线在本地 `cross/nt` 背景库不可用时，可调用独立的 NCBI BLAST MCP 服务完成 `RELATED_BACKGROUND_NT` 检查。
- 本地版本化 BLAST 数据库仍具有最高优先级；远程服务只作为背景库回退，不替代现有靶标专库或宿主专库。
- 远程命中根据返回的 query 比对序列、坐标与本批 primer/probe 做确定性关联；无法关联或存在非同序列歧义时整次检查标记为 `NOT_EVALUATED`，不生成综合评分。
- NCBI 公共 `nt` 被明确记录为实时数据库 `NCBI_NT_PUBLIC_LIVE`，报告保留查询时间、数据库规模、服务地址及“非固定快照”语义。
- 已知靶标别名补充 NCBI 学名与 accession：CSFV 对应 `Pestivirus C` / `NC_002657.1`，胞内劳森菌对应 `Lawsonia intracellularis`，用于排除靶标自身命中。
- 特异性未完成时，报告会显示具体的数据库未配置或远程搜索失败原因。
- M4 引擎版本升级为 `wf01-v0.2-m4.6`；既有特异性阈值与三维评分权重不变。

### 部署配置

```dotenv
BLAST_SEARCH_SERVICE_URL=http://host.docker.internal:8090
BLAST_SEARCH_TIMEOUT_SECONDS=360
BLAST_SEARCH_MAX_SEQS=100
```

Docker Compose 已加入 Linux `host-gateway` 映射。部署前目录校验可将该远程服务视为 `cross:default:blast` 的背景搜索提供方；容器就绪检查会访问 `/health`，实际 MCP 搜索能力仍在每次 WF-01 搜索时再次验证。

### 科学边界

- 靶标专库与已提供宿主的宿主专库仍必须实际可用，否则对应模式保持 `NOT_EVALUATED`。
- 远程 NCBI `nt` 不是固定数据库快照；报告通过访问时间和数据库规模保留可审计性，但不能宣称完全可复现。
- 任一必需模式失败或未评估时，不伪造三维综合评分。

<a id="release-1-0-2"></a>

## OligoSmith 1.0.2

### 评分规则修正

- 评分策略升级为 `wf01-v0.2-three-dimension-document-framework-v4`。
- 普通探针相对 Tm 满足 `8℃≤探针 Tm－正反向引物平均 Tm≤10℃` 时统一得 100 分；区间外仍由 R07 硬性排除。
- 修复小数 Tm 在 8℃、10℃边界附近可能受浮点误差影响的问题。
- 扩增子长度采用固定分段评分：70 bp 为 70 分并保留边界 WARNING；`70<x≤150 bp` 为 100 分；`150<x≤200 bp` 为 70 分。
- 删除 151～200 bp 的线性递减算法，避免合法的 200 bp 候选被错误计为 0 分。

### 保持不变

- 模板最小长度保持 80 nt。
- 默认档、L1、L2 的扩增子范围分别保持 70～150 bp、70～180 bp、70～200 bp。
- 普通探针长度保持 22～32 nt。
- 三维评分权重、软规则扣分、两条运行路线和报告输出契约保持不变。

### 验证

- 增加探针相对 Tm 7.99℃、8℃、9℃、10℃、10.01℃及小数边界用例。
- 增加扩增子长度 70、71、150、151、180、200 bp 的分段评分用例。
- 项目全量自动化测试通过。

<a id="release-1-0-1"></a>

## OligoSmith 1.0.1

### 评分与排名修复

- 仅序列路线在 Primer3、硬规则、软规则和去重完成后生成正式“设计评分”与排名。
- 设计评分由热力学 55.5% 与扩增子特征 44.5% 组成，再应用软规则扣分；特异性明确标记为未评估，不参与该分数。
- 完整路线生成“综合评分”与排名，由热力学 35.7%、特异性 35.7% 与扩增子特征 28.6% 组成，再应用软规则扣分。
- 报告根据实际执行路线显示“设计评分”或“综合评分”；完整路线的特异性未完成或失败时，不生成或展示伪造的综合评分。

### WF-01 路线

- `create_wf01_batch` 使用 `sequence`、`target_organism`、`host`、`task_name` 明确参数。
- 仅提供合格序列时运行 `DESIGN_ONLY`；提供序列与靶标时运行 `FULL_SPECIFICITY`，提供宿主时追加宿主专库排除。
- 直接输入不产生中间弹窗；最终 `primer_design_output` 是唯一报告弹窗。
- 0 个候选、候选不足、BLAST 数据库未就绪或特异性异常仍继续生成可追溯的最终报告。
- 文件上传兼容流程保持不变。

### 兼容性与验证

- 胞内劳森菌 BLAST 短序列检索修复与报告导出修复已包含在本版本中。
- 本版本将上一份同名 `1.0.0` 覆盖包正式升级为 `1.0.1`，避免镜像标签、安装目录和回滚记录混淆。

### 报告排版

- 报告开头新增“原始模板序列”，按设计对象展示输入序列、长度和 5′→3′ 方向。
- 候选参数区和指标区删除“退火温度”，使用说明中也不再要求退火温度梯度。
- 候选卡片、参数表和汇总表增加宽度约束，修复评分与卡片右侧越出外层页面的问题。
- 优化任务编号展示；长编号在桌面端保持单行，窄屏下允许安全换行。
- 已在 1480px 桌面视口和 760px 窄屏视口通过真实浏览器排版核验，页面没有整页横向溢出。

### 最终输出卡片

- 首次设计完成、再次查看报告和请求下载地址统一返回 `primer_design_output`。
- `structuredContent` 与 `content[0].text` 使用完全相同的卡片 JSON，兼容 HiMat 的结构化与文本两条渲染链路。
- `get_wf01_batch` 在已完成批次上直接重放报告卡片；不再让模型把附件改写为 Markdown 链接。
- `get_wf01_export` 按需生成 CSV/PDF 后重新读取最新批次，将新增附件一并放入报告卡片。

<a id="release-1-0-0"></a>

## OligoSmith 1.0.0

### 流程调整

- 主 MCP 工具精简为 11 个，Primer3 保持为服务内部能力。
- 创建 WF-01 批次后自动运行到报告完成或真正需要用户补充输入的位置。
- 输入规范化与文件物化统一收口到 Service 层，MCP 不再重复解析。
- 仅输入合格序列时运行设计流程并将特异性标记为未评估；提供靶标时运行靶标命中与综合背景检测，宿主存在时追加宿主专库排除。
- 直接输入改为 `sequence / target_organism / host / task_name` 明确参数，由后端确定 `DESIGN_ONLY` 或 `FULL_SPECIFICITY`，不再弹输入表单。
- 直接输入的阶段异常和特异性缺失不产生中间弹窗；最终 `primer_design_output` 是唯一报告弹窗。
- 后端按实际执行路径生成普通文字路线回执，报告弹窗结构中不混入路线说明。

### 候选不足

- 少于 3 个候选不再弹出确认表单，也不阻塞报告生成。
- 1–2 个候选按现有结果生成报告并标记“候选不足”。
- 0 个候选正常生成空结果报告，记录 Primer3 未生成、硬规则淘汰或特异性淘汰等可确定原因。
- 所有硬规则、软规则与特异性阈值保持不变。

### 评分与排名

- 仅序列路线在 Primer3、硬规则、软规则和去重完成后生成正式“设计评分”与排名，不再把 M3 的已有计算结果以空分数写入报告。
- 设计评分由热力学与扩增子特征按原权重归一化后组成：热力学 55.5%，扩增子特征 44.5%；软规则扣分继续生效，特异性明确标记为未评估且不参与该分数。
- 完整特异性路线继续生成“综合评分”与排名：热力学 35.7%，特异性 35.7%，扩增子特征 28.6%，再应用软规则扣分。
- HTML 报告会根据每个靶标的实际路线显示“设计评分”或“综合评分”；批量任务混合两条路线时分别说明评分口径，避免跨口径误比较。

### 维护性

- 删除未公开的冗余 MCP 包装、未调用配置/示例函数和未使用 import。
- 移除独立 NCBI 远程复核链路，BLAST 使用服务器本地版本化数据库。
- 靶标短寡核苷酸检索继续使用宽松 E-value 以保留大型数据库中的 20 nt 精确命中，同时在 BLAST 命令层按现有判定表过滤覆盖度或一致性低于 95% 的无用局部匹配，避免 M4 原始证据和 M6 报告异常膨胀。

<a id="release-0-3-38"></a>

## OligoSmith 0.3.38

本版本基于 0.3.37，仅修复 WF-01 输入表单原样确认时可能出现的误校验。

### 表单确认兼容修复

- `primer_design_input_update` 仍是启动预检所必需的结构化确认消息。
- 用户未修改任何字段时，兼容 `data={}` 和 `data.designInput={}`，服务端从原始
  `primer_design_input` 继承全部设计字段。
- 普通按钮文字或缺少结构化确认消息仍不能启动预检。
- 若第一次预检只是模型选错了历史表单对象，允许从当前上下文复用原始表单和当前更新并修正一次，
  不再要求用户重复点击“确认开始设计”。

### 保持不变

- 0.3.37 的 `primer_design_output` JSON 与 HiMat Output 表单链路保持不变。
- 上传文件链路、Primer3、规则、M2-M6 服务流程、新版报告格式及数据库配置保持不变。
- 0.3.35 仍是不可部署的中间版本。

<a id="release-0-3-37"></a>

## OligoSmith 0.3.37

本版本以 0.3.31 为运行逻辑基线，仅保留后续完成的报告格式优化。

### 保留：报告格式

- 保留候选汇总表、候选详细参数表、失败数据高亮和输入文件信息等报告展示改进。
- 保留报告生成所需的数据整理与 HTML 渲染逻辑。

### 回退：规则与 MCP

- 撤销 0.3.32 引入的 R05/R06 固定引物对普通探针再生成逻辑，恢复 0.3.31 的 Primer3 与 M2 行为。
- 撤销 0.3.33–0.3.34 的 MCP 提示词、MCP 输出包装和单阶段推进/超时调整，恢复 0.3.31 的 MCP 与服务行为。
- 数据库目录和服务器上的胞内劳森菌资源不属于本次代码回退范围。

### 最终输出兼容修复

- 仅调整 `primer_design_output` 的 MCP 文本通道：`content[0].text` 返回与
  `structuredContent` 完全相同的紧凑 JSON，供 HiMat 渲染报告预览和下载表单。
- 输入、交互、规则、Primer3、M2-M6 服务流程、新版报告格式和数据库配置均保持不变。

0.3.35 是回退过程中的中间版本，不作为本版本的发布或部署基线。

<a id="release-0-3-31"></a>

## OligoSmith 0.3.31

### WF-01 双输入模式修复

- WF-01 设计输入现在明确支持两条并列路径：上传 FASTA 文件，或直接粘贴/手动输入序列。
- 直接序列在 `prepare_wf01_design_input` 阶段保存为服务端内部 artifact；表单只携带
  `sourceFile.inputArtifactId`，不会把序列正文放入后续模型上下文。
- 用户确认后，`preflight_wf01_batch` 和 `create_wf01_batch` 可从原始表单自动恢复直接序列引用，
  不再错误要求 HiMat 上传文件引用，也不要求模型再次复制 FASTA。
- 上传文件路径保持兼容：先通过 `download_himat_file(file_id)` 取得 `input_artifact_id`，再传给预检和建批。
- 新增 artifact、MCP 输入归一化、JSON Schema 和端到端协议测试，覆盖裸序列、FASTA 文本、上传文件及
  引用冲突校验。

### 安全与兼容性

- 两种输入方式都使用同一套不透明 artifact 引用和后端解析机制。
- MCP 结果、审计日志和前端确认表单均不回显完整生物序列。
- 科学计算、报告、附件发布和已有上传文件流程保持不变。

<a id="release-0-3-30"></a>

## OligoSmith 0.3.30

### 中途交互裸 JSON 修复

- `agent_interaction` 继续通过 MCP 顶层 `structuredContent` 返回，HiMat 可直接据此生成
  `block_data` 并渲染用户决策弹窗。
- 同一 `agent_interaction` 现在也以紧凑裸 JSON 原样写入 `content[0].text`，兼容当前仍将
  MCP 文本结果交给模型生成助手消息的 HiMat 编排链。
- MCP 指令和相关工具说明要求模型逐字输出该 JSON，并立即结束轮次；禁止改写成自然语言问题、
  文字版选项、Markdown 代码块或添加任何前后缀。
- 新增协议测试，校验文本通道能解析为与 `structuredContent` 完全相同的对象，且不包含 MCP
  外层包装字段。

### 未改变的范围

- `primer_design_input` 仍使用结构化表单和同值 JSON 兼容通道。
- `primer_design_output` 仍由 `structuredContent` 驱动附件卡片，`content[0].text` 只返回中文
  完成总结和每个靶标的真实 Top 3，不重复输出整份结果 JSON。
- 科学计算、报告生成、附件发布、输入 artifact、下载地址和用户交互回复协议保持不变。

<a id="release-0-3-29"></a>

## OligoSmith 0.3.29

### 最终结果展示

- `primer_design_output` 继续通过 MCP 顶层 `structuredContent` 返回，HiMat 的附件卡片协议、
  报告地址和 RustFS 发布逻辑保持不变。
- 最终工具结果不再把整份 `primer_design_output` JSON 复制到 `content[0].text`，避免宿主已经
  渲染附件卡片后，模型又把同一 JSON 作为普通文字重复输出。
- `content[0].text` 改为服务端确定性生成的中文完成总结；模型只需原样输出该总结，不得复述
  `structuredContent` 或自行重建候选。
- 完成总结按每个靶标展示真实排名前三的候选，包括候选编号、得分、结论、证据等级、正反向引物、
  探针和扩增子长度。不同靶标的排名不混合比较。

### 未改变的范围

- `primer_design_input` 表单兼容通道、输入去重、原始上传文件名和静默工具链保持不变。
- `agent_interaction` 继续只通过结构化通道返回，不生成重复文字选项。
- M2-M6 科学计算、报告生成、附件发布、下载 URL、任务数据和输入 artifact 均保持兼容。

<a id="release-0-3-28"></a>

## OligoSmith 0.3.28

### 输入表单

- 为 `prepare_wf01_design_input` 增加服务端重复调用保护。同一调用方在同一轮次内短时间重复提交相同参数时，仅第一次返回可渲染表单，后续调用返回 `DUPLICATE_SUPPRESSED` 控制结果。
- 支持通过 MCP 请求 `_meta.turnId` 及兼容的消息、会话、线程标识划分去重范围；没有显式轮次标识时使用短时间窗口兜底。
- `prepare_wf01_design_input` 要求传入 `file_names`。单文件显示上传时的原始文件名；一次上传多个文件时显示“多个文件”。
- 移除 `uploaded.fasta` 伪文件名兜底；旧 REST 请求没有文件名时显示“未获取文件名”。

### 最终输出表单

- 修复 M6 完成结果被包装为 `{"content":[],"structuredContent":...}` 后无法被 HiMat 渲染的问题。
- 进程内调用直接返回裸 `primer_design_output`；标准 MCP 响应继续将同一业务对象写入顶层 `structuredContent`，并在 `content[0].text` 提供 JSON 镜像。
- 最终回复约束为唯一的助手裸 JSON 输出，不允许附加进度说明、代码围栏或 MCP 包装字段。

### 流程稳定性

- 用户确认后的下载、预检、建批和 M2-M6 执行改为静默工具链，禁止在中间工具调用之间重复输出历史进度。
- 预检兼容 HiMat 转发的 `confirmed=true` 表单快照；输入错误不再向模型返回完整表单示例，并明确禁止在当前轮次自行重建输入或重试。

### 部署

- 默认镜像标签、OCI 镜像版本、发布清单及安装完成提示同步升级为 `0.3.28`。
- 部署包继续采用 Docker Compose 源码包格式，包含 SHA-256 文件清单和外部校验文件。

<a id="release-0-3-27"></a>

## OligoSmith 0.3.27

### 输入确认表单兼容性测试

- `prepare_wf01_design_input` 继续通过 MCP `structuredContent` 返回严格的
  `type + data` 业务对象。
- 同一份表单 JSON 也写入 `content[0].text`，兼容当前只把 MCP 文本内容交给
  模型的 HiMat 客户端，用于验证此前的字段缺失判断是否由 `content=[]` 引起。
- 文本镜像只包含设计输入字段和上传文件名，不包含 FASTA 正文、文件下载地址、
  服务器路径或 `input_artifact_id`。
- 增加服务端重复调用保护：同一已认证调用方在同一会话轮次内短时间重复提交完全相同的
  `prepare_wf01_design_input` 参数时，只有第一次返回可渲染表单；后续调用返回
  `DUPLICATE_SUPPRESSED` 控制结果，避免出现两个输入表单。
- MCP 客户端可通过请求 `_meta.turnId`（也兼容常见 message/conversation/thread/chat ID）
  明确轮次边界；未提供时使用短时调用窗口兜底。不同认证调用方不会互相去重。
- `prepare_wf01_design_input` 新增必填 `file_names` 数组。单文件的
  `sourceFile.fileName` 使用附件原始显示名；一次上传多个文件时固定显示“多个文件”。
- 移除 `uploaded.fasta` 伪文件名兜底；旧 REST 兼容请求未提供任何文件名时显示“未获取文件名”。
- 修复 M6 完成结果在 HiMat 中被错误序列化为
  `{"content":[],"structuredContent":...}` 的问题。进程内工具调用现在直接返回裸
  `primer_design_output`，标准 MCP 响应仍把它放入顶层 `structuredContent`。
- 同一份最终输出 JSON 镜像到 `content[0].text`。当前 HiMat 文本兼容通道必须将该字符串原样作为
  唯一的助手裸 JSON 输出，不得添加进度说明、代码围栏或 MCP 包装字段。
- 用户确认后的下载、预检、建批和 M2-M6 调用改为明确的静默工具链约束；中间工具调用采样不得产生或
  复述任何进度文字，避免 HiMat 将旧助手内容逐轮拼接后形成指数式重复。
- 预检兼容 HiMat 转发的 `confirmed=true` 表单快照；确认仍由独立的
  `primer_design_input_update` 校验。输入错误不再向模型返回完整表单示例，并禁止当前轮次自行重建和重试。
- `download_himat_file`、输出表单与报告发布工具保持不变。

<a id="release-0-3-26"></a>

## OligoSmith 0.3.26

本版本新增 HiMat 上传文件的服务端输入传递能力。模型只调度文件引用，不读取 FASTA 正文；报告生成、附件发布和现有输出下载地址保持不变。

### HiMat 输入文件工具

- 新增 MCP 工具 `download_himat_file(file_id)`。
- 下载基地址默认为 `https://himat.ws.scqcyz.cn/api/files/download`，可通过 `HIMAT_FILE_DOWNLOAD_BASE_URL` 覆盖。
- 工具只返回 `input_artifact_id`、文件名、类型、大小和 SHA-256，不返回文件正文、本地路径或源下载地址。
- 输入文件保存在 `AGENTD_STATE_DIR/input-artifacts/`，预检和建批可复用同一个引用。

### WF-01 输入边界

- `preflight_wf01_batch` 与 `create_wf01_batch` 支持 `payload.input_artifact_id`。
- FASTA 下载、UTF-8 解码和解析全部在服务端内部完成。
- 模型可见的预检结果移除 `template.sequence` 与 `templates[].sequence`，保留记录名、长度、校验和、来源文件和校验状态。
- 内部 REST/服务兼容路径继续支持原有内联序列输入。

### 部署与验收

- 新增 `HIMAT_FILE_DOWNLOAD_BASE_URL`、`HIMAT_FILE_DOWNLOAD_TIMEOUT_SECONDS` 和 `AGENTD_INPUT_FILE_MAX_BYTES` 配置。
- 部署验收脚本改为检查公开工具 `download_himat_file`，不再错误检查仅供内部调用的 `run_wf01_stage`。
- MCP 公开工具总数为 27。

### 未改变的范围

- M2-M7 科学阶段执行方式不变。
- 报告生成、RustFS 附件发布、报告 URL 和导出工具不变。
- MCP 鉴权方式不变。

<a id="release-0-3-25"></a>

## OligoSmith 0.3.25

本版本进一步精简 WF-01 人员阅读报告中的 BLAST 摘要展示，不改变科学计算或底层审计数据。

### 报告精简

- 从前十名候选报告的三类 BLAST 摘要中移除“数据库版本”列。
- 从科学过程详情报告的候选 BLAST 摘要、特异性检测汇总和特异性命中明细中移除“数据库版本”列。
- 保留检测结论、命中数量、代表性命中、相似度、覆盖率和 E-value。

### 报告排版

- 修复科学过程详情报告章节标题因文字长度不同而横向错位的问题。
- 展开符号、标题说明和记录数改为固定三列布局，所有章节标题统一左对齐。

### 数据追溯

- 规范报告对象和科学数据包中的数据库版本元数据继续保留。
- 本次仅调整 HTML 阅读层，不修改候选生成、规则判定、特异性计算或综合评分。

<a id="release-0-3-24"></a>

## OligoSmith 0.3.24

本版本补全 WF-01 候选交付报告与科学过程详情报告中的 R-21 必需数据展示。保守性评分按当前确认的发布范围暂缓，不参与本版本 R-21 通过条件，也不计入综合评分。

### 候选详情

- 每套候选新增关键 ΔG 表，展示正向引物、反向引物和 TaqMan 探针的发卡、同源二聚体及 3′ 端自身二聚体 ΔG。
- 补充正反向引物异源二聚体和 3′ 端异源二聚体 ΔG，统一使用 `kcal/mol`。
- 每套候选新增三类 BLAST 命中摘要，分别展示靶标覆盖、宿主排除和近缘种/背景序列检查。
- BLAST 摘要包含检测结论、数据库版本、命中数量、代表性物种或序列、相似度、覆盖率和 E-value。
- E-value 使用科学计数法展示，避免极小数被格式化为 `0`。

### R-21 完整性校验

- R-21 改为逐项验证真实内容，不再仅检查对象是否存在。
- 必检内容包括引物、探针和扩增产物序列，三条寡核苷酸的 Tm，关键 ΔG，扩增子长度以及 BLAST 摘要或明确的前置排除原因。
- 缺失任一必需数据时，R-21 标记为失败，规范报告标记为不完整。
- 保守性评分明确记录为“本版本暂缓”，不伪造结果，也不作为本版本 R-21 失败条件。

### 报告范围

- 前十名交付报告与科学过程详情报告均使用相同的候选热力学和 BLAST 摘要组件。
- 候选生成、科学工具计算、既有规则判定及综合评分算法未在本版本中改变。

<a id="release-0-3-23"></a>

## OligoSmith 0.3.23

本版本重构 WF-01 科学过程详情报告的阅读结构，不改变候选生成、检测、评分或排序算法。

### 报告排版

- 新增报告目录，并显示每个章节的记录数量。
- 数据量较大的章节使用可折叠结构，避免数百个候选和数千条检测结果同时铺满页面。
- 全部候选改为逐候选详情：分别展示正向引物、反向引物、TaqMan 探针和扩增子序列。
- 逐项检测按候选分组，候选标题直接汇总通过、未通过和需关注的项目数量。
- 前十名交付报告与科学过程详情报告的用途在页面顶部明确区分。

### 可读名称

- 候选统一使用 `候选 001（最终第 1 名）` 这类稳定编号。
- FASTA 标题中的 accession、病原简称、设计区域和坐标被整理为可辨认的靶标名称。
- 补全不成对的中文括号，并规范 `5′UTR`、`3′UTR` 等区域名称。
- 时间统一转换为北京时间，文件大小使用 KB/MB，长校验值采用缩略展示并保留完整提示。
- 内部阶段码、状态码和常见未完成原因转换为中文说明。
- `Primer3 原始罚分` 改为 `Primer3 配对罚分（越低越优）`，Tm、GC 和 E-value 增加解释。

### 数据完整性

- HTML 仍保留全部候选、全部逐项检测、全部评分和全部特异性命中。
- 科学工具数据包内的九份 CSV 和服务端不可变原始证据继续保留。
- 本次只调整报告阅读层，不修改科学计算、规则判定和评分结果。

<a id="release-0-3-22"></a>

## OligoSmith 0.3.22

本版本恢复 HiMat 最初约定的 WF-01 输入弹窗业务格式，并把模型轮次控制规则收敛到 MCP 服务。

### 输入弹窗格式

- `primer_design_input` 顶层严格只返回 `type` 和 `data`。
- 不再向 HiMat 返回 `flowControl`、`interactionId`、`deduplicationKey` 或 `onDuplicate`。
- MCP `structuredContent` 和 REST 兼容接口均使用相同格式。
- JSON Schema 使用 `additionalProperties=false` 固定该对外契约。

### MCP 调用规则

- 同一个用户新设计请求最多调用一次 `prepare_wf01_design_input`。
- 无论字段是否完整，返回后不得重试、再次调用或更换字段名调用。
- 返回后不得复述 JSON、输出助手文字或继续调用其他工具；必须立即结束当前轮次。
- 上述要求写入 MCP 服务 instructions 和工具 description，不再依赖返回 JSON 中的控制字段。

### 兼容性

- 新表单只生成 `type + data`。
- MCP 输入适配层仍可读取 0.3.21 及更早版本已经保存在对话中的扩展表单，保证滚动升级期间
  已打开表单仍能完成预检和创建批次。
- M1-M6 批次内部的 `agent_interaction` 及其交互编号不受此次调整影响。
- 0.3.21 增加的候选报告、科学过程详情报告和科学工具数据包保持不变。

<a id="release-0-3-21"></a>

## OligoSmith 0.3.21

本版本为 WF-01 增加独立的科学过程详情报告和人类可读科学工具数据包。

### 默认交付附件

M6 完成后自动生成并上传三个附件：

1. `YYYY-MM-DD_HH-mm-ss_引物探针设计报告.html`
2. `YYYY-MM-DD_HH-mm-ss_科学过程详情报告.html`
3. `YYYY-MM-DD_HH-mm-ss_科学工具原始数据.zip`

Top10 候选报告仍是主附件。过程报告展示工具、输入参数、全部候选、逐项检测、
评分、特异性命中及未完成项目。

### 人类可读数据

- 用户下载的 ZIP 只包含中文说明、科学过程 HTML 和九份中文表头 CSV。
- ZIP 不包含 JSON、程序代码、内部字段名、命令行、代码式日志或访问密钥。
- MAFFT、Primer3、BLAST 等不可变原始证据继续保存在服务端内部，用于完整性校验和审计。
- 三个默认附件均在 M6 返回前上传 RustFS，并将完整下载 URL 写入
  `primer_design_output.data.attachments`。

### 兼容性

- `primaryAttachmentId` 仍为 `attachment_html`。
- CSV 和 PDF 继续按需生成。
- 内部 UUID、用户任务编号和原有 canonical report 数据结构保持不变。
- `install.sh` 通过 `bash` 调用预检和验收脚本，Windows 上传并解压后不再依赖子脚本的可执行位。

<a id="release-0-3-20"></a>

## OligoSmith 0.3.20

本版本为 WF-01 增加便于人工辨认的任务编号，同时保留 UUID 作为内部主键。

### 任务编号

格式：`WF01-YYMMDD-HHMM-XXXX`

示例：`WF01-260808-1306-8EC6`

- `WF01`：工作流类型。
- `260808`：北京时间创建日期。
- `1306`：北京时间创建时刻（时、分）。
- `8EC6`：内部 UUID 的前四位。

### 兼容性

- `batch_id` 继续使用完整 UUID，API 路径、持久化目录和工具调用参数不变。
- 新增 `batch_code`，供用户、HiMat 摘要和报告展示。
- `conversation_context.activeBatchCode` 提供当前任务编号；
  `activeBatchId` 仍保留给编排器调用后续工具。
- HTML 报告首页和完成消息摘要显示任务编号。
- 历史批次在读取时根据原 `batch_id` 与 `created_at` 自动计算任务编号，无需迁移文件。

<a id="release-0-3-19"></a>

## OligoSmith 0.3.19

本版本修正 HiMat 附件地址契约。Agent D 在 RustFS 上传及完整性校验成功后，将完整、可直接下载的
RustFS 对象 URL 保存到导出元数据，并原样用于 `primer_design_output` 和 `get_wf01_export`。

### 变更

- `attachments[].fileUrl` 从容器兼容路径 `/data/<fileName>` 改为完整 RustFS URL。
- URL 结构为 `<RUSTFS_PUBLIC_BASE_URL>/<bucket>/<object key>`。
- 中文文件名在 `fileUrl` 中按 UTF-8 百分号编码；`fileName` 仍保持中文。
- HTML、PDF、CSV 和科学原始数据 ZIP 共用同一发布地址逻辑。
- `get_wf01_export` 的 `file_url`、`download_url` 和 HTML `preview_url` 使用持久化的发布 URL，
  不再根据本地文件名重新拼接地址。
- 新增 `RUSTFS_PUBLIC_BASE_URL` 部署变量，默认值为
  `https://rustfs.ne.scqcyz.cn`。

生产示例：

```json
{
  "fileName": "2026-08-08_11-07-17_引物探针设计报告.html",
  "fileUrl": "https://rustfs.ne.scqcyz.cn/wens/data/2026-08-08_11-07-17_%E5%BC%95%E7%89%A9%E6%8E%A2%E9%92%88%E8%AE%BE%E8%AE%A1%E6%8A%A5%E5%91%8A.html"
}
```

<a id="release-0-3-18"></a>

## OligoSmith Agent D 0.3.18

### 附件发布修复

- 新增 RustFS S3 发布器，M6 HTML 以及按需导出的 PDF、CSV、ZIP 均可上传至
  `wens/data/<fileName>`。
- `primer_design_output` 的 JSON 结构不变，`fileUrl` 继续为 `/data/<fileName>`。
- 上传后使用 `HEAD Object` 校验文件大小和 SHA-256 元数据。
- RustFS 启用时，HTML 未上传成功不会返回可下载的完成卡片。
- 部署验证新增 RustFS `HEAD Bucket` 连通性与权限检查。
- RustFS 账号仅通过 `deploy/.env` 注入，发布包不包含凭据。

<a id="release-0-3-17"></a>

## OligoSmith Agent D 0.3.17

发布日期：2026-08-07

### 本次更新

- HTML 交付报告改用报告生成时的北京时间命名，格式为
  `YYYY-MM-DD_HH-mm-ss_引物探针设计报告.html`，不再使用默认任务名或批次 UUID 作为用户文件名。
- 按需生成的 PDF 使用相同的时间前缀，方便将同一次设计的报告归组。
- 时间中的冒号改为连字符，确保 Linux、Windows 和对象存储均可安全保存和下载。
- CSV 候选结果与科学工具原始数据 ZIP 保持原有任务名命名，不改变既有数据附件契约。
- 包含 0.3.16 的前 10 候选精简报告、逐项检测说明和中文警告原因。

### 数据与升级

- 继续使用原有 `agentd-jobs` Docker 持久化卷，无需迁移历史任务。
- 历史文件名不会自动修改；升级后新运行 M6 生成时间命名的报告。

### 验证

- Python 全量自动化测试通过。
- 部署包内置 `PACKAGE-MANIFEST.sha256`，并提供独立 ZIP SHA-256 校验文件。

<a id="release-0-3-16"></a>

## OligoSmith Agent D 0.3.16

发布日期：2026-08-07

### 本次更新

- 将 M6 默认 HTML 改为面向实验人员的精简交付报告，每个设计对象只展示综合排名前 10
  的候选，不再嵌入完整 canonical JSON，也不展示内部规则编号、字段名、检索记录和运行日志。
- 每套候选展示扩增子长度与 GC、综合评分、PCR 设计评分、特异性评分、靶标命中覆盖率、
  保守性状态、退火温度状态、两条引物温差、正反向引物、TaqMan 探针及扩增子序列。
- 每套候选逐项列出已通过、需关注、未通过和未完成的检测项目，并使用中文解释检测含义；
  软性提醒与硬性未通过明确区分，提醒项同时说明具体触发原因。
- 保留完整 canonical report 作为服务端审计与 CSV/PDF 按需导出的数据源；本次不调整过程
  详情报告，也不改变科学评分与排序公式。
- 包含 0.3.15 的 MCP 完成结果结构修复和 0.3.14 的同名附件解析修复。

### 数据与升级

- 继续使用原有 `agentd-jobs` Docker 持久化卷，不需要迁移历史批次或报告文件。
- 已经生成的历史 HTML 不会自动改写；升级后新运行 M6 生成新版交付报告。

### 验证

- Python 全量自动化测试通过。
- 部署包内置 `PACKAGE-MANIFEST.sha256`，并提供独立 ZIP SHA-256 校验文件。

<a id="release-0-3-15"></a>

## OligoSmith Agent D 0.3.15

发布日期：2026-08-07

### 本次更新

- 固定 M6 完成结果的 MCP `structuredContent` 为 `PrimerDesignOutputData`：顶层仅包含
  `data` 和 `type`，并按 `data`、`type` 顺序构造。
- Agent D 不生成 `block_data` 或 `blockId`；这两个字段继续由 HiMat 宿主在消息存储层添加。
- 新增严格回归测试，验证完成结果没有 `ui_message`、`block_data` 或 `blockId` 等额外包装。
- 包含 0.3.14 的同名附件修复：`/data/<文件名>` 在重复任务中提供最近实际生成的文件，供
  HiMat 接管并转存至对象存储。

### 数据与升级

- 继续使用原有 `agentd-jobs` Docker 持久化卷，不需要迁移历史批次或报告文件。

### 验证

- Python 自动化测试：170 项通过，另有 9 个子测试通过。
- 部署包内置 `PACKAGE-MANIFEST.sha256`，并提供独立 ZIP SHA-256 校验文件。

<a id="release-0-3-14"></a>

## OligoSmith Agent D 0.3.14

发布日期：2026-08-07

### 本次更新

- 保持宿主附件接管协议不变：`fileUrl`、`file_url`、`download_url` 和 HTML
  `preview_url` 继续返回 `/data/<文件名>`，不拼接域名或批次路径。
- 修复重复运行同名任务后 `/data/<文件名>` 返回“报告附件文件名不唯一”的问题。
- 平面附件源地址现在忽略文件已经丢失的陈旧元数据，并返回最近实际生成的同名文件，供宿主
  后端立即抓取后转存至对象存储。
- 新增两个批次生成同名 HTML 时的回归测试。

### 数据与升级

- 继续使用原有 `agentd-jobs` Docker 持久化卷，不需要迁移任务或报告数据。
- 升级后重新执行一次报告/科学数据导出，宿主即可通过原有 `/data/<文件名>` 协议接管附件。

### 验证

- Python 自动化测试：170 项通过，另有 9 个子测试通过。
- 部署包内置 `PACKAGE-MANIFEST.sha256`，并在 ZIP 同目录提供独立 SHA-256 校验文件。

<a id="release-0-3-13"></a>

## OligoSmith Agent D 0.3.13

发布日期：2026-08-07

### 本次更新

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

### 数据与升级

- 继续使用 `agentd-jobs` Docker 持久化卷，不应删除或重建该卷。
- 原有任务状态和报告目录保持兼容。
- 大体量科学原始数据只保存在 artifact 文件中；普通 MCP 响应仅返回清单和下载地址。
- 本版本尚未处理“智能体自行构造但服务器不存在的报告附件地址”这一后续问题。

### 验证

- Python 自动化测试：165 项通过。
- 部署包内置 `PACKAGE-MANIFEST.sha256`。
- ZIP 同目录提供独立 SHA-256 校验文件。

<a id="release-0-3-11"></a>

## Agent D 0.3.11 发布说明

### 新版 WF01 v0.2 接入 MAFFT

- 多条输入序列默认作为同一靶标：M1 保留全部模板，M2 调用 MAFFT 并等待完成。
- MAFFT 输出由 Python 计算保守区，Primer3 按“保守区 × 用户/default/L1/L2 去重参数档”执行。
- 单条输入序列跳过 MAFFT，直接使用整条模板进入 Primer3。
- 候选的引物、探针和扩增子坐标会从保守区局部坐标映射回首条参考序列。
- M2 结果新增 MAFFT 状态、比对方法、序列数、保守区及保守性元数据，便于审计。

### 输入兼容

- `sequence_mode` 默认值为 `alignment_target`。
- 原“多记录分别作为独立靶标”的行为仍可通过 `sequence_mode=independent_targets` 显式启用。

### 部署边界

- 本版本不新增数据库依赖，也不要求继续下载数据文件。
- 服务器镜像沿用现有 `mafft_executable=mafft` 配置；部署后只需验证容器内 MAFFT 可执行并运行 WF01 多序列冒烟测试。

<a id="release-0-3-10"></a>

## Agent D 0.3.10 发布说明

### 输出范围修正

- 正常完成结果默认展示每个靶标按 M5 最终排名得到的 Top10 候选。
- 最终摘要优先使用 M5 的 `top10`，不再误用中间阶段候选。
- HTML 不可用时，文字兜底同样默认只返回 `default_results` 中的 Top10。
- 只有用户明确提出“查看全部候选”时，智能体才执行
  `all_candidates_on_request`，分页读取并展示全部可交付候选。
- MCP 指令明确禁止智能体在没有用户请求时主动加载完整候选池。

### 保留的 0.3.9 修复

- HTML 仍是主交付物；PDF、XLSX 独立生成，失败时不阻断 HTML。
- 所有文件均不可用时仍可提供文字结果。
- PDF 超长字段保护和 canonical report 持久化策略保持不变。

### 验证

- 覆盖默认 Top10、M5 排名优先级和“明确请求后才查看全部”的自动化测试。

<a id="release-0-3-9"></a>

## Agent D 0.3.9 发布说明

> 此版本已被 0.3.10 取代。0.3.9 的文字兜底曾要求默认读取全部候选，
> 不符合“默认 Top10、用户明确请求后才查看全部”的产品约定，请勿再部署 0.3.9。

### 修复内容

- M6 以 HTML 作为主交付物，PDF 调整为不阻断流程的可选附件。
- HTML、XLSX、PDF 改为逐格式独立渲染和保存；任一格式失败时保留其他成功格式。
- canonical report 在导出前持久化，避免格式渲染异常导致完整科学结果丢失。
- `m6_execution` 新增 `export_status` 和 `export_failures`，明确记录部分导出失败。
- PDF 可见表格单元格增加长度上限，完整数据仍保存在 PDF 内嵌的
  `canonical-report.json`，避免超长字段触发 ReportLab `LayoutError`。
- HTML 不可用时，智能体结果新增确定性的文字兜底：按页读取全部
  `all_deliverable_candidates`，而不是只返回前 3 条或前 10 条预览。
- `get_wf01_report` 新增 `candidate_scope="deliverable"`，用于完整文字结果分页。

### 行为约定

- HTML 成功、PDF 失败：M6 为 `COMPLETED`，导出状态为 `PARTIAL`，用户仍可直接打开 HTML。
- 所有文件导出失败：canonical report 仍保留；智能体必须按分页计划输出全部候选的文字结果。
- PDF、XLSX 的失败不再导致 HTML 下载入口消失。

### 验证

- PDF 异常隔离、HTML 主附件和文字分页兜底均有自动化回归测试。
- 使用 33 条候选及超长字段生成 37 页压力测试 PDF，代表页面无表格溢出或布局异常。

<a id="release-0-3-8"></a>

## Agent D 0.3.8 发布说明

### 核心变更

- 新增 `run_wf01_until_blocked` MCP 工具，由 Python 状态机自动执行
  M2-M6；大模型不再逐阶段分析候选数据。
- 遇到 `WAIT_FOR_USER` 时自动停止，用户提交
  `agent_interaction_response` 后默认自动恢复，直至下一次交互或 M6 完成。
- `get_wf01_batch`、`run_wf01_stage` 和批次创建结果改为
  `wf01-agent-view-v1` 摘要，不再把完整候选池写入模型上下文。
- `get_wf01_report` 默认返回报告摘要；指定靶标时每页最多返回 25 个压缩候选。
- 新增 `get_wf01_candidate`，仅在显式请求时展开一个候选的规则详情。
- 增加 `MCP_MAX_TOOL_RESULT_BYTES` 工具响应保护，默认上限为 65536 字节。

### 上下文体积验证

对造成问题的真实 M2 MCP 响应进行回归验证：

- 原始文件：3,958,748 字节；
- 内部紧凑批次 JSON：1,322,763 字节；
- 新智能体摘要：1,270 字节；
- 智能体可见响应缩减 99.9%。

完整科学数据、候选规则证据和 M6 canonical report 仍保存在服务端，
HTML、XLSX、PDF 导出不受影响。

### 兼容性

- REST 完整批次和 canonical report 契约保持不变。
- `run_wf01_stage` 继续保留，定位为兼容/诊断工具。
- MCP 工具总数由 24 增加到 26。
- HiMat 正常流程应在批次创建后调用一次 `run_wf01_until_blocked`。

### 验证

- 完整自动化测试集：153 项通过。
- MCP 中文结构化字段仍按 UTF-8 原样往返。

<a id="release-0-3-7"></a>

## Agent D 0.3.7 发布说明

本版本增加可查询的脱敏智能体工具审计日志，并明确区分 HiMat 业务数据库、Agent D 自身批次存储和科学数据库。

### 主要变更

- 新增 MCP 工具 `get_agent_audit_log` 和 REST 接口 `GET /v1/audit/agent`。
- 审计日志记录工具开始/完成/失败、输入信封、字段来源、有效合并值、工作流状态、数据库就绪状态和稳定错误信息。
- `mcp_input_mapping` 新增 `input_mode`、`field_sources` 和 `effective_design_input`，可直接判断字段来自原表单还是用户确认更新。
- FASTA/序列只记录长度、SHA-256 和记录数；JWT、Authorization、密码、密钥和 token 全部脱敏。
- 明确不记录模型内部思维链；日志只保存可观察、可验证的执行事实和决策依据。
- MCP 指令明确：`开始设计`/`确认开始设计`按钮文字不是结构化表单更新，Agent D 不能据此继续，也不能直接读取 HiMat 私有业务数据库。

### 本次问题的结论

截图中的表单已经包含宿主“鸡”和覆盖要求“单一毒株”，但确认后的新用户消息只显示“确认开始设计”。如果 HiMat 没有把 `primer_design_input_update.data.designInput` 一并传回智能体，Agent D 无法从 HiMat 后端自动补读这些值。

部署后可查询 `operation=wf01_preflight`：没有记录表示智能体根本没有调用预检；`TOOL_FAILED` 表示调用但消息结构或字段不完整；`TOOL_COMPLETED` 中的 `field_sources` 和 `effective_design_input` 表示最终真实使用的数据。

<a id="release-0-3-6"></a>

## Agent D 0.3.6 发布说明

本版本为 WF-01 输入确认和询问弹窗增加硬性用户轮次边界，避免智能体返回 UI JSON 后在同一轮继续思考、输出或执行后续阶段。

### 主要变更

- `primer_design_input` 和 `agent_interaction` 新增统一的 `flowControl`：
  `action=WAIT_FOR_USER`、`stopCurrentTurn=true`，并声明恢复所需的用户消息类型。
- MCP 系统指令和工具描述明确规定：等待 JSON 是当前助手轮次的终点；必须原样返回 UI JSON，且不得继续调用工具或自行构造用户确认。
- MCP `preflight_wf01_batch` 与 `create_wf01_batch` 现在要求同时提供原始 `design_input_form` 和真实的 role=user `user_field_updates`，不再暴露跳过首次确认的直接入口。
- 所有 M2-M6 阶段统一检查未解决交互。只要存在 `WAITING_USER`、`USER_SUBMITTED` 或 `RUNNING` 交互，后端就以 `next_action=WAIT_FOR_USER` 拒绝继续。
- 所有含等待弹窗的 WF-01 API/MCP 结果统一返回 `next_action=WAIT_FOR_USER`；用户提交回复并完成确定性路由后，才返回 `RUN_M2` 或 `RUN_M6`。

### 前后端时序

1. 智能体返回输入表单或询问弹窗 JSON，并结束当前轮次。
2. 前端渲染 JSON，等待用户填写或选择。
3. 用户点击确认后，前端发送新的 role=user 更新/回复消息。
4. 智能体在新轮次提交回复，根据后端返回的 `next_action` 恢复工作流。

内部 REST/service 仍保留 canonical payload 适配能力，供非对话式兼容调用使用；MCP 对话入口不能借此绕过用户确认。

<a id="release-0-3-5"></a>

## Agent D 0.3.5 发布说明

本版本以《引物智能体 WF-01 PRD v0.2》为基线，修复 HiMat 中 WF-01 用户交互后无法继续运行的问题。

### 主要变更

- 新增由 Agent D 确定性生成的 `primer_design_input`、`agent_interaction` 和
  `primer_design_output` UI 消息，流程不再依赖大模型能否输出严格 JSON。
- REST 和 MCP 均支持从 canonical payload 或 `designInput` 直接归一化输入。
- 新增交互回复提交接口与 MCP 工具，支持单题扁平结构和多题数组结构。
- 用户回复会持久化为 `USER_SUBMITTED`/`RESOLVED`，并返回明确的 `next_action`。
- M1 确认可将批次推进到 `QUEUED`；M5 接受当前结果可进入 M6；调整约束可基于
  显式 `target_changes` 创建不可变的 M7 新版本。
- 存在待处理交互时禁止进入 M6。
- M6 完成消息以实际生成的 HTML 为主附件，并仅公布真实存在的 HTML、XLSX、PDF 文件。
- 部署脚本现在遵循 `AGENTD_REQUIRE_DATABASES`：严格生产模式会阻止缺库上线，流程联调
  模式允许启动但必须保留 `NOT_EVALUATED`。

### HiMat 接入要求

HiMat 必须直接消费 REST/MCP 结构化结果中的 `ui_message` 和 `next_action`，不能从大模型
自然语言回复中重建流程状态。大模型输出非 JSON、Markdown 或额外文字时，工作流仍应继续。

### 兼容性

- PRD 基线保持 WF-01 v0.2。
- canonical report schema 保持 `3.0.0-phase3`。
- 原 0.3.4 任务记录不原地修改；M7 重设计继续创建新版本。
