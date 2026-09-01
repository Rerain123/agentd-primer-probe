# 2026-09-01 仓库重组台账

## 目标

- 根 `AGENTS.md` 作为约 100 行的智能体目录页，通过链接渐进披露。
- 根 `README.md` 只承担面向人的产品说明。
- 明确区分全局架构、单工作流施工、运维、参考、原始输入和历史。
- 不删除原始设计资料和用户未提交改动。

## 迁移对照

| 旧位置 | 新位置 | 类别 |
|---|---|---|
| `docs/PROJECT_ENGINEERING_GUIDE.md` | `docs/architecture/PROJECT_ENGINEERING_GUIDE.md` | 全局架构/交接 |
| `docs/WORKFLOW_ISOLATION_ARCHITECTURE.md` | `docs/architecture/WORKFLOW_ISOLATION_ARCHITECTURE.md` | 全局架构 |
| `docs/WF01_*`、`WF02_*`、`WF03_*`、`WF05_*` | `docs/workflows/wfXX/` | 单工作流施工 |
| `docs/MCP_SERVER.md` 等五份接入/部署文档 | `docs/operations/` | 运维与接入 |
| `docs/SKILLS.md` | `docs/reference/SKILLS.md` | 稳定参考 |
| `docs/CHANGELOG.md`、`HISTORY_PHASES.md` | `docs/history/` | 历史 |
| 外层 `AgentD_引物探针智能体_工具+工作流/` 中 8 份原稿 | `docs/source-materials/legacy-agentd-design/` | 原始设计输入 |
| 外层同名 ZIP | `_archive/source-packages/` | 原始包备份 |
| 仓库根零字节未分类文件 `({client` | `_archive/2026-09-01_repository-reorganization/unclassified/` | 隔离确认后清理 |

## 完整性

原始设计稿迁移前后哈希一致，详见 `docs/source-materials/MANIFEST.sha256`。`PROJECT_ENGINEERING_GUIDE.md` 在整理前已有用户未提交的生产部署补充；该补充内容原样保留，本次只另外修复了文件迁移后的导航路径。

`.venv/`、`.idea/`、`.playwright-cli/`、`dist/`、`output/`、`tmp/` 等本地生成物由 `.gitignore` 隔离；它们的性质已在仓库结构文档中明确，不得与项目源文件混淆。

## 后续清理

经当前架构复核并获得用户授权后，删除以下不再承担现行职责的内容：

- 重组前根 README 与旧文档索引快照；旧内容继续由 Git 历史保存；
- 零字节未分类文件及其临时隔离目录；
- 2026-08-19 旧目录盘点、归档临时文件、旧 QA 预览和未被当前项目引用的 `primer3web-4.1.0` 源码树；
- 本地 `.playwright-cli/`、`output/` 验证产物；
- `dist/` 中 1.2.11 及更早发布物，以及临时的 `wf04-hotfix-20260828` 版 1.2.12；仅保留正式的 1.2.12 回滚包和当前 1.2.13。

原始设计资料、同源 ZIP 备份、绑定运行时快照、退休工作流实现、项目说明和历史任务 JSON 均未删除。
