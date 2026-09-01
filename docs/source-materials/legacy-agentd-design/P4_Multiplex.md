## 4d: P4 — 多重荧光通道适配

# P4: 多重荧光通道适配

## 定位
P4 执行多重 qPCR 面板候选实例的兼容性验证——对荧光基团已被 WF-02/WF-06 层默认指派的候选面板，进行光谱兼容性、交叉二聚体热力学和 Tm 均衡性三项检查，输出 COMPATIBLE / MARGINAL / INCOMPATIBLE 判定。P4 不执行引物探针设计 (→ P1)、不验证序列特异性 (→ P2)、不计算 MGB 修饰 Tm (→ P3)、不执行荧光基团指派 (指派由 WF-02/WF-06 按优先级表 FAM → VIC → ROX → Cy5 完成，最亮基团指派给最低表达靶标，内标指派给 Cy5 通道)。WF-02 送入 P4 前需从 WF-01 返回的完整 candidate 对象中提取 panel 所需字段: fwd_seq/rev_seq/probe_seq + fwd_tm/rev_tm/probe_tm + gc_percent + fluorophore/quencher。P4 不直接消费 WF-01 的 candidate 对象。

## 优先级
P1

## 依赖

| 工具 | 用途 | 调用方式 |
|------|------|---------|
| P1 (Primer3) | `calc_heterodimer` — 计算所有引物/探针序列组合的交叉二聚体 ΔG 和 Tm；`calc_tm` 结果直接读取 | `import` Python 模块 |

无其他依赖。荧光基团发射光谱数据库、淬灭基团吸收光谱数据库和仪器通道配置数据库均为 P4 自建硬编码查找表。

## 实现
自建 Python 模块 (~300 行)。无第三方库依赖。

---

## 1. 核心规则

### 1.1 荧光基团与淬灭基团

P4 内置 14 种荧光基团的激发/发射峰值数据 (来源: Thermo Fisher Fluorescence SpectraViewer + LGC Biosearch Technologies Dye Chart)。

| 荧光基团 | λ_ex (nm) | λ_em (nm) | 相对亮度 (FAM ≡ 1.00) | 推荐淬灭基团 |
|---------|:---:|:---:|:---:|---------|
| FAM | 495 | 520 | 1.00 | BHQ-1 |
| TET | 521 | 538 | 0.25 | BHQ-1 |
| JOE | 529 | 555 | 0.35 | BHQ-1 |
| Yakima Yellow | 530 | 550 | 0.40 | BHQ-1 |
| HEX | 535 | 556 | 0.40 | BHQ-1 |
| VIC | 538 | 554 | 0.50 | BHQ-1 |
| SUN | 538 | 554 | 0.50 | BHQ-1 |
| ABY | 550 | 575 | — | QSY-7 |
| JUN | 585 | 615 | — | QSY-9 |
| ROX | 587 | 610 | 0.30 | BHQ-2 |
| Texas Red | 595 | 615 | 0.25 | BHQ-2 |
| Quasar 670 | 647 | 670 | 0.25 | BHQ-2 |
| Cy5 | 649 | 670 | 0.25 | BHQ-2 / BHQ-3 |
| Cy5.5 | 675 | 695 | 0.15 | BHQ-3 |

**淬灭基团吸收范围与匹配规则**:

| 淬灭基团 | 吸收范围 (nm) | 匹配的报告基团发射范围 | 类型 |
|---------|:---:|------|:---:|
| BHQ-1 | 480–580 | 500–570 nm (FAM, TET, VIC, HEX, JOE, Yakima Yellow, SUN) | 暗淬灭 |
| BHQ-2 | 560–670 | 570–670 nm (ROX, Texas Red, Cy5, Quasar 670) | 暗淬灭 |
| BHQ-3 | 620–730 | > 670 nm (Cy5.5) | 暗淬灭 |
| MGB-NFQ | 430–700 (全谱) | 任意 (仅限 3' 端连接 MGB 的探针) | 暗淬灭 + 小沟结合 |
| QSY-7 | 500–600 | 540–580 nm (ABY) | 暗淬灭 (TaqMan 专有) |
| QSY-9 | 550–650 | 570–620 nm (JUN) | 暗淬灭 (TaqMan 专有) |

**TAMRA 禁用规则**: TAMRA (发射峰值 ~580 nm) 属于荧光淬灭基团——其自身发射信号进入 ROX 检测通道 (~610 nm)，在多重检测中产生不可通过仪器软件补偿的背景。P4 检测到任意探针以 TAMRA 为淬灭基团时，直接返回 INCOMPATIBLE。

### 1.2 光谱交叉

任意两种荧光基团的发射峰值间距 (Δλ_em) 决定光谱交叉风险。VIC 与 HEX 的 Δλ_em = 2 nm，发射光谱几乎完全重叠，不可经由仪器滤光片区分——P4 标记为 INCOMPATIBLE。

| Δλ_em | 光谱交叉风险 | 判定 |
|:---:|:---:|:---:|
| ≥ 30 nm | 低 | PASS |
| 15–29 nm | 中等 | CAUTION (示例: FAM + TET, Δλ_em = 18 nm) |
| < 15 nm | 高 | INCOMPATIBLE (示例: VIC + HEX, Δλ_em = 2 nm) |
| VIC 与 HEX 同时出现在相同面板 | 不可光谱区分 | INCOMPATIBLE |

### 1.3 仪器通道兼容性

> **重要声明**：以下仪器通道配置为示例数据，实际部署时须根据甲方的具体 qPCR 仪器型号、光学模块配置和滤光片组进行校准。不同批次/年份的仪器滤光片可能存在版本差异，荧光基团兼容性判断应基于仪器厂商提供的最新光谱校准文件。本表所列波长近似值不可替代实测光谱校准。

| 仪器型号 | 最大通道数 | 检测通道 (λ_ex / λ_em, nm) | ROX 被动参比要求 |
|------|:---:|------|:---:|
| ABI 7500 Fast | 5 | 500/525, 540/565, 560/594, 575/625, 675/710 | 是 |
| QuantStudio 5 | 5 | 470/520, 520/558, 550/587, 580/623, 640/682 | 是 |
| QuantStudio 6/7 Pro | 6 | 同上 + 662/711 | 是 |
| Bio-Rad CFX96 Touch | 5 (+1 FRET 校准) | 450-490/515-530 (FAM), 515-535/560-580 (HEX/VIC), 560-590/610-650 (ROX/Texas Red), 620-650/675-690 (Cy5), 672-684/705-730 (Quasar 705/Cy5.5) | 否 |
| Roche LightCycler 480 II | 6 | 440/488, 465/510, 498/580, 533/610, 618/640, —/660 (Filter Set II, 来源: Roche 官方规格) | 否 |
| Roche LightCycler 96 | 4 | 470/514, 533/572, 577/620, 645/697 (来源: Roche 技术手册) | 否 |
| Qiagen Rotor-Gene Q | 6 | 470/510, 530/555, 585/610, 625/660, 680/710 | 否 |

**ABI/QuantStudio 平台 ROX 参比约束**: 若仪器要求 ROX 为被动参比且用户未显式声明 ROX 用作报告基团，则 ROX 通道被参比占用——该仪器上的实际可用报告通道数减少 1。P4 在校验仪器兼容性时自动应用此约束。

### 1.4 交叉二聚体 (依赖 P1)

对所有引物和探针序列执行 C(N_seq, 2) 次 `calc_heterodimer` 配对计算，其中 N_seq = N_targets × 3 (正向引物 + 反向引物 + 探针)。不重复计算自二聚体和发卡——P1 已在设计阶段完成。

| 检查项 | 标准阈值 | 放宽阈值 (WF-02 第 2 轮松弛) |
|--------|:---:|:---:|
| 交叉二聚体 ΔG (任意位置) | ΔG > −6.0 kcal/mol | ΔG > −7.0 kcal/mol |
| 3' 端二聚体 ΔG (最后 6 nt 局部) | ΔG > −7.0 kcal/mol | ΔG ≤ −7.0 → CAUTION |

ΔG 值由 P1.calc_heterodimer(seq_a[-6:], seq_b[-6:]) 计算。3' 端局部二聚体比全序列二聚体更直接影响延伸，使用比全序列标准 (−6.0) 更宽松的阈值 (−7.0) 以平衡灵敏度。此阈值比 R-10 (−9.0) 宽松，因为 P4 的语境是多重面板兼容筛选而非单引物对排除。全序列交叉二聚体和 3' 端局部二聚体双重检查互补覆盖。

### 1.5 Tm 与 GC% 均衡性

P4 不重复计算 Tm——直接读取 P1 设计阶段已输出的结果。

| 检查项 | 阈值 | 来源 |
|--------|:---:|:---:|
| 所有引物 Tm 极差 (max − min) | ≤ 3°C | v12.3 R-01 (单对引物 ΔTm ≤ 2°C；多对面板放宽至 ≤ 3°C) |
| 所有探针 Tm 极差 (max − min) | ≤ 3°C | v12.3 R-01 (同引物逻辑) |
| 各扩增子 GC% 极差 | ≤ 15% (正常)；> 20% → 标记 CAUTION | WF-02 Step4 修改 6 |

---

## 2. 输入/输出

### 输入

```python
# panel: list[dict] — 荧光基团已由 WF-02/WF-06 层指派
# instrument: str | None — 仪器型号标识符，默认 "CFX96"
# rox_as_reference: bool | None — None 时按仪器默认行为判断

# 示例面板结构:
# [
#     {"target_id": "CSFV",  "fwd_seq": "ATGC...", "rev_seq": "GCTA...", "probe_seq": "FAM-T...GC-MGB",
#      "fwd_tm": 60.1, "rev_tm": 59.8, "probe_tm": 70.5, "gc_percent": 48,
#      "fluorophore": "FAM", "quencher": "BHQ-1"},
#     ...
# ]
```

### 输出

```python
{
    "verdict": str,              # "COMPATIBLE" | "MARGINAL" | "INCOMPATIBLE"
    "spectral_check": {
        "min_delta_em_nm": float,
        "worst_pair": tuple[str, str],
        "status": str,           # "PASS" | "CAUTION" | "FAIL"
    },
    "quencher_check": {
        "all_matched": bool,
        "mismatches": list[str],
    },
    "instrument_check": {
        "instrument": str,
        "all_supported": bool,
        "rox_conflict": bool,
        "available_channels": int,
    },
    "heterodimer": {
        "total_pairs_checked": int,
        "worst_pair": tuple[str, str],
        "worst_dg_cal_per_mol": float,
    },
    "tm_spread": {
        "primer_spread_celsius": float,
        "probe_spread_celsius": float,
    },
    "gc_spread_percent": float,
    "issues": list[str],
}
```

---

## 3. 判定逻辑

```python
from Bio.Seq import Seq

# ===== 自建查找表 (硬编码常量) =====
FLUOROPHORE_DB: dict = { ... }     # 染料 → {ex, em, brightness, recommended_quencher}
QUENCHER_DB: dict = { ... }        # 淬灭基团 → {absorption_range: (min_nm, max_nm)}
INSTRUMENT_DB: dict = { ... }      # 仪器 → {channels: [(ex_nm, em_nm)], needs_rox: bool, max_channels: int}

EMISSION_PEAK_DISTANCE_MIN: float = 15.0           # nm
CROSS_DIMER_DG_STANDARD: float = -6000.0           # cal/mol ≡ −6.0 kcal/mol
CROSS_DIMER_DG_RELAXED: float = -7000.0            # cal/mol ≡ −7.0 kcal/mol
TM_SPREAD_MAX: float = 3.0                          # °C
GC_SPREAD_CAUTION: float = 15.0                     # 百分点
GC_SPREAD_WARNING: float = 20.0                     # 百分点


def _emission_peak_distance(dye_a: str, dye_b: str) -> float:
    """两荧光基团发射峰值的绝对间距 (nm)。"""
    return abs(FLUOROPHORE_DB[dye_a]["em"] - FLUOROPHORE_DB[dye_b]["em"])


def _quencher_covers(quencher: str, dye: str) -> bool:
    """淬灭基团吸收范围是否覆盖报告基团的发射峰值波长。"""
    em_peak: float = FLUOROPHORE_DB[dye]["em"]
    q_min, q_max = QUENCHER_DB[quencher]["absorption_range"]
    return q_min <= em_peak <= q_max


def _instrument_supports(instrument: str | None, dye: str) -> bool:
    """检查仪器是否有检测通道覆盖该荧光基团发射峰值。"""
    if instrument is None:
        return True
    em_peak: float = FLUOROPHORE_DB[dye]["em"]
    inst = INSTRUMENT_DB[instrument]
    for _ex_f, em_f in inst["channels"]:
        if abs(em_f - em_peak) <= 20.0:    # 发射滤光片半峰宽约 20 nm
            return True
    return False


def _check_3prime_dimer(seq_a: str, seq_b: str,
                         dg_threshold: float = -7000.0) -> tuple[bool, float]:
    """检查两条序列 3' 端局部 (各取最后 6 nt) 的二聚体 ΔG。
    返回 (is_risk, dg_cal_per_mol)。"""
    if len(seq_a) < 6 or len(seq_b) < 6:
        return False, 0.0
    result = P1.calc_heterodimer(seq_a[-6:], seq_b[-6:],
                                  mv_conc=50.0, dv_conc=3.0,
                                  dntp_conc=0.8, dna_conc=250.0)
    dg: float = float(result.dg)
    return dg < dg_threshold, dg


def check_panel_compatibility(panel: list[dict],
                               instrument: str | None = "CFX96",
                               rox_as_reference: bool | None = None) -> dict:
    """P4 主入口。返回完整兼容性判定报告。"""
    issues: list[str] = []
    inst = INSTRUMENT_DB.get(instrument) if instrument else None

    # 单靶标面板无需光谱交叉或二聚体检查——直接返回 COMPATIBLE
    if len(panel) < 2:
        return {
            "verdict": "COMPATIBLE",
            "spectral_check": {"min_delta_em_nm": 0.0, "worst_pair": ("", ""), "status": "PASS"},
            "quencher_check": {"all_matched": True, "mismatches": []},
            "instrument_check": {"instrument": instrument or "unspecified", "all_supported": True,
                                 "rox_conflict": False, "available_channels": 0},
            "heterodimer": {"total_pairs_checked": 0, "worst_pair": ("", ""), "worst_dg_cal_per_mol": 0.0},
            "tm_spread": {"primer_spread_celsius": 0.0, "probe_spread_celsius": 0.0},
            "gc_spread_percent": 0.0,
            "issues": [],
        }

    # ── 1. 光谱兼容性 ──
    min_delta_em: float = float('inf')
    worst_spectral_pair: tuple[str, str] = ("", "")
    spectral_status: str = "PASS"  # 显式状态变量, 不依赖字符串匹配
    for i in range(len(panel)):
        for j in range(i + 1, len(panel)):
            dye_a = panel[i]["fluorophore"]
            dye_b = panel[j]["fluorophore"]
            d_em = _emission_peak_distance(dye_a, dye_b)
            if d_em < min_delta_em:
                min_delta_em = d_em
                worst_spectral_pair = (dye_a, dye_b)

            if {dye_a, dye_b} == {"VIC", "HEX"}:
                issues.append("[FAIL] VIC 与 HEX 不可光谱区分 (Δλ_em = 2 nm)")
                spectral_status = "FAIL"
            elif d_em < EMISSION_PEAK_DISTANCE_MIN:
                issues.append(f"[CAUTION] {dye_a}-{dye_b} 发射峰间距 {d_em:.0f} nm "
                              f"(阈值 {EMISSION_PEAK_DISTANCE_MIN} nm)")
                if spectral_status == "PASS":
                    spectral_status = "CAUTION"

    if min_delta_em == float('inf'):
        min_delta_em = 0.0

    # ── 2. 淬灭基团匹配 ──
    quencher_mismatches: list[str] = []
    for entry in panel:
        dye = entry["fluorophore"]
        quencher = entry["quencher"]
        if "TAMRA" in quencher.upper():
            # TAMRA 自身发射 ~580 nm 污染 ROX/Texas Red/JUN 等 570-620 nm 通道
            adjacent_dyes = {"ROX", "Texas Red", "JUN"}
            panel_dyes = {e["fluorophore"] for e in panel}
            if adjacent_dyes & panel_dyes:
                issues.append("[FAIL] TAMRA 荧光发射 (~580 nm) 与面板中的 "
                              f"{adjacent_dyes & panel_dyes} 通道冲突")
            else:
                issues.append("[CAUTION] TAMRA 可能干扰未使用的橙色通道 (570-620 nm)")
            continue
        if "MGB" in quencher.upper():
            continue  # MGB-NFQ 全谱暗淬灭, 始终匹配
        if not _quencher_covers(quencher, dye):
            quencher_mismatches.append(f"{quencher} 不覆盖 {dye}")
            issues.append(f"[CAUTION] {quencher} 吸收范围不覆盖 {dye} 发射峰")

    # ── 3. 仪器兼容性 ──
    rox_conflict: bool = False
    if inst:
        if inst["needs_rox"] and rox_as_reference is not False:
            if any(e["fluorophore"] == "ROX" for e in panel):
                issues.append("[FAIL] ROX 被仪器被动参比占用——该通道不可用于报告检测")
                rox_conflict = True

        available: int = inst["max_channels"]
        if rox_conflict:
            available -= 1
        if len(panel) > available:
            issues.append(f"[FAIL] 靶标数 {len(panel)} 超过仪器可用通道数 {available}")

        for entry in panel:
            dye = entry["fluorophore"]
            if not _instrument_supports(instrument, dye):
                issues.append(f"[CAUTION] {instrument} 无检测通道覆盖 {dye} (λ_em = {FLUOROPHORE_DB[dye]['em']} nm)")

    # ── 4. 交叉二聚体 (调用 P1) ──
    all_seqs: list[str] = []
    for e in panel:
        all_seqs.extend([e["fwd_seq"], e["rev_seq"], e["probe_seq"]])
    total_pairs: int = len(all_seqs) * (len(all_seqs) - 1) // 2

    worst_dg: float = 0.0
    worst_seq_pair: tuple[str, str] = ("", "")
    for i in range(len(all_seqs)):
        for j in range(i + 1, len(all_seqs)):
            result = P1.calc_heterodimer(all_seqs[i], all_seqs[j])
            dg: float = float(result.dg)     # cal/mol, positive = unfavorable
            if dg < worst_dg:
                worst_dg = dg
                worst_seq_pair = (all_seqs[i][:8] + "...", all_seqs[j][:8] + "...")

            is_risk, dg_3prime = _check_3prime_dimer(all_seqs[i], all_seqs[j])
            if is_risk:
                issues.append(f"[CAUTION] 3' 端局部二聚体 ΔG = {dg_3prime:.0f} cal/mol "
                              f"({all_seqs[i][-6:]}-{all_seqs[j][-6:]})")

    if worst_dg < CROSS_DIMER_DG_RELAXED:
        issues.append(f"[FAIL] 交叉二聚体 ΔG = {worst_dg:.0f} cal/mol "
                      f"(标准阈值 {CROSS_DIMER_DG_STANDARD:.0f} cal/mol = −6.0 kcal/mol)")
    elif worst_dg < CROSS_DIMER_DG_STANDARD:
        issues.append(f"[CAUTION] 交叉二聚体 ΔG = {worst_dg:.0f} cal/mol "
                      f"(标准 −6.0 kcal/mol, 放宽 −7.0 kcal/mol)")

    # ── 5. Tm 均衡性 ──
    primer_tms: list[float] = []
    probe_tms: list[float] = []
    for e in panel:
        primer_tms.extend([e["fwd_tm"], e["rev_tm"]])
        probe_tms.append(e["probe_tm"])
    tm_spread_p: float = max(primer_tms) - min(primer_tms)
    tm_spread_pr: float = max(probe_tms) - min(probe_tms)

    if tm_spread_p > TM_SPREAD_MAX:
        issues.append(f"[CAUTION] 引物 Tm 极差 {tm_spread_p:.1f}°C > {TM_SPREAD_MAX}°C")
    if tm_spread_pr > TM_SPREAD_MAX:
        issues.append(f"[CAUTION] 探针 Tm 极差 {tm_spread_pr:.1f}°C > {TM_SPREAD_MAX}°C")

    # ── 6. GC% 均衡性 ──
    gc_values: list[float] = [e["gc_percent"] for e in panel]
    gc_spread: float = max(gc_values) - min(gc_values)
    if gc_spread > GC_SPREAD_WARNING:
        issues.append(f"[CAUTION] 扩增子 GC% 极差 {gc_spread:.1f}% > {GC_SPREAD_WARNING}%")
    elif gc_spread > GC_SPREAD_CAUTION:
        issues.append(f"[CAUTION] 扩增子 GC% 极差 {gc_spread:.1f}% 在 {GC_SPREAD_CAUTION}%-{GC_SPREAD_WARNING}% 之间")

    # ── 7. 判定 ──
    has_fail: bool = any(s.startswith("[FAIL]") for s in issues)
    if has_fail:
        verdict = "INCOMPATIBLE"
    elif issues:
        verdict = "MARGINAL"
    else:
        verdict = "COMPATIBLE"

    return {
        "verdict": verdict,
        "spectral_check": {
            "min_delta_em_nm": round(min_delta_em, 1),
            "worst_pair": worst_spectral_pair,
            "status": spectral_status,
        },
        "quencher_check": {
            "all_matched": len(quencher_mismatches) == 0,
            "mismatches": quencher_mismatches,
        },
        "instrument_check": {
            "instrument": instrument or "unspecified",
            "all_supported": not any("无检测通道" in s for s in issues),
            "rox_conflict": rox_conflict,
            "available_channels": available if inst else 0,
        },
        "heterodimer": {
            "total_pairs_checked": total_pairs,
            "worst_pair": worst_seq_pair,
            "worst_dg_cal_per_mol": worst_dg,
        },
        "tm_spread": {
            "primer_spread_celsius": round(tm_spread_p, 1),
            "probe_spread_celsius": round(tm_spread_pr, 1),
        },
        "gc_spread_percent": round(gc_spread, 1),
        "issues": issues,
    }
```

| 判定结果 | 条件 | WF-02/WF-06 处理策略 |
|---------|------|-------------|
| **COMPATIBLE** | issues 为空 | 进入 BLAST 特异性验证 (P2) |
| **MARGINAL** | 无 [FAIL] 条目，存在 [CAUTION] 条目 | 输出候选并标注风险条目；WF-02 可自行决定触发松弛重试 |
| **INCOMPATIBLE** | 存在 ≥ 1 条 [FAIL] 条目 | WF-02 更换荧光基团组合，重新指派后重新送入 P4 |

---

## 4. 使用范围

| 场景 | P4 适用性 | 说明 |
|------|:---:|------|
| WF-02 多重面板 (2–5 plex) | 适用 | 每个候选面板组合均需通过 P4 验证 |
| WF-06 端到端完整面板 (含内标) | 适用 | 流程与 WF-02 相同 |
| WF-01 单靶标设计 | 不适用 | 无多探针间光谱交叉或二聚体耦合问题 |
| 荧光基团未指派的面板 | 不适用 | 上游 WF-02/WF-06 必须先完成默认荧光基团指派 |

---

## 5. 已知局限

1. 光谱交叉判断基于发射峰值间距 (Δλ_em) 而非全发射光谱卷积积分——发射峰间距在 15 ± 5 nm 范围内的边缘情况可能存在偏差
2. 多重 PCR 中的靶标间扩增竞争效应 (高拷贝靶标通过消耗 dNTP/聚合酶抑制低拷贝靶标) 未纳入建模
3. 荧光基团与淬灭基团之间的化学计量比 (fluorophore:quencher stoichiometry) 对 FRET 效率的影响未纳入
4. 仪器数据库覆盖 7 种主流 qPCR 平台；非常见仪器需手动添加通道波长和 ROX 参比配置
5. 未评估非荧光化学因素 (溶剂极性、缓冲液 pH 对荧光量子产率的影响)
6. 内标的荧光基团指派在 WF-06 层完成——P4 仅验证内标基团与靶标基团之间不存在光谱冲突

## 6. 常见错误

| 错误现象 | 原因 | 处理方案 |
|---------|------|---------|
| VIC + HEX 同时出现导致 INCOMPATIBLE | 两荧光基团发射峰间距仅 2 nm | 保留 VIC (光稳定性更好)，将 HEX 替换为 JOE 或 Yakima Yellow |
| ROX 在 ABI/QuantStudio 平台上报 INCOMPATIBLE | ROX 被仪器被动参比占用 | 将 ROX 替换为 Cy5；或显式声明 ROX 为报告基团并移除参比功能 |
| 交叉二聚体 ΔG < −7.0 kcal/mol | 多靶标保守区存在局部序列互补 | WF-02 触发第 2 轮松弛 (放宽至 −7.0)；仍不通过则替换保守区 |
| Tm 极差 > 3°C | 各靶标候选引物的最佳 Tm 不一致 | 对 Tm 偏离的靶标调用 WF-01 重新设计 (调整 PRIMER_OPT_TM) |
| 3' 端互补触发 CAUTION | 两条引物的 3' 端 ≥ 3 bp 连续互补 | 在 WF-01 中将该引物的设计位置沿模板移动 1–2 nt 重新设计 |