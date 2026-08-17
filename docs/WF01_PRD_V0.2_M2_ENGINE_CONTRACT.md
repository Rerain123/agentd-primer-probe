# WF-01 PRD v0.2 M2：普通 TaqMan 单靶标引擎契约

M2 消费 M1 创建的 `QUEUED` 批次，对其中每个靶标独立运行 Primer3 和新版规则引擎。实现入口：

- `POST /v1/wf01/tasks/{batch_id}/run-m2`
- `AgentDService.execute_wf01_v02_m2(batch_id, options)`
- 纯单靶标函数 `WF01V02TargetEngine.run_target(target)`

M2 不修改旧 `/v1/wf01/design`。单序列输入跳过 MAFFT，直接把整条模板交给 Primer3；多序列输入先调用 MAFFT，等待比对完成后由 Python 计算保守区，再按保守区调用 Primer3。简并、MGB、ViennaRNA、多重和内标模块仍不进入新版 WF01 主链路。

## 状态边界

成功执行 M2 后：

- 批次由 `QUEUED` 变为 `RUNNING`，M2 完成后变为 `EXPLORING`。
- 靶标由 `QUEUED` 变为 `RUNNING`，M2 完成后变为 `EXPLORING`。
- `search_complete=false`，原因固定为 `M3_FINITE_EXPLORATION_MANIFEST_NOT_RUN`。
- 候选通过 R01-R10 后只标为 `AWAITING_SPECIFICITY`，不能标为最终 PASS。
- R19 为 `NOT_EVALUATED`，特异性状态为 `PENDING_M4`。
- 去重、评分、Top10 均为 `PENDING_M3`。

因此，M2 输出不能被当作最终可交付 Top10。

## 参数档与去重

| 档位 | 引物 Tm | 产物长度 | 引物长度 | 普通探针长度 |
|---|---:|---:|---:|---:|
| default | 58–62°C | 70–150 bp | 18–28 nt | 22–32 nt |
| L1 | 58–65°C | 70–180 bp | 18–28 nt | 22–32 nt |
| L2 | 50–65°C | 70–200 bp | 18–28 nt | 22–32 nt |

若 M1 存在用户参数覆盖，则先加入 `user` 档。参数字典完全相同的档位只调用一次 Primer3，并把来源标签合并到同一个 `parameter_run`。本期没有 L3。

Primer3 生成阶段把探针绝对 Tm 窗口换算为：

- 最小值：当前档引物最小 Tm + 8°C。
- 最优值：当前档引物最优 Tm + 9°C。
- 最大值：当前档引物最大 Tm + 10°C。

这只是候选生成窗口；每个候选仍必须由 R07 按“两条实际引物平均 Tm +8–10°C”重新硬判定。

M2 每个“整条模板 × 去重参数档”设计单元请求最多 50 个 Primer3 结果。这个数量不是 Top10。M2 现在会在调用 Primer3 前生成版本化 `ExplorationManifest`；M3 验证所有单元的终态、判断 Primer3 搜索完整性，并执行去重、评分准备和 Top10 预览。当前整条模板策略仍等待 DEC-01 业务确认。

## 规则输出

M2 使用独立的 `WF01V02RuleEngine`，不复用旧版含 MGB 和旧长度范围的规则引擎。

- R01-R10：硬规则。任一 `FAIL` 进入 `hard_rule_rejected_candidates`；任一 `NOT_EVALUATED` 进入 `hard_rule_incomplete_candidates`。
- R01、R02、R03 的软警戒分别输出 `R01_SOFT`、`R02_SOFT`、`R03_SOFT`。
- R11、R12：软规则，WARNING 不会排除候选。
- 固定软扣分在 M2 记录但不计算最终分：R01_SOFT 5%、R03_SOFT 2%、R11 3%、R12 3%，采用加法累积。
- R02 恰为 70 bp 只记录 `amplicon_feature_degraded=true`，最终扩增子特征分由 M3 计算。
- R13、R14、R15、R17：`NOT_APPLICABLE`。
- R16：`NOT_EVALUATED`（本期不做保守性）。
- R18：按 Primer3/热力学元数据完整性判定。
- R19：`NOT_EVALUATED`（M4 尚未运行）。
- R20：明确 `in_silico_only` 和未实验验证。
- R21：`NOT_EVALUATED`（M6 完整报告尚未汇总）。

开发降级模式必须显式传入：

```json
{"allow_development_fallback": true}
```

降级算法没有真实 ΔG 时，R09/R10/R11/R12 会如实返回 `NOT_EVALUATED`；候选不能进入硬规则通过集合。

## M2 持久化结构

每个靶标新增：

- `m2_execution.parameter_runs`
- `m2_execution.exploration_units`
- `m2_execution.candidate_counts`
- `m2_execution.hard_rule_pass_candidates`
- `m2_execution.hard_rule_rejected_candidates`
- `m2_execution.hard_rule_incomplete_candidates`
- `m2_execution.primer3_status`

批次和靶标事件按严格递增 `event_seq` 记录 M2 开始、靶标开始、靶标完成/工具失败和 M2 完成。

## 自动化验收

测试文件：`tests/test_wf01_v02_m2.py`

覆盖：

- AC-PAR-01 至 AC-PAR-03。
- AC-RUL-01 至 AC-RUL-05 的 M2 部分。
- AC-SCP-01 的 M2 无越界模块调用部分。
- AC-EXE-01 的 Primer3/规则/状态记录部分。
- AC-EXE-02 的整条模板参数设计单元部分。
- R13-R21 在 M2 时点不伪造未执行证据。

本地真实 Primer3 2.3.0 冒烟测试使用三靶标固定样例，每个靶标完成 default/L1/L2 共 150 个原始候选，且所有靶标保持 `EXPLORING`、`search_complete=false`。
