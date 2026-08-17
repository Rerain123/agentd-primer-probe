# OligoSmith Agent D 0.3.15

发布日期：2026-08-07

## 本次更新

- 固定 M6 完成结果的 MCP `structuredContent` 为 `PrimerDesignOutputData`：顶层仅包含
  `data` 和 `type`，并按 `data`、`type` 顺序构造。
- Agent D 不生成 `block_data` 或 `blockId`；这两个字段继续由 HiMat 宿主在消息存储层添加。
- 新增严格回归测试，验证完成结果没有 `ui_message`、`block_data` 或 `blockId` 等额外包装。
- 包含 0.3.14 的同名附件修复：`/data/<文件名>` 在重复任务中提供最近实际生成的文件，供
  HiMat 接管并转存至对象存储。

## 数据与升级

- 继续使用原有 `agentd-jobs` Docker 持久化卷，不需要迁移历史批次或报告文件。

## 验证

- Python 自动化测试：170 项通过，另有 9 个子测试通过。
- 部署包内置 `PACKAGE-MANIFEST.sha256`，并提供独立 ZIP SHA-256 校验文件。
