---
name: oligosmith-wf05
description: 使用 OligoSmith WF-05 新建、重试或查询现有方案的序列集覆盖评估任务。用户明确指定 WF-05，或要求按代表性序列集检查现有方案时使用；不要用于设计新方案或其他工作流。
---

# OligoSmith WF-05

只使用本 Skill 声明的 WF-05 MCP 依赖。模型负责收集入口字段和选择操作，不参与后端节点执行、统计或判定。后端只绑定专家文档指定的既有工具；不可用时返回明确状态，不新建替代工具。

## 执行

1. 收集 `forward_primer`、`reverse_primer`，以及 `input_artifact_id` 或 `sequences` 二者之一；其余字段仅在用户提供时传递。
2. 有序列集附件时先调用 `download_himat_file`，再把返回的顶层 `input_artifact_id` 原样传给 `run_wf05_variation`。
3. 缺少入口必填字段时只询问缺失项；字段齐全时立即调用运行工具。
4. 只有后端返回 `retry.allowed = true`，且用户补充了输入或依赖已修复时，才调用 `retry_wf05_variation`。
5. 用户后续明确要求重新查看既有任务时调用 `get_wf05_report`；收到终态结果时按下方交付规则回复后结束。

运行或重试前读取 [references/contract.md](references/contract.md)，确认输入互斥、不可变任务和跨工作流边界。

## 边界

- 只传递用户提供或工具返回的字段。
- 不自行执行、计算、重判或改写后端结果。
- 不请求、构造或启用合同外的工具或替代计算路径。
- 收到 `primer_design_output` 时，终态卡片已由工具直接交付；不得生成助手文本、解释或重建 JSON，不得再次调用工具，并立即结束本轮。
- 不调用其他工作流的工具；WF-05 返回交接建议时只向用户说明，不自动执行。
