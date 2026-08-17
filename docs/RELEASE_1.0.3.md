# OligoSmith 1.0.3

## 修复内容

- WF-01 完整特异性路线在本地 `cross/nt` 背景库不可用时，可调用独立的 NCBI BLAST MCP 服务完成 `RELATED_BACKGROUND_NT` 检查。
- 本地版本化 BLAST 数据库仍具有最高优先级；远程服务只作为背景库回退，不替代现有靶标专库或宿主专库。
- 远程命中根据返回的 query 比对序列、坐标与本批 primer/probe 做确定性关联；无法关联或存在非同序列歧义时整次检查标记为 `NOT_EVALUATED`，不生成综合评分。
- NCBI 公共 `nt` 被明确记录为实时数据库 `NCBI_NT_PUBLIC_LIVE`，报告保留查询时间、数据库规模、服务地址及“非固定快照”语义。
- 已知靶标别名补充 NCBI 学名与 accession：CSFV 对应 `Pestivirus C` / `NC_002657.1`，胞内劳森菌对应 `Lawsonia intracellularis`，用于排除靶标自身命中。
- 特异性未完成时，报告会显示具体的数据库未配置或远程搜索失败原因。
- M4 引擎版本升级为 `wf01-v0.2-m4.6`；既有特异性阈值与三维评分权重不变。

## 部署配置

```dotenv
BLAST_SEARCH_SERVICE_URL=http://host.docker.internal:8090
BLAST_SEARCH_TIMEOUT_SECONDS=360
BLAST_SEARCH_MAX_SEQS=100
```

Docker Compose 已加入 Linux `host-gateway` 映射。部署前目录校验可将该远程服务视为 `cross:default:blast` 的背景搜索提供方；容器就绪检查会访问 `/health`，实际 MCP 搜索能力仍在每次 WF-01 搜索时再次验证。

## 科学边界

- 靶标专库与已提供宿主的宿主专库仍必须实际可用，否则对应模式保持 `NOT_EVALUATED`。
- 远程 NCBI `nt` 不是固定数据库快照；报告通过访问时间和数据库规模保留可审计性，但不能宣称完全可复现。
- 任一必需模式失败或未评估时，不伪造三维综合评分。
