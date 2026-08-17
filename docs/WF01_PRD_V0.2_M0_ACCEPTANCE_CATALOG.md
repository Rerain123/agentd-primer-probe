# WF-01 PRD v0.2 M0：验收目录

所有用例以新版 PRD 为唯一判定来源。`PLANNED` 表示用例已冻结、尚待对应实现阶段落地；不应拿当前旧工作流的通过结果替代这些验收。

| 用例 | 阶段 | 验收条件 | 预期结论 | 状态 |
|---|---|---|---|---|
| AC-SCP-01 | M1-M2 | 发送多条模板 | 逐条独立建靶标；无 MAFFT/保守性/MGB/L3 调用 | IMPLEMENTED_M1_M2 |
| AC-IN-01 | M1 | 三记录 FASTA | 创建 3 个按原顺序排列的靶标 | IMPLEMENTED_M1 |
| AC-IN-02 | M1 | 多文件/多记录输入 | 记录 ID、来源文件、序号、长度完整保留 | IMPLEMENTED_M1 |
| AC-IN-03 | M1 | 79 nt 与 80 nt 模板 | 79 nt 阻塞；80 nt 可进入后续预检 | IMPLEMENTED_M1 |
| AC-IN-04 | M1 | 重复 ID、非法字符、缺失序列 | 返回带靶标作用域的结构化 Issue | IMPLEMENTED_M1 |
| AC-IN-05 | M1 | 只给名称或 accession、不给模板 | `PENDING_CONFIRMATION`，不得自动抓取模板 | IMPLEMENTED_M1 |
| AC-IN-06 | M1-M4 | 未能解析物种/对象 ID | 返回可修正或移除的 Issue；不得标记 PASS | IMPLEMENTED_M1_M4 |
| AC-IN-07 | M1 | 批次默认与靶标参数冲突 | 合法靶标级值覆盖批次值并被记录 | IMPLEMENTED_M1 |
| AC-IN-08 | M1 | 输入完整与输入不完整批次 | 前者自动运行；后者待确认 | IMPLEMENTED_M1 |
| AC-PAR-01 | M2 | 默认档 | 58-62°C、70-150 bp、18-28 nt、探针 22-32 nt | IMPLEMENTED_M2 |
| AC-PAR-02 | M2 | L1/L2 | L1=58-65/70-180；L2=50-65/70-200 | IMPLEMENTED_M2 |
| AC-PAR-03 | M2 | 用户档与默认/L1/L2 相同 | 同一最终参数组合只执行一次 | IMPLEMENTED_M2 |
| AC-RUL-01 | M2 | R01-R04 边界候选 | 硬规则失败候选被排除 | IMPLEMENTED_M2 |
| AC-RUL-02 | M2 | R05-R08 探针边界候选 | 仅普通探针重新生成；无 MGB 兜底 | IMPLEMENTED_M2 |
| AC-RUL-03 | M2 | R09/R10 ΔG 和 3′ 参与边界 | 触发阈值即排除 | IMPLEMENTED_M2 |
| AC-RUL-04 | M2 | 候选不足 | 硬规则阈值不变 | IMPLEMENTED_M2 |
| AC-RUL-05 | M2-M3 | R11/R12 和其他软警戒 | 候选保留、逐项 WARNING、扣分累加 | IMPLEMENTED_M2_M3 |
| AC-EXE-01 | M2-M5 | 单靶标正常运行 | 依序记录 Primer3、规则、三类特异性、汇总 | IMPLEMENTED_M2_M5_PROVISIONAL_POLICIES |
| AC-EXE-02 | M2-M3 | 多档/多区域探索 | 每个 ExplorationUnit 有参数、区域和计数 | IMPLEMENTED_M3_PROVISIONAL_WHOLE_TEMPLATE_POLICY; DEC_01_APPROVAL_PENDING |
| AC-EXE-03 | M5 | 一靶标失败、其他靶标可运行 | 批次部分成功，成功靶标结果保留 | IMPLEMENTED_M5 |
| AC-EXE-04 | M3 | 在第十个候选后仍有未跑单元 | 不早停，直至 ExplorationManifest 完成 | IMPLEMENTED_M3; DEC_01_POLICY_APPROVAL_PENDING |
| AC-DED-01 | M3 | 同靶标相同三条寡核苷酸 | 合并为一个候选并保留所有来源 | IMPLEMENTED_M3 |
| AC-DED-02 | M3 | 不同靶标相同三条寡核苷酸 | 绝不跨靶标合并 | IMPLEMENTED_M3 |
| AC-SCO-01 | M3-M4 | 多项软规则同时命中 | 基础分与每项扣分、最终分均可解释 | IMPLEMENTED_M3_M4_PROVISIONAL_POLICY |
| AC-SCO-02 | M3-M4 | 有/无保守性字段的候选 | 仅 35.7/35.7/28.6 参与本期评分 | IMPLEMENTED_M3_M4_PROVISIONAL_POLICY |
| AC-SPE-01 | M4 | 完整特异性对象集 | target、host、related/background 分别有结果 | IMPLEMENTED_M4_PROVISIONAL_POLICY; DEC_02_APPROVAL_PENDING |
| AC-SPE-02 | M4 | 特异性命中 | 保存原始输入、标准化对象、库版本、阈值、命中详情 | IMPLEMENTED_M4_PROVISIONAL_POLICY; DEC_02_APPROVAL_PENDING |
| AC-SPE-03 | M4-M5 | 对象解析失败或 nt/工具异常 | `NOT_EVALUATED`，不得 PASS 或称搜索穷尽 | IMPLEMENTED_M4_M5 |
| AC-STA-01 | M5 | 0、1-9、>=10 候选与完整/不完整搜索组合 | 正确输出四种靶标结果状态 | IMPLEMENTED_M5 |
| AC-STA-02 | M5 | 混合靶标状态批次 | 批次成功/部分成功/失败正确汇总 | IMPLEMENTED_M5 |
| AC-STA-03 | M5 | 外部工具中断 | 记录完成范围、未完成范围、原因和建议 | IMPLEMENTED_M5 |
| AC-RPT-01 | M6 | 任意完成批次 | R01-R21、二级结构、热力学元数据、免责声明齐全 | IMPLEMENTED_M6 |
| AC-RPT-02 | M6 | 在线只筛选一个靶标后导出 | HTML/Excel/PDF 仍含全批次完整数据 | IMPLEMENTED_M6 |
| AC-RPT-03 | M6 | 生成完整报告 | Top10、全合格、全排除、搜索记录和命中证据齐全 | IMPLEMENTED_M6 |
| AC-RPT-04 | M6 | 本期外能力 | MGB/多重/内标为 NOT_APPLICABLE；保守性和二级结构为 NOT_EVALUATED | IMPLEMENTED_M6 |
| AC-VER-01 | M7 | 修改一个靶标创建新版本 | 修改靶标重跑，未修改靶标复用且记录 diff | IMPLEMENTED_M7 |

## 执行规则

- 每个用例落地后必须实现自动化测试；科学工具不可用时使用确定性的 adapter mock，不得用开发降级结果证明特异性 PASS。
- 每个测试名称包含用例 ID，例如 `test_ac_in_01_multifasta_creates_independent_targets`。
- 合并代码前，测试必须同时验证输出值、规则状态、溯源字段和不应出现的本期外能力。
- 满足所有 `PLANNED` 用例，并完成 DEC-01/DEC-02 的业务确认后，才可声明 WF-01 PRD v0.2 完成。
