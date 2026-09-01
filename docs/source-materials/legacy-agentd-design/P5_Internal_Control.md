## 4e: P5 — 内标引物设计

# P5: 内标引物设计

## 定位
P5 是内标引物设计的完整编排模块——对外暴露单一入口 `design_internal_control()`，内部编排 NCBI Entrez 序列获取、C5 (MAFFT) 保守性分析、P1 (Primer3) 引物探针设计与热力学验证（含 R-05 探针5'≠G 检查 + R-06 坐标间距检查）、P2 (BLAST) 宿主特异性验证、P6 (ViennaRNA) 扩增子二级结构检查，输出完整的内标引物方案与验证报告。染料基团的默认指派 (Cy5, 最远通道) 在 P5 内完成；若用户同时提供靶标引物列表，P5 自动执行与靶标的交叉二聚体检查。P5 不执行 MGB 修饰 Tm 计算 (→ P3)、不检查多重荧光兼容性 (→ P4)、不执行简并碱基策略 (→ WF-01 并行策略)。

## 优先级
P1

## 依赖

| 工具 | 用途 | 调用方式 |
|------|------|---------|
| NCBI Entrez (Biopython Bio.Entrez) | 获取宿主管家基因 RefSeq 序列及多品种同源序列 | `Bio.Entrez.efetch` |
| C5 (MAFFT) | 多品种/品系管家基因序列比对, 计算保守性 | `subprocess.run` 调用 CLI |
| P1 (Primer3) | `design_primers` — 设计内标引物与探针; `calc_heterodimer` — 与靶标交叉二聚体检查 | `import` Python 模块 |
| P2 (BLAST) | `check_host` + `check_cross_species` — 确认引物仅命中宿主管家基因, 排除近缘种/共感染病原体交叉 | `import` Python 模块 |
| P6 (ViennaRNA) | `assess_amplicon_secondary_structure` — 内标扩增子二级结构检查 (R-补充); 不可用时跳过 | `import` Python 模块 |

## 实现
自建 Python 模块 (~300 行)。依赖 P1/P2/C5 均可用状态下运行；P6 不可用时跳过不阻塞。P1/P2/C5 任一不可用时 P5 返回降级错误码。

---

## 1. 管家基因数据库 (P5 维护)

### 1.1 默认管家基因与 RefSeq 序列

P5 内置三个物种 × 三个默认管家基因的 RefSeq 编号。序列由 NCBI Entrez 实时获取——不硬编码序列本身 (序列版本可能更新)。

| 基因 | 猪 (Sus scrofa) | 牛 (Bos taurus) | 鸡 (Gallus gallus) | 典型 Ct |
|------|------|------|------|:---:|
| β-actin (ACTB) | NM_213978 | NM_173979.3 | NM_205518.1 | 22–28 |
| GAPDH | NM_001206359.1 | NM_001034034.2 | NM_204305.1 | 20–26 |
| 18S rRNA | NR_046261 `[已验证: Sus scrofa]` | NR_036642 `[已验证: Bos taurus]` | NR_046237 `[物种待确认: 使用前须通过 NCBI Nucleotide 验证 Gallus gallus 归属; 若验证失败则改用鸡基因组 rRNA locus]` | 9–16 |

### 1.2 默认优先级与选择逻辑

```
用户未提供 preferred_genes:
  1. β-actin (ACTB)  → 保守性 ≥ 0.99? → 使用
  2. GAPDH           → 保守性 ≥ 0.99? → 使用
  3. 18S rRNA        → 保守性 ≥ 0.99? → 使用 (强制启用限量引物策略)
  4. 备选: RPLP0, B2M, SDHA, TBP, HMBS → 需用户在 preferred_genes 中提供 RefSeq 编号 (P5 未内置备选基因的 RefSeq 数据库)
  5. 全部失败 → 返回 FAIL + "建议使用外源性异源内标"

用户提供了 preferred_genes:
  按用户指定的顺序依次尝试
  全部失败 → 降级到默认优先级列表
```

### 1.3 18S rRNA 限量引物策略

18S rRNA 的天然高丰度 (Ct 9–16) 导致扩增曲线过早饱和——必须执行限量引物处理：

- 引物浓度: 从标准 200–400 nM 降至 **50–100 nM**
- 设计参数传递: `PRIMER_DNA_CONC = 50.0` (替代标准的 250.0 nM)
- 预期 Ct: 延迟至 20–30 (落在靶标范围内, 对应 SOP R-15)
- 输出标注: `"limiting_primer_strategy": "18S rRNA 引物浓度 50–100 nM"`

不执行限量处理将导致内标 Ct 过早而无法有效检测 PCR 抑制剂——WOAH 第 2.2.3 章对此风险有明确警告。

---

## 2. 输入/输出

### 输入

```python
# host_species: str — "pig" | "cattle" | "chicken"
# preferred_genes: list[str] | None — 用户指定的管家基因列表 (基因符号, 如 ["TBP", "HMBS"])
# existing_targets: list[dict] | None — 已有靶标引物, 用于交叉二聚体检查
#   [{"target_id": str, "fwd_seq": str, "rev_seq": str, "probe_seq": str}, ...]

P5.design_internal_control(
    host_species="pig",
    preferred_genes=None,          # None = 使用默认优先级
    existing_targets=None,         # 不执行交叉验证
)
```

### 输出

```python
{
    "gene_selected": str,              # 最终选定的管家基因符号
    "refseq_accession": str,           # 使用的 RefSeq 编号
    "conservation": float,             # 多品种保守性 (0–1)
    "conservation_status": str,        # "PASS" | "CAUTION"
    "primers": [
        {
            "primer_id": str,
            "fwd_seq": str, "rev_seq": str, "probe_seq": str,
            "fwd_tm": float, "rev_tm": float, "probe_tm": float,
            "gc_percent": float, "product_size": int,
            "amplicon_seq": str,       # 来源: 从RefSeq模板截取 forward→reverse 完整序列 (R-21; P1坐标)
            "fluorophore": "Cy5",      # 内标固定使用最远通道
            "quencher": str,           # BHQ-2 (Cy5 推荐)
            "amplicon_structure": dict | None,  # 来源: P6; {"status":"PASS"|"CAUTION"|"WARNING"|"not_evaluated","mfe_dg":float,...}; P6不可用时={"status":"not_evaluated"}
        },
        ...
    ],
    "limiting_primer_strategy": str | None,  # 18S rRNA 时限量为 "50–100 nM"
    "cross_check": {
        "performed": bool,
        "heterodimer_pairs_checked": int,
        "worst_heterodimer_dg_cal_per_mol": float,
        "verdict": str,               # "PASS" | "CAUTION" | "FAIL"
    } | None,
    "specificity_check": {
        "host_hits": int,
        "pathogen_cross_hits": int,
        "verdict": str,
    },
    "verdict": str,                    # "PASS" | "CAUTION" | "FAIL"
    "warnings": list[str],
}
```

---

## 3. 全流程

```python
def design_internal_control(host_species: str,
                             preferred_genes: list[str] | None = None,
                             existing_targets: list[dict] | None = None) -> dict:
    """P5 主入口。编排管家基因选择→保守性→设计→R-05/R-06检查→交叉验证→特异性验证→二级结构检查全流程。"""
    warnings: list[str] = []

    # 物种支持检查: P5 仅内置 pig/cattle/chicken——必须置于函数最前, 避免无意义的基因遍历
    if host_species not in REFSEQ_DB:
        return {
            "verdict": "FAIL",
            "warnings": [f"P5 尚未内置 {host_species} 的管家基因 RefSeq 编号。已支持: pig/cattle/chicken。对于未内置物种，请使用 preferred_genes 参数提供自定义管家基因 RefSeq 编号。"],
        }

    # ── Step 1: 基因选择 ──
    gene_queue = preferred_genes or DEFAULT_PRIORITY[host_species]
    selected_gene = None
    refseq_id = None
    refseq_seq = None  # 选定的 RefSeq 序列, Step2 设计+Step5 P6 截取扩增子均需使用
    conservation = 0.0
    cons_status = "FAIL"

    # 第一轮: 遍历全部基因, 收集所有保守性结果
    results = []
    for gene in gene_queue:
        species_db = REFSEQ_DB.get(host_species, {})
        if gene not in species_db:
            warnings.append(f"{gene} 未在 P5 RefSeq 数据库中注册, 跳过 (需用户在 preferred_genes 中提供 RefSeq 编号)")
            continue
        refseq_id = species_db[gene]
        # 从 NCBI 获取 RefSeq 序列 (实时, 不做本地缓存)
        refseq_seq = fetch_refseq(refseq_id)
        # 获取同物种 ≥ 5 个品种/品系的同源序列
        homologs = fetch_homologs(gene, host_species, min_strains=5)
        # C5 (MAFFT) 多序列比对 → 计算保守性
        conservation = run_mafft_and_calc_conservation([refseq_seq] + homologs)
        results.append((gene, refseq_id, conservation))

    # 第二轮: 优先选 ≥ 0.99 且优先级最高的; 若无, 取 ≥ 0.97 中 conservation 最高的
    pass_genes = [(g, r, c) for g, r, c in results if c >= 0.99]
    caution_genes = [(g, r, c) for g, r, c in results if 0.97 <= c < 0.99]

    if pass_genes:
        selected_gene, refseq_id, conservation = pass_genes[0]  # 已按优先级排序
        refseq_seq = fetch_refseq(refseq_id)  # 重新获取选定基因的序列(供Step2/Step5使用)
        cons_status = "PASS"
    elif caution_genes:
        caution_genes.sort(key=lambda x: x[2], reverse=True)  # conservation 最高的优先
        selected_gene, refseq_id, conservation = caution_genes[0]
        refseq_seq = fetch_refseq(refseq_id)
        cons_status = "CAUTION"
        warnings.append(f"{selected_gene} 保守性 {conservation:.2f} (< 0.99), 已选为最优可用基因")
    else:
        for g, r, c in results:
            warnings.append(f"{g} 保守性 {c:.2f} < 0.97, 跳过")

    if selected_gene is None:
        return {
            "verdict": "FAIL",
            "warnings": warnings,
            "recommendation": (
                "所有候选管家基因保守性均 < 0.97。建议: "
                "(1) 提供更多品种/品系序列; "
                "(2) 指定已知在目标物种/组织中稳定的自定义管家基因; "
                "(3) 考虑使用外源性异源内标 (人工合成序列 + 独立引物探针)"
            ),
        }

    # ── Step 2: 引物设计 (P1) ──
    dna_conc = 50.0 if selected_gene == "18S rRNA" else 250.0  # 限量引物策略
    design_result = P1.design_primers(
        seq_args={'SEQUENCE_TEMPLATE': refseq_seq},
        global_args={
            'PRIMER_PICK_LEFT_PRIMER': 1,
            'PRIMER_PICK_RIGHT_PRIMER': 1,
            'PRIMER_PICK_INTERNAL_OLIGO': 1,
            'PRIMER_PRODUCT_SIZE_RANGE': [[70, 120]],
            'PRIMER_OPT_TM': 60.0, 'PRIMER_MIN_TM': 58.0, 'PRIMER_MAX_TM': 62.0,
            'PRIMER_INTERNAL_OPT_TM': 70.0, 'PRIMER_INTERNAL_MIN_TM': 68.0, 'PRIMER_INTERNAL_MAX_TM': 72.0,
            'PRIMER_DNA_CONC': dna_conc,
            'PRIMER_NUM_RETURN': 5,
            'PRIMER_MAX_POLY_X': 3,
        }
    )

    primers = extract_primer_pairs(design_result, fluorophore="Cy5", quencher="BHQ-2")
    if not primers:
        return {"verdict": "FAIL", "warnings": warnings + ["P1 设计返回 0 套候选"]}

    # P1 设计后执行硬性规则过滤 (对齐 WF-01 _design_round, R-01~R-04/R-07~R-10)
    for p in list(primers):  # 遍历副本以安全删除
        fwd_tm, rev_tm = p["fwd_tm"], p["rev_tm"]
        # R-01: Tm 50-65, ΔTm≤2
        if not (50 <= fwd_tm <= 65) or not (50 <= rev_tm <= 65) or abs(fwd_tm - rev_tm) > 2:
            primers.remove(p); continue
        # R-02: ≥50bp
        if p["product_size"] < 50:
            primers.remove(p); continue
        # R-03: 3'端5bp G+C≤2, 无≥4G
        fwd_3end = p["fwd_seq"][-5:]; rev_3end = p["rev_seq"][-5:]
        if sum(1 for b in fwd_3end if b in 'GC') > 2 or sum(1 for b in rev_3end if b in 'GC') > 2:
            primers.remove(p); continue
        if 'GGGG' in p["fwd_seq"] or 'GGGG' in p["rev_seq"]:
            primers.remove(p); continue
        # R-04: 18-28nt
        if not (18 <= len(p["fwd_seq"]) <= 28) or not (18 <= len(p["rev_seq"]) <= 28):
            primers.remove(p); continue
        # R-07: 探针Tm比引物高8-10°C (内标不用MGB, 始终执行R-07)
        avg_tm = (fwd_tm + rev_tm) / 2
        if not (8 <= p["probe_tm"] - avg_tm <= 10):
            primers.remove(p); continue
        # R-08: 探针18-30nt (内标不用MGB)
        if not (18 <= len(p["probe_seq"]) <= 30):
            primers.remove(p); continue
        # R-09: 3'端二聚体 ΔG ≥ -6.0
        end_dg = float(P1.calc_heterodimer(p["fwd_seq"][-6:], p["rev_seq"][-6:]).dg)
        if end_dg < -6000:  # cal/mol
            primers.remove(p); continue
        # R-10: 交叉二聚体3'端 ΔG ≥ -9.0
        f3 = float(P1.calc_heterodimer(p["fwd_seq"][-6:], p["rev_seq"]).dg)
        r3 = float(P1.calc_heterodimer(p["rev_seq"][-6:], p["fwd_seq"]).dg)
        if f3 < -9000 or r3 < -9000:
            primers.remove(p); continue

    if not primers:
        return {"verdict": "FAIL", "warnings": warnings + ["全部内标候选被硬性规则(R-01~R-10)排除"]}

    # P1 设计后立即执行 R-05 (探针5'≠G) 和 R-06 (间距≥5bp)
    # 使用 P1 输出的坐标字段, 与 WF-01 Step4 逻辑一致
    for p in primers:
        # R-05: 探针5'≠G (含补救)
        if p["probe_seq"][0] == 'G':
            from Bio.Seq import Seq
            rc = str(Seq(p["probe_seq"]).reverse_complement())
            if rc[0] != 'G':
                p["probe_seq"] = rc
                warnings.append(f"{p['primer_id']} 探针5'=G→反义链替换(R-05补救)")
            else:
                warnings.append(f"{p['primer_id']} 探针5'=G且反义链也5'=G, 排除")
                primers.remove(p)
                continue
        # R-06: 间距检查 (使用 Primer3 输出坐标)
        if (p["probe_start"] - p["fwd_end"]) < 5 or (p["rev_start"] - p["probe_end"]) < 5:
            warnings.append(f"{p['primer_id']} 探针间距不足5bp, 排除")
            primers.remove(p)
            continue

    if not primers:
        return {"verdict": "FAIL", "warnings": warnings + ["全部内标候选因R-05/R-06被排除"]}

    # P1 设计后执行软性规则检查 (对齐 WF-01, R-01b/R-02软/R-03b/R-11/R-12)
    # 不排除候选，仅写入 warnings
    for p in primers:
        # R-01b: Tm<55 → WARNING
        if p["fwd_tm"] < 55:
            warnings.append(f"{p['primer_id']} Forward Tm={p['fwd_tm']:.1f}<55°C (R-01b)")
        if p["rev_tm"] < 55:
            warnings.append(f"{p['primer_id']} Reverse Tm={p['rev_tm']:.1f}<55°C (R-01b)")
        # R-02软: 50-70bp → WARNING
        if 50 <= p["product_size"] < 70:
            warnings.append(f"{p['primer_id']} 扩增子{p['product_size']}bp偏短(50-70bp) (R-02)")
        # R-03b: GC clamp缺失 / 连续≥3相同碱基
        if sum(1 for b in p["fwd_seq"][-5:] if b in 'GC') == 0:
            warnings.append(f"{p['primer_id']} Forward 3'端无GC clamp (R-03b)")
        if sum(1 for b in p["rev_seq"][-5:] if b in 'GC') == 0:
            warnings.append(f"{p['primer_id']} Reverse 3'端无GC clamp (R-03b)")
        for base in 'ATGC':
            if base * 3 in p["fwd_seq"]:
                warnings.append(f"{p['primer_id']} Forward含≥3连续{base} (R-03b)")
            if base * 3 in p["rev_seq"]:
                warnings.append(f"{p['primer_id']} Reverse含≥3连续{base} (R-03b)")
        # R-11: 发卡 ΔG<-3.0 → WARNING (含探针)
        for label, seq in [("Fwd",p["fwd_seq"]),("Rev",p["rev_seq"]),("Probe",p["probe_seq"])]:
            hp_dg = float(P1.calc_hairpin(seq).dg)
            if hp_dg < -3000:  # cal/mol
                warnings.append(f"{p['primer_id']} {label}发卡ΔG={hp_dg:.0f}<-3.0 (R-11)")
        # R-12: 非3'端弱二聚体 -9.0~-6.0 kcal/mol
        full_dg = float(P1.calc_heterodimer(p["fwd_seq"], p["rev_seq"]).dg)
        end_dg = float(P1.calc_heterodimer(p["fwd_seq"][-6:], p["rev_seq"][-6:]).dg)
        if -9000 <= full_dg < -6000 and end_dg >= -6000:
            warnings.append(f"{p['primer_id']} 非3'端弱二聚体ΔG={full_dg:.0f} (R-12)")

    # ── Step 3: 与靶标交叉验证 (条件执行, P1) ──
    cross_check = None
    if existing_targets:
        # Cy5 通道冲突检查: 内标默认使用 Cy5, 若靶标已占用则发出警告
        if any(t.get("fluorophore") == "Cy5" for t in existing_targets):
            warnings.append("靶标已占用 Cy5 通道, 内标需改用其他通道 (建议 Quasar 670 或 Texas Red)")
        worst_dg = 0.0
        pairs_checked = 0
        for primer_set in primers:
            for target in existing_targets:
                for t_seq in [target["fwd_seq"], target["rev_seq"], target.get("probe_seq", "")]:
                    if not t_seq:
                        continue
                    for p_seq in [primer_set["fwd_seq"], primer_set["rev_seq"], primer_set["probe_seq"]]:
                        dg = float(P1.calc_heterodimer(p_seq, t_seq).dg)
                        pairs_checked += 1
                        if dg < worst_dg:
                            worst_dg = dg

        cross_verdict = "PASS" if worst_dg > -6000 else ("CAUTION" if worst_dg > -7000 else "FAIL")
        cross_check = {
            "performed": True,
            "heterodimer_pairs_checked": pairs_checked,
            "worst_heterodimer_dg_cal_per_mol": worst_dg,
            "verdict": cross_verdict,
        }
        if cross_verdict != "PASS":
            warnings.append(f"与靶标交叉二聚体 ΔG = {worst_dg:.0f} cal/mol")

    # ── Step 4: 宿主特异性验证 (P2) ──
    # 引物和探针均送入宿主特异性验证: 引物命中管家基因是预期行为,
    # 探针同样需要确认仅命中目标管家基因 locus 而非宿主基因组的其他位置 (如伪基因)
    spec_result = P2.check_host(
        primers=[{"id": p["primer_id"], "seq": p["fwd_seq"]} for p in primers] +
                [{"id": p["primer_id"] + "_rev", "seq": p["rev_seq"]} for p in primers] +
                [{"id": p["primer_id"] + "_probe", "seq": p["probe_seq"]} for p in primers],
        host_species=host_species,
    )

    # ── Step 5: 扩增子二级结构检查 (P6, 条件执行) ──
    # 内标扩增子如有强二级结构，Ct异常→内标失效→误判PCR抑制（假阴性风险）
    # P6 不可用时跳过——R-补充为软性规则，不阻塞内标设计
    p6_available = False
    try:
        from P6_ViennaRNA import assess_amplicon_secondary_structure as p6_assess
        p6_available = True
    except ImportError:
        pass

    # 截取扩增产物完整序列 (R-21, 使用P1输出坐标)
    for p in primers:
        p["amplicon_seq"] = refseq_seq[p["fwd_start"]:p["rev_end"]]

    if p6_available:
        for p in primers:
            p["amplicon_structure"] = p6_assess(p["amplicon_seq"])
            if p["amplicon_structure"]["status"] in ("WARNING", "CAUTION"):
                warnings.append(
                    f"内标{p['primer_id']}扩增子二级结构ΔG={p['amplicon_structure']['mfe_dg']:.1f} "
                    f"({p['amplicon_structure']['status']})"
                )
    else:
        for p in primers:
            p["amplicon_structure"] = {"status": "not_evaluated", "mfe_dg": 0.0,
                                        "mfe_structure": "", "tool": "not_evaluated"}

    # ── 汇总判定 ──
    failed = (cons_status == "FAIL" or not primers or
              (cross_check and cross_check["verdict"] == "FAIL") or
              spec_result["verdict"] == "FAIL")
    cautioned = bool(warnings)

    return {
        "gene_selected": selected_gene,
        "refseq_accession": refseq_id,
        "conservation": round(conservation, 3),
        "conservation_status": cons_status,
        "primers": primers,
        "limiting_primer_strategy": "18S rRNA 引物浓度 50–100 nM" if selected_gene == "18S rRNA" else None,
        "cross_check": cross_check,
        "specificity_check": spec_result,
        "verdict": "FAIL" if failed else ("CAUTION" if cautioned else "PASS"),
        "warnings": warnings,
    }
```

---

## 4. 判定逻辑汇总

| 步骤 | 条件 | 判定 |
|------|------|:---:|
| 保守性 | ≥ 0.99 | PASS |
| 保守性 | 0.97–0.99 | CAUTION |
| 保守性 | < 0.97 (全部基因) | FAIL |
| P1 设计 | 返回 ≥ 1 套候选 | PASS |
| P1 设计 | 返回 0 套 | FAIL |
| R-01~R-10硬性过滤 | 全部通过 (Tm/长度/3'端/二聚体) | PASS |
| R-01~R-10硬性过滤 | 任一违反 → 排除该候选; 全部排除 | FAIL |
| R-05 探针5'≠G | probe[0]≠G; =G则排除(无补救) | PASS/FAIL |
| R-06 间距≥5bp | 使用P1坐标检查 | PASS/FAIL |
| R-01b (软) | Tm<55°C | WARNING, 不排除 |
| R-02软 | 50-70bp | WARNING, 不排除 |
| R-03b (软) | GC clamp缺失/≥3连续相同碱基 | WARNING, 不排除 |
| R-11 (软) | 发卡ΔG<-3.0 | WARNING, 不排除 |
| R-12 (软) | 非3'端弱二聚体 | WARNING, 不排除 |
| 交叉二聚体 (条件) | ΔG > −6.0 kcal/mol | PASS |
| 交叉二聚体 (条件) | −7.0 < ΔG ≤ −6.0 | CAUTION |
| 交叉二聚体 (条件) | ΔG ≤ −7.0 | FAIL |
| 宿主特异性 | 仅命中管家基因 | PASS |
| 宿主特异性 | 命中管家基因 + 其他宿主区域 | CAUTION/FAIL (P2 判定) |
| 扩增子二级结构 (条件) | ΔG ≥ -20 (PASS) | 继续 |
| 扩增子二级结构 (条件) | -30 ≤ ΔG < -20 (CAUTION) | WARNING 标注, 不排除 |
| 扩增子二级结构 (条件) | ΔG < -30 (WARNING) | WARNING 标注, 不排除 |
| 扩增子二级结构 (条件) | P6 不可用 | 跳过, 标注 "not_evaluated" |

---

## 5. 使用范围

| 场景 | P5 适用性 | 说明 |
|------|:---:|------|
| WF-03 内标引物设计 (主入口) | 适用 | 编排全流程, WF-03 仅处理失败兜底 |
| WF-02 多重面板 Step2 | 适用 | 直接调用 P5, 绕过 WF-03 的兜底包装 |
| WF-06 端到端完整面板 Step3 | 适用 | 同上, 直接调用 P5 |
| 不存在 BLAST 宿主数据库 (C6 未建库) | 不适用 | P5 依赖 P2 → P2 依赖 C6 |
| 用户未提供物种 | 不适用 | `host_species` 为必填参数 |

---

## 6. 已知局限

1. 管家基因表达稳定性是物种-组织-处理条件依赖的——P5 的保守性评估仅检查序列保守性 (基因组水平的同源性), 不评估表达水平的组织间或处理间差异。MIQE 指南要求通过实验 (geNorm/NormFinder) 验证内参基因的表达稳定性, P5 无法替代此步骤
2. 18S rRNA 由 RNA 聚合酶 I 转录, 其丰度不完全反映 mRNA 群体的完整性——当靶标为 mRNA 时, 18S 作为内标的生物学有效性低于 mRNA 类管家基因
3. 内标引物与探针固定使用 Cy5 通道 (最远通道)——若靶标已占用 Cy5, 调用方需在送入 P5 前自行调整
4. 外源性异源内标在全失败时仅输出建议文本——P5 不自动生成外源性内标的序列、引物和探针。此功能需后续版本补充
5. P5 内置管家基因 RefSeq 数据库仅覆盖 pig/cattle/chicken 三个物种——P2 支持的 sheep/goat 在 P5 中无内置 RefSeq 编号。对于未内置物种，用户需通过 preferred_genes 参数提供自定义管家基因的 RefSeq 编号
6. 备选管家基因 (RPLP0、B2M、SDHA、TBP、HMBS) 未在 P5 的 REFSEQ_DB 中注册——若三个默认基因全部失败，降级的备选基因需用户自行提供 RefSeq 编号
7. 管家基因 RefSeq 编号硬编码——若 NCBI 更新 RefSeq 版本 (如 NM_ 升级为新版本号), P5 需同步更新数据库
8. P6 扩增子二级结构预测精确率仅约 50% (Binet et al. 2023)——内标扩增子的 R-补充 结果为参考值, 不可替代实验验证。P6 不可用时该步骤跳过, 不阻塞内标设计

## 7. 常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| 三个默认管家基因保守性均 < 0.97 | 目标物种品种间遗传多样性高或 NCBI 可获取的同源序列不足 | 扩大检索范围 (更多品种); 指定备选管家基因; 考虑外源性内标 |
| P1 在内标保守区返回 0 套候选 | 靶标保守区序列 GC 含量极端或存在大量重复 | 切换下一个管家基因; 或扩大 P1 的 SIZE_RANGE |
| 18S rRNA 内标 Ct 偏离预期 | 未启用限量引物策略或限量浓度不合适 | 检查 `PRIMER_DNA_CONC` 是否设为 50.0 nM |
| 内标与靶标交叉二聚体 FAIL | 管家基因序列与靶标序列存在偶然的局部互补 | 在 P1 设计时对交叉区域进行避让; 或切换管家基因 |