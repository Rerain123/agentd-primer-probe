---
name: oligosmith-wf02
description: 使用 OligoSmith WF-02 新建、续跑、交互、查询或导出多目标组合任务。用户明确指定 WF-02 或请求该组合工作流时使用；不要用于其他工作流。
---

# OligoSmith WF-02

只使用本 Skill 声明的 WF-02 MCP 依赖完成编排。后端状态机是执行顺序和结果的唯一权威来源。

## 执行

1. 有附件时逐个调用 `download_himat_file`，然后用返回的引用调用 `create_wf02_batch`。
2. 后端返回输入交互时调用 `submit_wf02_input_response`；返回子任务交互时调用 `submit_wf02_child_response` 或 `submit_wf02_internal_control_response`。
3. 完整建单后后端立即提交持久化后台任务并返回任务编号，无需保持 HiMat 连接。若返回 `WAIT_FOR_BACKGROUND`，只输出工具给出的 `user_message` 一次并立即结束本轮；不得轮询、重复调用运行工具或输出阶段过程文字。
4. 若 `create_wf02_batch` 出现请求级失败或连接超时，不得在同一轮自动重试，因为任务可能已经持久化；只说明请求中断并结束本轮。用户后续明确要求“继续”时，才可用完全相同的原参数再次调用；后端会在短时间窗口内接回同一任务，不重复建单。
5. 用户后续明确说“查询进度”“查询刚才的任务”或提供任务编号时，只调用一次 `get_wf02_batch`。仍在运行时简短返回当前状态并停止；完成时按下方交付规则回复后停止。详情、导出或新版本分别调用 `get_wf02_report`、`get_wf02_panel`、`get_wf02_export` 或 `create_wf02_version`。

## 边界

- 只传递用户提供或工具返回的字段，不猜测缺失值。
- 不在模型中模拟节点、执行计算或改写持久化状态。
- 收到 `primer_design_output` 时，终态卡片已由工具直接交付；不得生成助手文本、解释或重建 JSON，不得再次调用工具，并立即结束本轮。
- 不调用其他工作流的工具。
