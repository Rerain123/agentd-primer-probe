# P5 运行契约

本地使用 `WF-03`，服务器端使用 `POST /v1/wf03/internal-control`。

| 输入 | 含义 |
|---|---|
| `host_species` | 必填；内置优先级支持猪、牛或鸡 |
| `preferred_genes` | 可选；按顺序覆盖 ACTB、GAPDH、18S rRNA 默认优先级 |
| `gene_sequences` / `gene_fasta_paths` | 用户提供的版本化同源序列 |
| `existing_targets` | 可选；用于交叉二聚体评估的靶标寡核苷酸 |

P5 至少需要 5 条同源序列。保守性 ≥0.99 时通过，0.97–0.99 时警告，更低时不予选择。缺少来源数据时，P5 返回 `NOT_CONFIGURED`。默认使用 Cy5/BHQ-2；已有靶标占用 Cy5 时必须由多重层解决。P1、MAFFT 和 BLAST+ 的可用性决定完整能力；P6 为可选工具，不可用时必须报告 `not_evaluated`。
