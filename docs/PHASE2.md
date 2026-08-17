# Agent D 第二阶段实现与架构

## 交付边界

第二阶段版本为 `0.2.0`，输出 Schema 为 `2.0.0-phase2`。本阶段交付：

- P3：MGB 探针序列上下文修正 Tm、5 条硬性规则、5 条软性规则和不确定性声明。
- P6：ViennaRNA 2.5+、Mathews 2004 DNA 参数、60°C/50 mM Na⁺ 条件下的扩增子 MFE；提供次优结构 API。
- WF-01 增强：短探针或显式 MGB 模式、扩增子二级结构、简并引物并行策略和结构风险降权。
- WF-05：代表性序列比对、≥2 次观测的变异识别、风险分级、变异 Tm、三档覆盖率及重设计建议。
- C29/C30：候选横向比较 Markdown 与确定性自然语言解释。
- C7：扩展后的统一 JSON Schema、工具版本和配置 SHA-256 证据链。

WF-02、WF-03、WF-06、WF-07、P4、P5 不在第二阶段范围内。

## 总体流程

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

## P3 计算边界

P3 先调用 P1 的 SantaLucia 1998 Tm，再按 AT 比例施加公开数据工程近似修正。结果固定声明 `estimated_error_celsius=3.0` 和模型名称，不冒充 Primer Express 专利算法。修正后目标区间为 65–72°C。

WF-01 在 `use_mgb=true` 或 `probe_type=TaqMan_MGB` 时强制使用 13–20 nt MGB 设计参数。未显式指定时，≤20 nt 的短探针进入 P3；普通长探针继续执行 R-07。

## P6 降级边界

P6 可用时必须加载 `RNA.params_load_DNA_Mathews2004()`，温度设为 60°C，单价盐浓度设为 0.050 M。判定为：

- `PASS`：ΔG ≥ -20 kcal/mol；
- `CAUTION`：-30 ≤ ΔG < -20 kcal/mol；
- `WARNING`：ΔG < -30 kcal/mol。

ViennaRNA 不可用或原生库报错时，输出 `not_evaluated`，不以经验公式伪造 MFE，也不阻断 P6 所在工作流。

## 简并策略

策略只处理落在候选引物结合区内、且替代碱基至少在 2 条序列中出现的位点。触发后：

1. 将两条引物分别编码为 IUPAC；
2. 最多接受 3 个简并位点和 8 个引物对组合；
3. 对每个精确序列调用 P1 计算 Tm；
4. 对全部精确序列批量执行目标、宿主、近缘种 P2 检查；
5. 最大 Tm 极差 ≤2°C 为 `FEASIBLE`，2–4°C 为 `MARGINAL`，>4°C 为 `INFEASIBLE`。

该支路不改变标准候选列表，输出位于 `degenerate_strategy`。实际混合引物池竞争、3′端延伸效率和阈值仍需梯度 PCR 标定。

## WF-05 输入约定

请求必须包含：

- `forward_primer`、`reverse_primer`，`probe` 可选；
- `sequences` 或 `fasta_path`；第一条序列作为参考；
- 可选 `target_id`、`target_species`。

首条序列必须能定位完整扩增子。序列数少于 30 时继续运行，但设置 `sequences_warning`。变异频率分母为全部输入序列数。

## 报告与证据

C29 直接由结构化结果生成 Markdown。C30 当前使用 `deterministic_scientific_template`，输出中明确 `llm_used=false`，避免在未配置受控模型、提示词版本和审计记录时声称使用 LLM。两者都属于展示层，不反向修改科学判定。

每次结果记录：Agent 版本、Schema 版本、完整配置 SHA-256、各工具可用性和版本、计算方法、规则实际值及阈值。降级结果只能为 `PARTIAL` 或明确的未评估状态。

## 验收项

1. WF-01 普通模式、MGB 模式和 WF-04 不回归。
2. P3 对 FAM 第二位 G、长度、连续碱基、3′端 G 和 GC% 输出硬性判定。
3. P6 输出参数集、温度、盐浓度、MFE、结构、工具版本和阈值状态。
4. 简并策略能枚举 2 个双等位位点的 4 种组合并计算 Tm 极差。
5. WF-05 对 30 条测试序列中的 3′端变异给出 HIGH，并在覆盖率低于 95% 时建议重设计。
6. 所有候选仍输出完整 24 条 R 规则记录。
7. CLI、API 与任务存储使用同一 Workflow 和 Schema。
8. 所有自动测试通过后才可标记阶段完成。
