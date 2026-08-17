---
name: agentd-p5-internal-control
description: "通过 Agent D P5 设计并验证宿主 qPCR 内标。用于 ACTB、GAPDH、18S rRNA、管家基因、内标、内参、宿主内标或内标检测体系设计相关请求。"
---

# Agent D P5 宿主内标设计

通过 WF-03、WF-02 或 WF-06 使用 P5。P5 从用户提供或服务器目录中的序列选择宿主管家基因检测体系；生产运行中不得从公共互联网获取未版本化序列。

## 执行

1. 要求提供 `host_species`；内置优先级支持猪、牛和鸡。
2. 通过 `gene_sequences`、`gene_fasta_paths` 或目录资源 `internal:<species>:<gene>:sequences` 提供至少 5 条同源序列。
3. 运行 `WF-03`（`POST /v1/wf03/internal-control`）。构建面板时传入已有靶标寡核苷酸，以执行交叉二聚体检查。
4. 报告所选基因、RefSeq 标识、保守性、候选检测体系、靶标交叉检查、特异性检查、P6 状态及警告。
5. 把 `NOT_CONFIGURED` 视为集成缺口，不得视为内标设计成功。对 18S rRNA 保留 50–100 nM 限制性引物策略。

## 边界

- 序列保守性不能证明表达稳定；必须要求 geNorm/NormFinder 类实验确认。
- P4 报告通道冲突时，P5 不得自行确定最终多重荧光染料。
- 读取 [references/contract.md](references/contract.md)，确认数据来源与判定规则。
