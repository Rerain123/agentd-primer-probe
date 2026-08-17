# P2 运行契约

新设计使用 `WF-01`，已有检测体系使用 `WF-04`。API 分别调用 `/v1/wf01/design` 或 `/v1/wf04/validate`。

| P2 模式 | 数据库字段 | 结果语义 |
|---|---|---|
| 靶标确认 | `target_db` | 上游和下游引物都必须具有合格靶标命中 |
| 宿主排除（可选） | `host_db` + `host_species` | 提供宿主时，高风险宿主命中导致检测体系失败；否则该模式不适用 |
| 跨物种检查 | `cross_db` + `target_species` | 命中产生交叉反应警告 |

服务使用 `blastn-short`；靶标/跨物种 e-value 为 `1e-5`，宿主 e-value 为 `0.1`。正常生产结果应满足 `specificity.method = blastn`，并已评估靶标与跨物种/背景模式；仅在提供宿主时评估宿主模式。目录提供数据库信息时，数据库版本及路径解析保存在 `provenance.database_resolution`。

## 数据库边界

P2 仅对本地配置、版本化的靶标、宿主及跨物种/背景数据库运行 BLAST+。本契约不包含公共远程 BLAST 提交或异步复核端点。
