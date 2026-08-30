# Agent D 文档导航

> 整理日期：2026-08-30
> 原则：一个主题只有一个当前入口；历史通过 `CHANGELOG.md`、`HISTORY_PHASES.md` 或 Git 查询。

## 当前入口

| 目的 | 文档 | 定位 |
|---|---|---|
| 智能体开发交接 | [项目工程文档](PROJECT_ENGINEERING_GUIDE.md) | 当前架构、代码入口、工作流、MCP、测试和部署主链路 |
| 了解、开发或排查 WF-01 | [WF-01 当前实现与运行路线](WF01_CURRENT_IMPLEMENTATION.md) | WF-01 唯一现行文档 |
| 开发新的 WF-02 | [WF-02 v0.1 实施计划](WF02_V0.1_IMPLEMENTATION_PLAN.md) | 当前计划与节点台账 |
| 了解、开发或排查 WF-03 | [WF-03 当前实现](WF03_CURRENT_IMPLEMENTATION.md) | MCP、后端、P5边界、重入与报告契约 |
| 了解、开发或排查 WF-05 | [WF-05 当前实现](WF05_CURRENT_IMPLEMENTATION.md) | 隔离运行包、六节点、重试、MCP 与报告契约 |
| 部署和使用 MCP | [MCP 服务](MCP_SERVER.md) | 鉴权、工具、模型上下文与 UI 宿主边界 |
| 安装、升级和回滚 | [服务器安装](SERVER_INSTALL.md) | 服务器交付手册 |
| 配置附件对象存储 | [RustFS 附件发布](RUSTFS_ARTIFACT_STORAGE.md) | 对象存储、权限和完整性 |
| 查询智能体执行事实 | [智能体工具审计](AGENT_AUDIT_LOG.md) | 脱敏审计与排障 |
| 查询 P1～P6 Skill | [Skill 目录](SKILLS.md) | Skill 入口和语言规范 |
| 查询版本变化 | [统一变更记录](CHANGELOG.md) | 发布历史 |
| 查询早期阶段架构 | [阶段实现历史](HISTORY_PHASES.md) | 非现行历史资料 |

## 本次瘦身结果

- WF-01 的旧 M0 基线、验收目录、决策台账、M1～M7 阶段契约、旧 UI 契约、科学产物契约和契约化之前的历史，已经收口为 `WF01_CURRENT_IMPLEMENTATION.md`。
- 现行文档按当前代码、MCP、HTTP、Schema 和测试描述实际路线，包括 `alignment_target`、`independent_targets`、`DESIGN_ONLY`、`FULL_SPECIFICITY`、自动 M2～M6、可选 M7、前端消息、报告和科学证据。
- 32 份单版本发布说明已经合并为 `CHANGELOG.md`。
- `PHASE1.md`～`PHASE3.md` 已经合并为 `HISTORY_PHASES.md`。
- 旧输入、交互、弹窗和输出说明不再独立维护；字段级契约直接使用 `schemas/*.schema.json`。

## 维护规则

1. WF-01 行为变化同步修改代码、JSON Schema、测试和 `WF01_CURRENT_IMPLEMENTATION.md`，不再新增 M0～M7 或决策台账文档。
2. 不再创建 `RELEASE_x.y.z.md`；发布变化写入 `CHANGELOG.md`。
3. 历史实现只写入明确标记为历史的文档，不与当前入口并列。
4. WF-02 节点状态只维护在 `WF02_V0.1_IMPLEMENTATION_PLAN.md`。
5. WF-03 行为变化同步修改后端、MCP、Schema、测试和 `WF03_CURRENT_IMPLEMENTATION.md`；不得在 Workflow 或模型提示中复制 P1～P6 科学算法。
6. WF-05 行为变化同步修改独立快照、MCP、Schema、测试和 `WF05_CURRENT_IMPLEMENTATION.md`；不得从快照导回可变顶层实现。
7. 新增文档前先确认不能扩展现有主题；新增后必须登记到本页。
8. 删除或改名文档后必须检查仓库内残留引用和相对链接。
