## 4c: P3 — MGB探针Tm计算

# P3: MGB探针Tm修正计算

## 定位
P3 计算 TaqMan MGB 探针的修正熔解温度 (corrected melting temperature) 并执行 MGB 探针专用设计规则检查。Primer3 (P1) 的 SantaLucia 1998 统一近邻 (unified nearest-neighbor, NN) 热力学参数集不包含 MGB (CDPI₃) 修饰项——P1 返回未修饰寡核苷酸的 Tm，P3 在此基础上施加序列上下文依赖的 MGB 修正项并输出修正后 Tm。P3 同时检查 5 条硬性规则和 5 条软性规则，硬性规则违反时标记 FAIL。

## 优先级
P1

## 依赖

| 工具 | 用途 | 调用方式 |
|------|------|---------|
| P1 (Primer3) | `calc_tm` — 获取 SantaLucia NN 未修饰基础 Tm | `import` Python 模块 |

## 实现
自建 Python 模块（~150 行）。无第三方库依赖。修正公式基于 Primer Express v2.0 设计指南 + Kutyavin et al. (2000) 实验数据 + ResearchGate Thermo Fisher 用户社区共识 (2015) + BOC Sciences 商业白皮书。

---

## 1. 核心规则

### 1.1 硬性规则（违反标记 FAIL）

| 编号 | 规则 | 违反时的后果 |
|:---:|------|------|
| R-MGB-01 | 5' 端 ≠ G (使用 FAM 作为报告基团时，第 2 位也 ≠ G)。对应 SOP R-05，P3 附加强制 FAM 第 2 位约束 | G 通过接触介导的光诱导电子转移 (PET) 淬灭相邻 FAM 荧光基团——Taq 聚合酶裂解探针后荧光信号仍被抑制，导致假阴性 |
| R-MGB-02 | 13 ≤ 探针长度 ≤ 20 nt | < 13 nt: MGB 也无法补偿 Tm 至 65°C 目标；> 20 nt: MGB 稳定化优势被寡核苷酸长度冲淡，应改用标准 TaqMan 探针。与 v12.3 R-08 一致 |
| R-MGB-03 | 无连续 ≥ 4 个相同碱基 | GGGG: G-G 堆积或异常二级结构 (在 13–20 nt 短探针中单段 G₄ 不足形成稳定 G-四链体, 但可导致非特异性聚集); CCCC: 干扰杂交动力学; AAAA/TTTT: 合成效率降低 |
| R-MGB-04 | 3' 端最后 4 个碱基中 G 的数量 ≤ 2 | 3' 端 G 残基 (无论是否连续) 通过空间位阻和氢键竞争干扰 CDPI₃ 正确折叠插入小沟。总数约束因为非连续 G 也可能通过不同空间取向产生叠加干扰 |
| R-MGB-05 | GC% ∈ [30%, 80%] | 超出范围时 Primer Express 的 Tm 预测可靠性下降 |

### 1.2 软性规则（违反标记 CAUTION）

| 编号 | 规则 | 理由 |
|:---:|------|------|
| R-MGB-S1 | 选择 C > G 的链作为探针序列 | C-rich 链荧光信号更强；C < G 时推荐评估反义链 |
| R-MGB-S2 | 避免 ≥ 6 个连续 A | 合成效率下降，杂交特异性降低 |
| R-MGB-S3 | 探针中部区域避免 ≥ 2 个 CC 二核苷酸重复 | CC 重复可能诱导异常二级结构 |
| R-MGB-S4 | 发卡结构茎 ≤ 2 bp，环 ≥ 4 nt | 短探针发卡形成自由能低，可能竞争模板结合 |
| R-MGB-S5 | 若设计含 SNP 位点的探针，SNP 位于探针中央 1/3 区域内，且距 3' 端 > 2 nt | 中央位点的错配引起最大程度的小沟几何扭曲 |

---

## 2. 计算

### 2.1 获取基础 Tm (调用 P1)

```python
base_tm: float = P1.calc_tm(
    seq=probe_sequence,
    mv_conc=50.0,       # Na⁺ 50 mM (qPCR master mix)
    dv_conc=3.0,        # Mg²⁺ 3 mM
    dntp_conc=0.8,      # dNTP 0.8 mM
    dna_conc=200.0,     # 探针浓度 200 nM
    tm_method='santalucia',
    salt_corrections_method='santalucia',
)
```

P1 内部使用 SantaLucia 1998 NN 参数 + SantaLucia 1998 盐浓度校正模型 (salt_corrections_method='santalucia')。Mg²⁺ 通过 dv_conc 参数经 MVC 转换后纳入 NN 公式。同 P1 定位声明一致，依据 von Ahsen et al. (2011)。

### 2.2 MGB 修正

MGB 热力学贡献与 NN 碱基配对是不可加和的耦合过程 (non-additive and context-dependent)——CDPI₃ 在探针-靶标双链形成后折叠进入小沟。以下分段查表法是基于公开实验数据的工程近似 (偏差约 ±3°C)。商业模型 (Primer Express) 将 MGB 贡献作为专利算法不可分离的内部组成部分 (not separable from NN parameters)。

文献报道全范围 +15~+30°C（含极端 AT 富集序列；纯 polyT 8mer 可达 40–49°C，来源: Kutyavin et al. (1997) NAR 25:3718–3723；ResearchGate 2015 + BOC Sciences）。本工程实现取保守子集 +7~+22°C（偏差 ±3°C），牺牲极端序列的覆盖率以换取典型探针的预测稳定性。

```python
def calc_mgb_correction(probe_seq: str) -> float:
    """返回 MGB Tm 修正值 (°C)。工程近似，偏差 ±3°C。"""
    at_fraction: float = (probe_seq.count('A') + probe_seq.count('T')) / len(probe_seq)

    if at_fraction >= 0.70:
        delta_tm = 20.0      # AT 富集: CDPI₃ 结合最强
    elif at_fraction >= 0.55:
        delta_tm = 16.0      # 混合偏 AT
    elif at_fraction >= 0.45:
        delta_tm = 13.0      # 均衡
    elif at_fraction >= 0.30:
        delta_tm = 10.0      # 混合偏 GC
    else:
        delta_tm = 7.0       # GC 富集: CDPI₃ 空间位阻最大

    if len(probe_seq) < 15:
        delta_tm += 2.0      # 更短的探针对 MGB 稳定化依赖更高

    return delta_tm
```

### 2.3 链选择 (MGB 不对称效应)

MGB 共价连接于 3' 端——sense 链探针与 antisense 链探针即使序列完全互补，修正后 Tm 可差 2–4°C。Primer Express 对两条链独立计算 Tm。P3 实现相同逻辑：

```python
from Bio.Seq import Seq

def choose_probe_strand(fwd_primer: str, rev_primer: str,
                        target_region: str) -> tuple[str, str]:
    """选择 C > G 的链作为探针序列。返回 (probe_seq, strand)。

    用途: 初始探针设计时的链选择（非 R-05 补救）。
    R-05 补救应使用 remedy_probe_5prime_G()——仅取反义链，不改变探针长度和位置。

    前置条件: target_region 参数应包含正向引物结合区、候选探针区域和反向引物结合区的全部序列。
    探针区域自动限定在两条引物之间: target_region[len(fwd_primer):-len(rev_primer)]。
    调用方 (WF-01 Step4 初始设计) 需确保传入的 target_region 已涵盖完整区间。
    """
    probe_region_start = len(fwd_primer)
    probe_region_end = len(target_region) - len(rev_primer)
    if probe_region_end <= probe_region_start:
        raise ValueError("正向引物与反向引物之间的间距不足，无法放置探针")
    probe_sense = target_region[probe_region_start:probe_region_end]
    probe_antisense = str(Seq(probe_sense).reverse_complement())

    c_sense = probe_sense.count('C')
    g_sense = probe_sense.count('G')

    if c_sense > g_sense:
        return probe_sense, "sense"
    elif g_sense > c_sense:
        return probe_antisense, "antisense"
    else:
        tm_sense = P1.calc_tm(probe_sense)
        tm_anti = P1.calc_tm(probe_antisense)
        return (probe_sense, "sense") if tm_sense >= tm_anti else (probe_antisense, "antisense")
```

### 2.4 主入口

```python
def _has_consecutive_run(seq: str, n: int) -> bool:
    """检查序列中是否存在连续 n 个相同碱基。"""
    for base in 'ATGC':
        if base * n in seq:
            return True
    return False


def _gc_percent(seq: str) -> float:
    return (seq.count('G') + seq.count('C')) / len(seq) * 100


def _check_soft_rules(probe_seq: str) -> list[str]:
    """执行 MGB 软性规则检查，返回警告列表。"""
    warnings: list[str] = []
    if 'AAAAAA' in probe_seq:
        warnings.append("连续 ≥6 个 A (R-MGB-S2)")
    # R-MGB-S3: CC 二核苷酸重复检查限定在探针中部区域 (去掉首尾各 3 nt)
    if len(probe_seq) > 6:
        middle = probe_seq[3:-3]
        if middle.count('CC') >= 2:
            warnings.append("中部 CC 二核苷酸重复 (R-MGB-S3)")
    # R-MGB-S4 (发卡) 和 R-MGB-S5 (SNP 位点) 需额外参数，在主入口中处理
    return warnings


def calc_mgb_tm(probe_seq: str, na_conc: float = 50.0, mg_conc: float = 3.0,
                probe_conc: float = 200.0, fluorophore: str = 'FAM') -> dict:
    """P3 主入口。计算 MGB 探针修正 Tm 并执行全部规则检查。

    Args:
        fluorophore: 报告基团标识符。R-MGB-01 在 fluorophore='FAM' 时额外检查第 2 位 ≠ G。
    """
    base_tm: float = P1.calc_tm(
        seq=probe_seq, mv_conc=na_conc, dv_conc=mg_conc,
        dntp_conc=0.8, dna_conc=probe_conc,
        tm_method='santalucia', salt_corrections_method='santalucia',
    )
    correction: float = calc_mgb_correction(probe_seq)
    corrected_tm: float = base_tm + correction

    # 硬性规则
    r_mgb_01_pass = probe_seq[0] != 'G'
    if fluorophore.upper() == 'FAM' and len(probe_seq) > 1:
        r_mgb_01_pass = r_mgb_01_pass and probe_seq[1] != 'G'
    rules: dict[str, bool] = {
        "R-MGB-01": r_mgb_01_pass,
        "R-MGB-02": 13 <= len(probe_seq) <= 20,
        "R-MGB-03": not _has_consecutive_run(probe_seq, 4),
        "R-MGB-04": probe_seq[-4:].count('G') <= 2,
        "R-MGB-05": 30.0 <= _gc_percent(probe_seq) <= 80.0,
    }

    # 软性规则
    soft_warnings: list[str] = _check_soft_rules(probe_seq)

    # 判定
    if not all(rules.values()):
        verdict = "FAIL"
    elif soft_warnings:
        verdict = "CAUTION"
    else:
        verdict = "PASS"

    # 置信度: 中等 GC 含量的修正偏差较小; 极端 GC 可靠性显著下降
    at_fraction: float = (probe_seq.count('A') + probe_seq.count('T')) / len(probe_seq)
    if 0.35 <= at_fraction <= 0.65:
        confidence = "high"    # 偏差约 ±2°C
    elif 0.25 <= at_fraction <= 0.75:
        confidence = "medium"  # 偏差约 ±3°C
    else:
        confidence = "low"     # 偏差约 ±5°C+, 可靠性显著下降 (已知局限第 2 条)

    return {
        "base_tm": round(base_tm, 1),
        "at_fraction": round(at_fraction, 2),
        "mgb_correction": round(correction, 1),
        "corrected_tm": round(corrected_tm, 1),
        "confidence": confidence,
        "rules_check": rules,
        "all_hard_pass": all(rules.values()),  # WF-01 Step4/P5 消费: 硬性规则是否全部通过
        "warnings": soft_warnings,
        "verdict": verdict,
    }
```

---

## 3. Tm + 长度决策矩阵

| 基础 Tm (P1) | 修正后 Tm | 探针长度 | 判定 | 建议动作 |
|--------------|----------|:---:|:---:|------|
| 55–60°C | 65–72°C | 15–20 nt | PASS (理想) | 修正 Tm 达 8–10°C 高于引物 Tm，直接使用 |
| 50–55°C | 60–65°C | 13–15 nt | CAUTION (Tm 偏低) | 加长探针 2–3 nt 或选择 AT 含量更高的相邻区域 |
| 60–65°C | 72–78°C | ~20 nt | CAUTION (Tm 偏高) | 缩短探针或选择 GC 含量更低的相邻区域 |
| < 50°C | < 60°C | — | FAIL | 重新选择靶标保守区 |
| > 65°C | > 78°C | — | FAIL | 重新选择靶标保守区 |

修正后 Tm 目标区间: **65–72°C**，对应 SOP R-13 (Primer Express 推荐: 基因表达检测 68–70°C；等位基因分型 65–67°C。AgentD 在用户未指定检测类型时默认 65–72°C)。

> ⚠️ 上表"基础 Tm → 修正后 Tm"的映射基于混合 AT 含量 (0.40–0.55) 下的典型 MGB 修正值 (+5~+12°C)。对于 AT ≥ 0.55 的探针 (修正值 +13~+22°C)，基础 Tm 55–60°C 可能产生 68–82°C 的修正后 Tm，实际判定以 `calc_mgb_correction()` 的输出为准，不以上表的粗略分段为准。

---

## 4. 使用范围

| 场景 | P3 适用性 | 说明 |
|------|:---:|------|
| 探针长度 ≤ 20 nt 且考虑 MGB 修饰 (WF-01 Step4) | 适用 | 替代 P1 的未修饰探针 Tm |
| 探针长度 > 20 nt | 不适用 | 使用标准 TaqMan，直接使用 P1 的 Tm |
| 普通 FAM-TAMRA/BHQ 双标记探针 | 不适用 | 无 MGB 修饰，使用 P1 的 Tm |
| SYBR Green 检测 (无探针) | 不适用 | 不涉及探针设计 |

---

## 5. 已知局限

1. MGB 修正值为基于公开实验数据的工程近似 (±3°C)；Primer Express 专利算法精度约为 ±2°C，但完整公式未公开
2. 极端 GC 含量 (< 25% 或 > 75%) 的探针 CdPI₃ 结合行为偏离分段表假设，修正可靠性显著下降
3. 探针二级结构 (发卡形成) 对 MGB 结合效率的干扰未在修正项中建模——CDPI₃ 插入小沟需要可接近的双链构象
4. CDPI₃ 对 RNA·DNA 杂合链的稳定化效应弱于 DNA·DNA 双链——rPCR 场景下 MGB 效果降低，P3 不做区分
5. 所有计算 Tm 为设计参考值，不可替代经验退火温度梯度实验

## 6. 常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| 修正后 Tm < 65°C | 探针过短或 AT 含量过高 | 加长 2–3 nt 或迁移至 GC 含量更高的相邻区域 |
| 修正后 Tm > 72°C | 探针过长或 GC 含量过高 | 缩短序列或迁移至 AT 含量更高的相邻区域 |
| 5' 端为 G (R-MGB-01 FAIL) | 触发了硬性规则 | 移码 1–2 nt 或换链 (切换到 antisense) |
| 两条候选链 Tm 差异 > 4°C | MGB 不对称效应 | 属于正常现象——选择 Tm 落在目标区间内的链 |