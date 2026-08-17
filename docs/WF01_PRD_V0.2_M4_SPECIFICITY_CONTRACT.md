# WF-01 PRD v0.2 M4：适用范围特异性与最终重排契约

M4 消费 M3 的全部硬规则通过候选。只输入序列时，工作流走 `DESIGN_ONLY`，不启动 BLAST+；输入序列和靶标后走 `FULL_SPECIFICITY`，为每个候选建立靶标命中和综合背景 nt 两条必需证据链。用户同时提供宿主时，再追加宿主专库排除证据链。M4 排除特异性失败候选，并按完整三维得分重新排序。

接口：

- `POST /v1/wf01/tasks/{batch_id}/run-m4`
- `AgentDService.execute_wf01_v02_m4(batch_id)`
- 纯靶标引擎 `WF01V02M4Engine.run_target(target)`

## 独立结果及适用性

每个 M3 硬规则通过候选均产生：

1. `TARGET_HIT`：提供靶标后必需。
2. `RELATED_BACKGROUND_NT`：提供靶标后必需；即使没有显式近缘种/背景对象，也对版本化综合背景库做广谱检索。
3. `HOST_EXCLUSION`：可选；仅当用户提供宿主时执行，否则返回带原因的 `NOT_APPLICABLE`，不降低总体状态。

每项证据保存：

- M1 的 `original_input` 和标准化 `SpecificityObject`。
- 数据库逻辑键、类型、绝对路径或 URI、版本、范围和解析状态。
- 当前策略版本的完整阈值。
- 正向引物、反向引物和探针查询序列。
- 命中 accession、identity、coverage、e-value、query/subject 坐标、3′ 错配、标题/物种和可用的热力学结果。
- `PASS`、`CAUTION`、`FAIL`、`NOT_EVALUATED` 或可选对象为空时的 `NOT_APPLICABLE`。
- 机器可读判定理由、工具错误和执行时间。

宿主按对象分别给出结果，再以最严格状态合并；related/background 使用同一版本化 nt/背景库，但原始对象列表和范围查询全部保留。

## 数据库解析门槛

M4 不根据名称猜测文件路径。数据库由 `AGENTD_DATABASE_CATALOG` 指向的资源目录提供，支持以下逻辑键：

```text
target:{identifier|taxid|accession|normalized_name|scientific_name}:blast
target:{target_id}:blast
target:default:blast

host:{identifier|taxid|accession|normalized_name|scientific_name}:blast

nt:default:blast
cross:default:blast
```

资源只有同时满足以下条件才是 `RESOLVED`：

- 已配置本地路径；
- FASTA 文件或 BLAST 数据库索引确实存在；
- `version` 非空且不等于 `SET_ON_SERVER`。

路径、版本或必需对象缺失时，该模式为 `NOT_EVALUATED`，不会调用 BLAST，也绝不产生特异性 PASS。M1 支持猪、牛、鸡、鸭、鹅、鸽的名称规范化，但这不等于宿主库已经配置。宿主未提供不是缺失输入，不会阻止 `TARGET_HIT` 和 `RELATED_BACKGROUND_NT`；若提供宿主但对应宿主库不可用，则该用户请求的宿主排除模式为 `NOT_EVALUATED`。

## 判定表与实验校准边界

当前实现使用可审计、可版本化的 in-silico 排序策略；阈值仍需实验数据持续校准：

```text
policy_version = wf01-v0.2-specificity-document-framework-v4
policy_status  = ACTIVE_FOR_IN_SILICO_RANKING_REQUIRES_EXPERIMENTAL_CALIBRATION
```

| 模式 | 当前临时判定 |
|---|---|
| TARGET_HIT | `blastn-short` 以 e-value 1000 做宽松检索；e-value 仅记录、不参与靶标命中判定。每条寡核苷酸 identity=100%、coverage=100%、3′ 错配=0 为 PASS；identity/coverage 均不低于 95% 为 CAUTION；否则 FAIL |
| HOST_EXCLUSION | e-value < 0.1；ΔG < -6 kcal/mol 且 3′ 错配不超过 1，或同 accession 1000 bp 内存在相向、异链的正反引物兼容位点时 FAIL；有其余有效命中时 CAUTION；无命中时 PASS |
| RELATED_BACKGROUND_NT | e-value < 1e-5；非靶标同 accession 1000 bp 内存在相向、异链的正反引物兼容位点时 FAIL；identity/coverage 均不低于 95% 且 3′ 错配不超过 1 时 CAUTION；无相关非靶标命中时 PASS |

宿主存在 BLAST 命中但缺少判定所需热力学数据时为 `NOT_EVALUATED`，不能按弱命中放行。

汇总状态进入评分时固定映射为 PASS=100、CAUTION=70、FAIL=0；
NOT_EVALUATED 不产生维度分。上述映射只服务于候选排序，不等同于实验性能结论。
实验校准导致阈值或映射变化时，必须更新策略版本、自动化边界用例和本契约。

## 工具与开发降级

- 只有真实 `blastn` 结果可以形成 PASS/CAUTION/FAIL。
- `exact_fasta_development_fallback` 只用于工程链路测试；其查询和命中会保留，但模式状态固定为 `NOT_EVALUATED`。
- BLAST 不可用、非零退出、超时、数据库错误或解析错误均转为模式级 `NOT_EVALUATED`。
- 一类数据库/工具异常不会删除其他模式证据，也不会删除其他靶标已经保存的结果。
- M4 当前完成本地版本化数据库的同步执行和靶标级故障隔离；NCBI/nt 的并发、重试、最大等待及断点策略仍受 `DEC-06` 阻塞。

## 排除、评分与 Top10

候选总体状态取适用的必需模式中的最严格值：`TARGET_HIT` 和 `RELATED_BACKGROUND_NT` 始终必需；`HOST_EXCLUSION` 仅在用户提供宿主时加入汇总。

```text
FAIL > NOT_EVALUATED > CAUTION > PASS
```

- 任一模式 `FAIL`：候选进入 `all_specificity_failed_candidates`，`score=null`、`rank=null`，不允许降低阈值挽回。
- 任一必需模式 `NOT_EVALUATED`：候选进入 `all_specificity_incomplete_candidates`，不得评分或进入 Top10。
- 全部模式完成且无 FAIL：PASS 临时特异性分为 100，CAUTION 为 70。
- 最终分继续使用 M3 固定权重 `0.357 / 0.357 / 0.286` 和逐项加法扣分。
- 所有合格候选重新排序，输出固定最多 10 个，同时保留全部合格、全部特异性排除、全部证据不完整和 M3 硬规则排除候选。

真实、版本化 BLAST 必需证据完整时，候选 `evidence_level=blast_verified`；若指定了宿主，则宿主排除证据也必须完整。否则为 `in_silico_only`。M4 会把 R19 更新为 `PASS`、`WARNING`、`FAIL` 或 `NOT_EVALUATED`。

## 搜索状态边界

M4 输出两个不同概念：

- `technical_search_complete=true`：M3 全部 Primer3 单元完成，且每个待检查候选的必需特异性模式均已完成。
- `search_complete=false`：M5 尚未汇总正式靶标/批次状态，并且 DEC-01/DEC-02 尚未业务签字。

即使 `technical_search_complete=true`，M4 也只标记：

```text
ranking_status = FINAL_UNDER_PROVISIONAL_POLICY
stage = M4_COMPLETE_AWAITING_M5
```

不能在 M4 提前声称整个 PRD 搜索正式完成。

## 验证

自动化测试：`tests/test_wf01_v02_m4.py`

覆盖：

- AC-SPE-01：靶标与综合背景结果独立存在，并按数据库批量搜索全部候选；宿主结果按用户输入决定执行或显式不适用。
- AC-SPE-02：原始/标准化对象、版本、阈值、查询、命中和坐标可审计。
- AC-SPE-03：对象、路径、版本、工具、热力学或 nt 异常不产生 PASS/得分/排名。
- 特异性 FAIL 排除、CAUTION 评分、M4 最终重排、R19 和证据等级。
- API 持久化、事件序号和禁止原地覆盖。
