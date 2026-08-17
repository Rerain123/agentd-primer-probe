# WF-01 PRD v0.2 M5：靶标与批次状态汇总契约

M5 不运行 Primer3 或 BLAST，也不改变任何科学阈值。它只消费 M2–M4 已保存的候选、完成范围和异常证据，形成严格的靶标状态、批次状态与待处理建议。

接口：

- `POST /v1/wf01/tasks/{batch_id}/run-m5`
- `AgentDService.execute_wf01_v02_m5(batch_id)`
- 纯汇总引擎 `WF01V02M5Engine`

## 搜索完整性与候选数量相互独立

M5 先判断 `search_complete`，再判断候选数量。候选达到 10 个不能使未完成搜索变成完整搜索。

`search_complete=true` 必须同时满足：

- M4 明确返回正式 `search_complete=true`；
- M4 的 `technical_search_complete=true`；
- 不存在未完成 ExplorationUnit；
- 不存在 `NOT_EVALUATED` 的候选特异性；
- 不存在缺少最终分或排名的“合格”候选。

`technical_search_complete` 与正式 `search_complete` 分开保存。DEC-01/DEC-02 尚未批准时，技术调用可以完成，但正式搜索仍不能宣称完成。

## 四种靶标结果状态

| 条件 | 靶标状态 | 含义 |
|---|---|---|
| `search_complete=true` 且可交付候选不少于 10 | `SUCCEEDED` | 搜索完整并获得固定 Top10 |
| `search_complete=true` 且可交付候选为 1–9 | `PARTIAL` | 搜索完整，但候选不足 10 |
| 可交付候选不少于 1，但 `search_complete=false` | `PARTIAL_INCOMPLETE` | 有待复核候选，但搜索或证据未完成 |
| 可交付候选为 0 | `FAILED` | 无可交付候选；搜索是否完整单独记录 |

因此：

- 0 个候选且搜索完整仍为 `FAILED`，同时允许 `search_complete=true`，表示完整搜索后确实没有合格候选。
- 10 个以上候选但 BLAST/数据库异常仍为 `PARTIAL_INCOMPLETE`，不能显示成功。
- 1–9 个候选只有在搜索完整时才是 `PARTIAL`；搜索不完整时仍是 `PARTIAL_INCOMPLETE`。

## 可交付候选与不完整证据

M5 的可交付候选包括：

1. M4 已完成特异性、具有最终分和正式排名的合格候选；
2. 已通过硬规则，但因工具、数据库、对象或策略未完成而缺少特异性终判的候选。

第二类候选不会伪造得分或正式排名：

```text
score = null
rank = null
m5_delivery.status = INCOMPLETE_EVIDENCE
selection_basis = M3_PRE_SPECIFICITY_PRIORITY
```

M5 使用独立的 `display_rank` 生成最多 10 个待复核展示项。正式合格候选优先，未完成证据候选按 M3 的预特异性顺序补充。特异性 `FAIL`、硬规则 `FAIL` 和硬规则证据不完整的候选仍保留在原阶段排除集合中，不会重新进入可交付候选。

如果 M4 整体中断，M5 会从 M3 已去重的硬规则通过候选恢复待复核结果；如果 M3 汇总中断，则从 M2 的硬规则通过记录恢复并在靶标内去重。恢复候选只可能形成 `PARTIAL_INCOMPLETE`。

## 完成范围、未完成范围和建议

每个靶标保存：

- `completed_scope`：Primer3 探索、特异性评估和候选交付的完成摘要。
- `incomplete_scope`：未完成 ExplorationUnit、候选/对象/模式、数据库键和版本、工具错误、阶段错误及待批准策略。
- `incomplete_reasons`：去重后的机器可读原因。
- `recommendations`：与原因对应的结构化后续动作。

推荐动作包括：

- `CONFIGURE_VERSIONED_SPECIFICITY_DATABASES`
- `RETRY_AFTER_TOOL_RECOVERY`
- `RETRY_INCOMPLETE_EXPLORATION_UNITS`
- `CONFIRM_PENDING_SCIENTIFIC_POLICIES`
- `RECALCULATE_INCOMPLETE_CANDIDATE_FIELDS`
- `REVIEW_INCOMPLETE_EVIDENCE_BEFORE_EXPERIMENT`
- `REDESIGN_WITH_UNCHANGED_HARD_THRESHOLDS`

工具中断只影响对应未完成范围；已完成靶标、候选、错误和命中证据全部保留。

## 三种批次结果状态

| 靶标矩阵 | 批次状态 |
|---|---|
| 所有靶标均为 `SUCCEEDED` | `SUCCEEDED` |
| 至少一个靶标有可交付候选，但并非全部 `SUCCEEDED` | `PARTIALLY_SUCCEEDED` |
| 所有靶标均为 `FAILED` | `FAILED` |

批次同时保存：

- 四种靶标状态计数；
- 可交付/合格/证据不完整候选总数；
- `target_status_matrix`；
- 失败靶标 ID 和探索未完成靶标 ID；
- 聚合后的建议；
- 正式与技术搜索完整性。

一个靶标失败不会删除其他靶标结果，也不会阻止批次形成 `PARTIALLY_SUCCEEDED`。

## 当前 M4 临时策略的影响

当前 M4 的 DEC-01/DEC-02 仍待业务批准；如果运行环境没有解析到已部署的版本化 BLAST 数据库，则会得到：

```text
target.status = PARTIAL_INCOMPLETE
batch.status = PARTIALLY_SUCCEEDED
search_complete = false
```

这不是计算失败，而是严格区分“已有待复核候选”和“正式搜索已完成”。M4 只接受服务器本地版本化数据库作为正式特异性证据。

## 状态与持久化

执行 M5 时：

1. 批次和靶标短暂进入 `SUMMARIZING`；
2. 每个靶标生成 `M5_COMPLETE_AWAITING_M6`；
3. 批次生成最终结果状态和状态矩阵；
4. 记录单调递增的开始、靶标汇总和批次完成事件；
5. 不允许原地覆盖 M5 结果。

M5 完成后进入 M6 canonical report 与导出契约，不在 M5 生成 HTML/Excel/PDF。

候选数量不再形成用户交互边界。每个靶标少于 3 个可交付候选时，M5 写入
`candidate_notice.status=INSUFFICIENT` 后直接进入 M6：1–2 个候选标记“候选不足”；
0 个候选保留 `NO_DELIVERABLE_CANDIDATES`，并根据已保存阶段计数记录 Primer3 未生成、
硬规则淘汰或特异性淘汰等原因。M6 必须照常生成空结果报告。

## 验证

自动化测试：`tests/test_wf01_v02_m5.py`

覆盖：

- AC-STA-01：0、1–9、至少 10 个候选与完整/不完整搜索的四状态矩阵。
- AC-STA-02：全成功、混合状态和全失败批次汇总。
- AC-STA-03：Primer3/BLAST 中断的完成范围、未完成范围、错误和建议。
- AC-EXE-03：失败靶标不影响其他靶标交付。
- AC-SPE-03：`NOT_EVALUATED` 不会升级为成功或搜索完整。
- API、持久化、事件顺序和禁止原地重跑。
