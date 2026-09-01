## 4a: P1 — Primer3 引物探针设计与热力学评估

# P1: Primer3 引物探针设计与热力学评估

## 定位
P1 是 AgentD 引物探针设计的唯一入口点。所有正向引物、反向引物、TaqMan 探针和 SYBR Green 引物的序列设计及热力学评估均通过 P1 完成。P1 封装 primer3-py (v2.0+)，提供设计端点（`design_primers`）和验证端点（`calc_tm`, `calc_hairpin`, `calc_homodimer`, `calc_heterodimer`, `calc_end_stability` 及对应的轻量 Tm-only 函数）两组接口。P1 使用 SantaLucia 1998 统一近邻热力学参数计算 Tm 和二级结构 ΔG；盐浓度校正使用 SantaLucia 1998 模型 (salt_corrections_method='santalucia', primer3 默认)。Mg²⁺ 通过 dv_conc=3 参数经 MVC 转换后纳入 NN 公式。所有 calc_tm 及验证函数调用统一使用 mv_conc=50, dv_conc=3, dntp_conc=0.8, dna_conc=250 (qPCR master mix 条件)。依据: von Ahsen et al. (2011) Brief Bioinform 12(5):514-517——在含 Mg²⁺ 的 PCR 条件下 SantaLucia 校正优于 Owczarzy 2004 (后者产生 +2.7°C 系统偏差)。P1 不计算 MGB 修饰 Tm（→ P3）、不验证 BLAST 特异性（→ P2）、不检查多重荧光兼容性（→ P4）、不计算扩增子二级结构（→ P6）。P1 的 `design_primers` 输出包含引物/探针在模板上的坐标字段 (PRIMER_LEFT_0, PRIMER_RIGHT_0_end, PRIMER_INTERNAL_0 等)，供下游 WF 层做 R-06 间距检查和 R-21 扩增产物序列截取。

## 优先级
P0b（最小闭环必需）

## 依赖

| 组件 | 用途 | 许可 |
|------|------|------|
| primer3-py (v2.0+) | 主设计引擎与热力学计算 | GPL-2.0+ |
| Bio.SeqIO | FASTA 序列输入解析 | Biopython License |

## 实现
Python 封装层（~200 行）。primer3-py 内部链接 Primer3 C 库，SantaLucia NN 模型适用于 ≤ 60 bp 的序列；超过此长度的序列自动切换为 GC% 经验公式 (Primer3 源码标注 Bolton-McCarthy 1962; 实际原始出处为 Marmur & Doty (1962) JMB 5:109-118，该误归因继承自 Sambrook/Maniatis 分子克隆手册)。

## 仓库
- 核心库: https://github.com/primer3-org/primer3
- Python 封装: `pip install primer3-py`

---

## 1. 设计 API

### 1.1 基础设计（TaqMan 探针模式）

```python
import primer3
from Bio import SeqIO

sequence = 'ATGCGTACGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCGATCG' * 3

result = primer3.design_primers(
    seq_args={'SEQUENCE_TEMPLATE': sequence},
    global_args={
        'PRIMER_TASK': 'pick_pcr_primers_and_hyb_probe',
        'PRIMER_PICK_LEFT_PRIMER': 1,
        'PRIMER_PICK_RIGHT_PRIMER': 1,
        'PRIMER_PICK_INTERNAL_OLIGO': 1,  # Design internal probe
        'PRIMER_NUM_RETURN': 10,
        'PRIMER_PRODUCT_SIZE_RANGE': [[70, 150]],  # Short amplicons for qPCR
        'PRIMER_OPT_TM': 60.0,
        'PRIMER_MIN_TM': 58.0,
        'PRIMER_MAX_TM': 62.0,
        'PRIMER_INTERNAL_OPT_TM': 70.0,  # Probe Tm ~10C higher
        'PRIMER_INTERNAL_MIN_TM': 68.0,
        'PRIMER_INTERNAL_MAX_TM': 72.0,
        'PRIMER_INTERNAL_MIN_SIZE': 18,
        'PRIMER_INTERNAL_OPT_SIZE': 25,
        'PRIMER_INTERNAL_MAX_SIZE': 30,
        'PRIMER_DNA_CONC': 250.0,
        'PRIMER_INTERNAL_DNA_CONC': 200.0,
    }
)
```

### 1.2 提取探针结果

```python
num_returned = result['PRIMER_PAIR_NUM_RETURNED']
print(f'Found {num_returned} primer/probe sets')

for i in range(num_returned):
    left = result[f'PRIMER_LEFT_{i}_SEQUENCE']
    right = result[f'PRIMER_RIGHT_{i}_SEQUENCE']
    probe = result[f'PRIMER_INTERNAL_{i}_SEQUENCE']
    probe_tm = result[f'PRIMER_INTERNAL_{i}_TM']
    left_tm = result[f'PRIMER_LEFT_{i}_TM']
    right_tm = result[f'PRIMER_RIGHT_{i}_TM']
    product_size = result[f'PRIMER_PAIR_{i}_PRODUCT_SIZE']

    print(f'Set {i}:')
    print(f'  Forward: {left} (Tm: {left_tm:.1f}C)')
    print(f'  Reverse: {right} (Tm: {right_tm:.1f}C)')
    print(f'  Probe:   {probe} (Tm: {probe_tm:.1f}C)')
    print(f'  Product: {product_size}bp')
```

### 1.3 qPCR 优化参数（含 SEQUENCE_TARGET）

```python
result = primer3.design_primers(
    seq_args={
        'SEQUENCE_TEMPLATE': sequence,
        'SEQUENCE_TARGET': [100, 30],  # Target region for probe
    },
    global_args={
        'PRIMER_PICK_INTERNAL_OLIGO': 1,
        'PRIMER_PRODUCT_SIZE_RANGE': [[60, 100], [100, 150]],  # Prefer short
        'PRIMER_NUM_RETURN': 5,
        # Primer parameters
        'PRIMER_OPT_SIZE': 20,
        'PRIMER_MIN_SIZE': 18,
        'PRIMER_MAX_SIZE': 25,
        'PRIMER_OPT_TM': 60.0,
        'PRIMER_MIN_TM': 58.0,
        'PRIMER_MAX_TM': 62.0,
        'PRIMER_OPT_GC_PERCENT': 50.0,
        'PRIMER_MIN_GC': 35.0,
        'PRIMER_MAX_GC': 65.0,
        # Probe parameters (TaqMan: Tm 8-10C higher than primers)
        'PRIMER_INTERNAL_OPT_SIZE': 25,
        'PRIMER_INTERNAL_MIN_SIZE': 18,
        'PRIMER_INTERNAL_MAX_SIZE': 30,
        'PRIMER_INTERNAL_OPT_TM': 70.0,
        'PRIMER_INTERNAL_MIN_TM': 68.0,
        'PRIMER_INTERNAL_MAX_TM': 72.0,
        'PRIMER_INTERNAL_MIN_GC': 30.0,
        'PRIMER_INTERNAL_MAX_GC': 70.0,
        # Avoid G at 5' end of probe (quenches FAM)
        'PRIMER_INTERNAL_OLIGO_SELF_ANY': 8,
        'PRIMER_MAX_POLY_X': 3,
    }
)
```

### 1.4 SYBR Green 模式（无探针）

```python
result = primer3.design_primers(
    seq_args={'SEQUENCE_TEMPLATE': sequence},
    global_args={
        'PRIMER_PICK_LEFT_PRIMER': 1,
        'PRIMER_PICK_RIGHT_PRIMER': 1,
        'PRIMER_PICK_INTERNAL_OLIGO': 0,  # No probe
        'PRIMER_PRODUCT_SIZE_RANGE': [[70, 200]],
        'PRIMER_OPT_TM': 60.0,
        'PRIMER_MIN_TM': 58.0,
        'PRIMER_MAX_TM': 62.0,
        'PRIMER_MAX_SELF_ANY': 4,  # Strict for SYBR specificity
        'PRIMER_MAX_SELF_END': 2,
        'PRIMER_PAIR_MAX_COMPL_ANY': 4,
        'PRIMER_PAIR_MAX_COMPL_END': 2,
    }
)
```

### 1.5 多重引物设计（兼容 Tm）

```python
targets = [
    {'name': 'gene1', 'seq': sequence1, 'target': [100, 30]},
    {'name': 'gene2', 'seq': sequence2, 'target': [150, 30]},
]

results = []
for target in targets:
    result = primer3.design_primers(
        seq_args={
            'SEQUENCE_TEMPLATE': target['seq'],
            'SEQUENCE_ID': target['name'],
            'SEQUENCE_TARGET': target['target'],
        },
        global_args={
            'PRIMER_PICK_INTERNAL_OLIGO': 1,
            'PRIMER_PRODUCT_SIZE_RANGE': [[70, 150]],
            'PRIMER_OPT_TM': 60.0,  # Same Tm for all
            'PRIMER_MAX_TM': 61.0,
            'PRIMER_MIN_TM': 59.0,
            'PRIMER_INTERNAL_OPT_TM': 70.0,
        }
    )
    results.append(result)
```

---

## 2. 验证 API

### 2.1 核心热力学函数

所有返回 `ThermoResult` 命名元组的函数包含字段 `structure_found: bool, tm: float, dg: float, dh: float, ds: float`。`dg` 单位为 cal/mol（正值 = 反应不利）。

⚠️ 以下签名中列出的参数值为 **AgentD qPCR 条件推荐值**（非 primer3-py API 原始默认值）。所有 AgentD 调用必须显式传递这些参数，不得依赖 API 默认值。

| 函数 | 用途 | 返回值 |
|------|------|--------|
| `primer3.calc_tm(seq, mv_conc=50, dv_conc=3, dntp_conc=0.8, dna_conc=250, salt_corrections_method='santalucia')` | SantaLucia NN Tm | `float` (°C) |
| `primer3.calc_hairpin(seq, mv_conc=50, dv_conc=3, dntp_conc=0.8, dna_conc=250, temp_c=37, max_loop=30)` | 发卡结构 | `ThermoResult` |
| `primer3.calc_homodimer(seq, mv_conc=50, dv_conc=3, dntp_conc=0.8, dna_conc=250, temp_c=37, max_loop=30)` | 自二聚体 | `ThermoResult` |
| `primer3.calc_heterodimer(seq1, seq2, mv_conc=50, dv_conc=3, dntp_conc=0.8, dna_conc=250, temp_c=37, max_loop=30)` | 交叉二聚体 | `ThermoResult` |
| `primer3.calc_end_stability(seq1, seq2, mv_conc=50, dv_conc=3, dntp_conc=0.8, dna_conc=250, temp_c=37, max_loop=30)` | 3' 端结合稳定性 (seq1=引物, seq2=模板) | `ThermoResult` |
| `primer3.calc_hairpin_tm(seq)` | 发卡 Tm (轻量) | `float` (°C) |
| `primer3.calc_homodimer_tm(seq)` | 自二聚体 Tm (轻量) | `float` (°C) |
| `primer3.calc_heterodimer_tm(seq1, seq2)` | 交叉二聚体 Tm (轻量) | `float` (°C) |

### 2.2 Tm 校验（含盐浓度）

```python
primer_seq = 'ATGCGATCGATCGATCGATC'

tm = primer3.calc_tm(primer_seq)
print(f'Standard Tm: {tm:.1f}C')

# 匹配 qPCR master mix 条件
tm_adjusted = primer3.calc_tm(
    primer_seq,
    mv_conc=50.0,    # Monovalent cation (K+, Na+) mM
    dv_conc=3.0,     # Divalent cation (Mg2+) mM
    dntp_conc=0.8,   # dNTP mM (reduces free Mg2+)
    dna_conc=250.0,  # Primer concentration nM
)
print(f'Adjusted Tm: {tm_adjusted:.1f}C')
```

### 2.3 发卡结构

```python
primer = 'ATGCGATCGATCGATCGATC'

hairpin = primer3.calc_hairpin(primer, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0)
print(f'Hairpin Tm: {hairpin.tm:.1f}C')
print(f'Hairpin dG: {hairpin.dg:.1f} cal/mol')
print(f'Hairpin dH: {hairpin.dh:.1f} cal/mol')
print(f'Hairpin dS: {hairpin.ds:.1f} cal/mol/K')

annealing_temp = 60.0
if hairpin.tm > annealing_temp - 10:
    print(f'WARNING: Hairpin Tm too high for annealing at {annealing_temp}C')
```

### 2.4 自二聚体

```python
homodimer = primer3.calc_homodimer(primer, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0)
print(f'Homodimer Tm: {homodimer.tm:.1f}C')
print(f'Homodimer dG: {homodimer.dg:.1f} cal/mol')

if homodimer.tm > 40:
    print('WARNING: Significant self-dimer potential')
```

### 2.5 交叉二聚体

```python
forward = 'ATGCGATCGATCGATCGATC'
reverse = 'GCTAGCTAGCTAGCTAGCTA'

heterodimer = primer3.calc_heterodimer(forward, reverse, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0)
print(f'Heterodimer Tm: {heterodimer.tm:.1f}C')
print(f'Heterodimer dG: {heterodimer.dg:.1f} cal/mol')

if heterodimer.tm > 40:
    print('WARNING: Significant primer dimer potential between forward and reverse')
```

### 2.6 完整引物验证

> ⚠️ 以下代码为 P1 功能演示。实际生产中的硬性/软性规则阈值由 WF 层按 R-01~R-12 执行（dG 单位: cal/mol），不依赖本函数的 print 警告。

```python
def validate_primer(primer_seq, name='Primer', annealing_temp=60.0):
    '''Comprehensive primer validation (demo; 实际阈值由WF层执行)'''
    print(f'\n=== Validating {name}: {primer_seq} ===')

    tm = primer3.calc_tm(primer_seq, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0, )
    gc = (primer_seq.count('G') + primer_seq.count('C')) / len(primer_seq) * 100
    print(f'Length: {len(primer_seq)}bp, Tm: {tm:.1f}C, GC: {gc:.1f}%')

    hairpin = primer3.calc_hairpin(primer_seq, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0)
    print(f'Hairpin Tm: {hairpin.tm:.1f}C, dG: {hairpin.dg:.1f}')
    if hairpin.tm > annealing_temp - 10:
        print('  WARNING: Hairpin may interfere with annealing')

    homodimer = primer3.calc_homodimer(primer_seq, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0)
    print(f'Homodimer Tm: {homodimer.tm:.1f}C, dG: {homodimer.dg:.1f}')
    if homodimer.tm > 40:
        print('  WARNING: Self-dimer potential')

    end_3 = primer_seq[-5:]
    end_gc = (end_3.count('G') + end_3.count('C'))
    print(f"3' end (last 5bp): {end_3}, {end_gc} G/C bases")
    if end_gc > 2:
        print("  WARNING: 3' end may be too GC-rich (R-03: G+C ≤ 2 in last 5 bp)")
    if end_gc == 0:
        print("  WARNING: 3' end lacks GC clamp")

    for base in 'ATGC':
        for run_len in range(4, len(primer_seq)):
            if base * run_len in primer_seq:
                print(f'  WARNING: Contains {base}x{run_len} run (R-03: ≥4 consecutive G excluded)')
                break

    return {'tm': tm, 'gc': gc, 'hairpin_tm': hairpin.tm, 'homodimer_tm': homodimer.tm}
```

### 2.7 引物对验证

> ⚠️ 同 §2.6: 演示代码，实际阈值由 WF 层执行（R-09 3'端二聚体 dG<-6.0→FAIL, R-10 交叉二聚体 dG<-9.0→FAIL）。

```python
def validate_primer_pair(forward, reverse, annealing_temp=60.0):
    '''Validate a primer pair (demo; 实际阈值由WF层执行)'''
    print(f'\n=== Primer Pair Validation ===')
    print(f'Forward: {forward}')
    print(f'Reverse: {reverse}')

    fwd_tm = primer3.calc_tm(forward, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0, )
    rev_tm = primer3.calc_tm(reverse, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0, )
    print(f'Tm Forward: {fwd_tm:.1f}C, Reverse: {rev_tm:.1f}C')
    print(f'Tm Difference: {abs(fwd_tm - rev_tm):.1f}C')
    if abs(fwd_tm - rev_tm) > 2:
        print('  WARNING: Tm difference > 2C')

    heterodimer = primer3.calc_heterodimer(forward, reverse, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0)
    print(f'Heterodimer Tm: {heterodimer.tm:.1f}C, dG: {heterodimer.dg:.1f}')
    if heterodimer.tm > 40:
        print('  WARNING: Significant primer dimer potential')

    end_heterodimer = primer3.calc_heterodimer(forward[-6:], reverse[-6:])
    print(f"3' end heterodimer Tm: {end_heterodimer.tm:.1f}C")
    if end_heterodimer.tm > 20:
        print("  WARNING: 3' ends may form stable dimer")

    fwd_hairpin = primer3.calc_hairpin(forward)
    rev_hairpin = primer3.calc_hairpin(reverse)
    fwd_homodimer = primer3.calc_homodimer(forward)
    rev_homodimer = primer3.calc_homodimer(reverse)

    return {
        'fwd_tm': fwd_tm, 'rev_tm': rev_tm,
        'heterodimer_tm': heterodimer.tm,
        'fwd_hairpin_tm': fwd_hairpin.tm, 'rev_hairpin_tm': rev_hairpin.tm,
    }
```

### 2.8 3' 端稳定性

```python
primer = 'ATGCGATCGATCGATCGATC'
template = 'GCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTA'

end_stability = primer3.calc_end_stability(primer, template, mv_conc=50.0, dv_conc=3.0, dntp_conc=0.8, dna_conc=250.0)
print(f"3' end stability: dG = {end_stability.dg:.1f} cal/mol")
if end_stability.dg < -9000:
    print("  Note: Very stable 3' end - good extension but watch for mispriming")
```

### 2.9 快速批量筛选（Tm-Only）

```python
hairpin_tm = primer3.calc_hairpin_tm(primer)
homodimer_tm = primer3.calc_homodimer_tm(primer)

def quick_screen_primers(primer_list, max_hairpin_tm=45, max_homodimer_tm=45):
    '''Fast screening using Tm-only functions'''
    passed, failed = [], []
    for seq in primer_list:
        ht = primer3.calc_hairpin_tm(seq)
        hm = primer3.calc_homodimer_tm(seq)
        if ht < max_hairpin_tm and hm < max_homodimer_tm:
            passed.append(seq)
        else:
            failed.append((seq, ht, hm))
    return passed, failed
```

---

## 3. AgentD v12.3 设计参数默认值

| 参数 | 引物 | TaqMan 探针 | SYBR Green 引物 |
|------|------|-----------|-----------------|
| 产物长度 (bp) | 70–150 | — | 70–200 |
| 最佳长度 (nt) | 20 | 25 | 20 |
| 长度范围 (nt) | 18–28 | 18–30 | 18–28 |
| OPT_TM (°C) | 60.0 | 70.0 | 60.0 |
| TM 范围 (°C) | 58.0–62.0 `[设计目标]` (R-01 可接受范围 50–65 `[汪博改版]`) | 68.0–72.0 | 58.0–62.0 |
| GC% 范围 | 35–65 | 30–70 | 35–65 |
| PRIMER_MAX_POLY_X | 3 | 3 | 3 |
| PRIMER_INTERNAL_OLIGO_SELF_ANY | — | 8 | — |
| PRIMER_PAIR_MAX_COMPL_ANY | — | — | 4 |
| 盐浓度 | Na⁺ 50 mM, Mg²⁺ 3 mM, dNTP 0.8 mM | 同 | 同 |
| DNA 浓度 (nM) | 250 | 200 | 250 |

---

## 4. 验证阈值 (对应 v12.3 红线)

| 属性 | 阈值 | v12.3 规则 |
|------|------|:---:|
| 引物 Tm | 50–65°C (中心 58–60°C, <55°C 标记 WARNING) | R-01 `[汪博改版]` |
| ΔTm (正向/反向) | ≤ 2°C | R-01 |
| GC% | 35–65% (最优 40–60%) | — (R-01 关联参数) |
| 3' 端最后 5 bp 中 G/C 数 | ≤ 2（硬排除） | R-03 |
| 3' 端 G/C=0 / ≥3 连续相同碱基 | —（标记 WARNING） | R-03b |
| 扩增子长度 | 70–150 bp | R-02 |
| 探针长度 | 18–30 nt (MGB: 13–20 nt) | R-08 |
| 探针 Tm (非 MGB) | 68–72°C | R-07 |
| 探针 5' 端 ≠ G | 必须 (TaqMan) | R-05 |
| 自二聚体 ΔG | > −6.0 kcal/mol (涉及3'端) / > −9.0 (不涉及3'端) | R-09 / R-12 |
| 发卡 ΔG | > −3.0 kcal/mol | R-11 |

---

## 5. 使用范围

| 场景 | P1 适用性 | 说明 |
|------|:---:|------|
| 单靶标引物探针设计 (WF-01) | 适用 | 主入口 |
| 多重面板各靶标独立设计 (WF-02) | 适用 | 每个靶标独立调用 P1 |
| 管家基因内标引物设计 (WF-03, via P5) | 适用 | P5 内部调用 P1.design_primers；18S rRNA 时 DNA_CONC=50 |
| 已有引物热力学验证 (WF-04) | 适用 | 仅使用验证 API，不使用设计 API |
| 已有引物问题诊断 (WF-07) | 适用 | 完整热力学参数 + 优化方案重设计 |
| MGB 探针基础 Tm 计算 (P3) | 适用 | `calc_tm` 获取未修正 Tm |
| 交叉二聚体检查 (P4) | 适用 | `calc_heterodimer` |
| 简并碱基策略 (WF-01 并行策略) | 适用 | 拆分后具体序列分别调 P1.design_primers + calc_tm |

---

## 6. 已知局限

1. SantaLucia NN 模型不使用 MGB/LNA 等修饰碱基的热力学参数——`calc_tm` 返回值为未修饰寡核苷酸 Tm
2. `design_primers` 不直接支持包含 IUPAC 简并碱基的模板序列——需预处理将简并位点拆分为多条精确序列分别设计
3. `PRIMER_NUM_RETURN` 硬上限约为 1000 (Primer3 C 库限制)
4. NN 模型最大适用长度 60 bp；超过此长度的序列自动降级为 GC% 经验公式 (Bolton-McCarthy 1962)
5. `calc_heterodimer` 对序列内所有位置赋予相等权重——不自动区分 3' 端与内部互补。Workflow 层需自行提取 3' 端局部序列进行单独检查

## 7. 常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| `design_primers` 返回 0 套 | 模板 GC 极端或靶标区过短 | 触发 WF-01 Step3 松弛逻辑: 扩大 SIZE_RANGE, 放宽 TM 范围 |
| 计算 Tm 与实验 Tm 偏差 > 2°C | 未设置 qPCR 盐浓度条件 | 显式设置 `mv_conc=50, dv_conc=3, dntp_conc=0.8` |
| 探针 Tm < 68°C | `PRIMER_INTERNAL_OPT_TM` 未调整为 70°C | 检查 global_args 中的 INTERNAL_TM 参数 |
| 跨工具 Tm 不一致 | 不同软件使用不同 NN 参数版本 | P1 固定使用 SantaLucia 1998 (primer3 默认) |