# Agent D：引物与探针设计智能体

这是温氏 AI4S 项目中 Agent D 的第三阶段完整工作流框架。当前版本已经覆盖 WF-01～WF-07、P1～P6、服务器可注入数据库目录、统一 Schema、CLI、Streamable HTTP MCP、HTTP API、任务记录、readiness 和 Docker Compose。

> **本期交付基线（优先级最高）**：`引物智能体WF01_PRD_v0.2` 仅定义 WF-01 的“多模板、多靶标、普通 TaqMan 独立设计”。该 PRD 与本 README 中保留的第三阶段历史能力有冲突时，以新版 PRD 和已登记的后续业务确认为准；MAFFT/保守性、简并、MGB、多重、内标和 ViennaRNA 不进入本期 WF-01 主流程。实施与验收请先查阅 [`docs/WF01_PRD_V0.2_M0_REQUIREMENTS_BASELINE.md`](docs/WF01_PRD_V0.2_M0_REQUIREMENTS_BASELINE.md)；每次业务裁决、实施状态和验证证据统一登记在 [`docs/WF01_PRD_V0.2_DECISION_LOG.md`](docs/WF01_PRD_V0.2_DECISION_LOG.md)。

> **新版实现进度**：M1～M7 已有工程实现，但不能据此宣称全部符合新版 PRD。多记录输入、MAFFT/保守区、评分细则、用户导出格式和报告数据一致性仍有待收口项；实时状态以 [`docs/WF01_PRD_V0.2_DECISION_LOG.md`](docs/WF01_PRD_V0.2_DECISION_LOG.md) 为准。BLAST/数据库异常必须原样保留为 `NOT_EVALUATED`/待复核，不得误报搜索完成。

> 本项目输出仅供科研设计参考；未经实验验证，不得直接用于临床或生产决策。

## 当前能力

| 能力 | 状态 |
|---|---|
| WF-01 单靶标 TaqMan 设计 | 已实现 |
| 独立 Primer3 序列设计工具 | 已实现；只需 ≥80 nt A/C/G/T 模板，特异性固定为 NOT_EVALUATED |
| WF-02 多重 qPCR 面板 | 已实现 |
| WF-03 内标引物设计 | 已实现 |
| WF-04 已有引物验证 | 已实现 |
| WF-05 变异耐受性分析 | 已实现 |
| WF-06 完整诊断面板 | 已实现 |
| WF-07 问题诊断与优化 | 已实现 |
| P1 Primer3 设计与热力学 | 已实现适配器 |
| P2 BLAST 靶标/宿主/近缘种检查 | 已实现编排器 |
| P3 MGB 探针修正 Tm 与专用规则 | 已实现 |
| P4 多重荧光和交叉二聚体兼容性 | 已实现 |
| P5 内标设计编排 | 已实现，数据由请求或服务器 Provider 注入 |
| P6 ViennaRNA 扩增子二级结构 | 已实现，可用性降级 |
| WF-01 简并引物并行策略 | 已实现，≤3 位点/≤8 组合 |
| C29 候选对比报告 | 已实现 Markdown 输出 |
| C30 结果解释 | 已实现可审计的确定性自然语言模板 |
| C1 序列 IO | 已实现 |
| C5 MAFFT | 已实现适配器 |
| C6 BLAST+ | 已实现适配器 |
| C7 统一 JSON Schema | 已实现 |
| C0 数据库目录 Provider | 已实现，正式数据部署时挂载 |

### WF-01 多靶标清单与两条执行路径

M1 会把粘贴序列或 FASTA 规范化为 `target_manifest`，并为每个独立靶标给出状态：

- `READY_DESIGN_ONLY`：序列可执行但缺少靶标；运行 Primer3、规则、去重和初步排序，特异性保持 `NOT_EVALUATED`。
- `READY_FULL`：序列和靶标齐全；运行靶标命中与综合背景特异性评估并最终重排。宿主为可选信息，提供时追加宿主专库排除。
- `NEEDS_CONFIRMATION`：存在需要用户确认或修正的问题。
- `BLOCKED`：模板缺失、少于 80 nt、含非法碱基或设计参数不可执行。

问题靶标可以在 M2 前修改或移除。全部纳入靶标可执行后，再确认批量启动。

## 安装

Python 要求 3.11 或更高版本。

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -e ".[scientific,api,dev]"
```

BLAST+ 和 MAFFT 是独立二进制工具，需由部署环境安装并挂载对应数据库。安装后先检查：

```powershell
.venv\Scripts\agentd.exe tool-status
```

`primer3-py` 含原生扩展。服务启动时会先在隔离子进程中探测其可加载性；Windows 安装目录含非 ASCII 字符时，服务自动建立版本化 ASCII 临时缓存以兼容 Primer3 C 库。若 DLL/ABI 仍不兼容，工具状态会返回 `native_probe_error`，主服务不会因此崩溃。可用 `AGENTD_ASCII_CACHE_DIR` 指定缓存根目录，生产部署仍推荐 Linux 容器和纯 ASCII 容器路径。

## 开发闭环

没有安装 Primer3、BLAST+ 或 MAFFT 时，可以显式启用开发降级模式验证工程链路。P6 不可用时会明确返回 `not_evaluated`：

```powershell
$env:PYTHONPATH = "src"
python -B -m agentd run --workflow WF-01 --input examples\wf01-request.json --output outputs\wf01-result.json --allow-fallback --no-persist
python -B -m agentd run --workflow WF-02 --input examples\wf02-request.json --output outputs\wf02-result.json --allow-fallback --no-persist
python -B -m agentd run --workflow WF-03 --input examples\wf03-request.json --output outputs\wf03-result.json --allow-fallback --no-persist
python -B -m agentd run --workflow WF-04 --input examples\wf04-request.json --output outputs\wf04-result.json --allow-fallback --no-persist
python -B -m agentd run --workflow WF-05 --input examples\wf05-request.json --output outputs\wf05-result.json --allow-fallback --no-persist
python -B -m agentd run --workflow WF-06 --input examples\wf06-request.json --output outputs\wf06-result.json --allow-fallback --no-persist
python -B -m agentd run --workflow WF-07 --input examples\wf07-request.json --output outputs\wf07-result.json --allow-fallback --no-persist
```

降级模式不是科学替代方案：输出固定标记为 `PARTIAL`、`in_silico_only`，缺失项目显示为 `NOT_EVALUATED` 或 `NOT_CHECKED`。

只在 Windows 本机使用时，可直接双击 `start-local.cmd` 启动、`test-local.cmd` 验证七个 Workflow、`stop-local.cmd` 停止。详见 `docs/LOCAL_USAGE.md`。

## HTTP API

生产智能体入口为 `POST/GET/DELETE /mcp`（Streamable HTTP MCP），使用 Bearer JWT 鉴权；`/health` 与 `/ready` 保持公开供反向代理和容器探活。部署、令牌生成及后台注册见 [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md)。以下 REST 端点作为兼容层保留，并与 MCP 共用同一套 `AgentDService`。

```powershell
.venv\Scripts\agentd.exe serve --host 0.0.0.0 --port 8000
```

端点：

- `GET /health`
- `GET /ready`
- `GET /v1/tool-status`
- `GET /v1/capabilities`
- `GET /v1/audit/agent`
- `POST /v1/tools/primer3/design`
- `POST /v1/wf01/tasks/preflight`
- `POST /v1/wf01/tasks`
- `GET /v1/wf01/tasks/{batch_id}`
- `PATCH /v1/wf01/tasks/{batch_id}/targets`
- `POST /v1/wf01/tasks/{batch_id}/confirm`
- `POST /v1/wf01/tasks/{batch_id}/run-until-blocked`
- `POST /v1/wf01/tasks/{batch_id}/run-m2`
- `POST /v1/wf01/tasks/{batch_id}/run-m3`
- `POST /v1/wf01/tasks/{batch_id}/run-m4`
- `POST /v1/wf01/tasks/{batch_id}/run-m5`
- `POST /v1/wf01/tasks/{batch_id}/run-m6`
- `POST /v1/wf01/tasks/{batch_id}/versions`
- `GET /v1/wf01/tasks/{batch_id}/report`
- `GET /v1/wf01/tasks/{batch_id}/scientific-artifacts`
- `GET /v1/wf01/tasks/{batch_id}/scientific-artifacts/{artifact_id}`
- `GET /v1/wf01/tasks/{batch_id}/exports/{format_name}`
- `POST /v1/wf01/design`
- `POST /v1/wf02/multiplex`
- `POST /v1/wf03/internal-control`
- `POST /v1/wf04/validate`
- `POST /v1/wf05/variation`
- `POST /v1/wf06/complete-panel`
- `POST /v1/wf07/diagnose`
- `POST /v1/workflows/{workflow}/run`
- `GET /v1/jobs/{job_id}`

API 与 CLI 使用同一 Workflow 和 JSON Schema，不维护两套业务逻辑。

WF-01 输入分级规则：只提供合格序列时立即运行 Primer3、硬规则和软规则，候选的
特异性标记为 `NOT_EVALUATED` 并提示后续补充靶标信息；提供靶标时，继续运行靶标命中和
广谱 `nt` 背景特异性评估；若同时提供宿主，再追加宿主专库排除。只有序列缺失、短于
80 nt、含非法/歧义碱基或设计参数无效时才阻止 Primer3。

MCP 工具调用会写入脱敏审计日志，可通过后台 REST 接口 `GET /v1/audit/agent`
查询工具名称、字段来源、有效输入、状态迁移、数据库就绪状态和错误。
日志不记录模型内部思维链，完整 FASTA、令牌和密钥只保留长度/哈希或被移除。

BLAST 特异性评估仅调用服务器上的 BLAST+ 和版本化数据库，不提供将序列
发送到公共 BLAST 服务的远程提交或复核旁路。

## 测试

标准库测试不依赖 pytest：

```powershell
$env:PYTHONPATH = "src"
python -B -m unittest discover -s tests -v
```

安装开发依赖后也可运行：

```powershell
pytest
```

## 生产配置要点

- 完整特异性流程需配置版本化靶标库和 `nt`/综合背景库；仅在用户提供宿主时才需对应宿主库。
- 用 `AGENTD_DATABASE_CATALOG` 指向服务器资源目录；Workflow 只使用逻辑资源键，不硬编码服务器路径。
- 宿主数据库映射位于 `config/species.json`。
- 所有设计阈值和评分权重位于 `config/defaults.json`，修改时必须版本化。
- 本地启动脚本将运行记录写入项目同级的 `agentd-runtime/local-jobs/`，避免源码目录只读或ACL继承导致持久化失败；生产环境应改为平台对象存储或数据库。
- 容器或只读工作目录可通过 `AGENTD_STATE_DIR` 指向持久化卷或其他可写目录。
- WF-01 同时支持上传 FASTA 文件和直接粘贴/手动输入序列。直接输入由智能体提取 `sequence`、`target_organism`、`host` 后立即调用 `create_wf01_batch`，不弹输入确认表单；上传文件暂保留 `prepare_wf01_design_input → download_himat_file → create_wf01_batch` 兼容链路。上传 FASTA 正文仅在后端内部解析，不进入后续模型上下文。下载基地址通过 `HIMAT_FILE_DOWNLOAD_BASE_URL` 配置。
- HiMat 交付附件通过 RustFS S3 发布；配置、最小权限和校验流程见 `docs/RUSTFS_ARTIFACT_STORAGE.md`。
- 不得在前端或 LLM 提示词中复制另一套科学阈值。

Linux 容器定义位于 `deploy/docker/Dockerfile`，完整服务器编排位于 `deploy/docker-compose.yml`。镜像包含 Primer3、ViennaRNA、BLAST+ 和 MAFFT；BLAST 数据库与序列数据通过只读卷挂载。

第三阶段架构、服务器资源目录和验收项见 `docs/PHASE3.md`；前两阶段文档保留为历史基线。

服务器交付使用 `deploy/build-package.ps1` 生成带 SHA-256 校验的 ZIP 安装包。完整的联网、离线、建库、升级和回滚流程见 `docs/SERVER_INSTALL.md`。

P1～P6 已封装为项目内可加载 Skill，目录和服务映射见 `docs/SKILLS.md`。
