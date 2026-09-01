# Agent D 智能体导航

> 这是入口页，不是项目百科全书。只在这里保留全局路由、必须遵守的边界和验证入口；细节按任务逐层打开。

## 开始工作

1. 先运行 `git status --short`，识别用户未提交的改动，不覆盖、不清理。
2. 需要产品背景时读 [README.md](README.md)；它面向人，不承载开发指令。
3. 打开 [文档导航](docs/README.md)，只选与当前任务相关的一条路线。
4. 用 `rg` 定位具体符号、Schema 和测试；不要预读整个仓库。
5. 只有在确认当前实现与验收边界后才开始修改。

## 按任务选下一页

| 任务 | 先读 | 再查 |
|---|---|---|
| 理解仓库分层 | [仓库结构](docs/architecture/REPOSITORY_LAYOUT.md) | [工程交接](docs/architecture/PROJECT_ENGINEERING_GUIDE.md) |
| 修改 WF-01～WF-05 | [工作流导航](docs/workflows/README.md) | 对应子目录、`skills/oligosmith-wf*/`、Schema 和测试 |
| 修改跨工作流边界 | [工作流隔离架构](docs/architecture/WORKFLOW_ISOLATION_ARCHITECTURE.md) | `src/agentd/workflow_isolation.py` 与快照完整性测试 |
| 修改 MCP、REST 或鉴权 | [MCP 服务](docs/operations/MCP_SERVER.md) | `src/agentd/mcp_server.py`、`api.py`、`service.py` |
| 部署、升级或回滚 | [运维导航](docs/operations/README.md) | `deploy/` 和部署测试 |
| 修改 P1～P6 工具 | [Skill 目录](docs/reference/SKILLS.md) | `skills/agentd-p*/`、工具实现与对应测试 |
| 追溯原始需求 | [原始设计输入](docs/source-materials/README.md) | 仅用于对照，不直接当作现行契约 |
| 追溯发布和阶段历史 | [历史导航](docs/history/README.md) | Git 历史 |

## 权威来源顺序

1. 可执行的代码、`schemas/` 与通过的测试共同定义当前行为。
2. `docs/workflows/` 记录单个工作流的现行路线和施工边界。
3. `docs/architecture/` 与 `docs/operations/` 解释跨模块结构和运行环境。
4. `docs/source-materials/` 是未改写的原始设计输入，可能早于当前代码。
5. `docs/history/` 和 Git 用于追溯，不得覆盖现行契约。

发现冲突时，不要猜测或复制第二套规则：用测试复现，确定代码实际行为，再同步唯一的现行文档。

## 仓库边界

| 路径 | 性质 |
|---|---|
| `src/agentd/` | 当前服务、共享边界和 WF-01 实现 |
| `src/agentd_workflow_snapshots/` | 绑定后的隔离工作流快照；不得随意回导或批量改写 |
| `schemas/` | 机器可验证的输入、状态与输出契约 |
| `skills/` | 模型可见的按需工具/工作流说明 |
| `tests/` / `testdata/` | 回归契约与可重复样例 |
| `config/` | 可版本化的默认配置；不放生产密钥 |
| `deploy/` | 容器、发布包和服务器脚本 |
| `dist/`、`output/`、`tmp/` | 本地生成物，不是源码或契约 |

## 必须保持的全局不变量

- 上层模型只做意图理解、字段收集和工具选择；状态推进、科学计算和报告由确定性后端完成。
- MCP、REST 和 CLI 应汇入同一 `AgentDService`，不得各自实现业务状态机。
- 工具、数据库或证据缺失时保持 `NOT_EVALUATED` / `NOT_CHECKED` / `PARTIAL`，不得伪造 `PASS`。
- 大文件和完整报告留在服务端，模型只传递不透明引用与紧凑摘要。
- 不在代码、文档、测试输出或发布包中写入密钥、令牌或生产 `.env`。
- 修改隔离快照前先读隔离架构并验证快照哈希与绑定关系。
- 本项目产出仅供科研设计参考；未经实验验证，不用于临床或生产决策。

## 修改闭环

1. 从最小的相关文档、入口函数和现有测试建立基线。
2. 优先添加或调整能暴露问题的窄测试，再做最小实现改动。
3. 字段变化同步 Schema、代码、测试和对应工作流文档。
4. 公开行为变化再更新产品 README；发布事实写入 `docs/history/CHANGELOG.md`。
5. 交付前检查变更范围，确认没有带入无关生成物或覆盖用户改动。

## 验证入口

```powershell
$env:PYTHONPATH = "src"
python -B -m unittest tests.test_repository_layout -v
python -B -m unittest discover -s tests -v
```

开发依赖已安装时可用 `pytest`。优先运行受影响的窄测试，然后再运行完整套件；工具缺失不能通过放宽科学结果来绕过。

## 文档放置规则

| 内容 | 位置 |
|---|---|
| 人类产品说明 | `README.md` |
| 智能体全局路由 | `AGENTS.md` |
| 跨模块架构和仓库规则 | `docs/architecture/` |
| 单个工作流现行实现/施工台账 | `docs/workflows/wfXX/` |
| 接入、部署、存储和运维 | `docs/operations/` |
| 稳定字段/工具参考 | `docs/reference/` 或机器可读 Schema |
| 原始设计输入 | `docs/source-materials/` |
| 变更与非现行记录 | `docs/history/` |

新建文档前，先扩展已有主题。如果确实需要新文档，必须把它登记到最近的 `README.md` 索引，并通过仓库结构测试。
