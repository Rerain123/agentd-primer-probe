---
name: agentd-p5-internal-control
description: "通过 Agent D P5 设计并验证宿主 qPCR 内标。用于 ACTB、GAPDH、18S rRNA、管家基因、内标、内参、宿主内标或内标检测体系设计相关请求。"
---

# Agent D P5 宿主内标设计

通过 WF-03 或 WF-02 使用 P5。P5 维护猪、牛、鸡的默认管家基因与 RefSeq 编号，并从 NCBI Entrez 获取参考序列和同物种同源序列。

## 执行

1. 要求提供 `host_species`；内置优先级支持猪、牛和鸡。
2. 默认按 ACTB、GAPDH、18S rRNA 顺序调用 Entrez；用户提供的 `gene_sequences`、`gene_fasta_paths` 或目录资源作为显式序列覆盖。
3. 运行 `WF-03`（`POST /v1/wf03/internal-control`）。构建面板时传入已有靶标寡核苷酸，以执行交叉二聚体检查。
4. 报告所选基因、RefSeq 标识、保守性、候选检测体系、靶标交叉检查、特异性检查、P6 状态及警告。
5. Entrez 获取失败或没有达到 5 条同源序列时报告 `FAIL` 和来源证据。对 18S rRNA 保留 50–100 nM 限制性引物策略。

## 边界

- 序列保守性不能证明表达稳定；必须要求 geNorm/NormFinder 类实验确认。
- P4 报告通道冲突时，P5 不得自行确定最终多重荧光染料。
- 读取 [references/contract.md](references/contract.md)，确认数据来源与判定规则。
