# WF-02 多重 qPCR 面板设计 — 测试用例

**目录**: `testdata/wf02_multiplex/`
**契约**: WF02-D-15（ACCEPTED）— 一个文件对应一个检测靶标；文件内多条同源序列
固定作为一个 `alignment_target` 进入 WF-01/MAFFT；禁止多文件绑同一靶标、同一文件
复用多个靶标、静默按 FASTA 记录拆靶标。

## 测试面板：猪呼吸道/消化道病原四重 qPCR

典型临床应用场景（同一份猪样本、同一反应管、同时检测 4 种病原）：

| 靶标文件 | 病原 | 检测基因 | 序列 | 用途 |
|---|---|---|---|---|
| `ASFV_B646L.fasta` | 非洲猪瘟病毒 | p72/B646L（分型金标准） | 3×300nt | 靶标 1 |
| `CSFV_5UTR.fasta` | 猪瘟病毒 | 5'UTR（保守检测区） | 5×300nt | 靶标 2 |
| `PRRSV2_ORF7.fasta` | 蓝耳病病毒 2 型 | ORF7/N 蛋白 | 3×300nt | 靶标 3 |
| `PRV_gB.fasta` | 伪狂犬病毒 | gB/UL27 | 3×300nt | 靶标 4 |

合计 14 条序列，全部为 300nt（符合输入长度要求），全部为 NCBI 真实序列。

## 数据来源与质检（2026-08-21 抓取）

每个靶标内的株均通过 **物种身份逐条确认 + 21-mer 同源复检**：

| 靶标 | 株列表 | 株间 21-mer 覆盖 |
|---|---|---|
| ASFV_B646L | OM324011.1(泰国), OM324010.1(泰国), PQ932065.1(斯里兰卡) | 95–100% |
| CSFV_5UTR | NC_002657.1(Brescia/HCLV/YN-WS/JJ18LOM-KYN02) | 见 alignment/README |
| PRRSV2_ORF7 | PV188152.1(Central), PV188150.1(North1), PV188151.1(North2) | 90–100% |
| PRV_gB | OR338846.1(MS), OP879621.1(GX), OP879617.1(GX) | 96–100% |

### 已剔除的错误记录（重要，避免踩坑）

- `OR885879.1` / `OR885877.1`：NCBI 标注 "Porcine reproductive and respiratory
  syndrome virus"（taxid 28344，未分型，疑似 PRRSV-1），与 PRRSV-2 ORF7 21-mer
  覆盖仅 3.1% —— **不同型别，不能放入 PRRSV2 靶标**
- `PQ511063.1`：标注 PRRSV-2 但 5' 端不一致（592nt 含非 ORF7 区段），与纯 ORF7
  覆盖 6–10%，已弃用
- `MW701412.1`：NCBI 检索噪声（实际是 Dothiorella sp. 真菌），绝非 ASFV
- `PX933426.1`：ASFV p72 但 2082nt 起点非 ATG（含 5' 侧翼），与标准 1941nt CDS 0% 对齐

## 使用方式（WF-02 输入契约）

按 N01–N03 契约提交 4 个靶标，每个靶标一个文件：

```json
{
  "host_species": "pig",
  "include_internal_control": true,
  "targets": [
    {"target_id": "ASFV",  "target_name": "African swine fever virus",  "gene": "B646L", "file": "ASFV_B646L.fasta"},
    {"target_id": "CSFV",  "target_name": "Classical swine fever virus", "gene": "5UTR",   "file": "CSFV_5UTR.fasta"},
    {"target_id": "PRRSV2","target_name": "PRRSV type 2",               "gene": "ORF7",   "file": "PRRSV2_ORF7.fasta"},
    {"target_id": "PRV",   "target_name": "Suid alphaherpesvirus 1",     "gene": "gB",     "file": "PRV_gB.fasta"}
  ]
}
```

预期行为：
- N03 识别为 4 靶标、4 文件一一映射（`file_count=1`/靶标，`one_file_per_target=true`）
- 每个靶标作为一个独立 WF-01 子任务，文件内多序列 → `alignment_target` → MAFFT 保守区 → Primer3
- 4 个靶标候选生成后进入 N15 笛卡尔积组合 → P4 荧光通道/交叉二聚体筛选

## 难度梯度

- **保守区设计（WF-01 子任务）**：CSFV 5'UTR 最保守（49% 位点完全保守），
  ASFV/PRV gB 次之，PRRSV2 ORF7 变异最大（3 株间 90% 覆盖）——可测保守区收窄
- **面板兼容性（WF-02 P4）**：4 靶标跨物种，天然存在交叉二聚体候选空间；扩增子
  长度差异设计上刻意不同（5'UTR/ORF7 较短，gB 区段较长），可测 R-17 的 20bp 规则

## 生成方式（可复现）

```python
esearch/efetch: 每靶标按物种+基因名检索完整 CDS，逐条 esummary 验物种，取 5' 端 300nt
复检: 21-mer 覆盖率 ≥90%（同源）; 字符集 ACGT; id 唯一
```