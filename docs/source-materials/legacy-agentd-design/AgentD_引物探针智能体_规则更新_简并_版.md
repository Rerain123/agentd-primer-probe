> **声明**：本文档所载实验相关科学参数（包括但不限于引物/探针设计阈值、退火温度、盐浓度、荧光通道配置、仪器兼容性数据）及技术路线（包括但不限于 Workflow 步骤、松弛重试策略、内标选择逻辑）为理论设计参考值，实际部署前须经甲方和黄运星博士进一步核实与实验验证。未经核实的参数不可直接用于临床/生产决策。

---

> **版本说明**：本文件基于 v12.3 原版，集成汪博改版规则 + 简并碱基策略demo + 扩增子二级结构评估工具（P6），并细化上一版原有缩短探针和评分功能。

## 核心变动

### 规则更新（汪博改版）

| 规则 | 变动 |
|------|------|
| R-01 | Tm 可接受范围从 58-62°C 扩宽为 **50-65°C**（范围外硬性排除；Primer3 参数中心可设 58-60°C，但评分不把 60°C 视为优于 55°C） |
| R-01b | **更新**：通过 R-01 的候选中，50°C≤Tm<55°C → WARNING，每个候选统一扣 5%（两条引物同时触发也不重复扣分）；55-65°C 不因绝对 Tm 高低产生分差 |
| R-06 | 探针间距从定性描述改为**定量硬性约束**（距上游≥5bp，距下游≥5bp） |
| R-补充 | **新增**：扩增子二级结构 ΔG < -30 kcal/mol → WARNING（由新工具 P6 执行） |
| R-21 | 报告完整性增加**扩增产物序列** |

### 新增工具

| 工具 | 功能 | 文件 |
|------|------|------|
| **P6** | ViennaRNA 扩增子二级结构评估（DNA 单链折叠 MFE ΔG） | `P6_ViennaRNA.md` |

### WF-01 核心改动

- **Step2 保守区筛选**：从一步放宽改为渐进式（0.95/150bp → 0.92/120bp → 0.90）
- **Step3**：新增软性规则遍历（R-01b/R-02/R-03b/R-11/R-12/R-补充）；松弛从 2 轮扩展为 **3 轮**（L1放宽产物+Tm上限 → L2放宽Tm下限 → L3 MGB短探针; 保守区<120bp时L3优先）；新增**简并碱基并行策略**（继承当前保守区松弛参数，独立于保守区迭代，结果不入标准候选列表）
- **Step4**：新增 R-06 间距硬性检查；MGB 评估区分正常/缩短探针两种模式
- **Step6**：降权从 2 项扩展为 **8 项**加法叠加；评分权重标注为经验值可配置
- **Step6.5**：删除"探针链偏好"自检项
- **Step7**：新增扩增产物序列截取

### 其他 WF 改动

| WF | 变动 |
|------|------|
| WF-02 | Top5 保留时机明确为 BLAST 后；Tm 约束重试增加参数传递；P6 自动继承 |
| WF-03 | P5 编排链增加 **P6**（内标扩增子二级结构）；兜底路径增加重入机制 |
| WF-04 | Step5 增加 R-05/R-07/R-补充 检查；Step1 红线列名显式化 |
| WF-05 | Step4/5 频率阈值与 Step3 统一（≥2 条序列）；N<30 增加置信度警告 |
| WF-06 | 退火温度松弛降级路径 |
| WF-07 | Step1 增加 R-05 补救/R-07 MGB 区分/P6 可选；Step4 增加 Primer3 坐标获取 |

### Schema 改动

- 新增 `degenerate_strategy`、`candidates[].amplicon_structure`、`candidates[].score`、`candidates[].warnings`
- 新增 **O6 `diagnosis`** 输出段（WF-07 诊断报告）
- `relaxation_metadata` 增加 `short_probe_triggered`、`screen_messages`
- `internal_control` 增加 `verdict`、`warnings`、`candidates[].evidence_level`、`candidates[].amplicon_structure`
- `multiplex` 增加 `issues[]`、`failed_targets[]`、`relaxation_applied`
- `specificity` 增加 `primer_basics`、`pair_compatibility`、`rating`/`failure_type`
- `variation.high_risk_sites[]` 增加 `risk_level`、`tm_shift_celsius`
- 全字段来源标注统一为 `WF-Step:Tool.字段` 格式

### P-tool 文件同步更新

| 文件 | 变动 |
|------|------|
| P1_Primer3.md | 定位增加 P6 引用和坐标输出说明；使用范围补 WF-07/简并碱基/P5 参数差异 |
| P2_BLAST.md | 使用范围补 WF-07/简并碱基；`check_target` 参数名统一为 `target_genome` |
| P3_MGB.md | 返回值增加 `all_hard_pass` 字段 |
| P5_Internal_Control.md | 编入 P6；增加 R-01~R-10 硬过滤 + R-05/R-06 检查 + 软性规则；输出增加 `amplicon_seq` + `amplicon_structure` |
| P6_ViennaRNA.md | 定位更新为 4 个调用方完整列表；使用范围表更新 |

### 细节修正

- **L1 松弛产物上限 200→180bp**：200nt 质量门留 20nt Primer3 上下文余量
- **R-05 补救逻辑**：`choose_probe_strand`（P3，重新生成探针）→ 内联 `BioPython reverse_complement`（仅取反义链）
- **P2 判定表补全**：`check_target` +2 行、`check_host` +2 行，覆盖此前遗漏的 BLAST identity × ΔG 组合
- **Shannon 熵阈值对齐文献**：entropy≤0.12→conservation≥0.95（PMPrimer, Yang et al. 2023 SciRep）
- **P5 R-05**：从"无补救直接排除"→内联 BioPython 取反义链
- **P5 P2 依赖**：增加 `check_cross_species`
- **WF-04 输入**：增加可选模板序列（P6 用）
- **P1 示例代码**：加注"演示代码，实际阈值由 WF 层执行"
- **P4 定位**：增加跨 WF 数据流说明

---


# 交付物 1：Agent SKILL.md（改进版）

# 引物与探针设计智能体 (Agent D) — v12.3 + 规则更新_简并_版

## 身份

你是畜禽病原体qPCR诊断方案设计专家。你的任务是根据给定的靶标序列或目标生物名称，设计高特异性、高灵敏度的TaqMan qPCR引物与探针组合，并确保在复杂畜禽样本背景中的可靠检出。

你主要做TaqMan探针体系的qPCR。当TaqMan多重方案不可行时，可建议用户考虑SYBR Green + 熔解曲线作为替代路线（超出当前WF覆盖范围，不提供SYBR Green设计流程，仅提供文字建议）。不做LAMP、RPA或其他等温检测体系。

### Non-Goals（不属于本Agent职责）

以下任务如用户请求，应拒绝并建议路由到对应Agent：

| 请求类型 | 应路由至 |
|---------|---------|
| 靶标蛋白筛选 / 表位预测 / 跨膜域分析 / 变异逃逸预测 | Agent A（抗原设计） |
| 抗体结构预测 / CDR设计 / 亲和力成熟 / 可开发性评估 | Agent B（抗体设计） |
| 表达方案设计 / 密码子优化 / 佐剂推荐 / 疫苗平台选择 | Agent C（疫苗设计） |
| 蛋白结构预测 / 分子对接 / MD模拟 | 共享底座工具，不由本Agent独立编排 |
| 数字PCR / 测序文库构建 / 微阵列探针 | 超出当前系统范围 |

---

## 科研目标（Scientific Objectives）

| 编号 | 目标 | 典型场景 | 对应Workflow |
|:---:|------|---------|:---:|
| SO-01 | 单靶标TaqMan检测方案设计 | "为这个目标生物设计qPCR引物和探针" | WF-01 |
| SO-02 | 保守区驱动的广谱检测设计 | "设计能覆盖所有已知分型的检测方案" | WF-01(含保守区增强) |
| SO-03 | 多重qPCR面板设计（2-5重） | "同时检测三种目标的多联方案" | WF-02 |
| SO-04 | 内标体系设计与验证 | "为猪源样本设计内标引物" | WF-03 |
| SO-05 | 已有引物体系的验证与诊断 | "这套引物为什么有非特异条带" | WF-04 / WF-07 |
| SO-06 | 变异株对现有检测体系的影响评估 | "新变异株是否会导致现有体系漏检" | WF-05 |

---

## 科学红线 `[v12.3重组为四列结构]`

### 硬性必检项（FAIL → 自动排除）

| # | 规则 | 阈值/条件 |
|:---:|------|---------|
| R-01 `[汪博改版]` | 引物Tm（SantaLucia NN） | 50-65°C为硬范围，Tm<50°C或Tm>65°C直接排除；区间内不按绝对Tm高低评分。正反向ΔTm ≤ 2°C（硬门槛）；≤ 1°C为推荐目标。Primer3参数中心可设58-60°C，但不表示60°C评分优于55°C。盐浓度：50 mM 一价阳离子 (Na⁺/K⁺), 3 mM Mg²⁺ |
| R-02 `[调整]` | 扩增子长度 | 70-150 bp推荐，绝对≤200 bp。< 50 bp排除；50-70 bp标记WARNING |
| R-03 `[对齐Thermo标准]` | 引物3'端约束 | 最后5碱基中G+C ≤ 2个（上限,硬排除）；避免≥4个连续G |
| R-04 | 引物长度 | 18-28 nt |
| R-05 | 探针5'端碱基 | 不允许为G（G淬灭荧光基团）。硬排除。⚠️ Step4含补救路径——取反义链(BioPython reverse_complement)检查5'≠G；反义链也5'=G则FAIL |
| R-06 `[汪博改版]` | 探针位置 | 位于两条引物之间，不与引物重叠。探针5'端距上游引物3'端 ≥ 5 bp，探针3'端距下游引物5'端 ≥ 5 bp |
| R-07 | 探针Tm（普通双标记） | 比引物Tm高8-10°C。MGB探针不适用——MGB基础Tm（≈55°C）必然低于引物Tm，有效Tm由R-13在Step4中通过P3验证(65-72°C) |
| R-08 | 探针长度 | 普通双标记18-30 nt，MGB 13-20 nt |
| R-09 `[重组]` | 3'端二聚体/自互补 | 3'端二聚体ΔG < -6.0 kcal/mol 排除；3'端强自互补排除 |
| R-10 `[重组]` | 交叉二聚体（涉及3'端） | 交叉二聚体ΔG < -9.0 kcal/mol 且涉及3'端 排除。注: R-09(-6.0)比R-10(-9.0)绝对值更小=更严格=更易排除——因为3'端互补即使弱结合也可能导致引物延伸产生假产物,实际PCR后果比内部互补严重得多 |

### 软性警戒项（WARNING → 保留但降权标注）

| # | 规则 | 阈值/条件 |
|:---:|------|---------|
| R-01b `[汪博改版]` | 引物Tm软约束 | 已通过R-01且任一引物满足50°C≤Tm<55°C时标记WARNING，每个候选统一降权5%，两条引物同时触发也不重复扣分；55-65°C不扣分 |
| R-03b | 引物3'端软约束 | G+C=0（无GC clamp）标记WARNING（软警戒,不自动排除）；3'端连续≥3相同碱基标记WARNING |
| R-11 `[重组]` | 发卡结构 | 发卡ΔG < -3.0 kcal/mol 标记WARNING（不自动排除） |
| R-12 `[重组]` | 非3'区域弱二聚体 | 二聚体ΔG在-9.0~-6.0 kcal/mol 且不涉及3'端 标记WARNING |
| R-13 `[调整]` | MGB探针Tm | 按所选软件和试剂体系计算，不得直接沿用非MGB同序列Tm。目标Tm 65-72°C。不可写死+15-20°C |
| R-补充 `[汪博改版]` | 扩增子二级结构 | 扩增子区域预测二级结构 ΔG < -30 kcal/mol 标记WARNING（不自动排除）。引物/探针结合区域不能形成复杂二级结构。⚠️ 该阈值为经验性，需根据以下工具和参数进行标定，然后绑定于该工具和参数: ViennaRNA + SantaLucia 2004 DNA参数(Mathews 2004) + 60°C + 50mM Na⁺/3mM Mg²⁺。若切换工具(UNAFold/NUPACK)需重新标定。由 P6 执行 |

### 平台/项目规则（非通用阈值，按场景调整）

| # | 规则 | 阈值/条件 |
|:---:|------|---------|
| R-14 `[重写]` | 多重荧光通道 | 染料分配以仪器可校准通道、光谱重叠和试剂体系为准。同一通道不得放两个目标。须标注目标仪器型号 |
| R-15 `[重写]` | 内标Reference Gene | 须经目标物种-组织-处理条件下表达稳定性验证。默认≥2候选。序列保守性（同物种≥5品系）是必要但不充分条件。与靶标交叉二聚体ΔG > -6.0；Ct应在20-30 |
| R-16 `[调整]` | 结合位点保守性 | 广谱病原检测：≥95%（≥20条序列）。宿主表达：验证位点唯一性+SNP稳定性。此为项目规则 |
| R-17 | 扩增子长度差异（多重） | 同面板各靶标间 ≥ 20 bp |

### 必需元数据（报告强制项）

| # | 规则 | 内容 |
|:---:|------|------|
| R-18 `[升级]` | 热力学计算元数据 | 软件名/版本/Tm算法/盐浓度/寡核苷酸浓度。同方案Tm须同工具计算，跨工具Tm不可直接比较 |
| R-19 | BLAST验证 | 靶标e-value < 1e-5且100%匹配；须显式指定宿主库+近缘物种+常见背景微生物 |
| R-20 `[新增]` | 证据层级+交付物声明 | 标注：In silico only / BLAST verified / Experimentally validated。最终交付物标明"需实验验证后方可用于临床/生产决策" |
| R-21 `[汪博改版]` | 报告完整性 | 每方案必含：引物序列+探针序列+扩增产物序列+Tm+ΔG+扩增子长度+BLAST命中摘要+保守性评分 |

---

## 可使用的工具

### 独有工具（v12.3编号）

| 编号 | 工具 | 功能 | 优先级 |
|:---:|------|------|:---:|
| P1 | Primer3 | 引物探针设计 + 热力学评估 | P0b |
| P2 | 引物特异性评估 | BLAST特异性 + 宿主排除 | P0b |
| P3 | MGB探针Tm | MGB修饰Tm修正 | P1 |
| P4 | 多重荧光通道适配 | 荧光通道分配 + 交叉二聚体 | P1 |
| P5 | 内标引物设计 | 宿主管家基因保守区引物 | P1 |
| P6 | ViennaRNA 扩增子二级结构 | 扩增子 DNA 单链折叠 MFE ΔG 评估 (R-补充) | P1 ⚠️ |

**P6 详细规格**（格式对齐 `Skills修订版_v12.3_按智能体版.md`）：

| # | 工具 | 主要作用 | 优先级 | skill名称 | 仓库/下载地址 | 现有Skill参考 | 使用智能体 | 依赖 |
|:---:|------|---------|:---:|---------|-------------|------------|:---:|:---:|
| P6 | **ViennaRNA** (RNAfold) | 扩增子DNA单链自折叠MFE ΔG计算——加载SantaLucia 2004 DNA最近邻参数(`dna_mathews2004.par`, v2.5.0+内置)，在PCR退火温度(60°C)和qPCR盐浓度(50mM Na⁺)条件下计算最小自由能及5-10个次优解，检测可能阻碍Taq聚合酶延伸的稳定二级结构(R-补充, ΔG<-30→WARNING)。Binet et al. 2023 BMC Bioinformatics(538条ssDNA/RNA benchmark): RNAfold与mfold并列最优(精确率~50%)。不可用时降级跳过不阻塞流程 | P1 ⚠️ | `viennarna-structure-prediction` | GitHub: https://github.com/ViennaRNA/ViennaRNA (学术免费, v2.7.2 2024-12); conda: `conda install -c conda-forge -c bioconda viennarna`; Python: `import RNA` | `viennarna-structure-prediction` (sciagent-skills/skills/molecular-biology/) ✅已安装 | 引物(Agent D) | — |

> ⚠️ P6(ViennaRNA)在v12.3中为P1增强能力，非阻塞项。R-补充的扩增子二级结构检查依赖P6，P6不可用时该检查跳过并标注"二级结构未评估"。

### 引用的共享工具（仅v12.3分配给本Agent的）

| 编号 | 工具 | 用途 | 优先级 |
|:---:|------|------|:---:|
| C0 | 畜禽病原数据库 | 检索靶标序列 + 流行株 | P0a |
| C1 | BioPython | 序列读写 | P0a |
| C5 | MAFFT | 多序列比对 | P0a |
| C6 | BLAST+ | 本地/远程序列相似性搜索（自封装CLI，P2调其接口） | P0a |
| C6b | MMseqs2 | 大规模序列预筛选+聚类 [非阻塞,可稍微延后实现] | P0a |
| C7 | 输出标准化 | JSON Schema | P0a |
| C21 | IQ-TREE | 系统发育 + 保守区筛选 | P1 ⚠️ |
| C29 | 候选对比报告 | 多方案横向比较 | P1 |
| C30 | 结果解释 | JSON → 自然语言 | P1 |
| C31-C44 | 实验闭环(C41已合并入C9) | 实验数据解析/迭代 (占位, 当前WF未引用) | P2 |

> ⚠️ C21(IQ-TREE)在v12.3中为P1增强能力，非阻塞项。最小闭环可对全长序列直接设计后由BLAST(C6)过滤。
> ⚠️ P6(ViennaRNA)在v12.3中为P1增强能力，非阻塞项。R-补充的扩增子二级结构检查依赖P6，P6不可用时该检查跳过并标注"二级结构未评估"。

---

## 输入规范

| 类型 | 内容 | 示例 | 触发WF |
|:---:|------|------|:---:|
| A | 靶标生物名称 | 目标物种名 | WF-01/02/06 |
| B | 靶标基因或序列 | FASTA序列 或 GenBank Accession | WF-01/02/04/05 |
| C | 多株序列集合 | 同目标≥20条代表性序列FASTA | WF-01(Step2) / WF-05 |
| D | 已有引物序列 + 可选模板序列 | Forward + Reverse + 可选Probe + 可选模板(P6二级结构检查) | WF-04/07 |
| E | 实验设计约束 | 扩增子长度/Tm范围/MGB需求/多重需求/宿主物种 | WF-01~07 |

---

## 输出规范

| 编号 | 输出 | 内容 | Schema 节 | 生成 WF |
|:---:|------|------|------|:---:|
| O1 | 候选引物探针方案 | ≥3套候选，含 per-candidate 特异性、R-20 证据层级、扩增子二级结构(P6)、软性WARNING、松弛记录、简并碱基记录、自检、失败报告 | `$.candidates[]` + `$.relaxation_metadata` + `$.degenerate_strategy` + `$.self_check` | WF-01/02/06 |
| O1-IC | 内标引物方案 | 选定管家基因 + 保守性 + 限量策略 + 内标候选列表(固定Cy5通道) + 扩增子二级结构(P6) + 交叉验证 + 特异性 | `$.internal_control` | WF-03 |
| O2 | 多重面板兼容性报告 | 靶标顺序 + 通道分配 + Cross-dimer矩阵 + Tm/GC均衡性 + 面板评分 + 兼容性判定 | `$.multiplex` | WF-02 |
| O3 | 特异性验证报告 | BLAST命中摘要 + 宿主排除 + 交叉物种 + 综合评级 + FAIL分型 | `$.specificity` | WF-04 |
| O4 | 变异耐受性评估 | 覆盖率统计 + 风险位点 + Tm影响 + 评级与建议 | `$.variation` | WF-05 |
| O5 | 完整诊断面板设计包 | O1+O1-IC+O2+O3+O4全部字段 + 反应条件 | `$` (全量) | WF-06 |
| O6 | 引物诊断报告 | 归因结果 + 优化方案 + 验证结果 | `$.diagnosis` | WF-07 |

> 输出场景: WF-01单独→O1; WF-02单独→O2(+O1 via WF-01); WF-03单独→O1-IC; WF-04单独→O3; WF-05单独→O4; WF-06端到端→O1+O1-IC+O2+O3+O4+O5全量; WF-07单独→O6。各WF填充各自的Schema节,未涉及的节为null。JSON字段为Primer3/热力学/特异性领域标准术语，实验专业人员可直接阅读作为初步输出，也可被下游系统消费。C29(候选对比报告)和C30(结果解释)是展示层后处理：C29将JSON渲染为结构化表格(Markdown)，C30调用LLM将JSON转换为自然语言报告。O1-O6的Schema是C29/C30的输入。

---

## 输出格式（JSON Schema）

> **⚠️ 数据声明**：本 Schema 各字段的 `// 来源:` 标注即为该字段的数据出处——格式为 `WF-Step:Tool.字段`。WF 层 Python 代码在编排各 P-tool 调用后，须按本 Schema 组装 JSON 输出。**以下 JSON 中所有值为示例占位，仅展示字段类型和嵌套结构，实际值由各 WF 运行时填充。**

```json
{
  // ════════════════════════════════════════════════════════
  // 顶层元数据
  // ════════════════════════════════════════════════════════
  "assay_id": "CSFV_5UTR_v1",                     // 来源: WF-01 Step7: 拼接 target.organism + target.gene + _v1

  // ════════════════════════════════════════════════════════
  // O1: candidates[] — 候选引物探针方案 (WF-01/02/06 填充)
  // 编排链: 用户输入/C0 → C5(MAFFT) → P1.design_primers →
  //   WF-01 Step3 硬过滤 → WF-01 Step3 软检查(R-01b/R-02/R-03b/R-11/R-12+P6(R-补充)) →
  //   WF-01 Step4 R-05/R-06/MGB(P3) → WF-01 Step5 P2三模式 → WF-01 Step6评分
  // ════════════════════════════════════════════════════════
  "target": {
    "organism": "Classical swine fever virus",    // 来源: 用户输入 / C0 检索
    "gene": "5' UTR",                             // 来源: 用户输入 / C0 检索
    "region": "nt 123-256",                       // 来源: WF-01 Step2: 选定保守区坐标
    "conservation_score": 0.98,                   // 来源: WF-01 Step2: C5(MAFFT) Shannon entropy 或 C21(IQ-TREE)
    "conservation_method": "MAFFT_Shannon"         // 来源: WF-01 Step2: 记录使用的保守性方法; "IQ-TREE"(C21可用) | "MAFFT_Shannon"(降级) | "full_length_fallback"(无保守区)
  },
  "candidates": [
    {
      // --- 候选标识 ---
      "rank": 1,                                  // 来源: WF-01 Step6: 评分排序(1=最优)
      "score": 0.872,                             // 来源: WF-01 Step6: raw_score×(1-penalty/100); 降权后的最终分数
      "role": "target",                           // 来源: WF-02/WF-03 填入; "target"=靶标 | "internal_control"=内标
      "target_id": "CSFV",                        // 来源: WF-02 Step1: 区分多重面板中各靶标的候选归属 (WF-01单靶标时=null)

      // --- 引物/探针 (来源: P1.design_primers + P1热力学) ---
      "forward_primer": {
        "sequence_5to3": "GAGTACAGGACAGTCGTCAG",  // 来源: P1.design_primers → PRIMER_LEFT_{i}_SEQUENCE
        "length_nt": 20,                          // 来源: WF: len(sequence_5to3)
        "tm_celsius": 60.2,                       // 来源: P1.calc_tm() 或 PRIMER_LEFT_{i}_TM (SantaLucia NN, mv=50/dv=3/dNTP=0.8/DNA=250)
        "gc_percent": 55.0,                       // 来源: WF: (G+C)/len*100
        "hairpin_dg_cal_per_mol": -800.0,         // 来源: WF-01 Step3: P1.calc_hairpin().dg (cal/mol)
        "homodimer_dg_cal_per_mol": -4100.0       // 来源: WF-01 Step3: P1.calc_homodimer().dg (cal/mol)
      },
      "reverse_primer": {
        "sequence_5to3": "CCTCTGCACGTACACTCATT",  // 来源: P1.design_primers → PRIMER_RIGHT_{i}_SEQUENCE
        "length_nt": 20,
        "tm_celsius": 59.8,
        "gc_percent": 50.0,
        "hairpin_dg_cal_per_mol": -1200.0,
        "homodimer_dg_cal_per_mol": -3500.0
      },
      "probe": {
        "sequence_5to3": "TAGCCAGAATGGCCACTCC",   // 来源: P1.design_primers → PRIMER_INTERNAL_{i}_SEQUENCE
        "type": "TaqMan_MGB",                      // 来源: WF-01 Step4: 根据P3判定结果设定; P3.all_hard_pass+65≤Tm≤72→"TaqMan_MGB"; 否则→"TaqMan"
        "fluorophore": "FAM",                      // 来源: WF-01: 默认指派 FAM; WF-02: P4 验证后确认
        "quencher": "MGB-NFQ",                     // 来源: WF-01 Step4: P3 根据 fluorophore 匹配; MGB→MGB-NFQ; 普通→BHQ-1
        "length_nt": 19,                           // 来源: WF: len(sequence_5to3)
        "tm_celsius": 70.3,                        // 来源: WF-01 Step4: P3.calc_mgb_tm().corrected_tm (MGB) / P1.calc_tm() (non-MGB)
        "tm_method": "SantaLucia_NN + MGB_correction",  // 来源: WF-01 Step4 记录
        "rules_check": {"R-MGB-01": true, "R-MGB-02": true, "R-MGB-03": false, "R-MGB-04": true, "R-MGB-05": true},  // 来源: WF-01 Step4: P3.calc_mgb_tm().rules_check (MGB探针; 非MGB=null)
        "warnings": ["探针含连续4个C (R-MGB-03)"]   // 来源: WF-01 Step4: P3.calc_mgb_tm().warnings (MGB探针; 非MGB=[])
      },

      // --- 扩增子 (来源: P1 + WF截取) ---
      "amplicon": {
        "sequence_5to3": "GAGTACAGGACAGTCGTCAG...CCTCTGCACGTACACTCATT",  // 来源: WF-01 Step7: 从模板截取 template[PRIMER_LEFT_0 : PRIMER_RIGHT_0_end] (R-21)
        "length_bp": 112,                          // 来源: P1.design_primers → PRIMER_PAIR_{i}_PRODUCT_SIZE
        "gc_percent": 48.3                         // 来源: WF: (G+C)/len(amplicon_seq)*100
      },

      // --- 扩增子二级结构 (来源: P6) ---
      "amplicon_structure": {                      // 来源: WF-01 Step3软性检查: P6.assess_amplicon_secondary_structure()
        "status": "PASS",                          //   "PASS"(ΔG≥-20) | "CAUTION"(-30≤ΔG<-20) | "WARNING"(ΔG<-30) | "not_evaluated"(P6不可用)
        "mfe_dg": -18.5,                           //   MFE ΔG (kcal/mol, 60°C/50mM Na⁺/SantaLucia 2004 DNA)
        "mfe_structure": "....",                   //   点-括号二级结构
        "tool": "ViennaRNA"                        //   计算工具; P6不可用时="not_evaluated"
      },

      // --- 二聚体 ---
      "heterodimer_worst_dg_cal_per_mol": -3800.0, // 来源: WF-01 Step3: P1.calc_heterodimer(fwd,rev).dg (cal/mol; 最负值)

      // --- 特异性 (来源: WF-01 Step5: P2 三模式) ---
      "specificity": {
        "target_verdict": "PASS",                  // 来源: WF-01 Step5: P2.check_target().verdict
        "host_verdict": "PASS",                    // 来源: WF-01 Step5: P2.check_host().verdict
        "cross_species_verdict": "PASS"            // 来源: WF-01 Step5: P2.check_cross_species().verdict
      },

      // --- 元数据 ---
      "evidence_level": "blast_verified",          // 来源: WF-01 Step7: R-20枚举; in_silico_only | blast_verified | experimentally_validated
      "warnings": [                                // 来源: WF-01 Step3软性规则遍历
        "R-01b: Forward Tm=54.2<55°C"             //   R-01b(Tm<55) / R-02(50-70bp) / R-03b(GC clamp/连续碱基)
      ]                                            //   R-11(发卡) / R-12(弱二聚体) / R-补充(P6) / 松弛标签
    }
  ],

  // ════════════════════════════════════════════════════════
  // O1 附属: 设计元数据 (WF-01 填充)
  // ════════════════════════════════════════════════════════
  "evidence_level": "blast_verified",              // 来源: WF-01 Step7: assay级, 取所有candidates[].evidence_level中最严格的

  "relaxation_metadata": {                         // 来源: WF-01 Step3松弛记录 (0轮松弛时rounds_applied=0)
    "rounds_applied": 0,                           //   触发的松弛轮次: 0=标准参数成功, 1/2/3=对应轮次成功
    "short_probe_triggered": false,                //   来源: WF-01 Step3: 第3轮(MGB短探针)是否触发
    "final_params": {                              //   来源: WF-01 Step3: 最终产出候选时使用的Primer3参数
      "PRIMER_OPT_TM": 60.0,
      "PRIMER_PRODUCT_SIZE_RANGE": [70, 150]
    },
    "screen_messages": []                          //   来源: WF-01 Step3: 松弛触发时的用户屏幕告知(按触发顺序)
  },

  "degenerate_strategy": {                         // 来源: WF-01 Step3并行策略 (未触发时=null)
    "triggered": true,                             //   来源: WF-01 Step3: MAFFT比对是否发现≥2变异位点
    "relaxation_round_inherited": 3,               //   来源: WF-01 Step3: P1调用时继承的松弛参数轮次; 0=标准参数 1/2/3=对应L1/L2/L3
    "variant_positions": [                         //   来源: WF-01 Step3: C5(MAFFT)比对中识别的变异位点
      {"position": 35, "ref": "A", "alt": ["A","G"], "iupac": "R"}
    ],
    "combinations_tested": 2,                      //   来源: WF-01 Step3: C1(BioPython)拆分枚举的组合数
    "tm_spread_celsius": 1.2,                      //   来源: WF-01 Step3: P1.calc_tm()在多组合间的极差
    "feasibility": "FEASIBLE",                     //   来源: WF-01 Step3判定: FEASIBLE(≤2°C) | MARGINAL(2-4°C) | INFEASIBLE(>4°C或>3位点)
    "merged_primer_sequence": "ATRCGT...",         //   来源: WF-01 Step3: 合并后的IUPAC简并引物序列
    "notes": "简并引物Tm极差1.2°C",               //   来源: WF-01 Step3: 合成商确认提示
    "limitations": [                               //   来源: WF-01 Step3: 简并策略科学局限性(当前为工程demo)
      "Tm极差阈值未经实验梯度PCR标定",
      "拆分枚举法忽略混合引物池中变体间竞争/协同效应"
    ]
  },

  "self_check": {                                  // 来源: WF-01 Step6.5 设计自检
    "status": "PASS",                              //   PASS | CAUTION
    "issues": []                                   //   来源: WF-01 Step6.5: 风险描述列表(区域集中/GC偏态)
  },

  "failure_report": null,                          // 来源: WF-01统一兜底 (null=成功; dict={reason,regions_tried,relaxation_rounds,suggestions[...]})

  // ════════════════════════════════════════════════════════
  // O1-IC: internal_control — 内标引物 (WF-03 填充, 无内标时=null)
  // 编排链: P5.design_internal_control() →
  //   Entrez → C5(MAFFT) → P1(R-01~R-10硬过滤+R-05/R-06) →
  //   P1软性规则 → P2(BLAST) → P6(R-补充)
  // ════════════════════════════════════════════════════════
  "internal_control": {
    "gene_selected": "ACTB",                       // 来源: P5 Step1: 选定的管家基因符号
    "refseq_accession": "NM_213978",               // 来源: P5 Step1: NCBI Entrez获取的RefSeq编号
    "conservation": 0.993,                         // 来源: P5 Step1: C5(MAFFT)多品种保守性评分
    "conservation_status": "PASS",                 // 来源: P5 Step1: PASS(≥0.99) | CAUTION(0.97-0.99)
    "limiting_primer_strategy": null,              // 来源: P5: 18S rRNA时为"50–100 nM"; 否则null
    "verdict": "PASS",                             // 来源: P5汇总判定: PASS | CAUTION | FAIL
    "warnings": [],                                // 来源: P5: 汇总所有Step的warnings(保守性/交叉二聚体/特异性/二级结构)
    "candidates": [
      {
        "rank": 1,
        "role": "internal_control",
        "forward_primer": {"sequence_5to3": "...", "length_nt": 20, "tm_celsius": 60.1, "gc_percent": 52.0, "hairpin_dg_cal_per_mol": -500.0, "homodimer_dg_cal_per_mol": -3200.0},  // 来源: P5 Step2: P1.design_primers (同O1)
        "reverse_primer": {"sequence_5to3": "...", "length_nt": 20, "tm_celsius": 59.7, "gc_percent": 48.0, "hairpin_dg_cal_per_mol": -700.0, "homodimer_dg_cal_per_mol": -2800.0},  // 来源: P5 Step2: P1.design_primers (同O1)
        "probe": {"sequence_5to3": "...", "type": "TaqMan_BHQ", "fluorophore": "Cy5", "quencher": "BHQ-2", "length_nt": 18, "tm_celsius": 70.5, "tm_method": "SantaLucia_NN"},  // 来源: P5 Step2: P1 + P5指派(IC固定Cy5/BHQ-2)
        "amplicon": {"sequence_5to3": "...", "length_bp": 95, "gc_percent": 50.5},  // 来源: P5 Step5: 从RefSeq截取 template[fwd_start:rev_end] (R-21)
        "amplicon_structure": {"status": "PASS", "mfe_dg": -15.2, "tool": "ViennaRNA"},  // 来源: P5 Step5: P6.assess_amplicon_secondary_structure(); P6不可用时={"status":"not_evaluated"}
        "cross_check": {"performed": true, "heterodimer_pairs_checked": 18, "worst_heterodimer_dg_cal_per_mol": -4500.0, "verdict": "PASS"},  // 来源: P5 Step3: P1.calc_heterodimer(IC,target)
        "specificity": {"host_verdict": "PASS", "cross_species_verdict": "PASS"},  // 来源: P5 Step4: P2.check_host + P2.check_cross_species; 与O1不同——IC不查target_verdict (P5不调check_target; 内标验证靶标无意义)
        "evidence_level": "blast_verified",         // 来源: WF-03 Step3: P2宿主验证通过→"blast_verified"; 否则→"in_silico_only" (R-20)
        "warnings": []                             // 来源: P5 Step2软性规则: R-01b/R-02软/R-03b/R-11/R-12 WARNING
      }
    ]
  },

  // ════════════════════════════════════════════════════════
  // O2: multiplex — 多重面板兼容性 (WF-02 填充, 单靶标时=null)
  // ════════════════════════════════════════════════════════
  "multiplex": {
    "panel_size": 3,                               // 来源: WF-02 Step1: 靶标数量
    "targets_order": ["CSFV","PCV2","PRRSV"],      // 来源: WF-02 Step1: 用户输入顺序
    "failed_targets": [],                          // 来源: WF-02 Step1: WF-01返回0候选的靶标ID列表
    "relaxation_applied": false,                   // 来源: WF-02 Step3: 是否触发了P4兼容性放宽(ΔG→Tm→分管)
    "channel_assignment": [                        // 来源: WF-02 Step3: 默认指派(FAM→VIC→ROX→Cy5) + P4光谱验证
      {"target_id":"CSFV","fluorophore":"FAM","quencher":"BHQ-1"},
      {"target_id":"PCV2","fluorophore":"VIC","quencher":"BHQ-1"},
      {"target_id":"PRRSV","fluorophore":"Cy5","quencher":"BHQ-2"}
    ],
    "heterodimer": {                               // 来源: WF-02 Step3: P4.check_panel_compatibility().heterodimer
      "worst_dg_cal_per_mol": -5200.0,
      "worst_pair": ["CSFV_fwd","PCV2_rev"],
      "total_pairs_checked": 36
    },
    "tm_spread_celsius": {"primers": 1.6, "probes": 1.4},  // 来源: WF-02 Step4: P4.tm_spread; WF-02做键名映射(primer_spread_celsius→primers, probe_spread_celsius→probes)
    "gc_spread_percent": 8.0,                      // 来源: WF-02 Step4: P4.gc_spread
    "issues": [                                    // 来源: WF-02 Step3: P4.check_panel_compatibility().issues (各[FAIL]/[CAUTION]原因明细)
      "[CAUTION] FAM-TET 发射峰间距 18 nm (阈值 15 nm)"
    ],
    "verdict": "COMPATIBLE"                        // 来源: WF-02 Step3: P4.check_panel_compatibility().verdict; COMPATIBLE | MARGINAL | INCOMPATIBLE
  },

  // ════════════════════════════════════════════════════════
  // O3: specificity — 特异性验证报告 (WF-04 填充)
  // ════════════════════════════════════════════════════════
  "specificity": {
    // --- P2 三模式结果 (来源: WF-04 Step2-4: P2) ---
    "target_verdict": "PASS",                      // 来源: WF-04 Step2: P2.check_target().verdict
    "target_identity_pct": 100.0,                  // 来源: WF-04 Step2: 从P2.check_target().details[]各hit中取max identity_pct; P2不直接返回此聚合值
    "target_evalue": 0.0,                          // 来源: WF-04 Step2: P2.check_target() blast_hits min evalue
    "host_verdict": "PASS",                        // 来源: WF-04 Step3: P2.check_host().verdict
    "hosts_checked": ["Sus scrofa"],               // 来源: WF-04 Step3: P2.check_host() 调用参数
    "host_hits_detail": [],                        // 来源: WF-04 Step3: P2.check_host().details
    "cross_species_verdict": "PASS",               // 来源: WF-04 Step4: P2.check_cross_species().verdict
    "cross_species_hits": [],                      // 来源: WF-04 Step4: P2.check_cross_species().details

    // --- 引物基础性质 (来源: WF-04 Step1: P1.validate_primer) ---
    "primer_basics": {
      "forward": {"tm_celsius": 60.2, "gc_percent": 55.0, "length_nt": 20,
                  "hairpin_dg": -800.0, "homodimer_dg": -4100.0},
      "reverse": {"tm_celsius": 59.8, "gc_percent": 50.0, "length_nt": 20,
                  "hairpin_dg": -1200.0, "homodimer_dg": -3500.0},
      "probe": {"tm_celsius": 70.3, "length_nt": 19}  // 如有
    },

    // --- 引物对配合性 (来源: WF-04 Step5) ---
    "pair_compatibility": {
      "heterodimer_dg_cal_per_mol": -3800.0,       // 来源: WF-04 Step5: P1.calc_heterodimer(fwd,rev)
      "expected_amplicon_length_bp": 112,           // 来源: WF-04 Step5: 从模板推算
      "probe_tm_gap_celsius": 10.1,                // 来源: WF-04 Step5: probe_tm - avg(primer_tm); MGB探针时=null(走R-13)
      "probe_5prime_is_G": false,                  // 来源: WF-04 Step5: R-05检查; true=已补救或WARNING
      "probe_spacing_verified": false              // 来源: WF-04 Step5: R-06; false=无Primer3坐标未验证
    },

    // --- 综合评级 (来源: WF-04 Step6) ---
    "rating": "PASS_WITH_WARNINGS",                // 来源: WF-04 Step6: PASS | PASS_WITH_WARNINGS | FAIL
    "failure_type": null,                          // 来源: WF-04 Step6: FAIL时=a|b|c|d|e; 非FAIL时=null
    "failure_detail": null                         // 来源: WF-04 Step6: FAIL时的具体描述和建议
  },

  // ════════════════════════════════════════════════════════
  // O4: variation — 变异耐受性评估 (WF-05 填充, 未评估时=null)
  // ════════════════════════════════════════════════════════
  "variation": {
    "sequences_analyzed": 32,                      // 来源: WF-05 Step1: 序列集总数 len()
    "sequences_warning": null,                     // 来源: WF-05 Step1: N<30时="序列数不足(N=xx),覆盖率统计置信度低"
    "coverage_100pct_match": 0.94,                 // 来源: WF-05 Step5: 完全匹配数/总序列数
    "coverage_1_mismatch": 0.98,                   // 来源: WF-05 Step5: 允许1个非3'端错配
    "high_risk_sites": [                           // 来源: WF-05 Step3+4: C5(MAFFT)→变异识别+P1重算Tm
      {
        "position": 8,                             //   来源: WF-05 Step3: MAFFT比对中的引物区域位置
        "ref": "A",                                //   来源: WF-05 Step3: 参考序列碱基
        "alt": "G",                                //   来源: WF-05 Step3: 变异碱基
        "frequency": 0.12,                         //   来源: WF-05 Step3: 变异碱基数/总序列数
        "risk_level": "HIGH",                      //   来源: WF-05 Step3: HIGH(3'端5nt) | MEDIUM(探针区) | LOW(5'端/中段)
        "tm_shift_celsius": 2.5,                   //   来源: WF-05 Step4: P1.calc_tm(变异后) - P1.calc_tm(参考); >2→标记可能漏检
        "location": "reverse_primer_3prime_5nt"    //   来源: WF-05 Step3: 变异在引物/探针上的位置描述
      }
    ],
    "verdict": "REDESIGN_RECOMMENDED",             // 来源: WF-05 Step6: ROBUST(≥98%+无3'高频变异) | ACCEPTABLE(95-98%) | REDESIGN_RECOMMENDED(<95%)
    "redesign_route": "WF-01"                      // 来源: WF-05 Step6: REDESIGN时→自动触发WF-01(排除变异热点区域)
  },

  // ════════════════════════════════════════════════════════
  // O5: recommended_conditions — 推荐反应条件 (WF-06 填充)
  // ════════════════════════════════════════════════════════
  "recommended_conditions": {
    "annealing_temp": {"celsius": 60, "basis": "standard_TaqMan_two_step"},  // 来源: WF-06 Step5: TaqMan标准两步法退火/延伸合并=60°C
    "annealing_temp_note": null,                   // 来源: WF-06 Step5: 松弛后Tm<58→降至58°C+延长退火至90s
    "cycle_count": 40,                             // 来源: WF-06 Step5: qPCR标准循环数
    "master_mix": {                                // 来源: WF-06 Step5: 推荐
      "type": "probe_based",
      "examples": ["TaqPath","PrimeTime"]
    },
    "primer_conc_nM": 300,                         // 来源: WF-06 Step5: 实验终浓度(P1设计浓度=250nM)
    "probe_conc_nM": 200,                          // 来源: WF-06 Step5
    "rox_reference": true,                         // 来源: WF-06 Step5: P4.instrument_check.needs_rox (ABI仪器)
    "template_input_ng": "50-100",                 // 来源: WF-06 Step5
    "hot_start": {                                 // 来源: WF-06 Step5
      "temp_celsius": 95,                          //   标准Taq激活温度
      "duration_sec": 120                          //   标准热启动时间
    },
    "notes": []                                    // 来源: WF-06 Step5: 汇总各子WF的WARNING和备注
  },

  // ════════════════════════════════════════════════════════
  // O6: diagnosis — 引物诊断报告 (WF-07 填充, 未诊断时=null)
  // ════════════════════════════════════════════════════════
  "diagnosis": {
    // --- 全面检查结果 (来源: WF-07 Step1) ---
    "thermodynamics": {                            // 来源: WF-07 Step1: P1完整热力学
      "forward": {"tm": 60.2, "gc": 55.0, "hairpin_dg": -800.0, "homodimer_dg": -4100.0},
      "reverse": {"tm": 59.8, "gc": 50.0, "hairpin_dg": -1200.0, "homodimer_dg": -3500.0},
      "probe": {"tm": 70.3, "gc": 52.6},          // 如有
      "heterodimer_dg": -3800.0
    },
    "blast_summary": {                             // 来源: WF-07 Step1: P2全面BLAST
      "target_match": true,
      "host_hits": 0,
      "cross_species_hits": 0
    },
    "hard_rule_violations": [],                    // 来源: WF-07 Step1: 违反的红线条款列表(R-01~R-10)
    "amplicon_structure": null,                    // 来源: WF-07 Step1: P6 (模板如提供+P6可用; 否则null)

    // --- 归因结果 (来源: WF-07 Step2: LLM+规则辅助) ---
    "attribution": {
      "cause": "引物二聚体(heterodimer ΔG=-4.5<-6.0)",  // 来源: WF-07 Step2: 问题根因描述
      "confidence": "HIGH",                        // 来源: WF-07 Step2: HIGH(可量化热力学) | MEDIUM(可能实验因素) | LOW(多因无法区分)
      "evidence": "heterodimer_dg_cal_per_mol=-4500"  // 来源: WF-07 Step2: 支持归因的量化证据
    },

    // --- 优化方案 (来源: WF-07 Step3) ---
    "optimization": {
      "strategy": "微调3'端1-2nt位置 / 换区域重设计(WF-01)",  // 来源: WF-07 Step3: 建议的优化策略
      "suggestions": [                             // 来源: WF-07 Step3: 1-3个具体优化建议
        "将reverse引物3'端第2位A→G以破坏二聚体配对",
        "或沿模板向下游移动reverse引物2nt"
      ]
    },

    // --- 验证结果 (来源: WF-07 Step4) ---
    "verification": {
      "performed": true,                           // 来源: WF-07 Step4: 是否执行了验证
      "verified_fix": "reverse引物A→G后heterodimer ΔG=-1.2",  // 来源: WF-07 Step4: 验证通过的优化方案
      "new_primers_validated": false               // 来源: WF-07 Step4: 优化后是否通过P1+P2+P3验证
    },

    // --- 报告元数据 ---
    "evidence_level": "blast_verified",            // 来源: WF-07 Step5: 继承原始设计的evidence_level
    "verdict": "OPTIMIZED",                        // 来源: WF-07: DIAGNOSED | OPTIMIZED | UNRESOLVED
    "warnings": []                                 // 来源: WF-07: 诊断过程中的注意事项
  },

  // ════════════════════════════════════════════════════════
  // 顶层汇总 (所有WF共用)
  // ════════════════════════════════════════════════════════
  "design_pass": true,                             // 来源: 任意candidate.specificity全部PASS/CAUTION = true
  "warnings": []                                   // 来源: 汇总: candidates[].warnings + self_check.issues + relaxation_metadata状态 + internal_control.warnings
}
```

> **P-tool → Schema 字段名映射**：以下 P-tool 原生输出字段名与 Schema 不一致。P2/P3 输出字段名与 Schema 一致，无需映射。P1 直接暴露 primer3 Boulder IO 键名（无自定义封装），WF 需做键名转换。

| P-tool | 原生字段 | Schema 字段 | 负责 WF |
|:---:|------|------|:---:|
| P1/Boulder IO | `PRIMER_LEFT_{i}_SEQUENCE` | `forward_primer.sequence_5to3` | WF-01/02/06 |
| P1/Boulder IO | `PRIMER_RIGHT_{i}_SEQUENCE` | `reverse_primer.sequence_5to3` | WF-01/02/06 |
| P1/Boulder IO | `PRIMER_INTERNAL_{i}_SEQUENCE` | `probe.sequence_5to3` | WF-01/02/06 |
| P1/Boulder IO | `PRIMER_LEFT_{i}_TM` | `forward_primer.tm_celsius` | WF-01/02/06 |
| P1/Boulder IO | `PRIMER_RIGHT_{i}_TM` | `reverse_primer.tm_celsius` | WF-01/02/06 |
| P1/Boulder IO | `PRIMER_INTERNAL_{i}_TM` | `probe.tm_celsius` | WF-01/02/06 |
| P1/Boulder IO | `PRIMER_PAIR_{i}_PRODUCT_SIZE` | `amplicon.length_bp` | WF-01/02/06 |
| P5 | `primers[].fwd_seq` | `forward_primer.sequence_5to3` | WF-03 |
| P5 | `primers[].rev_seq` | `reverse_primer.sequence_5to3` | WF-03 |
| P5 | `primers[].probe_seq` | `probe.sequence_5to3` | WF-03 |
| P5 | `primers[].product_size` | `amplicon.length_bp` | WF-03 |
| P5 | `primers[].fwd_tm` | `forward_primer.tm_celsius` | WF-03 |
| P5 | `primers[].rev_tm` | `reverse_primer.tm_celsius` | WF-03 |
| P5 | `primers[].probe_tm` | `probe.tm_celsius` | WF-03 |
| P5 | `primers[].gc_percent` | `forward_primer.gc_percent` / `reverse_primer.gc_percent` | WF-03 |
| P5 | `specificity_check` | `specificity` (结构重组: host_hits/pathogen_cross_hits/verdict → host_verdict/cross_species_verdict) | WF-03 |
| P4 | `tm_spread.primer_spread_celsius` | `tm_spread_celsius.primers` | WF-02 |
| P4 | `tm_spread.probe_spread_celsius` | `tm_spread_celsius.probes` | WF-02 |
| P6 | `mfe_dg` | `amplicon_structure.mfe_dg` | WF-01/03/04/07 |
| P6 | `status` | `amplicon_structure.status` | WF-01/03/04/07 |
| P6 | `mfe_structure` | `amplicon_structure.mfe_structure` | WF-01/03/04/07 |
| P6 | `parameter_set` | `amplicon_structure.tool` (WF做键名映射: "SantaLucia2004_DNA"→"ViennaRNA") | WF-01/03/04/07 |
| P5 | `verdict` | `internal_control.verdict` | WF-03 |
| P5 | `warnings` | `internal_control.warnings` | WF-03 |
| P5 | `primers[].amplicon_seq` | `internal_control.candidates[].amplicon.sequence_5to3` | WF-03 |
| P5 | `primers[].amplicon_structure` | `internal_control.candidates[].amplicon_structure` | WF-03 |
| P4 | `issues[]` | `multiplex.issues[]` | WF-02 |

> 未列出的字段名均与 P-tool 原生输出一致，无需转换。

**WF 层生成字段映射**（无 P-tool 直接来源，由 WF 代码根据 SOP 逻辑生成）:

| WF | 生成逻辑 | Schema 字段 |
|:---:|------|------|
| WF-01 Step3 | 松弛触发时屏幕告知文本 | `relaxation_metadata.screen_messages[]` |
| WF-01 Step3 | 第3轮 MGB 短探针触发标志 | `relaxation_metadata.short_probe_triggered` |
| WF-01 Step6 | per-candidate降权后最终分数 | `candidates[].score` |
| WF-03 Step3 | IC候选evidence_level(R-20) | `internal_control.candidates[].evidence_level` |
| WF-01 Step3 | 并行策略(简并碱基)全量结果(含继承的松弛轮次) | `degenerate_strategy.*` (8子字段) |
| WF-01 Step6.5 | 区域集中/GC偏态风险 | `self_check.issues[]` |
| WF-02 Step1 | 靶标归属标识 | `candidates[].target_id` |
| WF-02 Step1 | 设计失败的靶标列表 | `multiplex.failed_targets[]` |
| WF-02 Step3 | P4 兼容性放宽触发标志 | `multiplex.relaxation_applied` |
| WF-04 Step2 | 从 P2.details[] 聚合 max identity | `specificity.target_identity_pct` |
| WF-04 Step5 | 引物对配合性评估 | `specificity.pair_compatibility.*` |
| WF-04 Step6 | 综合评级+FAIL分型 | `specificity.rating` / `.failure_type` / `.failure_detail` |
| WF-05 Step3 | HIGH/MEDIUM/LOW 风险分级 | `variation.high_risk_sites[].risk_level` |
| WF-05 Step4 | 变异前后 Tm 差值 | `variation.high_risk_sites[].tm_shift_celsius` |
| WF-05 Step1 | N<30 置信度警告 | `variation.sequences_warning` |
| WF-07 Step2 | LLM+规则归因结果 | `diagnosis.attribution.*` |
| WF-07 Step3 | 优化方案 | `diagnosis.optimization.*` |
| WF-07 Step4 | 验证结果 | `diagnosis.verification.*` |

---

## 成功标准（Success Criteria）

以下任一条满足即视为任务成功：

| 编号 | 标准 | 对应WF |
|:---:|------|:---:|
| SC-01 | 获得通过全部红线的候选方案：标准模式≥3套。松弛模式(1-3轮,含MGB短探针)1-2套标注"参数放宽+候选不足"后仍视为部分成功。0套为失败 | WF-01/02/06 |
| SC-01a | [降级标准] 经1-3轮松弛后获得1-2套 → 标注"经[x]轮参数松弛(含MGB短探针),候选项不足(x/3)"→ 计为PASS_WITH_RELAXATION | WF-01 |
| SC-02 | 获得满足多重兼容性（通道 + Cross-dimer + Tm均衡）的面板方案 | WF-02 |
| SC-03 | 获得内标引物方案（含保守性验证+P2宿主特异性+P6二级结构检查） | WF-03 |
| SC-04 | 完成已有引物体系的全面诊断，给出可执行优化方案（含归因+验证） | WF-04/07 |
| SC-05 | 获得变异耐受性评估报告，含覆盖率统计和风险分级 | WF-05 |

**失败时输出要求：** 具体失败步骤 + 失败原因 + 建议下一步（补充数据 / 放宽约束 / 换策略）。不得静默失败。

---

（以上为交付物 1 改进版结束）

---

# 交付物 2：Router决策表

| 用户意图关键词/模式 | 路由目标 | 说明 |
|-------------------|---------|------|
| "设计引物" "设计探针" "检测XX" + 单个靶标 | **WF-01** | 最常见场景 |
| "多重" "多联" "同管" "面板" + 多个靶标 | **WF-02** | 含通道分配 |
| "内标" "内参" "IC" "管家基因" | **WF-03** | 宿主物种必须明确 |
| "验证引物" "检查特异性" "已有引物" + 序列 | **WF-04** | 不设计新引物 |
| "变异" "突变" "SNP" "漏检" "是否还能用" | **WF-05** | 评估变异覆盖 |
| "从头设计" "完整方案" "诊断试剂盒" + 病原体名 | **WF-06** | 端到端 |
| "引物有问题" "二聚体" "非特异" "优化" "改进" | **WF-07** | 修复现有设计 |
| 无法判断 | **LLM兜底** | 询问用户意图后路由 |

---

# 交付物 3：Workflow SOPs

---

## WF-01: 单靶标TaqMan引物探针设计

```
WF-01: 单靶标TaqMan引物探针设计

输入：靶标序列(FASTA) | 病原体名称+基因名 | GenBank Accession
输出：≥3套候选引物探针方案（标准模式）或 1-2套松弛模式方案 + 排序推荐 + 完整报告。0套时输出失败报告

Step 1: 靶标序列获取
  ├─ 序列输入 → 直接进Step 2
  ├─ 病原体+基因名 → C0(病原数据库)检索代表性序列
  │   ⚠️ C0返回0条 → 中止："数据库中未找到[病原名]，请提供FASTA序列或确认名称拼写"
  ├─ Accession号 → C1(BioPython Entrez)下载
  │   ⚠️ 无效Accession → 中止："Accession号[xxx]无效或已撤回"
  ├─ 质量门：序列≥200 nt
  │   ⚠️ 不满足 → 中止："检索到序列仅[xx]nt(<200nt)，无法设计"（如≥50nt可进入WF-04验证模式）
  └─ 输出：靶标参考序列 + 元数据（物种/基因/长度/来源/检索时间）

Step 2: 保守性分析
  ├─ C0检索同病原体≥20条代表性序列
  │   ⚠️ <20条 → 警告用户,降低保守性置信度但继续
  ├─ 【C6b可用时】MMseqs2快速聚类序列集,去冗余后再进MAFFT
  ├─ C5(MAFFT)多序列比对
  ├─ 【C21可用时】IQ-TREE系统发育+保守区筛选
  │   优先筛选conservation ≥ 0.95的连续区≥150 bp
  │   ⚠️ 如无满足条件区域 → 第1轮放宽为 conservation ≥ 0.92 且连续区≥120 bp，并标记警告
  │   ⚠️ 如仍无满足条件区域 → 第2轮放宽为 conservation ≥ 0.90，并标记"低置信度保守区"
  │   ⚠️ C21为P1增强能力,不可用时:
  │   → 降级方案:仅用MAFFT Shannon entropy做保守性评分
  │     (MAFFT输出比对→WF代码逐列计算Shannon熵 H=-Σpᵢlog₂pᵢ→滑动窗口聚合;
  │      阈值映射: H≤0.12→conservation≥0.95; H≤0.5→≥0.90; 此为WF层计算,非MAFFT直接输出。
  │      依据: PMPrimer(Yang et al.2023 SciRep)默认entropy≤0.12≈95%保守; 0.5≈90%)
  │   → 如仍无满足条件区域,对全长序列直接设计,后由BLAST(P2)过滤
  │   // C21方法降级与保守性阈值放宽独立运作，可叠加
  └─ 输出：保守区候选列表（按 (conservation DESC, length DESC, start ASC) 排序）
     + conservation_method字段标注使用了哪种方法

Step 3: 引物探针设计
  // _design_round(region_seq, rd, template_seq) — 原子操作，每轮松弛复用:
  //   Primer3设计 → 硬性过滤(R-01~R-10, 不含R-05/R-06; MGB模式下R-07跳过→Step4 R-13替代)
  //   → 有候选 → 软性规则检查(R-01b/R-02/R-03b/R-11/R-12/R-补充[P6])
  //   → 返回 (candidates, relaxation_label, screen_message)
  // 软性规则不排除候选, 仅写入 candidate.warnings[]; P6不可用→跳过标注

  ├─ _design_round(standard_params)  // MIN_TM=58 MAX_TM=62 SIZE=70-150 PROBE=18-30nt
  │   ├─ 有候选 → relaxation_label="" → Step 4
  │   └─ 0候选 → 松弛链 ↓

  // 松弛重试链: 保守区<120bp→跳过L1/L2直接L3(标准探针无物理空间)
  【L1】参数: SIZE=60-180 MAX_TM=65        // 从标准到L1→L2→L3: 逐级继承,各轮独立
  【L2】参数: 继承L1 + MIN_TM=50           // 每轮有候选即停止,执行软性规则检查
  【L3】参数: 继承L2 + OPT_TM=55 SIZE=13-20 // 屏幕告知→_design_round→有候选→short_probe_triggered=true→Step4
  全部失败 → 中止: "经3轮松弛仍无通过红线候选。建议换靶标基因/扩大保守区"

  // 并行策略——简并碱基 (仅执行1次, 独立于保守区迭代。继承当前保守区松弛成功参数)
  ⚠️ 触发: Step2 MAFFT≥2变异位点 + ≤3简并位点
  流程: C1(BioPython)识别变异→IUPAC编码→拆分枚举(≤8组合)→
        P1.design_primers(使用当前保守区已验证可用的松弛参数, 默认标准参数)+P1热力学→
        P2 BLAST逐一验证→可行性判定(Tm极差≤2→FEASIBLE/2-4→MARGINAL/>4→INFEASIBLE)
  ⚠️ Primer3不支持简并序列Tm——拆分枚举由WF+C1实现(工程兼容算法)
  ⚠️ C6b(MMseqs2)不适用简并引物预筛选
  结果写入 $.degenerate_strategy (不影响标准候选列表)

  ⚠️ 简并策略局限性（当前为工程demo，待黄运星博士和甲方确认技术细节后定稿）:
  1. Tm极差判定阈值(≤2°C/2-4°C/>4°C)基于热力学推理，未经实验梯度PCR标定
  2. 简并位点上限≤3为计算复杂度约束(8组合)，非生物学上限
  3. 拆分枚举法对每个变体独立计算Tm，但实际PCR中混合引物池存在变体间竞争/协同效应，有效Tm不等于各变体Tm的简单平均或极差
  4. 简并位点位于引物3'端时，即使Tm极差≤2°C，3'端错配的延伸效率差异可能远大于Tm差异
  5. FastPCR的平均ΔH/ΔS法可作为交叉验证参考，但FastPCR为独立Java桌面软件无法编程集成
  6. SantaLucia NN模型对简并序列无原生支持——拆分后分别计算为工程近似

  └─ metadata: 松弛→$.relaxation_metadata / 简并→$.degenerate_strategy / 短探针参数记入final_params

Step 4: MGB探针评估 + R-05/R-06 检查
  ├─ 对每个通过Step3的候选:
  │
  │   // --- MGB评估 ---
  │   如 (Step3第3轮触发) → 优先P3 MGB评估（如修正前基础Tm已≥65°C且满足R-07(8-10°C)→可跳过MGB，按普通TaqMan处理）
  │   如 (探针长度≤20nt 且 Step3第3轮未触发) → 可选P3 MGB评估
  │
  │   // --- R-05: 探针5'≠G (硬性) ---
  │   如 probe_seq[0] == 'G':
  │     → 调用BioPython Seq.reverse_complement()尝试反义链
  │     → 如反义链可行(5'≠G): 替换探针序列
  │     → 如不可行: FAIL, 排除该候选
  │   ⚠️ P3内部额外执行R-MGB-01补充约束(FAM 5'端第2位≠G)
  │
  │   // --- R-06: 探针间距 (硬性) [汪博改版] ---
  │   使用Primer3输出坐标(PRIMER_LEFT_0_end, PRIMER_RIGHT_0, PRIMER_INTERNAL_0, PRIMER_INTERNAL_0_end)
  │   如 (PRIMER_INTERNAL_0 - PRIMER_LEFT_0_end) < 5 → FAIL, 排除
  │   如 (PRIMER_RIGHT_0 - PRIMER_INTERNAL_0_end) < 5 → FAIL, 排除
  │
  │   // --- R-13: MGB修正Tm ---
  │   如触发MGB评估 → 验证修正Tm在65-72°C
  │
  └─ ⚠️ 如全部候选在Step4被排除(间距/5'G/MGB规则) → 回退到Step2选择下一个保守区,
     对新区执行Step3-4。保守区耗尽→统一兜底。

Step 5: 特异性验证
  // WF-01 Step5负责编排P2三个模式调用并聚合结果
  ├─ 对每个通过Step4的候选:
  │   1. target_result = P2.check_target(candidate, target_genome)
  │   2. host_result   = P2.check_host(candidate, host_species)
  │   3. cross_result  = P2.check_cross_species(candidate, target_species)
  │   4. 聚合: specificity = {
  │        "target_verdict": target_result.verdict,       // PASS|CAUTION|FAIL
  │        "host_verdict": host_result.verdict,
  │        "cross_species_verdict": cross_result.verdict
  │      }
  │   5. 任一verdict=FAIL → 候选排除; 全部PASS/CAUTION → 通过
  │
  ├─ ⚠️ 如全部候选被BLAST排除:
  │   → 回退到Step2选择下一个保守区 → 对新保守区执行Step3-5
  │   → 最多回退2次(默认最多遍历3个保守区; 配置扩展模式放宽到Top5)
  │   → 保守区耗尽→统一兜底
  └─ 输出: 通过特异性验证的候选列表

  // ===== 保守区遍历策略 =====
  // 目标: 收集≥3套候选。当前保守区候选≥3 → 停止。
  //       当前保守区候选<3 → 继续下一个保守区补充，直至≥3或耗尽。

Step 6: 综合评分与排序
  ├─ Score = 保守性×0.30 + 热力学×0.25 + 特异性×0.25 + 扩增子特征×0.20
  │   ⚠️ 上述权重为经验值，可配置。后续可由实验数据重新标定
  │   引物绝对Tm评分：通过R-01的50-65°C候选不做区间内高低排序；
  │   50≤Tm<55°C仅由R-01b统一扣5%；55≤Tm≤65°C不扣分；Tm<50°C或Tm>65°C直接排除
  │
  │   // 降权算法: 加法叠加。total_penalty = sum(各项降权百分比)
  │   // final_score = raw_score × (1 - total_penalty / 100)
  │
  ├─ 降权项（各项独立判定，百分比加法叠加）:
  │   · 低置信度保守性: -10%
  │   · MGB不适用: -5%
  │     (触发条件: Step3第3轮被触发且探针基础Tm<65°C(需要MGB但未使用),
  │      或用户输入明确要求MGB探针但候选未采用;
  │      L3候选基础Tm≥65°C跳过MGB→不扣分; 标准设计模式探针>20nt→不扣分)
  │   · R-01b (Tm<55°C): -5%
  │   · R-11 (发卡): -3%
  │   · R-12 (弱二聚体): -3%
  │   · R-补充 WARNING (ΔG<-30): -3%        (读 cand.amplicon_structure.status)
  │   · R-补充 CAUTION (-30≤ΔG<-20): -1%    (读 cand.amplicon_structure.status)
  │   · R-03b (GC clamp缺失): -2%
  │
  ├─ 取Top 3
  │   ⚠️ 如通过候选 1-2套 → 正常输出但标注"候选数量不足推荐阈值(3套)"
  └─ 输出: 排序候选列表

Step 6.5: 设计自检（Self-Check）
  ├─ 检查Top 3候选是否存在系统性风险:
  │   a) 如存在≥2个保守区但所有Top3候选全部集中在其中1个
  │      → 标记"区域集中风险"
  │   b) 所有候选扩增子GC%是否极端(>65%或<35%)
  │      → 标记"GC偏态"  // 注意: 扩增子GC非引物GC，无硬规则约束，仅CAUTION
  │   // 已删除原自检项"探针链偏好(全在同一链)"——商业软件Top3全在同一链为正常现象
  ├─ 如任一风险触发 → 输出追加CAUTION说明
  └─ 输出: 自检报告(PASS / CAUTION + 具体风险项)

Step 7: 报告生成
  ├─ evidence_level设定:
  │   per-candidate: P2 BLAST验证通过→"blast_verified"; P2未执行→"in_silico_only"
  │   (specificity默认值: {"target_verdict":"NOT_CHECKED","host_verdict":"NOT_CHECKED","cross_species_verdict":"NOT_CHECKED"})
  │   assay级($.evidence_level): 取所有candidates中最严格的层级
  │
  ├─ 提取扩增产物序列(R-21):
  │   使用Primer3输出坐标字段(PRIMER_LEFT_0, PRIMER_RIGHT_0_end)截取模板
  │   amplicon_seq = template_seq[PRIMER_LEFT_0 : PRIMER_RIGHT_0_end]
  │   写入 amplicon.sequence_5to3
  │
  ├─ C29(候选对比报告) Top 3对比表
  ├─ C30(结果解释) 自然语言推荐
  ├─ 包含Step 6.5自检结果
  └─ 输出: 完整设计报告(JSON + 自然语言摘要)

统一兜底处理（所有Step完成后触发）:
  ├─ 如最终通过候选 = 0:
  │   输出失败报告(不得输出空JSON或仅错误码):
  │   - 失败原因摘要(哪些Step筛掉了候选及数量: Step3红线/Step4 R-05&R-06/Step5 BLAST)
  │   - 已尝试的参数放宽记录(松弛轮次/最终参数值)
  │   - 如并行策略(简并碱基)已触发,附$.degenerate_strategy结果供参考
  │   - 建议下一步(a/b/c优先级排序)
  └─ 通过候选 ≥ 1:
      正常交付 + 如候选<3套标注"候选数量不足推荐阈值(3套)"
```

---

## WF-02: 多重qPCR面板设计

```
输入：靶标列表(2-4个) + 宿主物种 + 是否需要内标(默认需要)
输出：多重兼容的引物探针面板 + 荧光通道分配方案

Step 1: 单靶标设计（循环）
  ├─ 对每个靶标执行WF-01(Step 1-5)
  ├─ 每靶标保留Top 5候选（Step5 BLAST通过后取Top 5；给多重筛选留余量）
  │   ⚠️ 如某靶标WF-01返回0候选：
  │   → 该靶标标记FAIL，不阻断其他靶标继续设计
  │   → 最终报告标注"面板不完整：靶标[xxx]设计失败([原因])，当前面板覆盖[n-1]/[n]靶标"
  └─ 输出：N靶标 × 各0-5套候选

Step 2: 内标设计（条件执行）
  ├─ 需要内标 → 调用WF-03
  └─ 输出：内标候选列表

Step 3: 多重兼容性筛选
  ├─ 组合所有靶标候选+内标 → 所有可能面板
  ├─ P4(多重荧光通道适配)：
  │   a) 交叉二聚体：所有引物探针两两 → ΔG > -6.0 kcal/mol
  │   b) 荧光通道分配：FAM/VIC/ROX/Cy5/HEX 无重叠
  │   c) 扩增子长度差异≥20 bp
  ├─ 过滤不通过的组合
  │
  ├─ ⚠️ 如无任何面板通过兼容性筛选：
  │   【逐步放宽】
  │   第1步：放宽交叉二聚体阈值至ΔG > -7.0（标WARNING）
  │   第2步：放宽Tm极差至4°C（标WARNING）
  │   第3步：允许部分靶标放弃荧光通道（退为分管检测建议）
  │   如所有放宽仍无兼容面板 → 中止并输出：
  │   "当前候选引物探针无法组成兼容多重面板，建议:"
  │   "- 各靶标单独分管检测（提交WF-01独立设计）"
  │   "- 减少靶标数量重新设计"
  │   "- 降级为SYBR Green+熔解曲线方案(分管检测仍不可行时)"
  │   如部分靶标可组成面板 → 输出部分面板 + 不可合并靶标独立方案
  └─ 输出：兼容面板列表 + 放宽记录

Step 4: Tm + GC均衡性评估
  ├─ Tm均衡性：
  │   ├─ 同面板引物Tm极差≤3°C，探针Tm极差≤3°C
  │   │   ⚠️ 不满足 → 回到Step 1为对应靶标重设计，调用WF-01时传入tm_constraint参数
  │   │     (将PRIMER_MIN_TM/MAX_TM收紧至目标Tm±1°C, 如靶标Tm目标60°C→MIN=59 MAX=61)
  │   │   ⚠️ 最多重试2次,仍不满足→取最优组合标记Tm_WARNING并继续
  ├─ GC%均衡性（项目规则，非科学铁律）：
  │   ├─ 同面板各靶标扩增子GC%极差≤15%
  │   │   ⚠️ 极差>20% → 标记GC_WARNING:"同面板扩增子GC%差异大,退火温度相同时扩增效率可能不均"
  │   │   ⚠️ 极差15-20% → 标记GC_CAUTION
  └─ 输出：Tm + GC均衡性报告

Step 5: 面板评分+报告
  ├─ 面板评分 = Σ(单项评分) × 兼容性系数 × Tm均衡性系数
  ├─ Top 3面板
  ├─ C29+C30 生成报告：推荐通道分配+退火温度+注意事项
  └─ 输出：完整多重面板报告
```

---

## WF-03: 内标引物设计

```
输入：宿主物种 + 可选已有靶标引物序列 + 可选内标基因列表
输出：≥3套内标引物方案

Step 1: 内标引物设计
  ├─ 调用 P5.design_internal_control(host_species, preferred_genes, existing_targets)
  │   P5内部编排：NCBI Entrez获取管家基因RefSeq序列 →
  │   C5(MAFFT)多品种保守性分析 → P1(Primer3)引物探针设计 →
  │   (P1内部执行R-01~R-04/R-07~R-10硬性过滤; P5额外执行R-05探针5'≠G检查+R-06间距检查
  │    使用Primer3输出坐标) →
  │   P1(calc_heterodimer)与靶标交叉验证 → P2(BLAST)宿主特异性验证 →
  │   P6(ViennaRNA)扩增子二级结构检查（每个内标候选的扩增子；P6不可用时跳过）
  │   P5自动选择优先级：β-actin(1) > GAPDH(2) > 18S rRNA(3)
  │   18S rRNA自动启用限量引物策略(引物浓度50-100nM)
  │   // v12.3: 内标扩增子如有强二级结构，Ct异常会导致内标失效→误判PCR抑制
  └─ 输出：内标引物方案 + 验证报告

Step 2: 全失败兜底（条件执行）
  ├─ P5返回verdict=FAIL →
  │   按P5输出的失败原因和warnings评估：
  │   a) 所有管家基因保守性<0.97 → 建议用户提供自定义管家基因或RefSeq编号
  │      → 如用户提供RefSeq，路由回Step1以preferred_genes参数重新调用P5
  │   b) 备选管家基因(RPLP0, B2M, SDHA, TBP, HMBS)需用户提供RefSeq编号后重新调用P5
  │   c) 上述均不可行 → 建议使用外源性异源内标
  │      (人工合成序列+独立引物探针，满足WOAH第2.2.3章抑制对照最低要求)
  └─ 输出：兜底建议报告

Step 3: 报告
  ├─ 按 R-20 设定 evidence_level：P2 宿主验证通过 → "blast_verified"；否则 → "in_silico_only"
  └─ 内标引物报告 + 与靶标兼容性声明 + P5内部验证详情
```

---

## WF-04: 已有引物特异性验证

```
输入：引物序列(正+反,可选探针) + 靶标物种 + 宿主物种 + 可选模板序列(P6二级结构检查用; 非必填)
输出：特异性验证报告

Step 1: 序列校验
  ├─ 有效性检查(仅ATCG,长度合理)
  ├─ P1.validate_primer() 基础Tm/GC%/发卡/自二聚体
  │   正+反同时提供→追加P1.validate_primer_pair() 含heterodimer+3'端互补
  ├─ 红线检查（R-01~R-04, R-07~R-10；R-05(探针5'≠G)/R-06(间距)在Step5检查）
  │   违反则标记但不阻止后续验证
  └─ 输出：引物基础性质表

Step 2: 靶标匹配
  ├─ P2(BLAST vs 靶标) → 确认命中位置和匹配度
  └─ 输出：靶标命中详情

Step 3: 宿主排除
  ├─ P2(BLAST vs 宿主基因组 -db sus_scrofa等)
  │   有命中 → 判断重复区(低风险) vs 单拷贝区(高风险)
  └─ 输出：宿主命中详情

Step 4: 广谱交叉
  ├─ P2(BLAST vs NT) → 同属近缘种+共感染病原体
  └─ 输出：交叉命中列表

Step 5: 引物对配合性
  ├─ 正+反同时提供 → heterodimer + 预期扩增子长度
  ├─ 探针同时提供：
  │   · 验证位于引物间
  │   · R-05: probe[0]≠G（如=G→尝试反义链; 无模板序列无法切换→标WARNING非FAIL）
  │   · R-07: Tm差8-10°C；MGB探针→跳过R-07改R-13(65-72°C)
  │   · R-06间距无法精确检查(无Primer3坐标)→标注"间距未验证"
  ├─ 模板序列同时提供 → P6(ViennaRNA)扩增子二级结构检查(可选; P6不可用→跳过,标注)
  └─ 输出：配合性评估

Step 6: 综合评级
  ├─ 按 R-20 设定 evidence_level：已跑 P2 → "blast_verified"（默认），若用户后续提供实验验证数据则升级为 "experimentally_validated"
  ├─ PASS / PASS_WITH_WARNINGS / FAIL
  │   FAIL分型：
  │   a) 靶标无匹配 → "提供的引物与靶标基因组无匹配。请确认:(1)引物序列是否正确;(2)靶标物种是否与引物来源一致"
  │   b) 宿主强命中 → "引物在宿主基因组([宿主名])上存在高风险非特异命中(单拷贝区/e<0.1)。建议换区域重设计→路由到WF-01"
  │   c) 红线严重违反 → "[具体规则][具体数值]违反红线。修复方向:[建议]"
  │   d) 探针-引物Tm差<8°C → "探针Tm与引物Tm差不足。建议:调整探针长度或GC%使差值≥8°C"
  │   e) 近缘种广谱交叉 → "引物在近缘种([物种列表])存在匹配。如接受会同时检出近缘种→降为PASS_WITH_WARNINGS"
  │   f) R-补充 WARNING(ΔG<-30)/CAUTION(-30~-20) → 不影响PASS/FAIL判定，写入warnings并标注
```

---

## WF-05: 引物变异耐受性分析

```
输入：已有引物探针序列 + 靶标病原体名称 + 可选特定变异株
输出：变异耐受性评估报告

Step 1: 序列集获取
  ├─ C0(病原数据库)检索最新流行株（优先近2年）
  ├─ 覆盖主要基因型/血清型
  ├─ 质量门：≥30条序列
  └─ 输出：代表性序列集

Step 2: 多序列比对
  ├─ C5(MAFFT)比对
  ├─ 【C21可用时】IQ-TREE进化树辅助分型
  │   ⚠️ C21为P1增强,不可用时:仅用MAFFT比对结果
  ├─ 提取引物结合位点区域的比对
  └─ 输出：引物区域多序列比对

Step 3: 变异位点识别
  ├─ 扫描在 ≥2 条序列中观察到的变异位点（即最小可检测频率 = 2/N，N=序列总数）
  ├─ 风险分级：
  │   [高风险] 引物3'端5nt内变异
  │   [中风险] 探针区域变异
  │   [低风险] 引物5'端或中段变异
  └─ 输出：变异位点 + 风险分级

Step 4: Tm影响评估
  ├─ Step3识别的变异位点(≥2条序列) → P1(Primer3)重算引入变异后的Tm
  ├─ ΔTm > 2°C → 标记可能漏检
  └─ 输出：Tm影响评估表

Step 5: 覆盖率统计
  ├─ 100%匹配覆盖率、允许1个非3'端错配覆盖率、允许2个错配覆盖率
  ├─ 目标：100%匹配≥95%
  │   ⚠️ <95% → 建议重新设计或简并碱基
  │   ⚠️ N<30时 → 附带"序列数不足(N=[xx]),覆盖率统计置信度低"警告
  └─ 输出：覆盖率统计

Step 6: 评级与建议
  ├─ ROBUST(≥98%且无3'端高频变异)
  │   ACCEPTABLE(95-98%或低频3'端变异)
  │   REDESIGN_RECOMMENDED(<95%或高频3'端变异)
  ├─ REDESIGN → 自动触发WF-01（排除变异热点区域）
  └─ 输出：变异耐受性报告
```

---

## WF-06: 完整诊断面板设计（端到端）

```
输入：病原体名称(1个或多个) + 宿主物种 + 样本类型(可选) + 单靶标/多重
输出：完整诊断面板

Step 1: 靶标基因选择
  ├─ C0(病原数据库)检索基因组信息
  ├─ 【C6b可用时】MMseqs2聚类同类基因组,辅助靶标基因选择
  ├─ 用户未指定靶标基因 → LLM推荐（保守结构基因/高拷贝基因/已有诊断先例）
  │   ⚠️ LLM推荐须附理由,用户确认后继续
  ├─ 【C21可用时】IQ-TREE确认基因进化保守性
  │   ⚠️ C21为P1增强,不可用时:以MAFFT+BLAST覆盖
  └─ 输出：确认的靶标基因列表

Step 2: 引物探针设计
  ├─ 单靶标 → WF-01
  ├─ 多重 → WF-02
  └─ 输出：引物探针方案

Step 3: 内标设计
  ├─ WF-03
  └─ 输出：内标方案

Step 4: 变异耐受性预评估
  ├─ WF-05
  └─ 输出：变异耐受性评估

Step 5: 综合面板报告
  ├─ 汇总：靶标选择理由 + 引物探针 + 内标 + 特异性 + 变异覆盖率
  ├─ 填充 $.recommended_conditions（schema 见输出格式 §O5）
  │   annealing_temp: 标准TaqMan两步法退火/延伸=60°C(退火/延伸合并),
  │   引物Tm 50-65已为此优化(R-01 [汪博改版]), 中心58-60为设计目标。basis公式用于非标准场景:三步法时
  │   Ta=min(lowest_primer_tm-4,60);松弛后Tm<58时降至58°C并延长退火至90s
  ├─ C30(结果解释)自然语言设计报告
  └─ 输出：完整诊断面板设计包（全量 JSON Schema）
```

---

## WF-07: 引物问题诊断与优化

```
输入：问题引物探针序列 + 问题描述 + 可选实验条件
输出：问题诊断 + 优化方案

Step 1: 全面检查
  ├─ P1(thermodynamics)完整热力学参数
  ├─ P2(BLAST)全面特异性验证
  ├─ 所有红线条款逐项检查 (R-01~R-10; R-05含反义链补救(BioPython reverse_complement); R-07区分MGB/非MGB)
  ├─ 模板序列如提供 → P6(ViennaRNA)扩增子二级结构检查(R-补充; P6不可用→跳过,标注)
  └─ 输出：性质检查表

Step 2: 问题归因（LLM+规则辅助）
  ├─ 引物二聚体条带 → homodimer/heterodimer ΔG过低
  ├─ 非特异条带(高分子量) → 3'端互补/基因组多位点命中
  ├─ 非特异条带(低分子量) → 引物自身延伸(hairpin)
  ├─ 灵敏度差 → Tm偏低/探针失效/靶标变异(→WF-05排查)
  ├─ 背景荧光高 → 探针5'=G/淬灭基团效率低
  ├─ 归因置信度评估：
  │   ├─ 如归因可量化的热力学问题(二聚体ΔG明确超标/Tm明确偏移)→高置信度→直接给优化建议
  │   ├─ 如归因无法量化的实验因素(模板质量/PCR抑制物/操作误差)→中置信度→标注"可能原因,建议实验验证"
  │   └─ 如LLM无法区分多个可能原因→低置信度→输出：
  │       "自动化诊断无法确定问题根因。建议提供以下信息辅助人工判断:"
  │       "- 凝胶电泳图或熔解曲线"
  │       "- 退火温度梯度实验数据"
  │       "- 模板浓度和纯度(NanoDrop/Qubit)"
  │       "- 最近一次试剂更换/仪器校准时间"
  └─ 输出：归因结果 + 置信度 + 建议下一步

Step 3: 优化方案（按诊断分支）
  ├─ 二聚体 → 微调3'端1-2nt位置 / 换区域重设计(WF-01)
  ├─ 非特异 → 收紧退火温度建议(+2°C) / 换区域
  ├─ 灵敏度差 → 换MGB探针 / 变异检查(WF-05) / 缩短扩增子
  ├─ 背景荧光 → 重设计探针(避5'=G) / 换荧光通道
  └─ 输出：1-3个优化方案

Step 4: 优化方案验证
  ├─ 对每个优化方案：
  │   1. 先调用P1.design_primers(优化后序列, 模板)获取完整输出含坐标
  │   2. 执行WF-01 Step 4-5（MGB评估+R-05/R-06间距+BLAST特异性）
  └─ 输出：验证通过的优化方案

Step 5: 报告
  ├─ 按 R-20：evidence_level 继承已有设计的原始层级，诊断过程不改变
  ├─ 诊断报告：问题原因+证据+旧vs新参数对比
  └─ 输出：诊断与优化报告
```

---

# 交付物 4：Workflow调用关系与工具引用

> 六个 Tool SKILL.md 已独立交付为: P1_Primer3.md / P2_BLAST.md / P3_MGB.md / P4_Multiplex.md / P5_Internal_Control.md / P6_ViennaRNA.md

## 跨Workflow调用关系
```
WF-06(完整面板) ←── 端到端入口
  ├── WF-01(单靶标) 或 WF-02(多重)
  ├── WF-03(内标)
  └── WF-05(变异耐受性)

WF-02(多重面板)
  ├── WF-01 × N（循环）
  └── WF-03(内标)

WF-07(问题诊断)
  ├── 可能调用 WF-05(变异耐受性)
  └── 可能调用 WF-01(重新设计)

WF-01 ← 最基础单元,被复用
WF-03 ← 独立或被WF-02/WF-06调用
WF-04 ← 独立验证
WF-05 ← 独立或被WF-06/WF-07调用
```

## 工具×Workflow引用矩阵

| 工具 | WF-01 | WF-02 | WF-03 | WF-04 | WF-05 | WF-06 | WF-07 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **C0** | ✅ | via01 | — | — | ✅ | ✅ | — |
| **C1** | ✅ | via01 | — | — | — | via01 | — |
| **C5** | ✅ | via01 | ✅ | — | ✅ | via01 | — |
| **C6** | via P2 | via P2 | via P2 | via P2 | — | via P2 | via P2 |
| **C6b** | ✅ | via01 | — | — | ✅ | ✅ | — |
| **C7** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **C21** | ⚡P1 | via01 | — | — | ⚡P1 | ⚡P1 | — |
| **C29** | ✅ | ✅ | — | — | — | ✅ | ✅ |
| **C30** | ✅ | ✅ | — | — | — | ✅ | ✅ |
| **P1** | ✅ | via01 | ✅ | ✅ | ✅ | via01 | ✅ |
| **P2** | ✅ | via01 | ✅ | ✅ | — | via01 | ✅ |
| **P3** | ✅ | via01 | — | — | — | via01 | via01 |
| **P4** | — | ✅ | — | — | — | via02 | — |
| **P5** | — | via03 | ✅ | — | — | via03 | — |
| **P6** | ✅ | via01 | via03 | ⚡可选 | — | via01 | ⚡可选 |

> via01/02/03 = 通过调用WF-01/02/03间接引用  
> ⚡P1 = P1优先级增强能力,不可用时有降级方案

---

## 附录：AgentD 最新专家确认事项与阶段性实施基线（更新至 2026-08-19）

> **适用说明**：本附录记录最新专家确认事项和阶段性实施顺序。正文保持原样；如正文与本附录在执行路线、阶段范围、参数、内标或通道规则上存在冲突，以本附录为当前实施补充。未在本附录中调整的规则、工具定义和数据要求继续沿用正文。阶段性跳过表示 `NOT_EVALUATED`，不表示删除目标能力，也不得解释为 `PASS`。

### 1. 总体实施优先级

1. **第一优先级为 C5/MAFFT 及其涉及的 Workflow**：先完成代表序列准备、多序列比对、保守区识别、证据保存和结果映射。
2. MAFFT 落地后，先搭建 WF-02～WF-07 的完整流程骨架、状态流转、工具网关、数据结构和报告框架。
3. WF-01 的 P3/MGB 和简并引物/探针策略放入第二阶段；其他 Workflow 中涉及 P3/MGB 的逻辑也可在第一阶段跳过。
4. P3/MGB、简并或其他暂未执行的能力必须明确标记为 `NOT_EVALUATED` 或“当前阶段未执行”，不得计入通过结论或综合评分。
5. P4、WF-06 等依赖具体实验条件的选择逻辑采用配置化、可替换策略。目标仪器型号、ROX 参比要求和可用通道数等待进一步取得专家信息；在此之前可使用已声明的默认逻辑，不阻塞流程骨架建设，但必须标注配置来源和未校准风险。

### 2. WF-01 当前专家确认基线

#### 2.1 完整主路线与 Primer3 设计支线

WF-01 以正文定义的完整流程为**主路线**；`DESIGN_ONLY` 不是与完整流程并列的另一套 WF-01，而是从主流程设计阶段分出的 Primer3 设计支线。两者共用输入预检、P1 候选结构和报告基础字段，但结束位置与证据范围不同。报告必须明确本次走的是主路线还是设计支线，不得混用两者的结论。

##### 路线 A：Primer3 设计与报告支线（DESIGN_ONLY）

1. 本路线是从 WF-01 设计阶段分出的支线，只完成 P1（Primer3）候选设计、基础规则检查、候选排序和设计报告，随后结束。
2. 本支线不进入完整 WF-01 的后续验证链，不调用 P2，也不执行 BLAST 靶标、宿主或近缘物种特异性检查。
3. 仅当用户明确要求“只进行 Primer3 设计/只输出设计报告”时进入本支线；上层 Workflow 需要可交付验证候选时不得使用本支线代替完整主路线。
4. 报告标记 `execution_mode = DESIGN_ONLY`，同时明确标注“WF-01 设计支线”；BLAST 及特异性证据标记为 `NOT_EVALUATED`。
5. 未执行的特异性项目不得输出 `PASS`，不得描述为“已通过特异性验证”，也不得参与综合评分。

##### 路线 B：正文完整 WF-01 主路线（FULL_SPECIFICITY）

1. 本路线是 WF-01 的正式主路线，其目标流程与正文定义的完整 WF-01 保持一致，不应被缩减为“Primer3 + BLAST”两步流程。
2. 主路线按正文依次编排当前适用的输入与序列准备、MAFFT/保守区、P1 候选设计与松弛、简并并行策略、P3/MGB（触发时）、P2 特异性验证、P6 扩增子结构、评分、自检和完整报告等步骤。
3. 当前阶段尚未实现的能力只按阶段计划暂缓：MAFFT/保守区列为第一优先级；P3/MGB、L3 和简并策略列入第二阶段。暂缓节点保留在主路线定义和数据结构中，并标记为 `NOT_EVALUATED`，不得从完整 WF-01 目标流程中删除。
4. 当前可执行候选设计完成后必须调用 P2；按 P2 契约执行靶标命中、宿主背景及近缘/交叉物种检查，并保留命中记录、判定理由和证据来源。
5. 报告标记 `execution_mode = FULL_SPECIFICITY`，同时明确标注“WF-01 完整主路线”；只有实际完成相应检查后，才能输出对应的 `PASS`、`CAUTION` 或 `FAIL`。
6. BLAST、数据库、输入或其他主路线依赖不可用时，应在主路线内如实标记为 `NOT_EVALUATED`、阻塞或降级状态，不得自动切换成 `DESIGN_ONLY`，也不得自动等同于通过。
7. WF-02、WF-06 等需要完整验证候选的上层 Workflow 必须调用本主路线，不得调用 Primer3 设计支线代替。

#### 2.2 当前设计参数和松弛规则

以下为专家确认后的当前 WF-01 普通 TaqMan 实施参数：

| 参数档 | 扩增产物范围 | 引物 Tm 范围 | 普通探针长度 | 说明 |
|---|---:|---:|---:|---|
| `default` | 70–150 bp | 58–62°C | 22–32 nt | 当前标准设计档 |
| `L1` | 70–180 bp | 58–65°C | 22–32 nt | 放宽产物上限和引物 Tm 上限 |
| `L2` | 70–200 bp | 50–65°C | 22–32 nt | 进一步放宽引物 Tm 下限和产物上限 |

补充规则：

- 当前阶段只使用 `default`、`L1`、`L2` 以及一个经校验、去重后的用户参数档；不执行 L3。
- L3 与 MGB 短探针路径随 P3/MGB 一并进入第二阶段。
- 普通探针 5′ 端为 G 时直接排除，当前路线不执行反向互补挽救。
- 当前每个靶标固定输出最多 Top 10；不足 10 套时如实输出，不伪造候选。
- 当前搜索完整且有至少 10 套可交付候选时为 `SUCCEEDED`；1–9 套为 `PARTIAL`；有候选但搜索或证据不完整时为 `PARTIAL_INCOMPLETE`；0 套为 `FAILED`。
- 在保守性尚未接入评分前，`FULL_SPECIFICITY` 使用阶段性三维权重：热力学 0.357、特异性 0.357、扩增子特征 0.286；`DESIGN_ONLY` 仅对热力学和扩增子特征重新归一化。保守性接入后应发布新的版本化评分策略，不覆盖历史结果。

### 3. MAFFT 与保守区能力

1. MAFFT 作为 C5 既有工具调用，Workflow 不自行实现序列比对算法。
2. 直接涉及 MAFFT/保守区的主要路径包括 WF-01、WF-03/P5、WF-05；WF-02 和 WF-06 通过调用 WF-01/P5 间接继承相关证据。
3. 第一阶段应形成可追溯链路：序列来源与数量 → MAFFT 输入 → 比对结果 → 保守区坐标和序列 → 候选覆盖/保守性证据 → 报告字段。
4. 多序列输入时执行 MAFFT 和保守区识别；单序列输入不得声称完成群体保守性验证。
5. 代表序列获取、渐进放宽及无保守区时的全长回退继续以正文规则为目标实施。
6. 在 MAFFT 或保守性分析未实际完成前：
   - 保守性状态为 `NOT_EVALUATED`；
   - 不生成虚构的保守性分数；
   - 保守性不进入综合评分；
   - 报告说明序列数量、来源、是否执行比对及未完成原因。

### 4. P1–P6 工具复用边界

1. P1–P6 是 AgentD 的既有主要工具。Workflow 负责调用、编排和结果映射，不重复实现这些工具已经提供的核心算法、公式或阈值。
2. WF-01 的 `DESIGN_ONLY` 支线只调用 P1；`FULL_SPECIFICITY` 主路线按正文编排完整工具链，其中 P2 为必需的特异性工具，M4 不再自行定义另一套特异性策略。
3. M4 只负责准备 P2 输入、调用 P2、保存证据，并把 P2 的结果映射到 WF-01 规则和报告；identity、coverage、E-value、3′错配和 ΔG 等判定以 P2 返回结果为准。
4. P5 内部的宿主和交叉物种特异性验证同样调用 P2，不直接调用底层 BLAST 适配器另行维护特异性阈值。
5. P4 继续调用 P1 完成交叉二聚体等既有计算；WF-02/WF-06 不复制 P4 算法。
6. P6 按各 Workflow 已接入能力执行；不可用或尚未接入时按软性规则标记 `NOT_EVALUATED`，不得伪装为通过。
7. 完整主路线发生依赖缺失或工具失败时，保留原路线身份和失败证据；不得通过静默降级到 `DESIGN_ONLY` 来生成看似完成的结果。

### 5. WF-02 已确认规则

#### 5.1 重数、靶标数与内标

1. “重数”仅指目标靶标数，内标不计入重数，但额外占用 1 个检测通道。
2. WF-02 基础输入范围为 2–5 个目标靶标。
3. 实际可运行上限由目标仪器的报告通道数及 ROX 参比占用动态决定：
   - `所需信号通道数 = 目标靶标数 +（启用内标时为 1，否则为 0）`；
   - `可用报告通道数 = 仪器可用通道数 - ROX 参比占用通道数`；
   - 仅当所需信号通道数不大于可用报告通道数时才可组成完整面板。
4. 对五通道仪器，启用内标时目标靶标上限为 4；不启用内标时可支持 5 个目标靶标，但仍须通过仪器配置和 P4 验证。

#### 5.2 内标编排入口与失败行为

1. WF-02 Step 2 和 WF-06 对应阶段直接调用 `P5.design_internal_control()`；不经过 WF-03 的交互式兜底包装。
2. WF-03 保留为独立用户入口，与 WF-02/WF-06 共用 P5。
3. P5 未获得可用内标候选时，记录内标 `FAIL`、失败原因和风险警告，但不阻塞目标面板继续组装。
4. 降级为不含内标的面板时，最终报告必须明确说明“用户请求了内标，但本次未获得可用内标”，并保留后续补充内标或重新设计入口。

#### 5.3 通道指派责任和内标替代规则

1. WF-02/WF-06 是荧光通道指派责任方；P4 只负责通道兼容性、仪器适配和交叉二聚体等验证，不负责生成通道方案。
2. P5 只提供内标默认建议 `Cy5`，最终通道由 WF-02/WF-06 指派层结合目标仪器确定。
3. 内标通道选择顺序为：`Cy5 → 最远可用的远红通道 → 任意经 P4 验证的不冲突通道`。远红替代优先考虑目标仪器实际支持的 Cy5.5、Quasar 705 或等价通道。
4. 禁止选择与已占用通道发射峰差 `Δλ_em < 15 nm` 的替代通道。
5. Quasar 670 与 Cy5 发射峰近似相同，不作为 Cy5 被占用时的有效替代；Texas Red 也不得作为未经仪器与光谱验证的固定替代建议。
6. 缺少表达量数据时，目标通道可先按输入顺序和默认通道优先级指派；所有方案仍须由 P4 验证。

#### 5.4 仪器配置的阶段性处理

1. 需进一步确认的仪器信息包括：实际型号、是否需要 ROX 参比、ROX 是否占用检测通道、可用通道数、滤光片/染料兼容表及客户校准配置。
2. 在取得上述信息前，可以使用当前默认仪器配置和默认通道逻辑搭建并运行流程骨架。
3. 默认配置必须标记为 `UNVERIFIED_TOOL_DEFAULT` 或等价状态；其结果只能进入风险/待确认层，不得描述为已在客户真实仪器上验证。
4. 用户或专家提供真实仪器配置后，应冻结到任务运行快照，并由 P4 重新验证，不覆盖历史版本证据。

### 6. WF-02～WF-07 第一阶段骨架要求

1. MAFFT 公共能力落地后，依次完善 WF-02～WF-07 的输入校验、节点状态、工具调用边界、失败/交互分支、证据状态、版本追溯和报告框架。
2. 第一阶段不得在 Workflow 内复制 P1–P6 算法；只调用既有工具并映射其结果。
3. 涉及 P3/MGB 的节点在第一阶段可跳过，但必须保留节点、字段和 `NOT_EVALUATED` 状态，以便第二阶段接入。
4. 涉及具体实验条件但尚未确认的选择逻辑，应使用带策略版本的默认配置，并在输出中显示配置来源、风险和待确认项，不因此阻塞其余流程骨架。
5. 第二阶段再接入 WF-01 的 P3/MGB、L3 和简并策略，并同步补齐其他 Workflow 对 P3/MGB 结果的消费逻辑。

### 7. 数据、证据与报告一致性

1. 各 Workflow 应尽量共用候选和证据字段，通过 `execution_mode`、`evidence_level`、`tool_status`、策略版本、警告和未评估项区分证据范围。
2. 报告必须能够追溯实际调用的工具、工具状态、输入数据、序列来源、执行路线、松弛层级、仪器配置、通道指派来源及未完成原因。
3. `NOT_EVALUATED` 只表示该项未执行；不得转换为 `PASS`、不得参与相应维度评分，也不得据此生成实验可用性结论。
4. 后续接入 MAFFT 保守性、P3/MGB、简并策略或真实仪器配置时，应扩展并版本化同一套数据与报告结构，避免形成互不兼容的输出路线。
