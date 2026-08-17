# Agent D 第三阶段：完整框架与服务器部署边界

## 阶段结果

当前 MCP 服务版本为 `1.0.3`，输出 Schema 仍为 `3.0.0-phase3`。至此 Agent D 的七个 Workflow 已全部具备统一入口和可运行编排：

| Workflow | 能力 | 本地无数据库行为 |
|---|---|---|
| WF-01 | 单靶标引物探针设计 | 允许内联/FASTA 测试数据 |
| WF-02 | 2–4 靶标多重面板 | 内标缺失时输出不完整面板状态 |
| WF-03 | 内标设计 | 返回 `NOT_CONFIGURED` 或使用请求测试数据 |
| WF-04 | 已有方案验证 | 数据库项为 `NOT_CHECKED` |
| WF-05 | 变异耐受性 | 需要请求或目录提供代表性序列 |
| WF-06 | 完整诊断面板 | 未接入的内标/变异步骤保留结构化状态 |
| WF-07 | 问题诊断与优化 | 规则归因可运行，数据库证据不伪造 |

## 总体架构

```text
CLI / HTTP API
       │
       ├─ liveness / readiness / capabilities
       │
       ▼
AgentDService（统一路由、任务记录、报告、Schema）
       │
       ├─ DatabaseCatalog（逻辑资源键 → 服务器路径/URI）
       │
       ├─ WF-01 ─ P1/P2/P3/P6
       ├─ WF-02 ─ WF-01×N + P4 + P5
       ├─ WF-03 ─ P5
       ├─ WF-04 ─ P1/P2/P3/P6
       ├─ WF-05 ─ C5/P1
       ├─ WF-06 ─ WF-01/02/03/05
       └─ WF-07 ─ WF-04 + 可选WF-01/05
```

## P4 多重兼容性

P4 内置 14 种荧光基团、6 种淬灭基团和主流仪器通道配置。每套面板检查：

- 发射峰间距、重复染料和 VIC/HEX 冲突；
- TAMRA 禁用及报告基团—淬灭基团覆盖；
- 仪器滤光片、最大通道数和 ROX 被动参比冲突；
- 所有引物/探针两两全长及 3′端局部异源二聚体；
- 引物和探针 Tm 极差、扩增子 GC% 极差；
- WF-02 额外执行扩增子长度差 ≥20 bp。

WF-02 无严格兼容面板时依次记录二聚体/Tm 放宽和分管建议，不静默降低阈值。

## P5 内标设计

P5 默认优先级为 ACTB、GAPDH、18S rRNA，覆盖 pig、cattle、chicken 的 RefSeq 标识，但不把序列硬编码到代码中。序列来源顺序：

1. 请求的 `gene_fasta_paths`；
2. 请求的 `gene_sequences`；
3. DatabaseCatalog 的 `internal:<species>:<gene>:sequences`。

获取序列后执行 MAFFT 保守性、Primer3、R 规则、目标面板交叉二聚体、宿主/病原交叉检查及 ViennaRNA。18S rRNA 自动记录 50–100 nM 限量引物策略。无资源时返回 `NOT_CONFIGURED`，不联网临时抓取不可审计数据。

## 服务器数据库目录

数据库目录文件由环境变量指定：

```text
AGENTD_DATABASE_CATALOG=/config/database-catalog.json
```

目录格式示例：

```json
{
  "resources": {
    "target:CSFV:sequences": {
      "kind": "fasta",
      "path": "/data/sequences/target/CSFV.fasta",
      "version": "2026-07-01",
      "required": true
    },
    "target:CSFV:blast": {
      "kind": "blast_database",
      "path": "/data/blastdb/target/CSFV/current/db",
      "version": "2026-07-01",
      "required": true
    }
  }
}
```

可识别的逻辑键包括：

- `target:<target_id>:sequences`
- `target:<target_id>:blast`
- `host:<species>:blast`
- `cross:default:blast`
- `internal:<species>:<gene>:sequences`

目录中的版本会进入结果 provenance。BLAST 资源的 `path` 应填写传给 `blastn -db` 的数据库前缀；Provider 可识别该前缀对应的 `.nin/.nsq/.nhr` 等索引文件。数据库 URI 可以登记到目录，但当前文件型适配器不会把仅登记 URI 的资源判为可用；关系数据库或对象存储需在服务器联调阶段实现对应 Provider。

## 健康检查

- `/health`：进程存活，不检查外部资源。
- `/ready`：本地检查 Primer3；生产模式同时检查 Primer3、MAFFT、BLAST+ 和部署策略要求的数据库资源。
- `/v1/tool-status`：返回 P1～P6、C0/C1/C5/C6/C7 的详细状态。
- `/v1/capabilities`：返回七个 Workflow 和服务器环境变量契约。

本地配置 `require_databases_for_readiness=false`；生产配置和 Compose 固定为 `true`。因此本地可以验证框架，服务器缺库时容器不会被错误标记为 ready。

## Docker Compose

```powershell
Copy-Item deploy\.env.example deploy\.env
docker compose --env-file deploy\.env -f deploy\docker-compose.yml build
docker compose --env-file deploy\.env -f deploy\docker-compose.yml up -d
```

正式启动前必须：

1. 将 BLAST 数据库放入 `AGENTD_BLASTDB_DIR`；
2. 将代表性和内标序列放入 `AGENTD_SEQUENCE_DATA_DIR`；
3. 修改 `deploy/server-config/database-catalog.json` 的版本及路径；
4. 调用 `/ready`，确认所有 `required=true` 资源可用；
5. 调用 `/v1/tool-status`，确认 BLAST+、MAFFT、Primer3、ViennaRNA 均可用。

数据库文件不写入镜像，任务结果写入独立持久化卷。容器使用非 root 用户、只读根文件系统和受限资源；目标测试宿主的 `no-new-privileges` 会阻止非 root 进程创建 socket，因此该宿主不启用此项。

## 科学与框架边界

- C30 仍使用确定性解释模板并标记 `llm_used=false`。
- WF-07 归因为规则辅助，不把实验因素包装成确定结论。
- 本地精确匹配和预比对模式只用于工程测试，结果为 `PARTIAL/in_silico_only`。
- 生产证据等级必须由真实 BLAST+、版本化数据库和湿实验逐级提升。
- 数据库内容、权限、更新策略、性能压测和生产安全属于服务器联调阶段。

## 第三阶段验收

1. WF-01～WF-07 均可通过 Service、CLI 和 HTTP API 调用。
2. P4 可识别重复染料、VIC/HEX、TAMRA、ROX 参比及交叉二聚体风险。
3. P5 在有测试数据时完成内标候选，在无数据时返回 `NOT_CONFIGURED`。
4. WF-06 汇总设计、内标、变异和推荐反应条件。
5. WF-07 输出量化证据、归因置信度、优化路径和可选验证结果。
6. 数据库目录能将逻辑资源键解析为路径并记录版本证据。
7. `/health` 与 `/ready` 语义分离。
8. Docker Compose 使用只读数据库卷和独立任务结果卷。
9. 所有输出通过 Draft 2020-12 JSON Schema。
10. 全量自动测试通过。
