# 原始设计输入

本目录保存项目开始时的 Agent D、P1～P6 和跨智能体 Skills 设计资料。它们是“原始输入”，不是当前代码的最终说明。

## 保存规则

- 原文不删除、不静默改写；迁移后的 SHA-256 记录在 [MANIFEST.sha256](MANIFEST.sha256)。
- 如需修正现行行为，修改代码、Schema、测试和 `docs/workflows/` 中的当前文档，不回写原稿。
- 原稿与当前实现冲突时，将差异记入变更或决策说明，不让两套文档并列为现行权威源。

## 资料组

`legacy-agentd-design/` 中包含：

- Agent D 规则与简并版设计；
- P1 Primer3、P2 BLAST、P3 MGB、P4 Multiplex、P5 Internal Control、P6 ViennaRNA 原始工具说明；
- v12.3 跨智能体 Skills 开发清单。

与原文同源的 ZIP 备份保留在工作区外层 `_archive/source-packages/`，不进入应用发布包。
