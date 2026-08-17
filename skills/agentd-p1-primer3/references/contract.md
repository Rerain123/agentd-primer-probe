# P1 运行契约

本地使用已安装的 CLI：

```powershell
.venv\Scripts\agentd.exe run --workflow WF-01 --input request.json --output result.json
.venv\Scripts\agentd.exe run --workflow WF-04 --input request.json --output result.json
```

服务器端使用 `POST /v1/wf01/design` 和 `POST /v1/wf04/validate`。所有结果必须符合 `3.0.0-phase3` Schema。

| 操作 | 必填输入 | 主要输出 |
|---|---|---|
| 新设计 | `target_id` 与 `sequence`/`sequences`/`fasta_path` | 含 P1 Tm 与寡核苷酸的 `candidates[]` |
| 已有体系验证 | `forward_primer`、`reverse_primer`；可选 `probe`、`template` | 一个带规则判定的已验证候选 |

P1 使用 `config/defaults.json` 中配置的 Primer3 SantaLucia 1998 条件：50 mM 单价盐、3 mM 二价盐、0.8 mM dNTP、250 nM 引物和 200 nM 探针。R-01 至 R-12 的判定归工作流所有；P1 不独立作出临床决策。
