# OligoSmith Agent D：引物与探针设计智能体

Agent D 是温氏 AI4S 项目中的引物与探针设计服务。它把序列预检、Primer3 设计、特异性检查、多重兼容、内标设计、变异耐受性分析和报告交付收口到可追溯的确定性工作流中，并通过 MCP、HTTP API 和 CLI 提供统一能力。

> 本项目产出仅供科研设计参考。未经实验验证，不得直接用于临床或生产决策。

## 产品能力

| 工作流 | 用途 | 当前状态 |
|---|---|---|
| WF-01 | 普通 TaqMan 引物探针批次设计 | M1～M7 稳定实现 |
| WF-02 | 多重 qPCR 面板设计 | N01～N32 持久化后台执行 |
| WF-03 | 内标引物设计 | 独立后端、可重试、可交付报告 |
| WF-04 | 已有引物/探针验证 | 确定性六节点后端 |
| WF-05 | 变异耐受性分析 | 隔离运行包、不可变重试与终态报告 |

WF-06 和 WF-07 的第二阶段任务已结束，当前不提供公开运行时入口。

底层工具包括 P1 Primer3、P2 BLAST、P3 MGB、P4 多重兼容、P5 内标编排和 P6 ViennaRNA。BLAST+ 与 MAFFT 由部署环境提供，数据库通过服务器资源目录注入。

## 工作方式

Agent D 将“对话理解”和“科学执行”分开：

1. 上层智能体理解用户目的，收集必需字段并选择工具。
2. 后端对输入做结构化预检，将需要确认的问题显式返回。
3. 确定性工作流推进状态、调用科学工具、校验结果并保存证据。
4. 终态任务生成紧凑摘要、HTML 报告和可下载附件。

工具、数据库或证据不完整时，结果会保留 `NOT_EVALUATED`、`NOT_CHECKED` 或 `PARTIAL`，不会把未执行的科学检查冒充为 `PASS`。

## 输入与交付

常见输入包括 FASTA 文件或直接粘贴的序列、靶标生物、可选宿主、仪器/荧光通道以及特定工作流参数。大型文件与完整证据保留在服务端，对话中仅传递不透明引用和紧凑状态。

交付通常包含：

- 结构化任务状态与候选结果；
- 科学检查是否实际执行的证据；
- 可审计的 HTML 报告与数据导出；
- 失败、降级或需要用户确认时的明确说明。

## 快速开始

Python 要求 3.11 或更高版本。

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[scientific,api,mcp,dev]"
.venv\Scripts\agentd.exe tool-status
```

启动本地服务：

```powershell
.venv\Scripts\agentd.exe serve --host 127.0.0.1 --port 8000
```

`/health` 用于进程探活，`/ready` 用于依赖就绪检查。生产智能体优先连接按工作流隔离的 `/mcp/wf01/` 至 `/mcp/wf05/` 入口；鉴权、工具列表与客户端接入见 [MCP 服务文档](docs/operations/MCP_SERVER.md)。

## 开发与测试

```powershell
$env:PYTHONPATH = "src"
python -B -m unittest discover -s tests -v
```

安装开发依赖后也可以运行 `pytest`。未安装 Primer3、BLAST+ 或 MAFFT 时，可以用显式开发降级验证工程链路；降级不是科学替代方案。

## 仓库中的主要内容

| 目录 | 内容 |
|---|---|
| `src/` | Python 服务、工作流与隔离快照 |
| `schemas/` | 输入、交互、状态和报告 JSON Schema |
| `skills/` | P1～P6 与 WF-01～WF-05 的按需 Skill |
| `config/` | 可版本化的默认配置和资源目录示例 |
| `deploy/` | Docker、Compose、发布包和服务器脚本 |
| `tests/` / `testdata/` | 回归测试与可重复样例 |
| `docs/` | 分层的架构、工作流、运维、参考与历史文档 |

## 继续阅读

- [文档总导航](docs/README.md)
- [各工作流的当前状态](docs/workflows/README.md)
- [服务器请求与容器部署](docs/operations/SERVER_REQUEST_AND_DEPLOYMENT.md)
- [安装、升级和回滚](docs/operations/SERVER_INSTALL.md)
- [发布变更](docs/history/CHANGELOG.md)

智能体开发者从 [AGENTS.md](AGENTS.md) 开始；该文件只做路由，会引导到与当前任务相关的更深层文档。
