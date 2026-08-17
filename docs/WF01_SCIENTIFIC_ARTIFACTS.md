# WF-01 内部原始证据与用户数据包契约

## 目标

每个科学处理阶段结束后，服务器保存该阶段的原始输入、工具参数、工具原始输出和判定明细，供内部审计和专家复核。内部原始证据不作为用户交互消息，也不直接塞入模型上下文。

## 阶段覆盖

| 阶段 | 工具或处理器 | 保存的主要原始数据 |
|---|---|---|
| M1 | WF01_PREFLIGHT | 原始序列解析结果、标准化对象、参数、问题与证据 |
| M2 | MAFFT | 输入 FASTA、命令、退出码、标准输出、标准错误、比对结果或降级结果 |
| M2 | Primer3 | `sequence_args`、`global_args`、Primer3 原始返回字典；模拟或降级模式保存原始候选 |
| M2 | WF01_HARD_RULE_ENGINE | 每个候选及全部硬规则状态、证据和淘汰原因 |
| M3 | WF01_DEDUPLICATION / WF01_SCORING | 去重前后候选、重复折叠关系、各维度原始分和排序结果 |
| M4 | BLAST+ | 查询 FASTA、数据库、阈值、命令、退出码、原始 tabular stdout/stderr 及逐行字段 |
| M4 | Primer3_THERMODYNAMICS / WF01_SPECIFICITY_ENGINE | 命中序列、热力学计算值、坐标、对象/数据库版本和特异性判定 |
| M5 | WF01_DECISION_ENGINE | 最终分类、候选计数、Top 10、未完成原因和建议 |
| M6 | WF01_REPORT_ASSEMBLER | 完整 canonical report、完整性信息、导出执行信息和内容校验值 |
| M7 | WF01_VERSIONING_ENGINE | 来源版本、变更差异、复用目标和重跑目标 |

即使科学工具失败，也会在对应阶段文件中保存可获得的输入、状态和错误证据。开发降级结果必须带有 `DEVELOPMENT_FALLBACK` 等明确状态，不能冒充正式工具结果。

## 内部文件契约

原始文件符合 `schemas/wf01-scientific-artifact.schema.json`：

```json
{
  "schemaVersion": "wf01-scientific-artifact-v1",
  "artifactId": "m2-target_001-0123456789ab",
  "batchId": "...",
  "targetId": "target_001",
  "stage": "M2",
  "tools": ["MAFFT", "Primer3", "WF01_HARD_RULE_ENGINE"],
  "createdAt": "2026-08-06T10:00:00Z",
  "dataScope": "RAW_SCIENTIFIC_STAGE_OUTPUT",
  "rawData": {}
}
```

`artifactId` 含原始内容 SHA-256 的前 12 位。相同内容可复用同一文件，不同内容不会覆盖旧文件。清单中的 `sha256` 和 `sizeBytes` 用于下载后的完整性校验。

## 查询与下载

MCP 智能体下载科学工具数据包：

```text
get_wf01_export(batch_id, format_name="scientific_data")
```

专家后台客户端需要逐阶段原始 JSON 时，使用以下 REST 接口：

```text
GET /v1/wf01/tasks/{batch_id}/scientific-artifacts
GET /v1/wf01/tasks/{batch_id}/scientific-artifacts?stage=M4
GET /v1/wf01/tasks/{batch_id}/scientific-artifacts/{artifact_id}
```

内部原始 JSON 只用于审计和专家诊断，不加入普通用户的 `primer_design_output`。
M6 面向用户生成独立的科学过程详情 HTML 和科学工具数据 ZIP。该 ZIP 仅包含中文说明、
过程 HTML 和中文表头 CSV，不包含 JSON、程序代码、内部字段名、命令行或代码式日志。
启用 RustFS 后，用户附件上传完成并通过完整性校验后才返回完整下载 URL。
