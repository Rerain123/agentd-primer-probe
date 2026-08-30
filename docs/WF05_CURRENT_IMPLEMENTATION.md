# WF-05 当前实现

本文是 WF-05 v0.1 的现行工程契约。流程节点来源于项目规则文档；本页只描述代码隔离、接口、状态与交付边界，不复制工具算法。

## 架构结论

WF-05 绑定 `agentd_workflow_snapshots.wf05_v1`，运行标识为 `wf05-runtime-snapshot-v1`。该包只包含 WF-05 编排及实际运行依赖，不包含其他工作流的执行或报告分支，也不包含已退役工作流。包内使用相对导入，不得导回可变的顶层 `agentd` 实现。

专家规则文档和既有工具实现均是只读边界。WF-05 不创建新的计算工具，只调用 C0、C5、C7 和 P1；C21 当前未提供实现，因此固定记录为 `NOT_EVALUATED`，不得自行补写替代实现。

`alignment.py`、`database.py`、`schema.py` 和 `primer3_tool.py` 是从项目已有工具字节级原样冻结的快照，每份快照都有固定 SHA-256。运行 WF-05 前会校验这些摘要；任何改写、替换或缺失都会使 WF-05 闭合失败，不会以自行计算继续。

`AgentDService` 仅负责：

- 将不透明附件引用转换为后端内部路径；
- 路由到固定版本包并翻译公共异常；
- 将结果写入 WF-05 专属状态目录；
- 分发版本包生成的终态附件。

节点顺序、状态、判定映射、报告摘要与终态 HTML 由 WF-05 版本包持有；工具能力由上述既有工具快照持有。

## 六节点状态机

1. `WF05_STEP1_SEQUENCE_SET`
2. `WF05_STEP2_ALIGNMENT`
3. `WF05_STEP3_VARIANT_SCAN`
4. `WF05_STEP4_TM_IMPACT`
5. `WF05_STEP5_COVERAGE`
6. `WF05_STEP6_DECISION`

`execution_status` 表示执行证据是否完整，`decision` 表示评估结论，两者独立。开发降级会使执行状态保持 `PARTIAL`，不会被包装成完整执行。

输入校验失败不会创建任务；运行依赖失败会形成可持久化的 `FAILED / INCONCLUSIVE` 终态，保留六节点轨迹、机器可读错误和原始输入附件，后续可从该父任务创建不可变重试。

## 输入与附件

运行入口要求 `forward_primer`、`reverse_primer`，并要求 `input_artifact_id` 与 `sequences` 二选一。公共服务、JSON Schema 和重试契约使用同一字段白名单；未声明字段、直接本地路径及内部控制字段均被拒绝。附件由公共下载边界保存，文件正文和本地路径不进入模型上下文；解析发生在版本包内部。可选字段仅按用户显式输入传递。

版本包同时固定文件大小、记录数、单条长度和总字符量的工程资源上限。超过上限属于输入错误，在调用外部执行工具前终止，不创建半成品任务；这些上限只保护同步运行时资源，不参与结果判定。

## 重试与跨工作流边界

`retry_wf05_variation` 从持久化父输入创建新的 `job_id`，父结果不可变。只允许修改后端声明的白名单字段；父任务的私有输入附件缺失时明确拒绝重试，不使用公开摘要猜测或重建原始输入。

WF-05 不导入或调用其他工作流。需要后续处理时只返回结构化 `redesign_handoff`，其中 `automatic_execution` 固定为 `false`；是否创建另一个工作流任务由用户另行决定。

## 对外入口

专属 MCP：

- `download_himat_file`
- `run_wf05_variation`
- `retry_wf05_variation`
- `get_wf05_report`

REST 兼容层：

- `POST /v1/wf05/variation`
- `POST /v1/wf05/variation/{job_id}/retry`
- `GET /v1/wf05/variation/{job_id}/report`
- `POST /v1/workflows/WF-05/run`

终态采用公共 `primer_design_output` 卡片协议。HTTP 响应层将权威业务对象直接放入
JSON-RPC `result`，顶层只保留 `data/type`。HiMat 直接使用该结果生成卡片；
模型不得生成终态助手文本、解释、Markdown 或重建 JSON。

## 验证要求

- 后端契约、决策与不可变重试测试；
- 既有工具源文件与 WF-05 快照的逐字节相等和 SHA-256 校验；
- WF-05 编排源码不得定义新的 `Adapter`/`Tool` 类，不得直接调用外部进程；
- MCP 白名单、鉴权、本地化和终态等价测试；
- 快照文件集合、内容摘要与禁止绝对导入检查；
- 公共字段白名单一致性、HTML 注入转义和父输入缺失失败测试；
- 根目录兼容副本与版本目录权威结果的写隔离测试；
- WF-01～04 隔离与既有快照完整性回归。
