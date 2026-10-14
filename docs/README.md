# Agent D 文档导航

> 本页是文档目录。根 [README](../README.md) 介绍产品，根 [AGENTS](../AGENTS.md) 路由智能体工作；业务与工程细节按下表逐层打开。

## 按内容分类

| 目录 | 收录内容 | 适合在什么时候读 |
|---|---|---|
| [architecture/](architecture/README.md) | 仓库分层、全局架构、工作流隔离 | 改跨模块设计或首次接手项目 |
| [workflows/](workflows/README.md) | WF-01～WF-05 现行实现与施工台账 | 修改或排查某一个工作流 |
| [operations/](operations/README.md) | MCP、鉴权、服务器、发布、存储与审计 | 接入、部署或排查运行环境 |
| [reference/](reference/README.md) | Skill 等稳定参考入口 | 查找工具与机器可读契约 |
| [source-materials/](source-materials/README.md) | 原始资料分发范围说明 | GitHub 不包含专家原稿或原稿归档 |
| [history/](history/README.md) | 变更记录、阶段历史和整理台账 | 回答“为什么会变成这样” |

## 按工作目的选路

| 目的 | 唯一当前入口 |
|---|---|
| 理解项目代码和请求主链路 | [工程交接文档](architecture/PROJECT_ENGINEERING_GUIDE.md) |
| 判断文件应该放在哪里 | [仓库结构与归属](architecture/REPOSITORY_LAYOUT.md) |
| 开发 WF-01～WF-05 | [工作流导航](workflows/README.md) |
| 部署或连接 MCP | [运维导航](operations/README.md) |
| 查 P1～P6 Skill | [Skill 目录](reference/SKILLS.md) |
| 查版本变化 | [变更记录](history/CHANGELOG.md) |

## 阅读优先级

1. 代码、Schema 与测试是可执行的当前事实。
2. `workflows/` 中的现行文档说明某个工作流的施工边界。
3. `architecture/` 和 `operations/` 解释跨模块与运行环境。
4. `source-materials/` 仅说明 GitHub 资料排除范围；`history/` 用于追溯项目历史。

新增文档前先确认不能扩展现有主题。新文档必须登记到最近的分类 `README.md`，并通过 `tests.test_repository_layout`。
