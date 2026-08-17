---
name: agentd-p3-mgb
description: "通过 Agent D P3 评估 MGB qPCR 探针的修正 Tm 与专用规则。用于 MGB 探针、短探针、FAM 端部 G 淬灭、MGB Tm 修正、MGB 探针设计或 MGB 修正 Tm 相关请求。"
---

# Agent D P3 MGB 探针评估

仅对计划携带 MGB 修饰的探针使用 P3。P3 将 P1 的未修饰 Tm 与明确标注的公开证据工程近似结合；不得把结果表述为 Primer Express 输出。

## 执行

1. 要求提供探针序列，并在 `WF-01` 或 `WF-04` 中设置 `use_mgb: true`。
2. 已知时传入 `fluorophore`，并可传入从零开始计数的 `snp_position`。
3. 读取 `candidates[].mgb_assessment`，包括基础 Tm、修正量、修正后 Tm、全部硬规则与软规则、置信度和估计误差。
4. 拒绝硬规则失败结果。仅在保留警告及重新设计或湿实验确认建议时，才保留 `CAUTION` 结果。
5. 把修正值表述为典型误差约 ±3°C 的近似值；不得称为实验测得 Tm。

## 边界

- P3 仅适用于 13–20 nt 的 MGB 探针。普通 TaqMan/BHQ 探针使用标准 P1 探针评估。
- 不得用 P3 代替特异性评估、荧光面板兼容性评估或最终检测体系验收。
- 读取 [references/contract.md](references/contract.md)，确认精确规则与判定行为。
