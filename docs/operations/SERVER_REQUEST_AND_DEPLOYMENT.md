# Agent D 服务器请求与容器部署说明

本文汇总 Agent D（`agentd-primer-probe`）的服务器请求方式、访问地址、鉴权要求、网络链路、Docker 容器配置、数据挂载及部署状态检查方法。

> 文档对应项目版本：`1.2.13`
> 文档更新日期：2026-08-27（Asia/Shanghai）

## 1. 服务访问地址

| 用途 | 地址 | 请求方式 | 鉴权要求 |
|---|---|---|---|
| MCP 兼容入口 | `https://oligosmith.ne.scqcyz.cn/mcp` | Streamable HTTP，支持 `POST`、`GET`、`DELETE` | Bearer JWT |
| WF-01 MCP | `https://oligosmith.ne.scqcyz.cn/mcp/wf01/` | Streamable HTTP，只暴露 WF-01 工具 | Bearer JWT |
| WF-02 MCP | `https://oligosmith.ne.scqcyz.cn/mcp/wf02/` | Streamable HTTP，只暴露 WF-02 工具 | Bearer JWT |
| WF-03 MCP | `https://oligosmith.ne.scqcyz.cn/mcp/wf03/` | Streamable HTTP，只暴露 WF-03 工具 | Bearer JWT |
| WF-04 MCP | `https://oligosmith.ne.scqcyz.cn/mcp/wf04/` | Streamable HTTP，只暴露 WF-04 工具 | Bearer JWT |
| WF-05 MCP | `https://oligosmith.ne.scqcyz.cn/mcp/wf05/` | Streamable HTTP，只暴露 WF-05 工具 | Bearer JWT |
| REST API | `https://oligosmith.ne.scqcyz.cn/v1/...` | HTTP JSON API | Bearer JWT |
| 存活检查 | `https://oligosmith.ne.scqcyz.cn/health` | `GET` | 公开 |
| 科学就绪检查 | `https://oligosmith.ne.scqcyz.cn/ready` | `GET` | 公开 |
| 内置 Web UI | `https://oligosmith.ne.scqcyz.cn/` 或 `/ui` | `GET` | 默认受 Bearer JWT 保护 |

公网域名由 OpenResty/1Panel 反向代理到宿主机的 `http://127.0.0.1:8020`，再由 Docker 端口映射转发到 Agent D 容器的 `8000` 端口。

## 2. 请求方式

### 2.1 MCP 请求

生产智能体优先连接与当前 Skill 对应的 `/mcp/wf01/` 至 `/mcp/wf05/`，协议类型均为无状态 Streamable HTTP MCP。`/mcp` 保留为兼容回退入口。推荐通过支持 Streamable HTTP 的 MCP 客户端调用，而不是自行拼装底层 JSON-RPC 会话。

MCP 客户端配置示例：

```json
{
  "name": "OligoSmith",
  "type": "streamableHttp",
  "description": "引物与探针设计、验证、优化和报告生成",
    "baseUrl": "https://oligosmith.ne.scqcyz.cn/mcp/wf01/",
  "headers": {
    "Content-Type": "application/json",
    "Authorization": "Bearer YOUR_JWT"
  }
}
```

每个受限 MCP 地址只面向模型提供对应工作流的编排工具；`tools/list` 不会混入其他工作流。服务状态、审计及其他兼容入口仍可通过 REST API 或后台代码访问。

### 2.2 REST API 请求

REST API 是与 MCP 共用同一套 `AgentDService` 的兼容接口，适合后台系统和运维程序调用。

请求头：

```http
Content-Type: application/json
Authorization: Bearer YOUR_JWT
```

WF-01 建单示例：

```bash
curl -X POST \
  "https://oligosmith.ne.scqcyz.cn/v1/wf01/tasks" \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "task_name": "example",
    "sequence": "ACGT...至少80nt...",
    "batch_defaults": {
      "target_organism": {"name": "目标病原名称"},
      "hosts": [{"name": "pig"}]
    }
  }'
```

新 WF-01 任务必须提供靶标物种或病原名称，并提供直接序列或上传文件引用。生产完整路线不得静默降级为 `DESIGN_ONLY`。

### 2.3 健康检查

```bash
curl https://oligosmith.ne.scqcyz.cn/health
curl https://oligosmith.ne.scqcyz.cn/ready
```

- `/health` 只表示进程存活。
- `/ready` 检查科学工具和当前配置要求的数据库资源。
- `/ready` 返回成功并不一定表示全部动态靶标数据库均已准备好。

## 3. JWT 鉴权

除 `/health` 和 `/ready` 外，MCP 与 REST API 默认都需要 Bearer JWT。

在服务器安装目录中生成一个有效期为 30 天的 HS256 客户端令牌：

```bash
bash deploy/scripts/generate-jwt.sh 30 oligosmith-backend
```

随后将输出的 JWT 配置到客户端：

```http
Authorization: Bearer GENERATED_JWT
```

注意事项：

- `JWT_SECRET` 是服务器端签名密钥，不是客户端 Bearer Token。
- 不得把 `deploy/.env`、`JWT_SECRET` 或实际 JWT 提交到代码仓库。
- 轮换 `JWT_SECRET` 后，之前签发的全部令牌会立即失效。
- `MCP_JWT_REQUIRE_EXP=true` 时，客户端令牌必须包含有效的过期时间。
- 当前配置使用 `HS256`，代码也支持 `HS384` 和 `HS512`。

当 `MCP_PUBLIC_WF01_ARTIFACTS=true` 时，只有部分 WF-01 报告、导出文件和科学过程产物允许匿名只读访问。MCP、任务创建、交互回复、重新设计以及 WF-02 报告接口仍然需要 JWT。启用 RustFS 时，WF-02 终态卡片使用已发布对象的完整公网 URL；未启用时使用默认 24 小时有效、绑定批次与格式的签名只读附件地址。

## 4. 网络与反向代理链路

```text
客户端
  -> HTTPS / OpenResty（1Panel 反向代理）
  -> http://127.0.0.1:8020（宿主机）
  -> agentd:8000（Docker 容器）
  -> Uvicorn + FastMCP + FastAPI
```

推荐让 1Panel/OpenResty 将整个域名转发到 `http://127.0.0.1:8020`。全量及按工作流 MCP 地址、`/v1`、报告下载和健康检查使用同一个服务进程，不需要为每个 MCP 地址单独创建端口或子应用。

默认仅监听宿主机回环地址：

```dotenv
AGENTD_BIND_ADDRESS=127.0.0.1
AGENTD_PORT=8020
```

只有在防火墙、TLS、认证、限流和访问控制均已配置后，才应将 `AGENTD_BIND_ADDRESS` 改为 `0.0.0.0`。

## 5. Docker 容器信息

当前 Compose 只定义一个业务容器。

| 配置项 | 值 |
|---|---|
| Compose 项目名 | `agentd-primer-probe` |
| 服务名 | `agentd` |
| 镜像名 | `agentd-primer-probe:1.2.13` |
| 基础镜像 | `python:3.12-slim` |
| 容器工作目录 | `/app` |
| 容器监听地址 | `0.0.0.0:8000` |
| 默认宿主机映射 | `127.0.0.1:8020 -> 8000` |
| 启动程序 | `uvicorn agentd.mcp_server:app` |
| 重启策略 | `unless-stopped` |
| 运行用户 | 非 root，UID `10001` |
| CPU 限制 | 默认 `4.0` |
| 内存限制 | 默认 `8g` |
| PID 上限 | `256` |
| 停止宽限期 | 30 秒 |
| 根文件系统 | 只读 |
| 临时目录 | `/tmp`，512 MB tmpfs |
| 时区 | `Asia/Shanghai` |

容器启动命令：

```text
uvicorn agentd.mcp_server:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips "*"
```

Compose 健康检查访问容器内的：

```text
http://127.0.0.1:8000/ready
```

检查间隔 30 秒、超时 5 秒、启动等待 30 秒，连续失败 5 次后标记为不健康。

## 6. 容器内科学工具和运行依赖

镜像包含以下主要科学工具：

- Primer3（`primer3-py`）
- ViennaRNA
- NCBI BLAST+
- MAFFT
- Biopython

服务层主要依赖：

- FastAPI
- Uvicorn
- FastMCP
- PyJWT
- Boto3
- ReportLab
- pypdf

BLAST+、MAFFT 和中文字体通过 Debian 软件包安装；Primer3、ViennaRNA、FastAPI 和 MCP 等通过 Python 包安装。

## 7. 数据卷与目录

| 容器路径 | 来源 | 权限 | 用途 |
|---|---|---|---|
| `/config/production.json` | `deploy/server-config/production.json` | 只读 | 生产运行配置 |
| `/config/database-catalog.json` | `deploy/server-config/database-catalog.json` | 只读 | 数据库资源目录 |
| `/data/blastdb` | `AGENTD_BLASTDB_DIR` | 只读 | BLAST 数据库 |
| `/data/sequences` | `AGENTD_SEQUENCE_DATA_DIR` | 只读 | FASTA/序列数据 |
| `/data/jobs` | Docker 命名卷 `agentd-jobs` | 可写 | 任务、状态、报告和工件 |

默认宿主机目录配置：

```dotenv
AGENTD_BLASTDB_DIR=./server-data/blastdb
AGENTD_SEQUENCE_DATA_DIR=./server-data/sequences
```

容器以 UID `10001` 运行。挂载目录需要允许该用户遍历和读取，建议目录权限为 `755`、数据文件权限为 `644`。数据库和序列目录不应授予容器写权限。

不要执行以下命令，除非已经确认并备份任务记录：

```bash
docker compose down -v
```

普通的 `stop`、`restart` 和不带 `-v` 的 `down` 不会删除 `agentd-jobs` 卷。

## 8. 外部服务依赖

### 8.1 宿主机背景 BLAST 服务

当本地 `cross/nt` 背景数据库不可用时，Agent D 可调用宿主机 `8090` 端口的独立 BLAST 搜索服务：

```dotenv
BLAST_SEARCH_SERVICE_URL=http://host.docker.internal:8090
BLAST_SEARCH_TIMEOUT_SECONDS=360
BLAST_SEARCH_MAX_SEQS=100
```

Compose 已配置 Linux `host-gateway` 映射：

```text
host.docker.internal:host-gateway
```

该远程服务只接管综合背景检查。目标资源优先使用本地版本化条目；没有专用条目时，P2 使用任务中
已冻结并带 SHA-256 版本的输入快照创建任务级临时 FASTA。已提供宿主时，对应资源仍使用本地版本化条目。

### 8.2 HiMat 文件下载服务

```dotenv
HIMAT_FILE_DOWNLOAD_BASE_URL=https://himat.ws.scqcyz.cn/api/files/download
HIMAT_FILE_DOWNLOAD_TIMEOUT_SECONDS=300
AGENTD_INPUT_FILE_MAX_BYTES=1073741824
```

上传文件由 Agent D 后端下载和解析，FASTA 正文不进入模型上下文。

### 8.3 RustFS 对象存储

RustFS 用于发布 WF-01 与 WF-02 报告附件，模板默认关闭：

```dotenv
RUSTFS_ENABLED=false
RUSTFS_ENDPOINT_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_BUCKET=wens
RUSTFS_OBJECT_PREFIX=data
RUSTFS_PUBLIC_BASE_URL=https://rustfs.ne.scqcyz.cn
```

生产启用时，应使用只允许读写指定对象前缀的专用账号，不得使用 RustFS 管理员密钥。
WF-02 HTML、CSV、PDF 使用 `data/wf02-<batch_id>-multiplex-report.<ext>`，避免不同批次覆盖。
终态阶段只生成和发布 HTML；CSV/PDF 在用户明确请求对应格式时生成和发布。已由旧版本完成的批次
首次查询时只按渲染版本刷新 HTML，CSV/PDF 在明确请求时再按版本刷新，均不重新执行流程。
上传和 `HEAD Object` 完整性校验成功后，卡片才保存 RustFS 完整 URL。

### 8.4 NCBI Entrez

P5 内标设计可通过 NCBI Entrez 获取默认管家基因 RefSeq 和同物种同源序列：

```dotenv
NCBI_ENTREZ_EMAIL=
NCBI_ENTREZ_API_KEY=
```

生产环境应填写联系邮箱；API Key 可选。

## 9. 主要 REST API

### 9.1 服务和运维

- `GET /health`
- `GET /ready`
- `GET /v1/tool-status`
- `GET /v1/capabilities`
- `GET /v1/audit/agent`
- `GET /v1/jobs/{job_id}`

### 9.2 WF-01

- `POST /v1/wf01/tasks/preflight`
- `POST /v1/wf01/tasks`
- `GET /v1/wf01/tasks/{batch_id}`
- `PATCH /v1/wf01/tasks/{batch_id}/targets`
- `POST /v1/wf01/tasks/{batch_id}/confirm`
- `POST /v1/wf01/tasks/{batch_id}/run-until-blocked`
- `POST /v1/wf01/tasks/{batch_id}/run-m2` 至 `/run-m6`
- `POST /v1/wf01/tasks/{batch_id}/versions`
- `GET /v1/wf01/tasks/{batch_id}/report`
- `GET /v1/wf01/tasks/{batch_id}/scientific-artifacts`
- `GET /v1/wf01/tasks/{batch_id}/exports/{format_name}`

### 9.3 WF-02

- `POST /v1/wf02/tasks/preflight`
- `POST /v1/wf02/tasks`
- `GET /v1/wf02/tasks/{batch_id}`
- `GET /v1/wf02/drafts/{draft_id}`
- `POST /v1/wf02/tasks/{batch_id}/run-until-blocked`
- `POST /v1/wf02/tasks/{batch_id}/versions`
- `GET /v1/wf02/tasks/{batch_id}/report`
- `GET /v1/wf02/tasks/{batch_id}/panels/{panel_id}`
- `GET /v1/wf02/tasks/{batch_id}/exports/{format_name}`

### 9.4 WF-03～WF-05

- `POST /v1/wf03/internal-control`
- `POST /v1/wf03/internal-control/{job_id}/retry`
- `GET /v1/wf03/internal-control/{job_id}/report`
- `POST /v1/wf04/validate`
- `POST /v1/wf05/variation`
- `POST /v1/wf05/variation/{job_id}/retry`
- `GET /v1/wf05/variation/{job_id}/report`
- `POST /v1/workflows/{workflow}/run`

## 10. 构建、启动和运维

复制并修改环境变量：

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

推荐使用安装脚本：

```bash
bash deploy/scripts/install.sh
```

也可以直接使用 Compose：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml config --quiet
docker compose --env-file deploy/.env -f deploy/docker-compose.yml build --pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
bash deploy/scripts/verify.sh --wait 180
```

常用运维命令：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f agentd
docker compose --env-file deploy/.env -f deploy/docker-compose.yml restart agentd
docker compose --env-file deploy/.env -f deploy/docker-compose.yml stop
```

## 11. 当前线上状态

2026-08-20 对公网地址进行只读检查，结果如下：

| 检查项 | 结果 |
|---|---|
| `GET /health` | `200 OK` |
| `GET /ready` | `200 OK` |
| 未带 JWT 请求 `/mcp` | `401 Unauthorized`，符合预期 |
| 服务名称 | `agentd-primer-probe` |
| 运行阶段 | Phase 3 |
| 运行 Profile | `server-production` |
| 核心科学工具 | 就绪，无缺失工具 |
| 数据库目录 | 已配置，共登记 19 个资源 |
| 宿主猪 BLAST 库 | 可用 |
| 部分靶标 BLAST 库 | 可用 |
| 本地综合背景库 | 当前不可用 |
| 宿主机远程背景 BLAST | 已配置且健康 |

当前线上响应同时显示：

```text
databases_required=false
```

这意味着当前服务允许数据库不完整时进入 `ready` 状态，更接近交互链路或流程验收配置。它不等同于完整生产科学就绪。

正式科学验收前应：

1. 将 `AGENTD_REQUIRE_DATABASES` 设置为 `true`。
2. 补齐所有 `required=true` 的版本化数据库资源。
3. 把目录中的 `SET_ON_SERVER` 替换为真实、可追溯的数据库版本。
4. 重新执行 `validate_catalog.py` 和 `verify.sh`。
5. 确认 `/ready` 在强制数据库模式下仍返回 `ready`。

## 12. 相关项目文件

- `deploy/docker-compose.yml`：完整容器编排、端口、环境变量和数据卷。
- `Dockerfile`：仓库根目录的镜像构建入口、科学工具和启动命令。
- `deploy/.env.example`：服务器环境变量模板。
- `deploy/server-config/production.json`：生产运行配置。
- `deploy/server-config/database-catalog.json`：数据库资源目录模板。
- `docs/operations/MCP_SERVER.md`：MCP、JWT 和智能体接入说明。
- `docs/operations/SERVER_INSTALL.md`：服务器安装、升级、回滚和运维说明。
- `deploy/DATA_LAYOUT.md`：服务器数据目录规范。
- `src/agentd/mcp_server.py`：MCP 工具和服务装配入口。
- `src/agentd/api.py`：REST API 路由。
- `src/agentd/auth.py`：JWT Bearer 鉴权中间件。
