---
name: agentd-p4-multiplex
description: "通过 Agent D P4 检查多重 qPCR 面板的荧光、仪器通道、交叉二聚体、Tm 离散度及扩增子间隔。用于多重 PCR、面板兼容性、染料与通道分配、ROX、VIC/HEX、荧光通道或交叉二聚体相关请求。"
---

# Agent D P4 多重兼容性评估

各靶标均已有候选检测体系后，通过 WF-02 或 WF-06 使用 P4。P4 验证给定的通道分配，不得独立臆造通道方案。

## 执行

1. 收集 2–4 个靶标及其序列或目录标识、实际仪器型号，并确认 ROX 是否作为被动参比。
2. 运行 `WF-02`（`POST /v1/wf02/multiplex`）或多靶标 `WF-06`。
3. 检查 `multiplex.panels[].compatibility`、通道分配、放宽步骤及全部问题。
4. 仅把 `COMPATIBLE` 面板作为严格推荐。保留 `MARGINAL` 和 `INCOMPATIBLE` 及其原因；不得仅在文字中更换染料来抹除问题。
5. 没有严格兼容面板时，采用已记录的放宽或分管建议；需要重新设计时交回 WF-01/WF-02。

## 边界

- 必须提供实际 qPCR 仪器；不得仅声称“多重兼容”。
- P4 不替代 P2 特异性检查或 P1 单检测体系规则检查。
- 选择面板前读取 [references/contract.md](references/contract.md)。
