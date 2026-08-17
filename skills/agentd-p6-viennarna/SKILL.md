---
name: agentd-p6-viennarna
description: "通过 Agent D P6 ViennaRNA 评估 qPCR 扩增子二级结构。用于 RNAfold、扩增子 MFE、二级结构、稳定发卡、ViennaRNA、RNAfold、扩增子结构或 ΔG 结构风险相关请求。"
---

# Agent D P6 ViennaRNA 结构评估

把 P6 作为 WF-01、WF-03、WF-04、WF-06 或 WF-07 中的可选结构风险评估。P6 在固定的 PCR 导向条件下评估 DNA 扩增子；不得仅因 ViennaRNA 缺失而阻断工作流。

## 执行

1. 检查 `P6_ViennaRNA` 工具状态，并确认 DNA 参数可用。
2. 运行能够生成或验证扩增子的工作流；使用 WF-04/WF-07 评估已有检测体系时提供 `template`。
3. 读取每个候选或内标候选上的 `amplicon_structure`。
4. 保留计算条件、MFE ΔG、结构状态及警告。
5. P6 不可用或缺少扩增子时返回 `not_evaluated`，并把工作流结果保留为部分证据；不得伪造 ΔG。

## 边界

- 仅接受不超过 500 nt 的扩增子。
- 把 `WARNING`/`CAUTION` 视为重新设计或实验复核证据，不得直接断言扩增失败。
- 读取 [references/contract.md](references/contract.md)，确认参数与阈值语义。
