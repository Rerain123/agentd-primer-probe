# WF-01 PRD v0.2 M3：搜索、去重、评分与 Top10 契约

M3 消费 M2 已完成的候选发生记录，验证有限搜索清单，在单个靶标内去重，准备三维评分并生成非最终 Top10。

接口：

- `POST /v1/wf01/tasks/{batch_id}/run-m3`
- `AgentDService.execute_wf01_v02_m3(batch_id)`
- 纯靶标汇总函数 `WF01V02M3Engine.run_target(target)`

## 搜索清单 v1

PRD 没有规定区域切片、步长和每单元枚举数量，M0 将其登记为 DEC-01。当前实现采用显式、版本化且可替换的 v1 策略：

- 策略版本：`wf01-v0.2-whole-template-v1`。
- 区域：整条模板。
- 参数：去重后的 `user/default/L1/L2`。
- 每个单元最多返回 50 个 Primer3 结果。
- 最大枚举量：`参数单元数 × 50`。
- 当前不切片，因此步长为 `null`。
- 策略状态：`PROVISIONAL_PENDING_DEC_01_APPROVAL`。

所有 `ExplorationUnit` 在 Primer3 调用前生成，并保存：

- 单元 ID、参数来源和完整 Primer3 参数。
- 区域、排除区域、每单元最大返回数。
- `PENDING → RUNNING → COMPLETED/TOOL_ERROR` 状态。
- 开始/完成时间、生成/硬排除/硬通过/未评估计数。
- 候选 ID 和工具错误。

执行循环始终遍历整个预生成清单，没有候选数驱动的 `break`。达到第 10 个候选后仍执行剩余单元。

M3 区分两个状态：

- `primer3_search_complete=true`：所有预生成 Primer3 单元均成功完成。
- `search_complete=false`：M4 三类特异性尚未运行，因此整个 PRD 搜索不能声称完成。

## 靶标内去重

去重键严格为：

```text
SHA256(forward_primer + reverse_primer + probe)
```

规则：

- 每个靶标单独调用去重器，禁止跨靶标合并。
- 保留主候选，同时保存全部原候选 ID、参数档、搜索单元、区域和 Primer3 penalty。
- 合并 `user/default/L1/L2` 来源标签。
- 规则冲突选择更严格且已经完成的结果，并记录冲突摘要。
- 重复出现次数不参与评分。
- 输出原始发生数、唯一候选数和折叠重复数。

## 三维评分

固定权重：

| 维度 | 权重 |
|---|---:|
| 热力学 | 0.357 |
| 特异性 | 0.357 |
| 扩增子特征 | 0.286 |
| 保守性 | 0 |

保守性固定为 `NOT_EVALUATED`，不以 0 分进入归一化。

评分公式版本为 `wf01-v0.2-three-dimension-document-framework-v4`，仅用于
in-silico 候选排序，仍需后续实验数据校准：

```text
policy_status = ACTIVE_FOR_IN_SILICO_RANKING_REQUIRES_EXPERIMENTAL_CALIBRATION
score_scale  = 0..100
calculation_decimals = 6
display_decimals = 1
```

### 热力学维度

热力学由四个等权子项组成：

| 子项 | 子项权重 | 归一化方法 |
|---|---:|---|
| 正向引物 Tm | 0.25 | 通过 R01 硬规则的 50–65°C 候选统一得 100，不按绝对 Tm 高低排序 |
| 反向引物 Tm | 0.25 | 同上 |
| 正反向引物 ΔTm | 0.25 | `100×(2-ΔTm)/2`，限制在 0–100 |
| 探针相对 Tm | 0.25 | `8°C≤探针 Tm－平均引物 Tm≤10°C` 统一得 100；区间外由 R07 硬性排除 |

公式：

```text
thermodynamics =
    forward_tm_score × 0.25
  + reverse_tm_score × 0.25
  + primer_delta_tm_score × 0.25
  + probe_relative_tm_score × 0.25
```

发卡和二聚体仍由硬规则排除或软规则扣分处理，不在基础分中重复扣减。

引物绝对 Tm 的边界处理独立于热力学维度基础分：

- `<50°C` 或 `>65°C`：R01 硬规则失败，直接排除，不进入评分排序。
- `50°C ≤ Tm <55°C`：基础分不做线性降分；由 `R01_SOFT` 对候选统一扣 5%。
- `55°C ≤ Tm ≤65°C`：不扣分，55°C 与 60°C 等合法值在绝对 Tm 子项中地位相同。
- 同一候选无论一条还是两条引物低于 55°C，`R01_SOFT` 只扣一次，避免重复扣减。

### 特异性维度

M4 将所需特异性模式按最严格状态汇总：

| 汇总状态 | 维度分 | 后续处理 |
|---|---:|---|
| PASS | 100 | 进入排序 |
| CAUTION | 70 | 保留并标注，需要人工复核 |
| FAIL | 0 | 直接排除，不生成最终分 |
| NOT_EVALUATED | 无 | 不生成最终分，不得伪装为 PASS |

### 扩增子特征维度

长度和 GC 各占 0.5：

- `x=70 bp` 的长度子项为 70，并保留 R02 边界 WARNING。
- `70<x≤150 bp` 的长度子项为 100，属于推荐范围。
- `150<x≤200 bp` 的长度子项统一为 70，属于允许探索的非推荐范围。
- `x<70 bp` 或 `x>200 bp` 由 R02 硬性排除，不进入评分。
- GC 35–65% 为 100；范围外向 0 线性递减。

```text
amplicon_features = length_score × 0.5 + gc_score × 0.5
```

M3 阶段特异性为 `NOT_EVALUATED`，所以：

- `base_score=null`
- `final_score=null`
- `score=null`
- `rank=null`

M3 只用已经评估的热力学和扩增子维度形成 `pre_specificity_priority`，用于确定性预览，不能当作最终得分。

当 M4 提供特异性分后，统一公式为：

```text
base_score =
    thermodynamics × 0.357
  + specificity × 0.357
  + amplicon_features × 0.286

final_score =
    base_score × (1 - additive_penalty_percent / 100)
```

软扣分逐项保留并加总：

- `R01_SOFT`：5%
- `R03_SOFT`：2%
- `R11`：3%
- `R12`：3%

R02 的 70 bp 警戒只降低扩增子长度子项，不进入通用扣分。

总扣分封顶为 100%，最终分最低为 0。任一必需维度缺失时，
`base_score`、`final_score` 和正式 `rank` 均为空；仅特异性尚未运行时状态为
`PENDING_SPECIFICITY`，其他维度缺失时为 `INCOMPLETE_DIMENSIONS`。

最终同分按以下顺序稳定排序：

1. 最终分降序。
2. 累计软扣分升序。
3. 热力学分降序。
4. 特异性分降序。
5. 扩增子特征分降序。
6. Primer3 pair penalty 升序。
7. 去重键升序。

评分输出必须保存策略版本、0–100 量纲、三个维度原始分和权重、基础分、
逐项扣分、累计扣分、实际应用扣分、最终分、缺失维度及同分排序合同。

## Top10 边界

M3 输出：

- `provisional_top10`：最多 10 个，固定系统数量，调用方不可修改。
- `all_hard_rule_pass_candidates`：全部通过硬规则且等待 M4 的唯一候选。
- `all_hard_rule_rejected_candidates`：全部硬规则排除候选。
- `all_hard_rule_incomplete_candidates`：硬规则证据不完整候选。

`top10_is_final=false`，`ranking_status=PROVISIONAL_PENDING_M4_SPECIFICITY`。M4 完成特异性排除和最终评分后必须重新排序。

## 状态与持久化

M3 执行时批次短暂进入 `RUNNING`，完成后仍为 `EXPLORING`：

- `m3_outcome=COMPLETED` 或 `COMPLETED_WITH_BLOCKED_TARGETS`
- `primer3_search_complete` 表示本地 Primer3 搜索是否完整
- `search_complete=false`
- `search_incomplete_reason=M4_SPECIFICITY_NOT_RUN`

事件记录 M3 批次开始、靶标开始、靶标完成/阻塞和批次完成。M3 不允许原地重跑覆盖，后续重设计必须创建新版本。

## 验证

自动化测试：`tests/test_wf01_v02_m3.py`

真实 Primer3 2.3.0 三靶标冒烟结果：

- 每个靶标 150 个候选发生记录。
- 每个靶标去重后 112 个唯一候选。
- 折叠 38 个重复来源。
- 47 个通过硬规则并等待特异性。
- 65 个硬规则排除。
- 预览 Top10 数量为 10。
- `primer3_search_complete=true`，`search_complete=false`。
