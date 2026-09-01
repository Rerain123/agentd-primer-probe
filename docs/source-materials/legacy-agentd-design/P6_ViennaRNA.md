## 4f: P6 — ViennaRNA 扩增子二级结构评估

# P6: ViennaRNA 扩增子二级结构评估

## 定位
P6 使用 ViennaRNA Package (RNAfold) 计算扩增子区域 DNA 单链自折叠的最小自由能 (MFE ΔG)，检测可能阻碍 PCR 延伸的稳定二级结构。P6 封装 `ViennaRNA` Python 绑定 (`import RNA`)，加载 SantaLucia 2004 DNA 最近邻参数 (Mathews 2004)，在 PCR 退火温度 (60°C) 和 50 mM Na⁺ 条件下计算 MFE ΔG 及 5–10 个次优解。P6 是 R-补充 软性规则检查的可选工具——不可用时跳过不阻塞流程。调用方: WF-01 Step3 (主要)、WF-03/P5 (内标扩增子)、WF-04 Step5 (可选)、WF-07 Step1 (可选)。P6 持有的 DNA 参数选择、温度/盐浓度设定和 ΔG 阈值属于 P6 的领域知识，不归属 P1 或 C6。

## 优先级
P1（增强能力，不可用时降级为跳过）

## 依赖

| 工具 | 用途 | 调用方式 |
|------|------|---------|
| ViennaRNA (v2.5.0+) | RNAfold MFE 折叠 + 次优解 | `import RNA` Python 模块 |
| SantaLucia 2004 DNA 参数 | `dna_mathews2004.par` (内置于 ViennaRNA) | `RNA.params_load_DNA_Mathews2004()` |

## 实现
Python 封装模块（~60 行）。核心依赖 ViennaRNA Python 绑定。DNA 参数文件 `dna_mathews2004.par` 在 ViennaRNA v2.5.0+ 中已内置，通过 `RNA.params_load_DNA_Mathews2004()` 加载，无需额外下载 NNDB 参数文件。此函数修改全局状态 (`RNA.cvar`)，非线程安全——多线程/多次调用场景每次调用前必须重新执行。

## 仓库
- 核心库: https://github.com/ViennaRNA/ViennaRNA
- Python API 参考: https://www.tbi.univie.ac.at/RNA/ViennaRNA/refman/api_python.html

## 安装

```bash
# 推荐: conda 安装 (处理 C 库依赖，比 pip 更可靠)
conda install -c conda-forge -c bioconda viennarna

# 备选: pip 安装 (可能需预先安装 libRNA C 库)
pip install viennarna

# 验证
python -c "import RNA; print(RNA.fold('GGGCUAUUAGCUCAG'))"
```

---

## 1. 科学依据

**依据**: Binet T, Padiolleau-Lefèvre S, Octave S, Avalle B, Maffucci I. (2023) "Comparative Study of Single-stranded Oligonucleotides Secondary Structure Prediction Tools." *BMC Bioinformatics*, 24:422. DOI: 10.1186/s12859-023-05532-5. PMID: 37940855.

在 538 条 ssDNA/RNA 上对比 9 种工具，RNAfold 与 mfold 并列最优（精确预测率约 50%）。纳入 5–10 个次优解可显著提升准确率。

### 对 PCR 的影响机制

扩增子区域的稳定二级结构通过模板自折叠遮蔽引物结合位点，与引物-模板杂交形成竞争。模板折叠自由能越负，引物有效结合浓度越低，PCR 扩增效率随之降低。

### ΔG 阈值依据

70–150 bp 扩增子，平均每 bp 约 -0.3 kcal/mol。-30 kcal/mol 意味着约 100 bp 形成了稳定连续茎区，Taq 聚合酶无法穿过。

### 阈值绑定

R-补充中的 `ΔG < -30 kcal/mol → WARNING` 绑定到以下计算条件：

| 绑定项 | 值 |
|--------|-----|
| 工具 | ViennaRNA (RNAfold) |
| 能量参数 | SantaLucia 2004 DNA (Mathews 2004) |
| 温度 | 60°C (PCR 退火温度) |
| 离子条件 | 50 mM Na⁺ |
| 输出 | MFE ΔG (kcal/mol) |

⚠️ 切换到 UNAFold/mfold 或 NUPACK 时需重新标定阈值。

---

## 2. API

### 2.1 快速评估 — MFE ΔG

```python
import RNA


def assess_amplicon_secondary_structure(amplicon_seq: str) -> dict:
    """计算扩增子 DNA 单链自折叠 MFE ΔG。

    Args:
        amplicon_seq: 扩增子 DNA 序列 (5'→3', 含 T)

    Returns:
        {"mfe_dg": float, "mfe_structure": str, "status": str, "warnings": [str]}
    """
    # 加载 DNA 参数
    RNA.params_load_DNA_Mathews2004()

    # T → U
    rna_seq = amplicon_seq.replace('T', 'U').replace('t', 'u')

    # 设定 PCR 条件
    md = RNA.md()
    md.temperature = 60.0    # PCR 退火温度
    md.salt = 0.050          # 50 mM Na⁺
    md.uniq_ML = 1

    # 折叠
    fc = RNA.fold_compound(rna_seq, md)
    structure, mfe = fc.mfe()

    # 判定
    warnings = []
    if mfe < -30.0:
        warnings.append(
            f"扩增区域存在稳定二级结构 ΔG={mfe:.1f} kcal/mol (< -30), "
            "可能阻碍Taq聚合酶延伸"
        )
        status = "WARNING"
    elif mfe < -20.0:
        warnings.append(
            f"扩增区域存在中等二级结构 ΔG={mfe:.1f} kcal/mol "
            "(-30 ~ -20), 建议关注"
        )
        status = "CAUTION"
    else:
        status = "PASS"

    return {
        "mfe_dg": round(mfe, 1),
        "mfe_structure": structure,
        "temperature": 60.0,
        "salt_na": 50.0,
        "parameter_set": "SantaLucia2004_DNA",
        "status": status,
        "warnings": warnings,
    }
```

### 2.2 完整评估 — MFE ΔG + 次优解

次优解纳入可提升准确率 (Binet et al. 2023)。使用 `fc.subopt_cb()` 而非 `fc.subopt()`——后者 SWIG 转 C `FILE*` 有兼容性风险。

```python
def assess_amplicon_full(amplicon_seq: str, num_subopt: int = 10,
                          subopt_delta_kcal: float = 5.0) -> dict:
    """计算扩增子 MFE ΔG + 次优解。

    Args:
        amplicon_seq: 扩增子 DNA 序列
        num_subopt: 次优解最大数量 (默认 10)
        subopt_delta_kcal: 能量窗口 (kcal/mol, 默认 5.0)

    Returns:
        {"mfe_dg": float, "most_stable_dg": float,
         "subopt_structures": [{"structure": str, "dg": float}], ...}
    """
    RNA.params_load_DNA_Mathews2004()

    rna_seq = amplicon_seq.replace('T', 'U').replace('t', 'u')

    md = RNA.md()
    md.temperature = 60.0
    md.salt = 0.050
    md.uniq_ML = 1  # subopt 必须

    fc = RNA.fold_compound(rna_seq, md)

    # MFE
    mfe_structure, mfe = fc.mfe()

    # 次优解 — subopt_cb 是官方推荐 Python API
    # delta 单位: 10-cal/mol (deka-cal), 5.0 kcal → 500
    delta_dekacal = int(subopt_delta_kcal * 100)
    subopt_list = []

    def collect(structure, energy, data):
        if structure is not None:
            data.append({"structure": structure, "dg": round(energy, 1)})

    fc.subopt_cb(delta=delta_dekacal, cb=collect, data=subopt_list)
    subopt_structures = subopt_list[:num_subopt]

    # 最稳定值 (MFE 与次优解取最小)
    all_dg = [mfe] + [s["dg"] for s in subopt_structures]
    most_stable_dg = min(all_dg)

    # 判定
    warnings = []
    if most_stable_dg < -30.0:
        warnings.append(
            f"最稳定结构 ΔG={most_stable_dg:.1f} kcal/mol (< -30), "
            "可能阻碍Taq聚合酶延伸"
        )
        status = "WARNING"
    elif most_stable_dg < -20.0:
        warnings.append(
            f"存在中等稳定性结构 ΔG={most_stable_dg:.1f} kcal/mol "
            "(-30 ~ -20), 建议关注"
        )
        status = "CAUTION"
    else:
        status = "PASS"

    return {
        "mfe_dg": round(mfe, 1),
        "mfe_structure": mfe_structure,
        "most_stable_dg": round(most_stable_dg, 1),
        "subopt_structures": subopt_structures,
        "ensemble_diversity": f"{len(set(s['structure'] for s in subopt_structures))}/{len(subopt_structures)}",
        "temperature": 60.0,
        "salt_na": 50.0,
        "parameter_set": "SantaLucia2004_DNA",
        "confidence": "medium",  # ~50% 精确率 (Binet et al. 2023)
        "status": status,
        "warnings": warnings,
    }
```

---

## 3. 核心参数说明

### 3.1 DNA 参数

| 函数 | 说明 |
|------|------|
| `RNA.params_load_DNA_Mathews2004()` | 加载 SantaLucia 2004 DNA NN 参数。内置，无需额外文件。修改全局状态 |
| `RNA.params_load_RNA_Turner2004()` | 出厂默认 RNA 参数。**不适用于 DNA** |

### 3.2 model_details 关键字段

| 属性 | 值 | 说明 |
|------|-----|------|
| `md.temperature` | 60.0 | PCR 退火温度 (°C)，默认 37.0 不适用 |
| `md.salt` | 0.050 | 50 mM Na⁺ (M)。ViennaRNA 内部根据此值进行盐浓度校正 |
| `md.uniq_ML` | 1 | 唯一多分支环分解。MFE 和 subopt 必须 |

### 3.3 Mg²⁺

ViennaRNA `md.salt` 只接受单价阳离子浓度。qPCR 体系中的 Mg²⁺ (通常 3 mM) 不参与计算。当前保守使用 50 mM Na⁺ 单独近似——Mg²⁺ 的稳定化效应未计入，因此计算的 ΔG 较实际可能偏正（偏安全）。

### 3.4 subopt 能量单位

`fc.subopt_cb(delta, cb, data)` 的 delta 单位是 **10-cal/mol (deka-cal)**。`delta=500` = 5.0 kcal/mol。

---

## 4. 判定逻辑

| MFE ΔG (60°C, 50mM Na⁺) | 判定 | 动作 |
|--------|:---:|------|
| ΔG ≥ -20 kcal/mol | PASS | 继续 |
| -30 ≤ ΔG < -20 | CAUTION | 标记，不排除 |
| ΔG < -30 | WARNING | Step3 标记，Step6 降权 -3% |
| ViennaRNA 不可用 | 跳过 | 标注 "二级结构未评估" |

---

## 5. 输入/输出

### 5.1 快速评估

**输入**:
```
amplicon_seq: str    # 扩增子 DNA 序列 (5'→3', 含 T)
```

**输出**:
```python
{
    "mfe_dg": float,           # MFE ΔG (kcal/mol)
    "mfe_structure": str,      # 点-括号二级结构
    "temperature": 60.0,       # 计算温度 (°C)
    "salt_na": 50.0,           # Na⁺ 浓度 (mM)
    "parameter_set": str,      # 能量参数集标识 "SantaLucia2004_DNA"
    "status": str,             # "PASS" | "CAUTION" | "WARNING"
    "warnings": [str],         # 警告信息列表
}
```

### 5.2 完整评估

**输入**:
```
amplicon_seq: str         # 扩增子 DNA 序列 (5'→3', 含 T)
num_subopt: int = 10      # 次优解最大数量
subopt_delta_kcal: float = 5.0  # 能量窗口 (kcal/mol)
```

**输出**: 包含快速评估全部字段，额外增加：
```python
{
    # ... 快速评估全部字段 ...
    "most_stable_dg": float,        # 最稳定 ΔG (MFE 与次优解的最小值)
    "subopt_structures": [          # 次优解列表
        {"structure": str, "dg": float},
    ],
    "ensemble_diversity": str,      # 结构多样性 "unique/total"
    "confidence": str,              # 预测置信度 "medium"
}
```

---

## 6. 使用范围

| 场景 | 适用性 | 说明 |
|------|:---:|------|
| WF-01 Step3 扩增子二级结构检查 (R-补充) | ✅ 主要调用方 | 对每个候选扩增子调用 `assess_amplicon_secondary_structure()`，结果存入 `candidate.amplicon_structure` |
| WF-02 多重面板 | ✅ 间接 | 通过 WF-01 Step1-5 调用链继承，每个靶标独立检查 |
| WF-03/P5 内标引物 | ✅ P5内部调用 | P5 编排链含 P6：对内标候选扩增子逐一检查，结果存入 `internal_control.candidates[].amplicon_structure` |
| WF-04 已有引物验证 | ⚡ 可选 | Step5：模板序列如提供 + P6 可用 → 执行 R-补充 检查 |
| WF-06 端到端面板 | ✅ 间接 | 通过 WF-01 和 WF-03 调用链继承 |
| WF-07 问题诊断 | ⚡ 可选 | Step1：模板序列如提供 + P6 可用 → 执行 R-补充 检查 |
| 长度 > 500 nt | ❌ 不适用 | NN 模型精度下降，计算 O(n³) |
| 多链杂交竞争分析 | ❌ 不适用 | 需 NUPACK |
| ViennaRNA 未安装 | ❌ 降级跳过 | P0b 标注 "二级结构未评估" |

---

## 7. 已知局限

1. ssDNA 二级结构预测精确率仅约 50% (Binet et al. 2023)
2. SantaLucia 2004 参数为双链热力学参数——单链自折叠是工程近似
3. T→U 替换忽略胸腺嘧啶 5-甲基贡献，ΔG 影响 < 0.5 kcal/mol
4. Mg²⁺ 不直接参与计算，稳定性可能被低估（偏安全方向）
5. 复杂基序（多分支环、微型哑铃）表现差
6. 计算结果为设计参考值，不可替代实验优化
7. PCR 体系中引物/聚合酶竞争效应未建模

---

## 8. 常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| `import RNA` 失败 | ViennaRNA 未安装 | `conda install -c conda-forge -c bioconda viennarna` |
| ΔG 偏正 (> 0) | 未加载 DNA 参数，默认使用了 RNA Turner 参数 | 调用前执行 `RNA.params_load_DNA_Mathews2004()` |
| 温度/盐设置无效 | md 构造后未传入 fold_compound | 使用 `RNA.fold_compound(seq, md)` 构造器传入 md |
| 次优解数量为零 | delta 参数误传 kcal 值，实际单位为 10-cal/mol | 传入 `int(kcal * 100)`，例如 5 kcal → 500 |
| 多线程结果不一致 | `params_load_DNA_Mathews2004()` 修改全局 `RNA.cvar` 状态 | 每个线程每次调用前重新执行 `params_load_DNA_Mathews2004()` |

---

## 9. 部署分级

| 级别 | 条件 | 行为 |
|:---:|------|------|
| P1（默认） | ViennaRNA v2.5.0+ 已安装 | 60°C/50mM Na⁺ 计算 MFE ΔG + 次优解 |
| P1（备用） | UNAFold/mfold 可用 | 同功能，标注 "阈值需重新校准" |
| P2（增强） | NUPACK 4 可用 | 多链竞争分析（非当前需求） |
| P0b（最小闭环） | 无可用的折叠工具 | 跳过，标注 "二级结构未评估" |

---

## 10. 环境验证

```python
def check_viennarna_availability() -> dict:
    """AgentD 启动时调用。"""
    try:
        import RNA
        result = RNA.params_load_DNA_Mathews2004()
        dna_ok = bool(result)
        return {
            "available": True,
            "dna_params_available": dna_ok,
            "capability": "P1_DEFAULT" if dna_ok else "P0b_DEGRADED",
        }
    except ImportError:
        return {
            "available": False,
            "dna_params_available": False,
            "capability": "P0b_SKIP",
        }
```
