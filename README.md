# Agent D：引物与探针设计智能体

这是温氏 AI4S 项目中 Agent D 的工作流框架。当前稳定范围为相互隔离的 WF-01～WF-05。WF-06、WF-07 的第二阶段任务已经完成，当前暂不继续开发，也不提供现行运行时、CLI 或 HTTP 入口。项目同时包含 P1～P6、服务器可注入数据库目录、统一 Schema、CLI、Streamable HTTP MCP、HTTP API、任务记录、readiness 和 Docker Compose。

> **WF-01 当前实现**：M1～M7 批次路线已经实现。普通多记录输入默认组成一个 `alignment_target` 并按需运行 MAFFT；`target_manifest` 使用 `independent_targets`，逐靶标独立运行。当前入口、状态机、科学规则、前端消息、报告和兼容边界统一以 [`docs/WF01_CURRENT_IMPLEMENTATION.md`](docs/WF01_CURRENT_IMPLEMENTATION.md) 为准。

> **科学状态边界**：M6 报告生成完成与科学搜索完整是两件事。新的模型侧 WF-01 任务必须提供靶标并走 `FULL_SPECIFICITY`；缺少靶标时先询问用户，不再自动生成 `DESIGN_ONLY` 报告。底层兼容接口仍能读取历史 `DESIGN_ONLY` 批次。完整特异性路线仍保留待实验校准的策略状态，工具、数据库或证据异常不得误报为 `PASS` 或搜索完成。

> 本项目输出仅供科研设计参考；未经实验验证，不得直接用于临床或生产决策。

> 智能体开发同事请先阅读 [`docs/PROJECT_ENGINEERING_GUIDE.md`](docs/PROJECT_ENGINEERING_GUIDE.md)，其中汇总了代码架构、工作流、接口、测试和部署；专题文档入口见 [`docs/README.md`](docs/README.md)。新的 WF-02 按 [`WF02_V0.1_IMPLEMENTATION_PLAN.md`](docs/WF02_V0.1_IMPLEMENTATION_PLAN.md) 中的 HTML 节点台账实施，WF-03 以 [`WF03_CURRENT_IMPLEMENTATION.md`](docs/WF03_CURRENT_IMPLEMENTATION.md) 为准。

## 当前能力

| 能力 | 状态 |
|---|---|
| WF-01 普通 TaqMan 批次设计 | 已实现；支持 alignment target 与独立 target manifest，新任务自动执行 M1～M6，并支持可选的 M7 重设计 |
| 独立 Primer3 序列设计工具 | 已实现；只需 ≥80 nt A/C/G/T 模板，特异性固定为 NOT_EVALUATED |
| WF-02 多重 qPCR 面板 | 新 v0.1 已完成 HTML N01–N32；当前由可恢复的持久化后台任务执行，首个请求最多等待 235 秒，并在不改变科学契约的前提下优化大组合遍历和重复底层计算 |
| WF-03 内标引物设计 | 已实现确定性后端 + MCP `run/retry/report`；输入复用 WF-01 的下载与不透明引用边界，输出复用 WF-01 的 HTML 附件与 `primer_design_output` 协议 |
| WF-04 已有引物验证 | v0.1 已实现确定性六节点后端 + MCP `run/retry/report`；除内部节点外，输入输出复用 WF-01 边界，重试创建不可变子任务 |
| WF-05 变异耐受性分析 | v0.1 已实现独立运行包、六节点后端、不可变重试、终态报告和专属 MCP/Skill |
| P1 Primer3 设计与热力学 | 已实现适配器 |
| P2 BLAST 靶标/宿主/近缘种检查 | 已实现；是 WF-01 特异性检索与判定的唯一工具 |
| P3 MGB 探针修正 Tm 与专用规则 | 已实现 |
| P4 多重荧光和交叉二聚体兼容性 | 已实现 |
| P5 内标设计编排 | 已实现，数据由请求或服务器 Provider 注入 |
| P6 ViennaRNA 扩增子二级结构 | 已实现，可用性降级 |
| 历史简并引物策略 | 兼容模块保留，≤3 位点/≤8 组合；不进入当前 WF-01 批次路线 |
| C29 候选对比报告 | 已实现 Markdown 输出 |
| C30 结果解释 | 已实现可审计的确定性自然语言模板 |
| C1 序列 IO | 已实现 |
| C5 MAFFT | 已接入 WF-01 主路线；实际比对后计算 Shannon 保守区与候选结合位点证据 |
| C6 BLAST+ | 已实现适配器 |
| C7 统一 JSON Schema | 已实现 |
| C0 数据库目录 Provider | 已实现，正式数据部署时挂载 |

### WF-01 模板组织与科学执行路线

M1 先决定模板如何组成靶标：

- `alignment_target`：未提供 `target_manifest` 时的默认值。单记录直接设计；多记录组成同一靶标，M2 先运行 MAFFT 和保守区计算。
- `independent_targets`：显式使用 `target_manifest` 时强制采用。每条记录形成独立靶标，不运行 MAFFT。

`alignment_target` 多模板使用实际 MAFFT 输出计算 gap-aware Shannon 保守性，依次尝试 `≥0.95 / ≥150 bp`、`≥0.92 / ≥120 bp`、`≥0.90 / ≥90 bp`；代表序列目标为 N≥20。单模板、MAFFT 未实际完成或开发降级均不会伪造保守性分数。

随后为每个靶标给出状态和科学路线：

- `READY_DESIGN_ONLY`：序列可执行但缺少靶标；运行 Primer3、规则、去重和初步排序，特异性保持 `NOT_EVALUATED`。
- `READY_FULL`：序列和靶标齐全；必须调用 P2 运行靶标命中与综合背景特异性评估，再由 M4 映射结果并最终重排。宿主为可选信息，提供时追加宿主专库排除。
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

没有安装 Primer3、BLAST+ 或 MAFFT 时，可以显式启用开发降级模式验证工程链路。MAFFT 降级只允许继续工程设计，保守性固定为 `NOT_EVALUATED`；P6 不可用时也会明确返回 `not_evaluated`：

```powershell
$env:PYTHONPATH = "src"
$requestPath = ".\request.json"
python -B -m agentd run --workflow WF-05 --input $requestPath --output outputs\wf05-result.json --allow-fallback --no-persist
```

将 `$requestPath` 指向符合对应工作流输入 Schema 的 JSON 文件，并按需替换 `--workflow` 与输出文件名；仓库不再维护容易过时的 `examples/*.json` 副本。

当前 WF-01 批次路线请使用 MCP 工具或 `/v1/wf01/tasks` API，具体以 [`docs/WF01_CURRENT_IMPLEMENTATION.md`](docs/WF01_CURRENT_IMPLEMENTATION.md) 为准。WF-02 命令行兼容入口只负责调用新的 v0.1 建单服务；N06–N32 的智能体推进与报告交付使用 MCP 或下面的批次 REST 接口。WF-03、WF-04 与 WF-05 均绑定各自的独立后端、状态目录和终态交付；WF-05 由 MCP `run_wf05_variation` / `retry_wf05_variation` / `get_wf05_report` 暴露。通用 Workflow 入口仅作为兼容别名。WF-06、WF-07 的第二阶段任务已完成，当前暂不继续，也不属于现行公开入口。

降级模式不是科学替代方案：输出固定标记为 `PARTIAL`、`in_silico_only`，缺失项目显示为 `NOT_EVALUATED` 或 `NOT_CHECKED`。

## HTTP API

生产智能体优先使用 `POST/GET/DELETE /mcp/wf01/` 至 `/mcp/wf05/` 五个受限 Streamable HTTP MCP 入口；原 `/mcp` 保留为兼容回退入口。所有入口使用 Bearer JWT，`/health` 与 `/ready` 保持公开供反向代理和容器探活。五份按需加载 Skill 位于 `skills/oligosmith-wf01` 至 `skills/oligosmith-wf05`。部署、令牌生成及后台注册见 [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md)。以下 REST 端点作为兼容层保留，并与 MCP 共用同一套 `AgentDService`。

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
- `POST /v1/wf02/tasks/preflight`
- `POST /v1/wf02/tasks`
- `GET /v1/wf02/tasks/{batch_id}`
- `GET /v1/wf02/drafts/{draft_id}`
- `POST /v1/wf02/drafts/{draft_id}/interactions/response`
- `POST /v1/wf02/tasks/{batch_id}/run-until-blocked`
- `POST /v1/wf02/tasks/{batch_id}/targets/{target_id}/interactions/response`
- `POST /v1/wf02/tasks/{batch_id}/internal-control/interactions/response`
- `POST /v1/wf02/tasks/{batch_id}/versions`
- `GET /v1/wf02/tasks/{batch_id}/report`
- `GET /v1/wf02/tasks/{batch_id}/panels/{panel_id}`
- `GET /v1/wf02/tasks/{batch_id}/exports/{format_name}`
- `GET /v1/wf02/tasks/{batch_id}/exports/html/preview`
- `POST /v1/wf02/multiplex`
- `POST /v1/wf03/internal-control`
- `POST /v1/wf03/internal-control/{job_id}/retry`
- `GET /v1/wf03/internal-control/{job_id}/report`
- `POST /v1/wf04/validate`
- `POST /v1/wf04/validate/{job_id}/retry`
- `GET /v1/wf04/validate/{job_id}/report`
- `POST /v1/wf05/variation`
- `POST /v1/wf05/variation/{job_id}/retry`
- `GET /v1/wf05/variation/{job_id}/report`
- `POST /v1/workflows/{workflow}/run`
- `GET /v1/jobs/{job_id}`

通用 `/v1/workflows` API 与上述 CLI 共用兼容分发层；当前 WF-01 与新 WF-02 使用各自独立的批次 API/MCP 契约，不应混用输入格式。

WF-01 输入分级规则：只提供合格序列时立即运行 Primer3、硬规则和软规则，候选的
特异性标记为 `NOT_EVALUATED` 并提示后续补充靶标信息；提供靶标时，继续运行靶标命中和
广谱 `nt` 背景特异性评估；若同时提供宿主，再追加宿主专库排除。只有序列缺失、短于
80 nt、含非法/歧义碱基或设计参数无效时才阻止 Primer3。

MCP 工具调用会写入脱敏审计日志，可通过后台 REST 接口 `GET /v1/audit/agent`
查询工具名称、字段来源、有效输入、状态迁移、数据库就绪状态和错误。
日志不记录模型内部思维链，完整 FASTA、令牌和密钥只保留长度/哈希或被移除。

WF-01 的 M4 只编排 P2、映射规则并重排，不定义另一套特异性阈值或命中策略。P2 对目标资源
优先使用本地版本化条目；未配置专用条目时，把任务自身的不可变输入快照临时物化为执行资源。
综合背景优先使用本地 `cross/nt`，未解析时可转交 `BLAST_SEARCH_SERVICE_URL` 配置的远端服务。
宿主资源仍要求本地版本化条目。任一实际工具错误或无可用回退都保持 `NOT_EVALUATED`，不能产生虚假 `PASS`。

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
- 当前 WF-01 的设计阈值、规则和评分权重以 `src/agentd/wf01_v02*.py` 中的实现为准；`config/defaults.json` 仍服务于旧版通用 Workflow。两者修改时都必须版本化。
- 容器或只读工作目录可通过 `AGENTD_STATE_DIR` 指向持久化卷或其他可写目录。
- WF-01 同时支持上传 FASTA 文件和直接粘贴/手动输入序列，不再使用输入确认表单。直接输入调用 `create_wf01_batch(sequence=..., target_organism=...)`；上传文件调用 `download_himat_file → create_wf01_batch(input_artifact_id=..., target_organism=...)`。`target_organism` 是新任务必填字段，缺少时先以普通文本询问用户，不允许静默降级。上传 FASTA 正文仅在后端内部解析，不进入模型上下文。下载基地址通过 `HIMAT_FILE_DOWNLOAD_BASE_URL` 配置。
- WF-01 的完整现行路线只维护在 [`docs/WF01_CURRENT_IMPLEMENTATION.md`](docs/WF01_CURRENT_IMPLEMENTATION.md)，不再维护 M0～M7 分散契约。
- HiMat 交付附件通过 RustFS S3 发布；配置、最小权限和校验流程见 `docs/RUSTFS_ARTIFACT_STORAGE.md`。
- 不得在前端或 LLM 提示词中复制另一套科学阈值。

Linux 容器定义位于 `deploy/docker/Dockerfile`，完整服务器编排位于 `deploy/docker-compose.yml`。镜像包含 Primer3、ViennaRNA、BLAST+ 和 MAFFT；BLAST 数据库与序列数据通过只读卷挂载。

第一至第三阶段的历史架构、服务器资源目录和旧验收项统一收录在 [`docs/HISTORY_PHASES.md`](docs/HISTORY_PHASES.md)，仅作历史参考。

服务器交付使用 `deploy/build-package.ps1` 生成带 SHA-256 校验的 ZIP 安装包。完整的联网、离线、建库、升级和回滚流程见 `docs/SERVER_INSTALL.md`。

P1～P6 已封装为项目内可加载 Skill，目录和服务映射见 `docs/SKILLS.md`。
