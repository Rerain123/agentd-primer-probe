## 4b: P2 — 引物特异性评估

# P2: 引物特异性评估（BLAST + 宿主排除）

## 定位
P2 是编排层——不直接执行序列比对和热力学计算。P2 编排 C6 (BLAST+ v2.17.0) 执行全基因组和 NT 全库序列相似性搜索，编排 P1 (Primer3) 执行引物与命中区域的热力学双链稳定性评估，合并两项结果对引物探针进行三模式特异性判定。P2 不修改引物序列——判定失败时 Workflow 层的松弛与回退逻辑接管。P2 持有的宿主基因组物种映射表和 BLAST 判定阈值属于 P2 的领域知识，不归属 C6 或 P1。

## 优先级
P0b（最小闭环必需）

## 依赖

| 工具 | 用途 | 调用方式 |
|------|------|---------|
| C6 (BLAST+ v2.17.0) | 全基因组/NT 序列相似性搜索 | `subprocess.run` 调用 CLI |
| P1 (Primer3) | `calc_heterodimer` — 计算引物与命中区域的双链热力学稳定性 (ΔG, Tm) | `import` Python 模块 |

## 实现
Python 编排模块（~150 行）。自身不含序列比对或热力学算法——仅编排 C6 和 P1 的调用顺序并执行判定逻辑。

---

## 1. 三个检查模式

### 1.1 check_target — 靶标命中确认

确认引物探针在靶标基因组上的命中位置和序列匹配度。

**流程**:
1. C6 执行 `blastn -task blastn-short -db <target_genome> -query <primers.fasta> -evalue 1e-5 -outfmt "6 qseqid sseqid pident length evalue bitscore stitle"` → 获取所有序列匹配位点
2. 对每个 BLAST 命中区域，调用 `P1.calc_heterodimer(primer_seq, hit_context_seq)` → 计算结合 ΔG 和 Tm，评估结合稳定性
3. 合并判定

| BLAST | P1 热力学 | 判定 |
|-------|----------|:---:|
| identity ≥ 95%, e < 1e-5 | ΔG < −9.0 kcal/mol, 3'端 0 错配 | PASS |
| identity 90–95% | ΔG ∈ [−9.0, −6.0] kcal/mol, 3'端 ≤ 1 错配 | CAUTION |
| identity ≥ 95% | ΔG > −9.0 kcal/mol (结合偏弱) | CAUTION |
| identity < 90% | — | FAIL |
| 无命中 | — | FAIL |

### 1.2 check_host — 宿主基因组排除

排除引物探针在宿主基因组上的非特异性命中。

**流程**:
1. 自动映射宿主物种标识符到 BLAST 数据库名称（见第 2 节宿主基因组库）
2. C6 执行 `blastn -task blastn-short -db <host_db> -query <primers.fasta> -evalue 0.1 -outfmt 6` → 使用宽松 e-value 阈值以避免遗漏潜在假阳性
3. 对每个宿主命中区域:
   - C6 调用 `blastdbcmd -db <host_db> -entry <accession> -range <start>-<end>` → 提取命中区域的上下文序列
   - `P1.calc_heterodimer(primer_seq, context_seq)` → 计算实际结合热力学
   - 统计该引物在宿主基因组上的 BLAST 命中总数:
     · 命中数 1–3 → 单拷贝区或低拷贝区, 假阳性风险高
     · 命中数 > 10 → 散在重复序列 (LINE/SINE 等), 实际非特异扩增概率低, 标记低风险但保留 CAUTION
     · 正反向引物在同一染色体 ± 1000 bp 范围内各存在命中 → 可能形成宿主来源扩增子, 判定 FAIL
4. 判定

| 宿主命中 | P1 ΔG | 3' 端错配 | 判定 |
|---------|-------|:---:|:---:|
| 无命中 | — | — | PASS |
| 有命中, e < 0.1 | ΔG > −6.0 kcal/mol | ≥ 2 | CAUTION |
| 有命中, e < 0.1 | ΔG < −6.0 kcal/mol | ≤ 1 | FAIL |
| 有命中, e < 0.1 | ΔG < −6.0 kcal/mol | ≥ 2 | CAUTION (强结合但3'端有错配→延伸效率受限) |
| 有命中, e < 0.1 | ΔG > −6.0 kcal/mol | ≤ 1 | CAUTION (结合弱但3'端匹配→边缘风险) |

FAIL 时输出格式: `非特异性: 宿主 <host_name> <chromosome>:<position>, ΔG=<value> kcal/mol, 3'端 <n> 错配`。

### 1.3 check_cross_species — 近缘种与共感染交叉

在 NT 全库中搜索同属近缘种和已知共感染病原体的交叉命中。

**流程**:
1. 从 `target_species` 二名法字符串 (如 "Pasteurella multocida") 中提取属名 (genus)，构建 Entrez 查询: `<genus>[Organism] NOT <target_species>[Organism]`
2. C6 执行远程 `blastn -task blastn-short -db nt -query <primers.fasta> -evalue 1e-5 -remote`。⚠️ NCBI 远程 BLAST (-remote) 不支持在搜索阶段按分类学过滤——Entrez 查询的属级限制在结果解析阶段由 P2 自行执行。如本地部署 NT 数据库，可使用 `-taxidlist` 参数在搜索阶段过滤，显著提升效率
3. 解析交叉命中的物种、属、科分类信息，应用判定:

| 命中类型 | 判定 |
|---------|:---:|
| 无交叉命中 | PASS |
| 同属近缘种存在命中 | CAUTION (输出: "近缘种交叉: <n> 个同属物种") |
| 已知共感染病原体存在命中 | CAUTION (输出: "共感染交叉风险: <pathogen_name>") |

> 畜牧业诊断场景中，同属近缘种的交叉检出在某些应用语境中是可接受的（参考 WF-04 FAIL 类型 e 的 PASS_WITH_WARNINGS 降级规则）。

---

## 2. 宿主基因组库 (P2 维护)

数据库索引文件由 C6 (`makeblastdb`) 构建，物种标识符到 BLAST 数据库名称的映射是 P2 的领域知识：

| 宿主 | C6 `-db` 参数 | RefSeq 基因组版本 |
|------|-----------|-----------|
| 猪 (Sus scrofa) | sus_scrofa | Sscrofa11.1 (GCF_000003025.6) |
| 牛 (Bos taurus) | bos_taurus | ARS-UCD2.0 (GCF_002263795.3) |
| 鸡 (Gallus gallus) | gallus_gallus | GRCg7b (GCF_016699485.2) |
| 羊 (Ovis aries) | ovis_aries | ARS-UI_Ramb_v2.0 (GCF_016772045.2) |
| 山羊 (Capra hircus) | capra_hircus | ARS1.2 (GCF_001704415.2) |

---

## 3. 输入/输出

**输入 — check_target**:
```
primers: list[dict]  # [{"id": str, "seq": str}, ...]
target_genome: str   # 靶标基因组 BLAST 数据库路径或名称 (调用时由 WF 传入)
```

**输入 — check_host**:
```
primers: list[dict]
host_species: str     # "pig" | "cattle" | "chicken" | "sheep" | "goat"
```

**输入 — check_cross_species**:
```
primers: list[dict]
target_species: str   # 二名法格式, e.g. "Pasteurella multocida"
```

**输出 (统一)**:
```python
{
    "mode": str,             # "check_target" | "check_host" | "check_cross_species"
    "verdict": str,          # "PASS" | "CAUTION" | "FAIL"
    "blast_hits": int,       # 序列命中总数
    "details": list[dict],   # 各命中条目: {accession, identity_pct, evalue, position, chromosome, is_repeat_region}
    "recommendation": str | None,  # FAIL 时的建议动作
    "mode_specific": dict,   # 模式特定字段 (host_species, target_genus, ...)
}
```

---

## 4. 判定逻辑汇总

| 检查模式 | BLAST 条件 | P1 热力学条件 | 判定 |
|---------|-----------|-------------|:---:|
| check_target | identity ≥ 95%, e < 1e-5 | ΔG < −9.0, 3'端 0 错配 | PASS |
| check_target | identity 90–95% | ΔG ∈ [−9.0, −6.0], 3'端 ≤ 1 错配 | CAUTION |
| check_target | identity ≥ 95% | ΔG > −9.0 (结合偏弱) | CAUTION |
| check_target | identity < 90% | — | FAIL |
| check_target | 无命中 | — | FAIL |
| check_host | 无命中 | — | PASS |
| check_host | 有命中, e < 0.1 | ΔG > −6.0, 3'端 ≥ 2 错配 | CAUTION |
| check_host | 有命中, e < 0.1 | ΔG < −6.0, 3'端 ≤ 1 错配 | FAIL |
| check_host | 有命中, e < 0.1 | ΔG < −6.0, 3'端 ≥ 2 错配 | CAUTION |
| check_host | 有命中, e < 0.1 | ΔG > −6.0, 3'端 ≤ 1 错配 | CAUTION |
| check_cross_species | 无交叉命中 | — | PASS |
| check_cross_species | 同属物种有命中 | — | CAUTION |

---

## 5. 使用范围

| 场景 | P2 适用性 | 说明 |
|------|:---:|------|
| WF-01 Step5 特异性验证 | 适用 | `check_target` + `check_host` |
| WF-02 多重面板各靶标验证 | 适用 | 通过 WF-01 间接调用 (via01) |
| WF-03 Step4 内标特异性验证 | 适用 | `check_host` (确认仅命中宿主管家基因) |
| WF-04 已有引物全面验证 | 适用 | 三个检查模式全部调用 |
| WF-07 引物问题诊断 | 适用 | 全面特异性验证 (Step1) |
| 简并碱基策略 (WF-01 并行策略 Step5) | 适用 | 展开后的具体序列逐一 BLAST 验证 |
| C6 不可用时 | 不适用 | P2 无降级路径 |

---

## 6. 已知局限

1. P2 依赖 C6 (BLAST+) 和 P1 (Primer3) 同时可用——C6 不可用时 P2 完全失效，无降级路径
2. BLAST 的启发式种子延伸算法对序列中所有错配位置赋予相等打分权重，不根据错配距引物 3' 端的距离施加额外惩罚——P2 通过附加调用 `P1.calc_heterodimer` 补偿这一局限。若 P1 同时不可用，P2 跳过热力学验证并以 CAUTION 标注 "热力学验证缺失"
3. 长度 < 15 nt 的 MGB 探针在 `blastn-short` 模式 (word_size=7) 下灵敏度显著下降——P2 对短探针直接标记 CAUTION
4. 宿主基因组数据库需每 6 个月通过 NCBI `update_blastdb.pl` 工具更新一次
5. NT 全库检索 (check_cross_species 远程 BLAST) 的结果不可重现——NT 数据库每日增量更新。NT 命中结果仅用于信息参考，不参与 PASS/FAIL 判定

## 7. 常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| BLAST 对所有引物返回 0 命中 | 数据库未建或路径错误 | 检查 `makeblastdb` 构建结果和 `BLASTDB` 环境变量 |
| `blastdbcmd` 提取序列失败 | accession 格式不匹配 | 检查 BLAST 输出中 `sseqid` 字段的格式 |
| 宿主排除返回大量命中导致内存溢出 | 基因组存在大片段重复序列 | 预先对宿主基因组执行 RepeatMasker；或限制 `-max_target_seqs` |
| MGB 短探针 (< 15 nt) BLAST 无结果 | word_size=7 覆盖不足 | 标记 CAUTION 并跳过 BLAST 验证，依赖 P3 的 Tm 评估 |