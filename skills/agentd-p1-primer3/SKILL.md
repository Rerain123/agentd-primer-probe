---
name: agentd-p1-primer3
description: "通过 Agent D P1 Primer3 设计或验证 qPCR 引物、探针及热力学性质。用于 TaqMan 引物探针设计、引物 Tm、发卡、二聚体、已有检测体系验证，以及用户提到引物设计、探针设计、Tm、二聚体或已有引物验证时。"
---

# Agent D P1 Primer3 引物与探针设计

把 P1 作为寡核苷酸设计与热力学计算的唯一依据。调用现有 Agent D 服务；不得自行重建 Primer3 参数、改用其他公式计算 Tm，也不得静默启用降级算法。

## 请求路由

- 设计新检测体系：运行 `WF-01`（`POST /v1/wf01/design`）。
- 验证已有上游/下游引物及可选探针：运行 `WF-04`（`POST /v1/wf04/validate`）。
- 仅在用户明确要求 MGB 探针时启用 MGB 修正；设置 `use_mgb: true`，交由 P3 评估。
- 当前 Agent D 交付范围不包含 SYBR 设计；不得把含探针的结果标记为 SYBR。

## 执行

1. 检查 `GET /v1/tool-status` 或 `agentd tool-status`，确认 `P1.available` 为真。
2. 验证请求序列为 5′→3′ DNA，并保留调用方提供的检测体系与靶标标识。
3. 提交工作流请求，不得覆盖科学阈值。
4. 返回候选序列、扩增子序列与长度、Tm 方法、盐浓度与 DNA 浓度元数据、规则、证据等级、警告及实验验证免责声明。
5. P1 不可用时如实报告工具状态。仅当调用方明确要求时才允许开发降级；降级结果必须保持 `PARTIAL` 和 `in_silico_only`。

## 边界

- MGB 修正交给 P3，特异性评估交给 P2，多重兼容性评估交给 P4，扩增子折叠评估交给 P6。
- 把所有计算值视为设计证据，不得视为临床或生产结论。
- 构造请求或解释 P1 结果前，读取 [references/contract.md](references/contract.md)。
