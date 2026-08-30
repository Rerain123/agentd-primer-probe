---
name: oligosmith-wf01
description: 使用 OligoSmith WF-01 新建、恢复、查询或导出基础设计任务。用户明确指定 WF-01，或请求基础设计工作流时使用；不要用于 WF-02、WF-03 或 WF-04。
---

# OligoSmith WF-01

只使用本 Skill 声明的 WF-01 MCP 依赖完成编排。工作流执行、校验、状态和结果均以后端为准。

## 执行

1. 新任务调用 `create_wf01_batch`；有附件时先调用 `download_himat_file`，再传递其返回的引用。
2. 仅在后端明确返回待处理交互时调用 `revise_wf01_target_manifest` 或 `submit_wf01_interaction_response`。
3. 仅为恢复已有任务调用 `run_wf01_until_blocked`。
4. 用户后续明确请求查看、详情、导出或新版本时，分别调用 `get_wf01_batch`、`get_wf01_report`、`get_wf01_candidate`、`get_wf01_export` 或 `create_wf01_version`。
5. 根据工具返回的继续、等待或完成状态行动；收到终态结果时按下方交付规则回复后立即结束。

## 边界

- 只传递用户提供或工具返回的字段，不猜测缺失值。
- 不自行执行、补算、重判或重建工作流结果。
- 收到 `primer_design_output` 时，终态卡片已由工具直接交付；不得生成助手文本、解释或重建 JSON，不得再次调用工具，并立即结束本轮。
- 不调用其他工作流的工具。
