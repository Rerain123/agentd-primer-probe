# Agent D 阶段实现历史

> 本文合并原 `PHASE1.md`、`PHASE2.md` 和 `PHASE3.md`。这些内容用于追溯历史实现边界，不覆盖当前 WF-01 M0–M7 契约或 WF-02 v0.1 实施台账。

> 现行状态（2026-08-26）：旧 WF-06、WF-07 已从运行时退役。下文出现的相关能力、关系图和接口均为历史记录，不代表当前可用入口。

## 导航

- [第三阶段](#phase-3)
- [第二阶段](#phase-2)
- [第一阶段](#phase-1)

<a id="phase-3"></a>

## Agent D 第三阶段：完整框架与服务器部署边界

### 阶段结果

本节记录的历史基线版本为 `1.2.4`，输出 Schema 为 `3.0.0-phase3`。当时 Agent D 的七个 Workflow 均已提供统一入口和可运行编排；现行入口以页首退役说明为准：

| Workflow | 能力 | 本地无数据库行为 |
|---|---|---|
| WF-01 | 单靶标引物探针设计 | 允许内联/FASTA 测试数据 |
| WF-02 | 2–4 靶标多重面板 | P5 失败时记录原因并继续不含内标面板 |
| WF-03 | 内标设计 | P5 获取默认管家基因序列后执行完整设计 |
| WF-04 | 已有方案验证 | 数据库项为 `NOT_CHECKED` |
| WF-05 | 变异耐受性 | 需要请求或目录提供代表性序列 |
| WF-06 | 完整诊断面板 | 未接入的内标/变异步骤保留结构化状态 |
| WF-07 | 问题诊断与优化 | 规则归因可运行，数据库证据不伪造 |

### 总体架构

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

### P4 多重兼容性

P4 内置 14 种荧光基团、6 种淬灭基团和主流仪器通道配置。每套面板检查：

- 发射峰间距、重复染料和 VIC/HEX 冲突；
- TAMRA 禁用及报告基团—淬灭基团覆盖；
- 仪器滤光片、最大通道数和 ROX 被动参比冲突；
- 所有引物/探针两两全长及 3′端局部异源二聚体；
- 引物和探针 Tm 极差、扩增子 GC% 极差；
- WF-02 额外执行扩增子长度差 ≥20 bp。

WF-02 无严格兼容面板时依次记录二聚体/Tm 放宽和分管建议，不静默降低阈值。

### P5 内标设计

P5 默认优先级为 ACTB、GAPDH、18S rRNA，覆盖 pig、cattle、chicken 的 RefSeq 标识，但不把序列硬编码到代码中。序列来源顺序：

1. 请求的 `gene_fasta_paths`；
2. 请求的 `gene_sequences`；
3. 可选的 DatabaseCatalog 管家基因序列覆盖；
4. P5 根据内置 RefSeq 编号从 NCBI Entrez 实时获取参考序列及至少 5 条非重复同物种同源序列。

Entrez 来源记录查询式、访问时间、RefSeq 编号和实际 accession 列表。获取序列后执行 MAFFT 保守性、Primer3、R 规则、目标面板交叉二聚体、宿主/病原交叉检查及 ViennaRNA。18S rRNA 自动记录 50–100 nM 限量引物策略。获取失败时 P5 返回 `FAIL`；WF-02 按专家规则自动继续不含内标面板。

### 服务器数据库目录

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

### 健康检查

- `/health`：进程存活，不检查外部资源。
- `/ready`：本地检查 Primer3；生产模式同时检查 Primer3、MAFFT、BLAST+ 和部署策略要求的数据库资源。
- `/v1/tool-status`：返回 P1～P6、C0/C1/C5/C6/C7 的详细状态。
- `/v1/capabilities`：返回七个 Workflow 和服务器环境变量契约。

本地配置 `require_databases_for_readiness=false`；生产配置和 Compose 固定为 `true`。因此本地可以验证框架，服务器缺库时容器不会被错误标记为 ready。

### Docker Compose

```powershell
Copy-Item deploy\.env.example deploy\.env
docker compose --env-file deploy\.env -f deploy\docker-compose.yml build
docker compose --env-file deploy\.env -f deploy\docker-compose.yml up -d
```

正式启动前必须：

1. 将 BLAST 数据库放入 `AGENTD_BLASTDB_DIR`；
2. 将靶标代表性序列及可选的自定义序列覆盖放入 `AGENTD_SEQUENCE_DATA_DIR`；
3. 修改 `deploy/server-config/database-catalog.json` 的版本及路径；
4. 调用 `/ready`，确认所有 `required=true` 资源可用；
5. 调用 `/v1/tool-status`，确认 BLAST+、MAFFT、Primer3、ViennaRNA 均可用。

数据库文件不写入镜像，任务结果写入独立持久化卷。容器使用非 root 用户、只读根文件系统和受限资源；目标测试宿主的 `no-new-privileges` 会阻止非 root 进程创建 socket，因此该宿主不启用此项。

### 科学与框架边界

- C30 仍使用确定性解释模板并标记 `llm_used=false`。
- WF-07 归因为规则辅助，不把实验因素包装成确定结论。
- 本地精确匹配和预比对模式只用于工程测试，结果为 `PARTIAL/in_silico_only`。
- 生产证据等级必须由真实 BLAST+、版本化数据库和湿实验逐级提升。
- 数据库内容、权限、更新策略、性能压测和生产安全属于服务器联调阶段。

### 第三阶段验收

1. WF-01～WF-07 均可通过 Service、CLI 和 HTTP API 调用。
2. P4 可识别重复染料、VIC/HEX、TAMRA、ROX 参比及交叉二聚体风险。
3. P5 使用内置 RefSeq 配置通过 NCBI Entrez获取默认管家基因及同源序列，并保留可审计来源。
4. WF-06 汇总设计、内标、变异和推荐反应条件。
5. WF-07 输出量化证据、归因置信度、优化路径和可选验证结果。
6. 数据库目录能将逻辑资源键解析为路径并记录版本证据。
7. `/health` 与 `/ready` 语义分离。
8. Docker Compose 使用只读数据库卷和独立任务结果卷。
9. 所有输出通过 Draft 2020-12 JSON Schema。
10. 全量自动测试通过。

<a id="phase-2"></a>

## Agent D 第二阶段实现与架构

### 交付边界

第二阶段版本为 `0.2.0`，输出 Schema 为 `2.0.0-phase2`。本阶段交付：

- P3：MGB 探针序列上下文修正 Tm、5 条硬性规则、5 条软性规则和不确定性声明。
- P6：ViennaRNA 2.5+、Mathews 2004 DNA 参数、60°C/50 mM Na⁺ 条件下的扩增子 MFE；提供次优结构 API。
- WF-01 增强：短探针或显式 MGB 模式、扩增子二级结构、简并引物并行策略和结构风险降权。
- WF-05：代表性序列比对、≥2 次观测的变异识别、风险分级、变异 Tm、三档覆盖率及重设计建议。
- C29/C30：候选横向比较 Markdown 与确定性自然语言解释。
- C7：扩展后的统一 JSON Schema、工具版本和配置 SHA-256 证据链。

WF-02、WF-03、WF-06、WF-07、P4、P5 不在第二阶段范围内。

### 总体流程

```text
                           ┌─ P3 MGB评估（短探针/显式MGB）
输入 → C1规范化 → C5比对 → P1设计 ─┼─ P6扩增子结构
                           └─ R-01～R-21规则
                                      ↓
                   P2特异性 → 评分/排序 → C29/C30 → C7 Schema
                         ↘
                    简并并行支路：IUPAC → ≤8枚举 → P1 Tm → P2 BLAST
```

```text
WF-05输入（已有引物探针 + 序列集）
  → C5 MAFFT
  → 将参考序列坐标映射到比对列
  → 扫描引物/探针结合区中至少2次观测的变异
  → HIGH（引物3′末5nt）/MEDIUM（探针）/LOW（引物其余）
  → P1重算单变异Tm，|ΔTm|>2°C标记可能漏检
  → 完全匹配 / 1个非3′错配 / 2个错配覆盖率
  → ROBUST / ACCEPTABLE / REDESIGN_RECOMMENDED
  → C29/C30 + C7
```

### P3 计算边界

P3 先调用 P1 的 SantaLucia 1998 Tm，再按 AT 比例施加公开数据工程近似修正。结果固定声明 `estimated_error_celsius=3.0` 和模型名称，不冒充 Primer Express 专利算法。修正后目标区间为 65–72°C。

WF-01 在 `use_mgb=true` 或 `probe_type=TaqMan_MGB` 时强制使用 13–20 nt MGB 设计参数。未显式指定时，≤20 nt 的短探针进入 P3；普通长探针继续执行 R-07。

### P6 降级边界

P6 可用时必须加载 `RNA.params_load_DNA_Mathews2004()`，温度设为 60°C，单价盐浓度设为 0.050 M。判定为：

- `PASS`：ΔG ≥ -20 kcal/mol；
- `CAUTION`：-30 ≤ ΔG < -20 kcal/mol；
- `WARNING`：ΔG < -30 kcal/mol。

ViennaRNA 不可用或原生库报错时，输出 `not_evaluated`，不以经验公式伪造 MFE，也不阻断 P6 所在工作流。

### 简并策略

策略只处理落在候选引物结合区内、且替代碱基至少在 2 条序列中出现的位点。触发后：

1. 将两条引物分别编码为 IUPAC；
2. 最多接受 3 个简并位点和 8 个引物对组合；
3. 对每个精确序列调用 P1 计算 Tm；
4. 对全部精确序列批量执行目标、宿主、近缘种 P2 检查；
5. 最大 Tm 极差 ≤2°C 为 `FEASIBLE`，2–4°C 为 `MARGINAL`，>4°C 为 `INFEASIBLE`。

该支路不改变标准候选列表，输出位于 `degenerate_strategy`。实际混合引物池竞争、3′端延伸效率和阈值仍需梯度 PCR 标定。

### WF-05 输入约定

请求必须包含：

- `forward_primer`、`reverse_primer`，`probe` 可选；
- `sequences` 或 `fasta_path`；第一条序列作为参考；
- 可选 `target_id`、`target_species`。

首条序列必须能定位完整扩增子。序列数少于 30 时继续运行，但设置 `sequences_warning`。变异频率分母为全部输入序列数。

### 报告与证据

C29 直接由结构化结果生成 Markdown。C30 当前使用 `deterministic_scientific_template`，输出中明确 `llm_used=false`，避免在未配置受控模型、提示词版本和审计记录时声称使用 LLM。两者都属于展示层，不反向修改科学判定。

每次结果记录：Agent 版本、Schema 版本、完整配置 SHA-256、各工具可用性和版本、计算方法、规则实际值及阈值。降级结果只能为 `PARTIAL` 或明确的未评估状态。

### 验收项

1. WF-01 普通模式、MGB 模式和 WF-04 不回归。
2. P3 对 FAM 第二位 G、长度、连续碱基、3′端 G 和 GC% 输出硬性判定。
3. P6 输出参数集、温度、盐浓度、MFE、结构、工具版本和阈值状态。
4. 简并策略能枚举 2 个双等位位点的 4 种组合并计算 Tm 极差。
5. WF-05 对 30 条测试序列中的 3′端变异给出 HIGH，并在覆盖率低于 95% 时建议重设计。
6. 所有候选仍输出完整 24 条 R 规则记录。
7. CLI、API 与任务存储使用同一 Workflow 和 Schema。
8. 所有自动测试通过后才可标记阶段完成。

<a id="phase-1"></a>

## Agent D 第一阶段实现说明

### 实现边界

第一阶段交付两个可运行 Workflow：

- WF-01：单靶标 TaqMan 引物探针设计。
- WF-04：已有引物探针热力学和特异性验证。

专业能力包括 P1 Primer3 适配器、P2 特异性编排、C1 序列 IO、C5 MAFFT 适配器、C6 BLAST+ 适配器、C7 统一 JSON Schema，以及 R-01～R-21 规则引擎。

P3～P6、WF-02/03/05/06/07 和简并碱基正式策略不属于本阶段。其中 P6 对应的扩增子二级结构规则会稳定输出 `NOT_EVALUATED`，不会被静默视为通过。

### 科学计算边界

- Primer3、BLAST+、MAFFT 可用时调用真实工具。
- 开发降级功能必须由请求中的 `allow_fallback=true` 或 CLI 的 `--allow-fallback` 显式开启。
- 开发降级 Tm 使用 Wallace 近似，只用于验证工程链路；不标记为 SantaLucia。
- 开发降级特异性只做本地 FASTA 精确匹配，不产生 BLAST e-value。
- 任何降级结果均为 `PARTIAL` 和 `in_silico_only`。
- 未配置宿主或近缘种数据库时输出 `NOT_CHECKED`，不默认通过。
- P1 在导入 `primer3-py` 前通过子进程探测原生扩展。DLL、ABI 或路径兼容性错误只会让 P1 进入不可用/降级状态，不会导致 API 主进程崩溃。
- Windows 安装路径包含非 ASCII 字符时，P1 会将已安装的 `primer3` 包镜像到版本化 ASCII 临时缓存，再加载其热力学参数；缓存路径可通过 `AGENTD_ASCII_CACHE_DIR` 覆盖。

### Workflow 数据流

```text
输入校验
  → C1序列规范化
  → C5多序列比对/保守区
  → P1候选设计
  → R-01～R-18规则过滤
  → P2靶标/宿主/交叉检查
  → R-19～R-21证据与完整性
  → 候选评分、排序、自检
  → C7 Schema验证
  → JSON结果和任务记录
```

### 验收检查

1. 同一输入和同一配置产生可复现的候选顺序。
2. 每个候选输出完整的 R-01～R-21 判定记录。
3. 每项判定包含实际值、阈值、状态和数据来源。
4. P1/P2/C5 不可用时不会伪造科学结果。
5. WF-01 无候选时返回结构化失败报告和各阶段淘汰计数。
6. WF-04 支持模板可选；无模板时相关规则为 `NOT_EVALUATED`。
7. 输出通过 C7 结构校验并可由 CLI、API 和任务查询共同消费。
