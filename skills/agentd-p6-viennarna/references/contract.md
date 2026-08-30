# P6 运行契约

P6 通过 WF-01、WF-03 和 WF-04 间接运行。检查 `candidates[].amplicon_structure` 或 `internal_control.candidates[]`。

适配器在每次受保护调用时加载 ViennaRNA DNA Mathews 2004 参数，在 60°C、50 mM Na+ 条件下把 T→U 后折叠 DNA，最大支持 500 nt。

| MFE ΔG | 状态 |
|---|---|
| ≥ -20 kcal/mol | `PASS` |
| -30 至低于 -20 kcal/mol | `CAUTION` |
| < -30 kcal/mol | `WARNING` |
| 工具或扩增子不可用 | `not_evaluated` |

完整适配器可以返回次优结构；工作流结果通常携带 MFE 评估。这是一项预测能力有限的软规则，不得代替 PCR 优化。
