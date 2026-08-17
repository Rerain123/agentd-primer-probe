# P4 运行契约

使用 `WF-02`，传入 `targets`、`instrument`，以及可选的 `rox_as_reference` 和 `include_internal_control`。默认仪器为 `CFX96`。

P4 检查 14 种染料、淬灭基团、已配置仪器、全部完整及 3′ 局部异源二聚体组合、引物/探针 Tm 离散度、扩增子 GC 离散度，以及 WF-02 扩增子长度间隔。

| 严格条件 | 影响 |
|---|---|
| 染料重复、VIC 与 HEX 并用、发射波长差小于 15 nm | `INCOMPATIBLE` |
| TAMRA 淬灭基团、ROX 被动参比冲突、通道数量过多 | `INCOMPATIBLE` |
| 完整交叉二聚体低于 -7 kcal/mol | `INCOMPATIBLE` |
| Tm/GC/3′ 二聚体警告 | 没有其他失败时为 `MARGINAL` |
| 扩增子长度间隔小于 20 bp | `INCOMPATIBLE` |

`multiplex.channel_assignment` 是已分配的报告通道映射。存在兼容或边缘兼容面板时，完整 P4 报告位于 `multiplex.panels[].compatibility`。
