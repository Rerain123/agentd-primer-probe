# 仓库结构与文件归属

本文档回答两个问题：“我正在看的是什么？”和“新文件应该放在哪里？”。

## 顶层分层

```text
agentd-primer-probe/
├── README.md                    面向人的产品说明
├── AGENTS.md                    面向智能体的小型路由页
├── pyproject.toml                Python 包、依赖和测试配置
├── src/                          可执行源码和隔离快照
├── schemas/                      机器可读契约
├── skills/                       模型按需加载的 Skill
├── config/                       可版本化的默认配置
├── deploy/                       发布与服务器资产
├── tests/                        自动化契约与回归测试
├── testdata/                     可重复的测试输入
├── scripts/                      维护与科学验证脚本
└── docs/                         按主题分层的文档
```

## 源码与项目契约

| 路径 | 责任 | 修改时同步检查 |
|---|---|---|
| `src/agentd/` | 当前应用、共享适配层、服务门面与 WF-01 | `schemas/`、相关测试和现行工作流文档 |
| `src/agentd_workflow_snapshots/` | WF-02～WF-05 的绑定运行快照 | 隔离绑定、哈希和快照完整性测试 |
| `schemas/` | 对外字段、交互、任务状态与报告 | 生产/消费代码与 Schema 契约测试 |
| `skills/agentd-p*/` | P1～P6 工具 Skill | 工具实现和返回字段 |
| `skills/oligosmith-wf*/` | WF-01～WF-05 模型交互 Skill | MCP 工具范围、工作流状态和 UI 契约 |
| `config/` | 仓库默认值与可注入资源示例 | 配置加载、文档和部署模板 |
| `deploy/` | 构建、安装、验证、离线交付 | 部署测试与 `docs/operations/` |

## 文档归属

| 新内容的性质 | 目标位置 | 不应放在 |
|---|---|---|
| 产品价值、能力和使用入口 | 根 `README.md` | `AGENTS.md` 或单工作流施工稿 |
| 智能体路由和全局不变量 | 根 `AGENTS.md` | 巨型全量说明 |
| 全局分层、公共边界和架构 | `docs/architecture/` | 某个 `wfXX/` 目录 |
| 单个工作流的现行实现和台账 | `docs/workflows/wfXX/` | `docs/` 根目录 |
| 接入、鉴权、服务器和存储 | `docs/operations/` | 产品 README |
| 稳定工具或字段导航 | `docs/reference/` | 历史文档 |
| 未改写的原始需求与设计输入 | `docs/source-materials/` | 现行实现目录 |
| 发布记录、早期阶段和迁移台账 | `docs/history/` | 任何现行契约入口 |

## 本地生成物

以下目录可以出现在开发机上，但它们不是项目权威源：

| 路径 | 含义 |
|---|---|
| `.venv/` | 本地 Python 环境 |
| `.idea/` | IDE 本地元数据 |
| `.playwright-cli/` | 浏览器调试截图、日志和快照 |
| `dist/` | 已生成的发布包或热修交付 |
| `output/` | 本地报告、PDF 和浏览器验证产物 |
| `tmp/` | 临时比较或实验文件 |
| `__pycache__/`、`*.egg-info/` | Python 运行/构建缓存 |

这些内容由 `.gitignore` 隔离。诊断问题时可以查看，不得将它们当成源码、设计基线或验收事实。

## 层级化阅读

1. 从 `AGENTS.md` 或本页确定类别。
2. 读该分类的 `README.md`。
3. 只打开当前工作流或运维任务的现行文档。
4. 用文档中的代码、Schema 和测试入口继续下钻。
5. 只在需要解释来龙去脉时打开原始输入或历史。

这样可以使智能体的默认上下文保持小而准，同时让每一层都有可维护、可检查的入口。
