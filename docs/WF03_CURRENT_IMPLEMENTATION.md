# WF-03 当前实现

本文是 WF-03 宿主内标设计的现行实现契约。规则来源为
`AgentD_引物探针智能体_规则更新_简并_版.md` 的 WF-03，以及
`P5_Internal_Control.md` 和 `Skills修订版_v12.3_按智能体版.md`。

## 架构结论

WF-03 采用与 WF-01、WF-02 相同的“模型路由 + MCP 薄入口 + 确定性后端”边界：

1. LLM 只识别“宿主内标/内参/管家基因检测体系”意图，并把用户明确给出的输入传给 MCP。
2. MCP 不实现科学逻辑，只调用 `AgentDService`。
3. `WF03InternalControl` 只直接调用既有 `P5.design_internal_control()`。
4. P5 负责调用 Entrez、C5、P1、P2、P6；P2 的执行、阈值和逐候选判定由 P2 自身持有，P5 只映射结果，WF-03 不复制实现。
5. P3 不适用于普通内标探针；P4 和最终荧光通道指派由 WF-02 负责。

模型路由采用附件优先的硬约束：识别为 WF-03 且当前请求包含附件/file_id 时，必须先调用
`download_himat_file`，再把返回对象顶层的 `input_artifact_id` 原样传给
`run_wf03_internal_control`；只有无附件且必填字段已经存在时才直接运行。不得在工具调用前自行分析、
规划或生成结果；必填字段缺失时只询问该字段并停止。
HiMat 必须在服务升级后刷新 MCP `tools/list`。若宿主支持 `tool_choice`，WF-03 路由应直接指定
附件请求的首个工具为 `download_himat_file`，随后指定 `run_wf03_internal_control`，不能只依赖模型的自动工具选择。

```text
用户意图
  -> [有附件] download_himat_file
       -> 顶层 input_artifact_id
  -> run_wf03_internal_control (MCP)
  -> AgentDService.run_wf03_internal_control (持久化后端)
  -> WF03InternalControl
  -> P5.design_internal_control
       -> Entrez -> C5 -> P1 -> P2 -> P6
  -> C7规范结果 + HTML终态附件 + primer_design_output
```

任何 `PASS`、`CAUTION`、`FAIL`、Tm、ΔG、保守性或 BLAST 结论都必须来自后端工具证据，
不得由模型推断或补写。

## MCP 工具

| 工具 | 用途 | 模型责任 |
|---|---|---|
| `run_wf03_internal_control` | 新建并完整运行 WF-03 | 有附件时必须传下载工具返回的 `input_artifact_id` |
| `retry_wf03_internal_control` | 对失败任务执行 Step 1 不可变重入 | 仅在用户补证据或依赖修复后调用 |
| `get_wf03_report` | 读取持久化终态并重新交付输出卡片 | 不重新计算、不改写结论 |

完整结果按 `job_id` 保存；MCP 的 `run/retry/get` 均从该持久化结果生成 HTML 终态附件，并返回
与 WF-01 相同的 `primer_design_output` 顶层结构。`outputSchema` 声明并约束该终态分支；
HTTP 响应层将权威业务对象直接放入 JSON-RPC `result`，且只保留 `data/type`。
HiMat 直接使用该结果生成卡片；模型不得生成终态助手文本或重建 JSON。

## 输入与规范化

`host_species` 必填。后端确定性映射猪/牛/鸡的中英文名和学名；未识别物种原样交给 P5，
由 P5 根据自定义基因证据判断是否可运行。

可选字段：

- `input_artifact_id`、`input_gene`：由 WF-01 同一个 `download_himat_file` 生成的不透明引用及可选用户标签；
- `existing_targets`：已有靶标正反引物及可选探针，用于 P5/P1 交叉二聚体检查；
- `preferred_genes`、`gene_accessions`：用户明确指定的基因与 RefSeq；
- `gene_sequences`、`gene_fasta_paths`：版本化同物种同源序列；
- `host_db`、`cross_db`：已配置 P2 数据库。

附件正文不会进入模型上下文。服务层把 `input_artifact_id` 解析为仅后端可见的路径，并映射到 P5 的
`gene_fasta_paths`；优先使用显式 `input_gene`，其次使用唯一的 `preferred_genes`，最后使用安全文件名。
公开结果只保存附件 ID、文件名、哈希和映射标签，不返回内部路径。重入保存并复用原始附件引用。

字段契约见：

- `schemas/wf03-v01-input.schema.json`
- `schemas/wf03-v01-retry.schema.json`
- `schemas/wf03-v01-report.schema.json`

WF-03 不增加专用下载字段，与 WF-01 一样直接复用下载结果顶层的 `input_artifact_id`。文件正文、
服务器路径和路径型字段均在 `AgentDService` 内部解析，MCP 和模型只能传递不透明引用。

## 后端状态与证据映射

P5 每次运行只调用一次。WF-03 不执行隐藏重试，不把工具缺失转换为成功：

| 条件 | WF-03 状态 |
|---|---|
| P5 PASS、P2 正式验证、候选数 ≥3 | `SUCCEEDED` |
| 有 1–2 套候选、P5 CAUTION 或证据仅为 in silico | `PARTIAL` |
| P5 FAIL 或 0 套候选 | `FAILED` |

P5 通过 `P2SpecificityBatchService.evaluate_p5_batch()` 一次提交全部候选。P2 返回逐候选结果；
P5 将其映射到 `candidates[].specificity`、`evidence_level` 和 R-19/R-21。任一必需资源既未在
本地解析、也没有可用远端 provider 时，该分支保持 `NOT_EVALUATED`，WF-03 为 `PARTIAL`。

P6 是软性工具。不可用或部分未评估不淘汰候选，但必须保持 `NOT_EVALUATED` 或
`PARTIALLY_EVALUATED`，同时进入 warning 和 self-check；不得改写为 `PASS`。

未提供 `existing_targets` 时，与靶标兼容性固定为 `NOT_EVALUATED`。P5 的 Cy5 仅是默认建议，
独立 WF-03 不声称完成最终多重通道验证。表达稳定性始终标记 `NOT_EVALUATED`，需使用
geNorm/NormFinder 和湿实验确认。

## Step 2 重入与兜底

失败由后端按证据分类，包括低保守性、序列不足、依赖不可用、无候选、P2 特异性失败和
靶标交叉二聚体失败。报告固定给出：

1. 补充自定义管家基因及 RefSeq/同源序列后重入；
2. RPLP0、B2M、SDHA、TBP、HMBS 仅在用户提供 RefSeq 或版本化序列时尝试；
3. 仍不可行时建议外源性异源内标，WF-03 不自动生成其序列。

`retry_wf03_internal_control(parent_job_id, patch)` 从父任务的私有输入附件恢复数据，仅应用
白名单字段，并生成新 `job_id`。父结果不被覆盖，新结果在 provenance 和 trace 中记录
`parent_job_id`、失败原因与补丁字段。

## HTTP 兼容接口

- `POST /v1/wf03/internal-control`
- `POST /v1/wf03/internal-control/{job_id}/retry`
- `GET /v1/wf03/internal-control/{job_id}/report`

HTTP、MCP、CLI 共用 `WF03InternalControl`；没有第二套模型侧实现。

## 回归测试

- `tests/test_wf03_backend.py`：P5 单一边界、状态映射、P6 未评估、失败分类、持久化重入和 Schema；
- `tests/test_p5_p2_integration.py`：P5→P2 批量边界、逐候选映射、本地/远端资源选择和缺失资源状态；
- `tests/test_wf03_mcp.py`：MCP 仅委托服务、原样传递附件引用并返回终态卡片，不含节点实现；
- `tests/test_workflow_delivery.py`：复用 WF-01 输出协议、HTML 附件发布、幂等交付和路径校验；
- `tests/test_mcp_server.py`：公开工具清单、附件下载下游映射和意图路由指令；
- `tests/test_phase3_framework.py`：通用 C7 输出兼容性。
