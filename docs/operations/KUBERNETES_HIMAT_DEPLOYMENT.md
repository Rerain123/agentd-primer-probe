# OligoSmith 新服务器 Kubernetes 部署与验收

> 适用环境：Rancher 管理的 Kubernetes 集群 `maic-gpu-lab`
> 命名空间：`him-ws`
> 当前发布目标：OligoSmith `1.2.15`
> 更新日期：2026-10-12（Asia/Shanghai）

本文记录新服务器的实际接入方式、Codeup `test` 分支自动部署流程、Kubernetes 配置、
RustFS 发布配置和上线验收命令。生产密钥、JWT、RustFS AK/SK 不得写入本文或 Git。

## 1. 当前部署基线

| 项目 | 当前值 |
|---|---|
| 代码仓库 | `https://codeup.aliyun.com/6549a8ea3aaed849b035cf92/agent/oligosmith` |
| 自动部署分支 | `test` |
| 最近一次已推送应用提交（2026-09-14） | `cc6ff99c3e88fbcb2e422b63c6775145cb430753` |
| 应用版本 | `1.2.15` |
| 集群 | `maic-gpu-lab` |
| Namespace | `him-ws` |
| Deployment | `oligosmith`，1 个副本 |
| Service | `oligosmith`，`ClusterIP`，端口 `8000/TCP` |
| Service DNS | `oligosmith.him-ws.svc.cluster.local:8000` |
| Pod 标签 | `app.kubernetes.io/name=oligosmith` |
| 当前镜像名 | `harbor-core.base.svc.cluster.local/qcyz/oligosmith:latest` |
| HiMat 用户入口 | `https://himat.ws.scqcyz.cn` |
| HiMat MCP 地址 | `http://oligosmith.him-ws.svc.cluster.local:8000/mcp` |
| RustFS endpoint | `https://rustfs.ne.scqcyz.cn` |
| RustFS bucket/prefix | `oligosmith/data` |
| Secret | `oligosmith-secrets` |

`ClusterIP`、Pod IP 和所在节点都可能随重建变化。客户端必须使用 Service DNS，不得把
`10.43.*` Service IP 或 `10.42.*` Pod IP 写入配置。

2026-09-14 排障时曾观察到 Service IP `10.43.19.195`、Pod 端点
`10.42.1.239:8000`；这些值只用于比对当时日志，不属于部署配置。

旧地址 `oligosmith.ne.scqcyz.cn` 已停用。OligoSmith 不需要单独公网 Ingress；AI 通过
HiMat 后端在集群内访问 OligoSmith Service。

## 2. 实际访问链路

```text
用户浏览器
  -> https://himat.ws.scqcyz.cn
  -> himat-ws Ingress
  -> HiMat api Pod
  -> http://oligosmith.him-ws.svc.cluster.local:8000/mcp
  -> OligoSmith Deployment
  -> RustFS（终态报告附件）
```

当前 `himat-ws` Ingress 的 `/mcp` 路由指向 `sandbox:8000`，不是 OligoSmith。HiMat 对
OligoSmith 的调用使用后端保存的集群内 MCP 地址，因此不要用
`https://himat.ws.scqcyz.cn/mcp` 代替上述 Service DNS，也不要再为停用域名排查公网 404。

2026-09-14 的连通性检查结果：

- `api` Pod 访问 OligoSmith Service `/health` 返回 `200 OK`；
- `sandbox` Pod 访问同一 Service 和 Pod IP 均超时；
- Namespace 内未发现 `NetworkPolicy`；
- 当前调用者是 HiMat `api`，所以 `sandbox` 超时不阻断现行 MCP 链路。如果以后把调用者
  迁移到 `sandbox`，必须先继续排查 CNI、节点路由或平台侧隔离策略。

## 3. Kubernetes 配置

### 3.1 Deployment 与 Service

仓库当前没有完整的 Kubernetes Deployment/Service YAML。已纳入版本控制的 `Jenkinsfile` 只声明
流水线部署目标：

```text
cluster=prod
kubeConfigCredentialId=mataik8s
namespace=him-ws
workloadName=oligosmith
workloadType=deployment
target=latest
```

因此，最初导入 Rancher 的 YAML、环境变量、挂载和 Service 定义目前保存在集群对象中，不能仅靠
Git 仓库重建。修改前应从 Rancher 的“Show Configuration”或以下命令导出非敏感资源留档：

```bash
kubectl -n him-ws get deployment oligosmith -o yaml
kubectl -n him-ws get service oligosmith -o yaml
```

不要导出或提交 Secret 的值。后续应将脱敏、参数化的 Kubernetes 清单纳入版本控制；在此之前，
Rancher 中的 Deployment/Service 是运行配置的事实来源，本文只记录已确认字段。

先确认资源和端点：

```bash
kubectl -n him-ws get deployment oligosmith
kubectl -n him-ws get pods -l app.kubernetes.io/name=oligosmith -o wide
kubectl -n him-ws get svc oligosmith -o wide
kubectl -n him-ws get endpointslice \
  -l kubernetes.io/service-name=oligosmith -o wide
```

期望 Deployment 为 `1/1` Ready，Service 端口为 `8000`，EndpointSlice 中存在一个就绪的
Pod IP 和端口 `8000`。

### 3.2 MCP 主机校验

HiMat 请求的 HTTP `Host` 是 `oligosmith.him-ws.svc.cluster.local:8000`，端口属于 Host
的一部分。`MCP_ALLOWED_HOSTS` 缺少这一精确值时，MCP initialize 会返回：

```text
421 Misdirected Request Invalid Host header
```

当前环境推荐值如下；保留实际仍在使用的短名称即可，不再加入停用的公网域名：

```dotenv
MCP_PUBLIC_BASE_URL=http://oligosmith.him-ws.svc.cluster.local:8000
MCP_ALLOWED_HOSTS=oligosmith,oligosmith:8000,oligosmith.him-ws,oligosmith.him-ws:8000,oligosmith.him-ws.svc,oligosmith.him-ws.svc:8000,oligosmith.him-ws.svc.cluster.local,oligosmith.him-ws.svc.cluster.local:8000,127.0.0.1:8000,127.0.0.1:*
MCP_ALLOWED_ORIGINS=http://oligosmith.him-ws.svc.cluster.local:8000,https://himat.ws.scqcyz.cn
```

修改 Deployment 环境变量后等待滚动更新，再重新验证版本和 `tools/list`。当前报告附件依赖
RustFS 公网 URL；停用 OligoSmith 公网域名后，不应依赖 Agent D 本地签名 URL 作为浏览器
下载地址。

### 3.3 非敏感环境变量

当前 Deployment 至少应核对：

```dotenv
AGENTD_CONFIG=/config/production.json
AGENTD_DATABASE_CATALOG=/config/database-catalog.json
AGENTD_STATE_DIR=/data/jobs
AGENTD_RUNTIME_PROFILE=server-production
AGENTD_REQUIRE_DATABASES=false
BLASTDB=/data/blastdb

MCP_AUTH_REQUIRED=true
MCP_JWT_ALGORITHM=HS256
MCP_JWT_REQUIRE_EXP=true
MCP_PUBLIC_WF01_ARTIFACTS=true
MCP_MAX_TOOL_RESULT_BYTES=65536
MCP_WF02_SYNC_WAIT_SECONDS=235
WF02_BACKGROUND_WORKERS=1
WF02_ATTACHMENT_URL_TTL_SECONDS=86400

HIMAT_FILE_DOWNLOAD_BASE_URL=https://himat.ws.scqcyz.cn/api/files/download
HIMAT_FILE_DOWNLOAD_TIMEOUT_SECONDS=300
AGENTD_INPUT_FILE_MAX_BYTES=1073741824

RUSTFS_ENABLED=true
RUSTFS_ENDPOINT_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_BUCKET=oligosmith
RUSTFS_REGION=us-east-1
RUSTFS_OBJECT_PREFIX=data
RUSTFS_PUBLIC_BASE_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_VERIFY_TLS=true
RUSTFS_CONNECT_TIMEOUT_SECONDS=5
RUSTFS_READ_TIMEOUT_SECONDS=30
TZ=Asia/Shanghai
```

查看实际值时不要打印 Secret：

```bash
kubectl -n him-ws set env deployment/oligosmith --list \
  | grep -vE 'SECRET|ACCESS_KEY|TOKEN'
```

### 3.4 Secret

Deployment 通过 `envFrom` 引用 `him-ws/oligosmith-secrets`。Secret 必须包含：

```text
MCP_JWT_SECRET
RUSTFS_ACCESS_KEY_ID
RUSTFS_SECRET_ACCESS_KEY
```

只检查键名，不解码或输出值：

```bash
kubectl -n him-ws get secret oligosmith-secrets \
  -o go-template='{{range $key,$value := .data}}{{printf "%s\n" $key}}{{end}}'
```

RustFS AK/SK 必须属于仍然存在的专用账号，并有 `oligosmith/data/*` 的写入、读取、HEAD 和
删除验收对象权限。不要使用控制台会话令牌或管理员密钥代替 S3 Access Key。

## 4. RustFS 附件发布

当前存储位置：

```text
https://rustfs.ne.scqcyz.cn/oligosmith/data/<percent-encoded fileName>
```

RustFS 控制台：`https://rustfs.ne.scqcyz.cn/rustfs/console/browser/`。当前 `oligosmith` bucket
设置为公网只读；应用专用 AK/SK 负责写入和清理。公网只读只允许下载对象，不应允许匿名上传、
覆盖或删除。

应用不会把上述地址写死在终态契约中。`fileUrl` 由
`RUSTFS_PUBLIC_BASE_URL`、`RUSTFS_BUCKET` 和 `RUSTFS_OBJECT_PREFIX` 组合生成；
`primer_design_output` 接受后端生成的 HTTP(S) 完整 URL。

## 5. Codeup `test` 分支自动部署

### 5.1 发布前

1. 确认版本号、Schema、Deployment 配置和变更记录一致。
2. 先运行受影响测试，再运行完整测试套件。
3. 检查 `git status --short`，不要提交生产 Secret、`.env`、报告、数据库或用户测试数据。
4. 使用不可变版本标签或镜像 digest 最可靠。当前流水线仍使用 `latest` 时，必须以容器内
   `agentd.__version__` 和实际 `imageID` 验证部署结果。

### 5.2 推送触发

```bash
git status --short
git add <本次明确修改的文件>
git commit -m "<说明>"
git push codeup HEAD:test
```

2026-09-14 已推送：

```text
cc6ff99c3e88fbcb2e422b63c6775145cb430753
fix: allow configured artifact URLs
```

推送成功只表示 Codeup 已接收提交并触发流水线，不等于服务器已更新完成。必须继续执行第 6 节
验收。若远端 `test` 已前进，禁止强推覆盖；先拉取并确认差异。

## 6. 部署后验收

以下命令均可在 Rancher 的同一个 kubectl 终端依次执行。

### 6.1 滚动状态、版本和镜像摘要

```bash
kubectl -n him-ws rollout status deployment/oligosmith --timeout=180s

kubectl -n him-ws exec deployment/oligosmith -- \
  python -c 'import agentd; print(agentd.__version__)'

kubectl -n him-ws get pods \
  -l app.kubernetes.io/name=oligosmith \
  -o custom-columns='NAME:.metadata.name,CREATED:.metadata.creationTimestamp,IMAGE:.spec.containers[0].image,IMAGE_ID:.status.containerStatuses[0].imageID'
```

本次目标版本必须输出 `1.2.15`。镜像名使用 `latest` 时，镜像名本身不能证明部署完成，必须同时
记录 `imageID`。

### 6.2 健康与就绪

```bash
kubectl -n him-ws port-forward svc/oligosmith 18020:8000 \
  >/tmp/oligosmith-portforward.log 2>&1 &
PF_PID=$!
sleep 2

curl -i http://127.0.0.1:18020/health
curl -i http://127.0.0.1:18020/ready

kill "$PF_PID" 2>/dev/null || true
wait "$PF_PID" 2>/dev/null || true
cat /tmp/oligosmith-portforward.log
```

`/health` 期望 `200`。当前 `AGENTD_REQUIRE_DATABASES=false` 时，`/ready` 可以在数据库不完整的
情况下返回 `200`，但响应必须如实显示 `databases_ready=false` 和 `required_missing`。

### 6.3 HiMat API 到 OligoSmith

```bash
kubectl -n him-ws exec deployment/api -- \
  wget -qO- http://oligosmith.him-ws.svc.cluster.local:8000/health
```

期望返回：

```json
{"status":"ok","service":"agentd-primer-probe","phase":3}
```

### 6.4 MCP `tools/list`

```bash
kubectl -n him-ws port-forward svc/oligosmith 18020:8000 \
  >/tmp/oligo-pf.log 2>&1 &
PF_PID=$!
sleep 2

TOKEN="$(kubectl -n him-ws exec deployment/oligosmith -- \
python -c 'import os,jwt; from datetime import datetime,timedelta,timezone; s=os.environ.get("MCP_JWT_SECRET") or os.environ.get("JWT_SECRET"); now=datetime.now(timezone.utc); print(jwt.encode({"sub":"kubectl-check","iat":now,"exp":now+timedelta(minutes=10)},s,algorithm=os.environ.get("MCP_JWT_ALGORITHM","HS256")))')"

curl -sS -o /tmp/oligo-tools.json -w 'HTTP %{http_code}\n' \
  -X POST 'http://127.0.0.1:18020/mcp/wf01/' \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

grep -oE '"name"[[:space:]]*:[[:space:]]*"[^"]+"' /tmp/oligo-tools.json

unset TOKEN
kill "$PF_PID" 2>/dev/null || true
wait "$PF_PID" 2>/dev/null || true
```

HTTP 必须为 `200`。WF-01 列表应包含 `create_wf01_batch`、`run_wf01_until_blocked`、
`get_wf01_report`、`get_wf01_export` 等受限入口工具。

### 6.5 RustFS 写入验收

```bash
kubectl -n him-ws exec deployment/oligosmith -- python -c \
'import json; from agentd.object_storage import RustFSArtifactPublisher; print(json.dumps(RustFSArtifactPublisher.from_environment().probe_write(), ensure_ascii=False, indent=2))'
```

期望关键字段：

```json
{
  "status": "ready",
  "write_verified": true,
  "test_object_deleted": true,
  "available": true,
  "bucket": "oligosmith",
  "object_prefix": "data"
}
```

### 6.6 终态 Schema

```bash
kubectl -n him-ws exec deployment/oligosmith -- \
python -c 'import json; from agentd.config import PROJECT_ROOT; s=json.loads((PROJECT_ROOT/"schemas/primer-design-output.schema.json").read_text()); print(s["$defs"]["attachment"]["properties"]["fileUrl"]["oneOf"][0]["pattern"])'
```

`1.2.15` 应输出：

```text
^https?://[^\s]+$
```

最后在 HiMat 中发起一次最小 WF-01，确认：工具能被发现、报告写入 RustFS、终态 JSON 的
`attachments[].fileUrl` 指向 `oligosmith/data`，并正常显示报告卡片。

## 7. 已知错误与定位

| 现象 | 结论 | 处理 |
|---|---|---|
| 公网旧域名 `/health` 或 MCP 返回 Nginx `404` | 旧域名已停用且无 OligoSmith Ingress | 使用 HiMat 内部 Service DNS，不再排查旧域名 |
| MCP initialize 返回 `421 Invalid Host header` | `MCP_ALLOWED_HOSTS` 缺少带端口的实际 Host | 加入 `oligosmith.him-ws.svc.cluster.local:8000` 并滚动更新 |
| `api -> oligosmith` 返回 `200`，`sandbox -> oligosmith` 超时 | 当前 API 链路可用，sandbox 网络另有问题 | 现行调用保持 API；迁移前排查 CNI/节点策略 |
| RustFS `HeadBucket 403 Forbidden` | 桶不存在、账号无 bucket 权限或凭据不匹配 | 核对 bucket 和专用账号策略 |
| RustFS `InvalidAccessKeyId` | Secret 中 AK 不属于当前 RustFS | 更新 `oligosmith-secrets` 中 AK/SK 后滚动更新 |
| RustFS 写入成功但卡片报 `fileUrl` Schema 错误 | 运行版本仍是旧的固定地址契约 | 确认容器版本 `1.2.15` 和 Schema 模式 |
| 镜像显示 `latest` 但版本仍旧 | 流水线未完成、Pod 未重建或节点使用旧镜像 | 检查流水线、Pod 创建时间、`imageID` 和镜像拉取策略 |

## 8. 回滚

先查看历史：

```bash
kubectl -n him-ws rollout history deployment/oligosmith
```

平台若为每次发布写入不可变镜像标签或 digest，可回滚到已确认的 revision：

```bash
kubectl -n him-ws rollout undo deployment/oligosmith --to-revision=<revision>
kubectl -n him-ws rollout status deployment/oligosmith --timeout=180s
```

当前若所有 revision 都只写 `:latest`，`rollout undo` 不能保证拉回旧镜像。正式发布应让流水线写入
`1.2.15` 等不可变标签或镜像 digest，并在变更记录中保存 commit、tag、digest 和 Kubernetes
revision 的对应关系。回滚后必须重新执行第 6 节验收。

## 9. 当前科学就绪边界

2026-10-12 实时复核核心工具就绪，`missing_tools=[]`。完整 nt 已只读接入，
`cross:default:blast` 与 `nt:default:blast` 均可用；五宿主库接入后当前数据库配置：

```text
AGENTD_REQUIRE_DATABASES=false
databases_ready=true
required_missing=[]
```

本地 nt 读取与单卷实际检索已通过，完整 nt 的短引物检索及真实工作流仍待验收。
五宿主库已通过应用内读取和逐库短序列检索；内标离线序列、完整 nt 性能及端到端工作流
仍待补齐和验收，不能仅凭数据库就绪认定科学流程完整。
补齐带真实版本号的必需数据库后，应改为 `AGENTD_REQUIRE_DATABASES=true` 并重新执行 `/ready`
和端到端工作流验收。

### 9.1 本地离线数据库准备（2026-09-29，尚未在集群执行）

用户已确认旧部署的独立远程 BLAST MCP 被删除，甲方服务器只有内网，要求下载数据库后在
本地查询。本次交付不再依赖或尝试恢复该远程服务。工程交接文档中旧远程服务的地址只属于
历史记录，不是当前可用依赖。

原始 P2 设计写明 NT 全库检索；现行实现接受本地 `nt/cross` 资源，WF-02 子任务也通过自身
快照解析背景资源。支持自建 `cross` 目录不等于已经证明任意子集与全库覆盖等价；当前目录
解析不验证物种覆盖完整性。未另行明确缩小检索范围时，按原设计的完整 nt 背景准备离线库，
并为需要宿主排除的物种单独准备宿主基因组库。

纯内网交付路线为：可联网的准备环境下载和校验 → 保存数据库版本、官方元数据、校验记录及
传输用文件校验清单 → 经允许的文件传输渠道导入内网 PVC → 内网复核文件与 BLAST 可读性 →
只读挂载并执行真实 WF-01/WF-02 验收。用户已确认当前 Rancher 属于己方服务器，本阶段在
该集群下载并运行，之后将数据库与部署配置迁移到甲方内网服务器。
本节下载 Job 必须能访问 NCBI；网络实际可达性以 Job 日志为准。当前集群完成下载后应先
接入 OligoSmith 并验收两条工作流，再做离线交付，不直接在甲方纯内网执行下载 Job。

本次用户回传的 Pod 清单确认 OligoSmith 只有 `/data/jobs`、`/config`、`/tmp` 挂载；
抗原项目的 nr 和 IEDB 均为蛋白库，不能用于当前 WF-01/WF-02 的 `blastn` 核酸检索。

2026-09-29 直接读取 [NCBI nt 元数据](https://ftp.ncbi.nlm.nih.gov/blast/db/nt-nucl-metadata.json)：

| 字段 | 官方值 |
|---|---|
| dbname / dbtype | `nt` / `Nucleotide` |
| last-updated | `2026-09-15T00:00:00` |
| number-of-volumes | 383 |
| number-of-sequences | 131,305,035 |
| bytes-total-compressed | 1,001,214,994,022 bytes（932.45 GiB） |
| bytes-total | 1,176,534,434,870 bytes（1,095.73 GiB） |

压缩包与数据库文件合计约 1.98 TiB。建议使用独立 3 TiB PVC，为解压和重试保留余量；
这是联网下载/解压阶段的容量建议，不是 NCBI 强制要求。若内网仅导入解压后库文件，
无需同时保留全部压缩包，可另按实际数据库、宿主库及更新余量规划运行存储。
PVC 容量不证明 Ceph 存储池有足够物理余量，
副本/纠删码的物理消耗取决于集群配置。

[下载清单](../../deploy/kubernetes/nt-download.yaml) 创建 `him-ws/oligosmith-blast-nt`
（`ceph-rbd-retain`、RWO、3 TiB）和 `oligosmith-nt-download-r01` Job，使用官方
`ncbi/blast:2.17.0` 的已核实 digest。需集群可拉取 Docker Hub 镜像并访问 NCBI HTTPS；
若需 Harbor 镜像，先按同一 digest 导入，再替换镜像地址。

```bash
kubectl -n him-ws get resourcequota,limitrange
kubectl apply -f deploy/kubernetes/nt-download.yaml
kubectl -n him-ws get pvc oligosmith-blast-nt
kubectl -n him-ws get pods -l job-name=oligosmith-nt-download-r01 -o wide
kubectl -n him-ws logs -f job/oligosmith-nt-download-r01
```

在 Rancher kubectl 终端没有仓库文件时，将清单内容粘贴到 `kubectl apply -f - <<'YAML'`
与结尾 `YAML` 之间。Job 运行不依赖浏览器终端持续连接；关闭日志跟随不会取消下载。

Job 使用 NCBI 官方 `update_blastdb.pl --source ncbi --decompress nt`，执行下载校验并解压，
成功解压后清理对应压缩包，保留校验文件。随后执行 `blastdbcmd -info` 和默认
`blastdbcheck`（默认抽样检查，不是每条序列全检）。保存下载前后官方元数据；若检测到上游
版本变化，拒绝生成完成标记，保留已有数据待处理，不混用到应用。Job 最多重试两次，
总期限 14 天；重试复用已下载的完整分卷，未完成分卷可能重新下载。

PVC 内库前缀为 `nt-download-20260929-r01/raw/nt`。Job 内对应
`/data/nt-download-20260929-r01/raw/nt`。`DOWNLOAD_COMPLETE` 只表示下载和上述检查成功，
不表示 WF-01/WF-02 已通过真实检索验收。接入阶段再将卷只读挂到 OligoSmith，配置有真实版本
的 `cross:default:blast`（或 `nt:default:blast`）；宿主专库是否齐备也须单独确认，不能只因
nt 下载完成就开启完整就绪。RWO 卷挂载需考虑下载 Job 与应用所在节点，完成后先确认下载
Pod 已终止，再交接给应用。

### 9.2 nt 下载恢复（2026-10-09，r02 待用户执行）

用户回传确认 r01 为 `BackoffLimitExceeded`，3 个失败 Pod 已不存在；PVC 仍 Bound。
保留约 90 GiB、35 个压缩分卷、0 个已解压序列分卷，无完成标记。初始 nt 单库元数据记录
2026-09-15 / 383 卷，重试时记录为 2026-09-24 / 390 卷，确实发生版本变化。
后续重试会被 r01 的版本门禁阻断；首次失败原因没有日志，不能归因于版本门禁。

[恢复清单](../../deploy/kubernetes/nt-recover.yaml) 创建 r02 Job，复用原 PVC 和 raw 压缩包目录。
先检查已有包与保存的 MD5；校验失败时将对应 MD5 移入本次记录目录，使官方工具重新获取该包。
以官方下载器使用的总元数据清单中的 nt 条目为基准，每次尝试记录 JSON 规范化后的版本，
允许下一次尝试重新核对新版本，而不是永久锁在第一次记录。官方工具获取当前归档后，
逐卷重新获取官方 MD5 并比对全部当前清单中的压缩包；前后元数据一致才允许解压。
源仍为会更新的官方目录，不承诺固定历史版本永久可下载；若更新导致校验失败，保留证据再重试。

每次尝试日志保存至 `nt-download-20260929-r01/recovery-r02/<Pod名>/run.log`，元数据、MD5、
数据库检查日志和完成标记在同一目录。解压进入本次独立 `db` 目录，完成后由
`database-prefix.txt` 指明真实库前缀，不再使用 r01 预设的 raw/nt 路径。
保留压缩包，解压前检查数据库预计容量加 50 GiB 余量；旧尝试解压残留不足时停止，不能自动
清理后冒充成功。完整包可复用，更新或不完整的包可能重新下载，不承诺分卷内断点续传。

先删除已创建的只读诊断 Pod `oligosmith-nt-inspect-r01`，释放 RWO 挂载，再应用 r02；
不得删除 PVC。旧 r01 Job 已失败，不再运行。r02 尚未在集群执行和验收。

### 9.3 现有 nt 解压目录复核（2026-10-12，r04 已通过）

用户回传确认 r02 三个 Pod 均失败。首次 `oligosmith-nt-download-r02-5c6r4`
已生成 399 卷的数据库目录，但 `blastdbcheck` 在所有卷的 MetaData 测试抛出异常。
后两次尝试在下载工具的空间预检阶段退出：要求约 1.23 TB，剩余约 960 GB。
这不构成删除现有数据、重新下载或扩容的依据。

只读诊断 r03 显式设置 `BLASTDB` 为首次解压的 `db` 目录后，`blastdbcmd -info`
成功读取 BLASTDB v5：132,004,583 条序列、4,766,986,716,743 bases，库日期
2026-10-06。taxonomy 文件已存在；`nt.000` 的 `blastdbcheck -verbosity 3`
返回 0。现象支持 taxonomy 搜索路径影响原检查，但还没有所有卷通过的证据。

[现有数据库校验 Job](../../deploy/kubernetes/nt-verify-existing.yaml) 使用同一工具镜像，
显式设置 `BLASTDB`，比对首次下载前后元数据与分卷数，执行所有卷的默认抽样检查。
仅在原 PVC 新建 `nt-download-20260929-r01/validation-r04/<Pod名>/` 保存检查证据；
不调用下载或解压工具，不修改数据库文件。PVC 以可写方式挂载以保存这些证据。
无自动重试，最长 24 小时；日志跟随或 Rancher 终端断开不影响 Job。
`VALIDATION_COMPLETE` 仅在所有命令成功后生成，仍不代表 WF-01/WF-02 通过验收。

实际待接入的库前缀为
`/data/nt-download-20260929-r01/recovery-r02/oligosmith-nt-download-r02-5c6r4/db/nt`。
应用挂载路径改变时必须同步设置 `BLASTDB`，使其包含 taxonomy 所在目录。
用户回传 r04 为 `Complete 1/1`，运行约 21 分钟：399 个分卷与 1 个 alias
全部 `Result=SUCCESS`，`VERIFY_EXIT=0`，完成标记时间为
`2026-10-12T01:13:22Z`（北京时间 09:13:22）。现有数据库已通过上述抽样检查；
证据来自用户执行命令后的回传，本地没有直接连接集群。

下一步核对实际 Deployment、ConfigMap 中的数据库目录和应用镜像内的 BLAST 工具，
将上述 `db` 子目录只读挂到应用，并同时配置 taxonomy 搜索路径与真实数据库版本。
必须保留原有资源目录条目，分别检查 `nt:default:blast` 与 `cross:default:blast`，
避免 WF-01/WF-02 优先命中旧的 nt 配置。此时尚未完成应用接入或工作流真实检索验收；
nt 背景库也不自动替代代码单独解析的宿主库。

### 9.4 接入已验证 nt（2026-10-12，补丁已应用）

用户确认实际 Deployment 为 1 副本、`Recreate`，无 nodeSelector/affinity；现有
jobs、server-config、tmp 三个挂载不变。应用镜像内为 BLAST 2.16.0+，r04 校验镜像
为 2.17.0+，因此需要在应用中重新确认数据库可读性，不能将校验镜像的结果直接视为
应用运行验收。应用限制为 4 CPU、8 GiB；完整 nt 检索的耗时与内存还须实际测量。

[目录补丁](../../deploy/kubernetes/nt-catalog.patch.yaml) 仅 merge-patch ConfigMap
`oligosmith-server-config` 的 `database-catalog.json`，保留用户回传的三个宿主库条目。
同时登记 `nt:default:blast`、`cross:default:blast`，库前缀统一为
`/data/blastdb/nt-20261006/nt`。版本 `nt-20261006` 是按实际库日期命名的本地版本标识；
原始官方元数据与验证记录仍保存在 PVC 中。

[部署补丁](../../deploy/kubernetes/nt-deployment.patch.yaml) 使用 strategic merge，
只添加 nt 的只读 subPath 挂载和 BLAST 环境变量。`BLASTDB` 包含 taxonomy 所在目录。
不固定应用节点，由调度器处理两个 RWO PVC 的挂载；所有下载和校验任务须已结束。
原 `Recreate` 会造成短暂停机；RWO 卷转移节点可能需要等待卸载和重新挂载。

操作前保存现有 Deployment 与 ConfigMap，先应用目录补丁，再应用部署补丁触发重建。
保持 `AGENTD_REQUIRE_DATABASES=false`，因为猪宿主库仍是占位配置。

用户提供集群连接文件并授权直接配置后，已实时连接集群复核 r04 完成日志，备份原始
Deployment 和 ConfigMap，通过两项补丁的服务端 dry-run，再依次应用目录与部署补丁。
备份在操作机器临时目录 `oligosmith-before-nt-7c766217cffa447ea5652477e2daa61c/`，
包含 `deployment.json`、`configmap.json`；连接凭证不纳入仓库。
Deployment 成功完成重建，新 Pod `oligosmith-77dcdf6c54-pmnvm` 位于 `k8s-a100-02`，
状态 `1/1 Running`、0 次重启。

应用内验证结果：

- `blastdbcmd -info` 正常读取 132,004,583 条序列和 4,766,986,716,743 bases。
- 目录中的 nt 和 cross 均 `available=true`、版本 `nt-20261006`。
- WF-01 与 WF-02 冻结子流程各自的背景资源解析器均返回 `RESOLVED`，指向上述 nt 前缀。
- 从 `nt.000` 提取 `X17276.1` 的前 120 bp，在该卷实际运行 `blastn -task megablast`，
  得到 120 bp、100% 一致的匹配，taxonomy 返回 taxid 9646、`Ailuropoda melanoleuca`。
  这验证了应用内工具的单卷搜索与 taxonomy 读取，不是全 nt 性能或引物短序列验收。
- `/ready` 返回 `status=ready`、`core_ready=true`、`missing_tools=[]`，但
  `databases_ready=false`；唯一必需缺失项是 `host:pig:blast`。
- `BLAST_SEARCH_SERVICE_URL` 未配置，本次验证不依赖远程 BLAST 服务。

待办仍为补齐所需宿主专库，执行 WF-01/WF-02 的完整实际检索并测量全 nt 检索的资源与耗时。
当前完成的是 nt 挂载、目录配置、背景解析与单卷检索验证，不宣称两条工作流已完整验收。

### 9.5 项目补齐清单（2026-10-12 审查）

本次审查对照现行工作流文档、实际资源解析/P2/P5 代码与运行中的 1.2.15 服务。
以下“待验证”不等于已经发现功能失败；节点单元测试通过也不等于本集群的全库验收通过。

| 优先级 | 补齐事项 | 已确认事实 | 完成标准 |
|---|---|---|---|
| P0 | 五类本地宿主核酸库 | config/species.json 与原始 P2 设计列出猪、牛、鸡、绵羊、山羊；实际目录只有猪/牛/鸡占位条目，绵羊和山羊尚未登记。仅猪被标为 required，不代表项目范围只有猪。WF-02 会把宿主传给子任务，nt 不自动代替专库 | 按五物种范围准备版本化参考库，分别登记 host:pig:blast、host:cattle:blast、host:chicken:blast、host:sheep:blast、host:goat:blast，并验证输入物种映射与实际宿主检索 |
| P0 | P5 内标离线序列 | 猪/牛/鸡的 ACTB、GAPDH、18S rRNA 共 9 个 internal 条目均未登记；有效配置 Entrez enabled=true，本地缺序列时联网获取 | 按交付物种准备基因参考及同源序列，登记 internal:<species>:<gene>:sequences；当前最低 5 条要求应使用真实序列，不能复制凑数。保存来源、版本、校验；完成后关闭在线获取并验收 |
| P0 | 完整 nt 短引物检索与性能 | 已通过的是全卷抽样检查和单卷 megablast；实际 P2 使用 blastn-short、每次 timeout=600、max_target_seqs=100，未显式设置 num_threads；WF-02 冻结实现亦如此 | 用真实候选批量检索完整 nt，测量耗时、内存、命中截断与证据体积，覆盖超时/恢复；需要优化时不得只改顶层 WF-01 而遗漏 WF-02 的版本绑定 |
| P0 | WF-01/WF-02 集群端到端验收 | 目录解析通过不等于完整任务通过；WF-02 默认内标需要上述离线资源 | WF-01 单模板、多模板；WF-02 多靶标、含/不含内标；阴性/近缘/宿主风险、无候选、后台恢复、报告附件与 HiMat 卡片均保留真实证据 |
| P1 | 科学策略与交付状态口径 | M4 即使 technical_search_complete=true 仍返回 search_complete=false 和 DEC_01_DEC_02_POLICY_APPROVAL_PENDING；现行文档明确保留待实验校准状态 | 由科学负责人明确接受当前科研排序口径，或完成策略确认与实验校准后版本化更新；不得为得到 SUCCEEDED 直接删除门禁 |
| P1 | 甲方内网依赖与发布包 | RustFS/HiMat 当前使用己方域名；仓库只有 nt 增量补丁，没有可从零重建当前应用的完整 K8s 清单；镜像使用 latest | 提供完整 Deployment/Service/ConfigMap/PVC 与 Secret 引用模板、固定镜像 digest、内部 DNS/附件地址、数据库清单与校验、导入和回滚流程，并在无外网环境验收 |
| P1 | 运维与严格就绪 | AGENTD_REQUIRE_DATABASES=false，/ready=ready 同时 databases_ready=false；jobs PVC 20 GiB、tmp emptyDir 512 MiB、应用 4 CPU/8 GiB | 依业务范围补齐必需库后开启严格就绪；据真实负载确定 CPU/内存/临时空间与证据保留，补充任务/报告备份恢复及数据库升级切换演练 |
| P1 | 文档与可重复测试环境 | 工程交接的旧 Compose/远程 BLAST 章节仍使用“当前”措辞；WF-02 台账代码节点已 VERIFIED，服务器验收仍待完成 | 明确标注历史环境，区分实现、单测、真实集群验收；补完整 dev/api 依赖与固定测试环境，保存新的验收记录 |

已有工具无需按缺失项重复安装：集群包含 Primer3 2.3.1、BLAST 2.16.0+、MAFFT、
ViennaRNA 2.7.2。P6 通过 Python RNA 绑定加载 DNA 参数，实际 `available=true`；
没有 RNAfold CLI 不表示当前 P6 不可用。nr 和 IEDB 是蛋白库，不属于本次核酸检索的必补项。
有用户输入 FASTA 时，当前靶标检索可使用不可变输入快照；是否补病原参考/覆盖序列集应按
业务覆盖范围决定，不要求为运行主链路先下载所有病原库。

宿主排除库与内标支持范围必须分开：`config/species.json` 的宿主基因组清单为五物种，
当前 P5 默认内标配置及 Entrez 物种映射仅覆盖猪、牛、鸡。补齐绵羊/山羊基因组不自动
完成这两种动物的内标设计；若要求其完整内标能力，需另行补齐基因优先级、参考序列、
同源序列、物种映射与对应验收。宿主参考版本以仓库配置为计划基线，下载前仍需核对官方
assembly 身份和实际文件，不把配置中的版本文字视为已下载证据。

专家原文复核依据（2026-10-12）：

- P2 设计依据（原始资料不随 GitHub 分发）明确列出
  猪、牛、鸡、羊（Ovis aries，绵羊）、山羊五种宿主，第 3 节 `host_species` 输入也列出
  pig/cattle/chicken/sheep/goat。五物种范围来自专家原文，不是仅凭 species.json 推断。
- 专家原表的参考版本分别为 Sscrofa11.1 / GCF_000003025.6、
  ARS-UCD2.0 / GCF_002263795.3、GRCg7b / GCF_016699485.2、
  ARS-UI_Ramb_v2.0 / GCF_016772045.2、ARS1.2 / GCF_001704415.2。
  这是原文版本清单，尚不是对 NCBI 当前文件和命名的独立核实。
- P5 设计依据（原始资料不随 GitHub 分发）
  明确默认内标 RefSeq 仅覆盖猪/牛/鸡，且直接注明 P2 支持的 sheep/goat 无内置内标编号。
- P5 原表将鸡 18S rRNA 的 NR_046237 标为“物种待确认”，猪 ACTB 原编号 NM_213978
  与现行配置 NM_001444420.1 也不同。下载内标序列时须逐项核对物种、基因和版本来源，
  不能把原稿或配置中的 accession 直接视为已验证数据。

本次验证：仓库结构、快照完整性、特异性边界、WF-01 M4 共 49 项测试通过。
扩展 WF-02/P5 测试未全通过：47 个 WF-02 用例中 5 个 PDF 用例受本机缺 reportlab 阻断，
P5/P2 测试模块因本机缺 pytest 未加载；不能据此断言集群 PDF 或 P5 存在同样故障。
本次审查没有运行长时间全 nt 设计任务，也没有修改集群科学策略。

### 9.6 五宿主下载准备与空间预算（2026-10-12，下载已启动）

已直接读取 NCBI 官方 FTP 的 assembly report、assembly stats、MD5 清单和 genomic.fna.gz
响应头。确定物种 taxid、文件长度和校验值后，写入
[固定下载清单](../../deploy/kubernetes/host-genomes-20261012.tsv)。TSV 字段依次为
species、taxid、assembly accession、文件前缀、MD5、压缩字节数、总碱基数、FTP 相对目录。

| 物种 | 官方目录前缀 | genomic.fna.gz 字节数 | assembly 总碱基数 |
|---|---|---:|---:|
| 猪 | GCF_000003025.6_Sscrofa11.1 | 780,761,875 | 2,501,912,388 |
| 牛 | GCF_002263795.3_ARS-UCD2.0 | 854,312,987 | 2,770,686,120 |
| 鸡 | GCF_016699485.2_bGalGal1.mat.broiler.GRCg7b | 327,035,795 | 1,053,332,251 |
| 绵羊 | GCF_016772045.2_ARS-UI_Ramb_v3.0 | 834,286,865 | 2,654,063,983 |
| 山羊 | GCF_001704415.2_ARS1.2 | 892,212,880 | 2,922,617,086 |

压缩包合计 3,688,610,402 bytes（3.435 GiB），总碱基约 119 亿。
FASTA 解压文本预计约 11.3 GiB（碱基字符为 11.085 GiB，另加换行和标题）。
索引、构建中间文件、taxonomy、日志另预留 15–25 GiB 是保守工程预算，尚非建库实测值。
单版本构建建议至少 50 GiB；当前准备独立 100 GiB 的 `oligosmith-blast-hosts` PVC，
用于保留源文件、运行索引和后续更新余量。少量内标参考/同源 FASTA 可另预留 1 GiB，
无需为它们下载全物种转录组。以上为 PVC 逻辑容量，不代表 Ceph 存储池物理可分配量已核实。

专家原文绵羊一行的名称与 accession 不一致：官方 `GCF_016772045.2` 对应
ARS-UI_Ramb_v3.0，ARS-UI_Ramb_v2.0 对应 `.1`。本次保留专家指定 accession `.2`，
使用已核实的 v3.0 官方目录，不根据文档简称拼接下载链接。

[下载/建库脚本](../../deploy/kubernetes/build-host-genomes.sh)和
[PVC/Job 清单](../../deploy/kubernetes/host-genomes-download.yaml)已准备，且服务端 dry-run
及 Bash 语法检查通过。随后用户明确要求直接在服务器下载，已创建实际 PVC 和 Job，
启动状态如下；尚未取得五库全部建库校验完成的证据。
脚本逐物种下载完整 genomic.fna.gz，核对固定 MD5 与官方当前 MD5、物种 taxid 和 accession，
解压后用 makeblastdb 建立带序列 ID/taxid 的核酸库，检查入库总碱基数、blastdbcmd 和
blastdbcheck，并保存 SHA-256 与每库完成标记。失败不会自动把未通过库登记为可用。

Job 使用 2 CPU/8 GiB 上限，逐库执行，最长 48 小时且不自动重试；完成前不修改应用配置。
为避免再下载 taxonomy，Job 在 k8s-a100-02 同节点只读挂载现有 nt，从中复制三项 taxonomy
文件到新宿主 PVC。后续应用只读挂载宿主 PVC，配置五个 host 条目并追加 taxonomy 搜索路径，
再进行实际 2.16.0 应用工具的检索验收；这一步需要在建库成功后实施。

内标编号核查：NCBI EFetch 确认现行配置中猪 ACTB/GAPDH/18S、牛 ACTB/GAPDH/18S、
鸡 ACTB/GAPDH 的物种和基因身份匹配。鸡候选 `NR_046237` 实际解析为
`NR_046237.3`、Rattus norvegicus、taxid 10116，必须排除，不能用于鸡。
官方鸡 18S GenBank 候选 `AF173612.1` 可作为后续筛选起点，但并非 RefSeq，仍需核对
与基因组的对应、完整性和同源样本覆盖；不能直接把一条记录复制为五条满足数量要求。

主要官方核查入口：
[五物种 FTP 根目录](https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/)、
[绵羊 .2 assembly](https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_016772045.2/)、
[错误鸡编号的实际记录](https://www.ncbi.nlm.nih.gov/nuccore/NR_046237.3)、
[鸡 18S 候选记录](https://www.ncbi.nlm.nih.gov/nuccore/AF173612.1)。

2026-10-12 09:54（Asia/Shanghai）实际启动记录：

- ConfigMap：`oligosmith-host-build-r01`。
- PVC：`oligosmith-blast-hosts`，100 GiB，Bound，PV 为
  `pvc-61c4f026-c89f-466d-a6f0-b433a06f36a6`。
- Job：`oligosmith-host-build-r01`；Pod：`oligosmith-host-build-r01-vp8cn`，
  节点 `k8s-a100-02`，启动后 Running。
- 持久化目录：`/data/hosts-20261012-r01`；日志 `build.log`。
- 首次进度确认猪基因组 `.part` 文件约 129 MiB，下载正在进行。首轮 MD5 检查提示
  原归档尚不存在是进入下载分支的预期现象，不表示新下载包已损坏。
- 原 OligoSmith Pod 保持 `1/1 Running`。本次没有修改应用配置或开启严格数据库就绪。
- 后续须检查 Job Complete、`HOST_DATABASES_COMPLETE`、`HOST_BUILD_EXIT=0` 与
  五个物种的 `VALIDATION_COMPLETE`，再实施应用挂载和宿主目录登记。

### 9.7 五宿主库应用接入（2026-10-12，已完成）

实时复核下载建库 Job `Complete 1/1`，总耗时约 41 分钟；五库均记录预期总碱基数一致
与 `VALIDATION_COMPLETE`，最终 `HOST_DATABASES_COMPLETE`、`HOST_BUILD_EXIT=0` 时间
为 `2026-10-12T02:35:46Z`。数据总占用约 18 GiB（包括压缩包、FASTA、索引等）。

用户授权接入后，先保存 Deployment/ConfigMap 到本机临时目录
`oligosmith-before-hosts-c8ed8ce056224abe92e3e370e245c584/`，再进行服务端预检并应用
[目录补丁](../../deploy/kubernetes/hosts-catalog.patch.json)与
[部署补丁](../../deploy/kubernetes/hosts-deployment.patch.yaml)。现有 nt 条目保持不变。
宿主 PVC 的 `hosts-20261012-r01` 子目录只读挂载到 `/data/blastdb/hosts-20261012`；
`BLASTDB` 添加其 taxonomy 目录。每个宿主库路径为 `<挂载目录>/<species>/<GCF版本>/db`，
版本标识为 `<GCF版本>-blastdb5-20261012`。五个标准 host 条目为 required；中英文、
学名、taxid 的等价条目为非必需别名，指向同一文件，供旧 M4 的精确键查找使用。

部署完成后 Pod `oligosmith-68ff6bd67-gb5dj` 为 `1/1 Running`、0 重启，节点
`k8s-a100-02`。应用内 BLAST 2.16.0+ 验证结果：

| 宿主 | 记录数 | 总碱基数 | 28 nt 精确匹配测试 |
|---|---:|---:|---|
| 猪 | 613 | 2,501,912,388 | PASS，返回 taxid 9823 |
| 牛 | 1,958 | 2,770,686,120 | PASS，返回 taxid 9913 |
| 鸡 | 214 | 1,053,332,251 | PASS，返回 taxid 9031 |
| 绵羊 | 143 | 2,654,063,983 | PASS，返回 taxid 9940 |
| 山羊 | 29,886 | 2,922,617,086 | PASS，返回 taxid 9925 |

测试用每库提取的 28 nt 序列，执行 `blastn-short`，限定 100% identity/coverage，
验证精确命中与 taxonomy，不作为完整 P2 策略或科学性能验收。
WF-01 与 WF-02 子流程各自从实际 M1 对象经过 M4 解析，每物种的英文、中名、学名
均指向对应宿主库并返回 RESOLVED。`/ready` 返回 ready、core_ready=true、
databases_ready=true，required_missing=[]；保持原 AGENTD_REQUIRE_DATABASES=false。

发现独立代码限制：两个工作流版本的 M1 SUPPORTED_HOSTS 未包含 sheep/goat，
在 NO_POPUPS 路线中仍产生 UNSUPPORTED_HOST_DATABASE 警告，但其生成的对象可以由
M4 正确解析并查询上述数据库。旧交互路线还可能要求确认。此次仅更改部署与目录，
没有改写已冻结 WF-02 快照；后续需按工作流版本隔离规则修正预检范围，不能声称该
用户提示问题已解决。P5 内标序列仍未离线补齐。

### 9.8 宿主预检与离线内标修复（2026-10-12）

本节取代 9.7 末段的待办状态。应用已切换到版本标签 `offline-20261012-r02` 的固定摘要：

```text
harbor-core.base.svc.cluster.local/qcyz/oligosmith@sha256:a9f6e84ddc94c5a8e4fec647777fd3f2b8e6ce5219aa470b2e0ace98a5fe44b1
```

基础镜像仍为原 `1.2.15` 镜像摘要 `a4654140604d08a9b0d60e05a13471375a61ed8600d935a997966c8740df1eec`；
新镜像追加已回归验证的源码和默认配置层，不改写 `latest` 标签。发布脚本
[publish-offline-image.py](../../deploy/kubernetes/publish-offline-image.py) 使用现有 Harbor
Secret 和 Registry V2 接口，凭据不进入镜像层或日志。源码构建目录及压缩包保留在宿主 PVC
`offline-release-r01/`、`offline-release-r02.tar.gz`；原始部署备份在本机临时目录
`oligosmith-before-offline-82dfd83f32094e17a36481a2678a4c99/`。

部署文件：

- [配置补丁](../../deploy/kubernetes/offline-config.patch.json)：登记九组内标，明确关闭
  `internal_control.entrez.enabled`；纠正鸡 ACTB 为 `NM_205518.2`，鸡 18S 为
  `AF173612.1`（GenBank，不能声称是 RefSeq）。旧鸡 ACTB `.1` 含 N，不用作设计参考。
- [Deployment 补丁](../../deploy/kubernetes/offline-deployment.patch.json)：固定镜像摘要，
  将同一宿主 PVC 的 `internal-controls-20261012-curated-r01` 子目录只读挂载为
  `/data/internal-controls`，开启 `AGENTD_REQUIRE_DATABASES=true`。
- 五宿主和完整 nt 挂载继续使用原目录。应用 `/ready` 已实测
  `ready/core_ready=true/databases_ready=true/databases_required=true`。

内标原始下载 Job `oligosmith-internal-download-r07` 已完成。原始资料存放于宿主 PVC
`internal-controls-20261012-r01/`，包括 GenBank、分类谱系、查询参数、FASTA 和 SHA-256。
[下载脚本](../../deploy/kubernetes/fetch-internal-controls.py) 核对物种和基因注释；
鸡 GAPDH 的补充转录本还要求与参考本地比对 ≥98% identity、≥90% query coverage、
长度 ≥80 nt，保留比对证据，不通过的条目不进入库。

原始条目长度差异较大，不能直接把片段覆盖缺失当作基因不保守。
[区间整理脚本](../../deploy/kubernetes/curate-internal-controls.py) 按覆盖范围选择至少五个
独立 accession 共同覆盖的最长无缺口参考区间，再纳入全部覆盖此区间的条目。
选择不使用 identity 阈值；保守性由原 MAFFT/P5 公式事后计算。相同 accession 的不同
版本只计一次，未改变碱基或科学阈值。整理后的清单记录来源坐标、模板范围、序列摘要和
质量结果；至少五个 accession 不代表已经证明五个品种覆盖。

| 宿主 | 基因 | 整理后序列数 | 保守性 | 现行阈值结果 |
|---|---|---:|---:|---|
| 猪 | ACTB | 5 | 0.9995（约） | PASS |
| 猪 | GAPDH | 5 | 0.9785（约） | CAUTION |
| 猪 | 18S | 7 | 0.2575（约） | FAIL，共同覆盖不足 |
| 牛 | ACTB | 6 | 0.3484（约） | FAIL，共同覆盖不足 |
| 牛 | GAPDH | 5 | 0.9987（约） | PASS |
| 牛 | 18S | 5 | 0.2983（约） | FAIL，共同覆盖不足 |
| 鸡 | ACTB | 5 | 0.9733（约） | CAUTION |
| 鸡 | GAPDH | 5 | 0.9989（约） | PASS |
| 鸡 | 18S | 5 | 0.9991（约） | PASS |

这些是模板区间保守性结果，不是引物特异性或完整 P5 的 PASS。P5 仍逐基因评估与切换，
候选保留 `P5_REFERENCE_INTERVAL_TEMPLATE` 和 `P5_BREED_COVERAGE_NOT_ESTABLISHED`
提示及来源坐标。资源存在不代表每个基因可以产出合格引物。

WF-01 的 M1 已补齐 sheep/goat 及五种宿主 taxid。WF-02 新父任务使用新建 v2 快照，
旧父任务依照持久化版本使用 v1；未知版本拒绝执行。v1 文件未修改。

### 9.9 实际验收记录与当前限制（2026-10-12）

- 真实 HTTP 预检：猪、牛、鸡、绵羊、山羊均 HTTP 200，`can_auto_run=true`，不再产生
  羊类不支持数据库的误报。
- 真实设计分支：单模板 8.38 秒、多模板 19.79 秒生成报告，状态均为
  `PARTIALLY_SUCCEEDED`。输入未提供全特异性上下文时保持降级，不能写为全流程 PASS。
- 生产报告链路：测试批次 `7bfc0fbb-9132-451c-88cd-897482748a64`；真实认证 REST
  读取及三种附件下载均 HTTP 200，字节摘要与本地文件一致。
- 真实 `/mcp` `tools/call get_wf01_batch` 返回 `primer_design_output` 卡片及三份
  HTTPS 附件。RustFS 的 HTML、科学过程 HTML、科学数据 ZIP 均实际下载成功且摘要一致。
  未操作浏览器，HiMat 前端视觉渲染未独立验收。
- 全 nt 性能 Job `oligosmith-nt-performance-r01`：应用同等 4 CPU/8 GiB 配额，
  3 条短序列、完整 nt、`blastn-short`、evalue=1000；600.41 秒达到限时，
  子进程峰值 RSS 8,393,792 KiB，输出 0 字节，结果 **TIMED_OUT**。
  Job 成功结束只表示测量脚本保存了结果，不表示 BLAST 检索成功。
- 完整工作流验收 Job `oligosmith-offline-acceptance-r01` 与独立 P5 Job
  `oligosmith-offline-p5-r01` 的详细结果写入宿主 PVC `acceptance-20261012-r01/`。
  验收使用真实工具与本地库；P5 检查额外阻断 NCBI DNS，防止静默联网。
  验收 Job 的生成文件保存在独立目录，不覆盖生产任务。

完整 nt 的首轮性能验收未通过，不能把目录挂载成功或卡片附件可下载等同于
WF01/WF02 完整科学验收通过。性能问题须根据实际超时终态及资源使用继续处理；不通过
缩小数据库、跳过背景检索或改低科学阈值宣称通过。

已收回完整结果（不是只看 Job 状态）：

| 验收项 | 结果 | 说明 |
|---|---|---|
| WF01 单模板完整 nt | PARTIALLY_SUCCEEDED，640.51 秒 | nt 检索 600 秒超时，报告保留未完成证据 |
| WF02 CSFV + PRRSV，要求内标 | WAITING_TARGET_RESOURCES，1324.2 秒 | 两靶标在 N08 均为 NT_SEARCH_TOOL_ERROR；多重组合、联合内标尚未执行 |
| 独立离线猪 ACTB P5 | 生成候选，CAUTION | 参考位点 PASS、宿主 CAUTION、病原背景 NOT_CHECKED（超时），blast_verified=false |
| WF02 持久化重载 | VERIFIED | 保持 v2 绑定和等待资源状态 |
| 已终态 WF01 重复调用/重载 | VERIFIED | M6 执行记录摘要不变，没有重新计算 |
| 第二轮 nt，4 线程/32 GiB | TIMED_OUT，601.75 秒 | evalue=1e-5；仍无完整输出 |

脱敏证据位于 `deploy/kubernetes/evidence/offline-20261012/`：`workflows.json`、
`p5-offline-pig-summary.json`、`resume.json`、`nt-performance-r02.json`。
第二轮 `ru_maxrss` 为 55,016,196 KiB，是进程报告的映射驻留指标，不能据此声称容器
超出 32 GiB 配额。后续 cgroup 实测 `memory.current` 约 32 GiB，主要为文件缓存，
`oom/oom_kill=0`；应同时记录进程 RSS 与 cgroup 计费内存。

### 9.10 BLAST 运行配置候选版与性能对照（2026-10-12）

候选镜像 `offline-20261012-r03` 已发布，尚未据此宣称生产性能验收通过：

```text
harbor-core.base.svc.cluster.local/qcyz/oligosmith@sha256:a2e0fd535201b4851f30e164db087548e52b97ee5d2becb2469cfd082c4a13c1
```

该版新增 `tools.blast_runtime`，默认值保持单线程、600 秒。示例运行配置如下，
具体生产值须依据完整扫描结果确定；以下不是已经通过验收的资源承诺：

```json
{
  "tools": {
    "blast_runtime": {
      "num_threads": 16,
      "timeout_seconds": 600,
      "mode_timeouts": {
        "RELATED_BACKGROUND_NT": 3600,
        "INTERNAL_PATHOGEN_CROSS": 3600
      }
    }
  }
}
```

配置仅控制资源，不改变 nt 范围、任务类型、evalue、阈值或输出字段。`-subject` 模式
保持单线程。错误配置拒绝执行，超时证据记录实际限时。WF02 新任务绑定 v3；已存在的
v1/v2 任务继续其原版本，修复后的完整验收应新建批次。604 项回归全部通过、0 跳过，
证据见 `runtime-tests.json`，源码快照有独立摘要及差异验证。

`oligosmith-nt-profile-r03` 在 4 线程、32 GiB 下运行到 600 秒时读取约 181.94 GiB，
累计 CPU 约 1017 秒，无 OOM。诊断数据保留后主动停止该 Job，状态记为
STOPPED_FOR_COMPARISON，未标记扫描完成。随后启动独立
`oligosmith-nt-profile-r04`：16 线程、32 GiB、3600 秒上限；暂不改变生产应用资源。
每 30 秒采集 `/proc` 与 cgroup 的 CPU、内存、磁盘读取和输出大小，记录保存在宿主 PVC
`internal-controls-20261012-r01/nt-profile-r04/`。测量结束仍需进行真实工作流复测。

线程参数用法依据 [NCBI BLAST 多线程说明](https://www.ncbi.nlm.nih.gov/books/NBK571452/)；
不强制切换线程模式，不以另一个数据库或较低灵敏度代替完整 nt 验收。

候选复测 Job `oligosmith-runtime-acceptance-r02` 已创建。其 init container 等待 r04
性能结果，只有 `COMPLETED` 且退出码 0 才启动；否则明确阻断。独立 ConfigMap
`oligosmith-runtime-acceptance-config-r02` 使用 16 线程、完整 nt 两种模式 7200 秒上限，
其他模式仍 600 秒。Job 使用 16 CPU/32 GiB，最长六小时，零自动重试；结果保存于宿主
PVC `acceptance-20261012-r02/`，不写生产任务目录、不修改生产 ConfigMap。
复测同时覆盖当前 WF01 和 WF02 v3，不复用旧 v2 的超时任务作为新版本证据。

发布文件为 `runtime-release-publish.json`、`runtime-acceptance.json`、
`runtime-acceptance-config.json`。冻结 v2/v3 目录在 `.gitattributes` 标记 `-text`，
防止 Windows/Git 换行转换破坏已登记的字节级摘要。

### 9.11 小分卷性能对照（2026-10-12）

r04 完整 nt 测量在 3604.69 秒超时，未完成。用户要求取消时限后创建 r05（脚本和
Job 均无执行截止时间）；随后按用户要求暂停 r05 及工作流复测。用户又授权先做小分卷
存储、内存、并行度对照，选定方案后再做完整 nt 验收。

选用 nt.000、nt.100、nt.200、nt.398，三条原短序列加四条从分卷真实序列提取的
40 nt 阳性对照；记录 accession 和坐标。准备版 r01 因首段缺失碱基无法选出有效对照
而失败，未进入扫描。r02 扩大提取窗口；所有测试保持同一查询、固定全 nt dbsize、
blastn-short、evalue=1e-5、max_target_seqs=100。分卷测试只用于性能诊断，不是科学验收。

测试脚本 `benchmark-nt-subset.py`，三组任务按依赖串行，避免竞争同一存储带宽：

- `oligosmith-nt-subset-r02-ceph-8gi`：Ceph RBD，8 GiB。
- `oligosmith-nt-subset-r02-ceph-32gi`：Ceph RBD，32 GiB。
- `oligosmith-nt-subset-r02-local-32gi`：节点磁盘 emptyDir 副本，32 GiB。

每组依次测试单进程 4 线程、单进程 16 线程、4 进程各 4 线程，再反向重复。
两次运行保存原始命中及排序去重摘要、阳性命中、cgroup 内存、CPU 和实际读取量。
不清空节点全局缓存，不能把后续热缓存结果写成冷盘性能；另用只读 O_DIRECT
每分卷读取 1 GiB 作存储对照。节点只显示 500 GB QEMU 虚拟盘，约 344 GB 剩余，
未验证为 NVMe，也不足以容纳整个 nt。emptyDir 副本只用于本次约十余 GiB 对照。

四进程分片会改变各片的 top-N 截取范围，即使 dbsize 固定也不能直接把拼接结果当作
原检索等价输出。`summarize-nt-subset.py` 比较完整行和排除 E-value 后的命中行，
有差异时不直接接入生产。结果位于宿主 PVC
`internal-controls-20261012-r01/nt-subset-20261012-r02/`。

r02 为探索数据：一个阳性片段含串联重复，单独产生约 20 万条 HSP；Ceph 32 GiB
组又命中了此前任务的共享页缓存，不能据此定量归因于内存配额。本地副本遗漏 BLAST v5
共享 `nt.ndb/nt.nos/nt.not/nt.ntf/nt.nto`，检查失败，未声称本地扫描成功。
r03 排队任务在开始正式测量前撤下，正式方案改为 r04：

- 四条阳性对照增加不同三核苷酸计数要求（40 nt 内至少 26 种），减少串联重复负担。
- 每组复制四分卷及上述 v5 共享索引，逐文件核对 SHA256。
- Ceph 两组使用同一宿主 Ceph RBD PVC 上的不同文件副本；本地组使用磁盘 emptyDir。
- 每次检索前只对本组副本执行 fsync 后的 POSIX_FADV_DONTNEED，禁止清空节点缓存。
  这隔离了原库共享 inode 缓存；该建议仍不保证清除设备/存储后端缓存。
- 采样间隔 0.2 秒。任务串行，无进程限时或 Job activeDeadlineSeconds。

现行任务名为 `oligosmith-nt-subset-r04-{ceph-8gi,ceph-32gi,local-32gi}`，证据目录
`internal-controls-20261012-r01/nt-subset-20261012-r04/`。本地临时盘不是已确认的 NVMe，
不能将其测试结论外推为采购 NVMe 的效果。最终方案应同时通过耗时与结果一致性检查。


r04 对照已完成：3 组 × 3 种并行方式 × 2 次重复，18 次均退出 0，规范化结果完全
一致，未出现 OOM。以下为两次运行中位数（秒），只报告工程指标：

| 存储与内存 | 单进程 4 线程 | 单进程 16 线程 | 4 进程 × 4 线程 |
|---|---:|---:|---:|
| Ceph RBD / 8 GiB | 39.807 | 15.989 | 10.471 |
| Ceph RBD / 32 GiB | 41.623 | 17.093 | 10.271 |
| 节点磁盘 emptyDir / 32 GiB | 35.684 | 12.672 | 9.767 |

Ceph 上增加线程约快 2.4～2.5 倍；增加内存没有在这组小分卷上显示提速。8 GiB
达到 cgroup 配额并触发内存回收，但无 OOM；32 GiB 留有余量。采样峰值包含副本准备
和页缓存的影响，不能解释为算法必需内存。本地组比同内存 Ceph 的 16 线程中位数约快
26%，但当前节点盘不足以放下完整 nt，且不等同于 NVMe 性能。

正式选择 Ceph、单进程 16 线程、32 GiB 上限（16 GiB 请求），保留现有检索语义。
本轮分片一致不抵消 r02 压力样例的差异，暂不将分片拼接接入应用，也不直接按分卷数
线性推算全库耗时。脱敏汇总：
`deploy/kubernetes/evidence/offline-20261012/nt-subset-r04-comparison.json`。

已启动独立完整库复测 `oligosmith-nt-profile-r06`，清单 `nt-profile-selected.json`。
使用已保存在服务端的相同输入，数据库只读，原始输入/命中不输出到聊天。
无进程执行时限、无 Job activeDeadlineSeconds、零自动重试；30 秒只是资源采样间隔。
新证据写入 `internal-controls-20261012-r01/nt-profile-r06/`，避免混用此前暂停证据。
脚本将失败退出码传给 Job。生产镜像、配额和配置未修改，r05 与原工作流复测仍暂停。
完整库复测现已完成；小分卷与完整库执行通过仍不能代替 WF01/WF02 端到端验收。


r06 最终状态为 COMPLETED，退出码 0，2026-10-12 16:50:48（Asia/Shanghai）Job
完成。实际检索耗时 3000.32 秒（50 分钟），无执行时限；101 个资源采样中 cgroup
内存峰值 31.999 GiB，累计读取 958.939 GiB，CPU 累计 5053.555 秒，OOM 与
OOM kill 均为 0，Pod 重启 0。RSS 包含映射页，不能直接作为 32 GiB 容器配额的
实耗值。累计读取量也不代表数据库覆盖百分比。

最终脱敏证据为 `deploy/kubernetes/evidence/offline-20261012/nt-profile-r06-result.json`；
由只读 Job `oligosmith-nt-summary-r06` 从服务端证据提取，不输出查询或检索内容。
此次证明选定配置可正常完成全库执行，未构成端到端科学验收。50 分钟耗时仍需纳入
工作流后台执行和资源排队设计；生产配置未自动升级。原工作流验收 Job 仍保持暂停，
其中两小时进程限时和六小时 Job 截止不满足用户当前不设执行时限的要求，不应直接恢复。


### 9.12 成功检索缓存与 WF02 v4（2026-10-12）

新增 `tools.blast_cache`，默认关闭；部署启用时需绝对目录。缓存限于有真实版本的
本地 `blast_database` 资源，只保存退出码 0 的原始 BLAST 批量结果。失败、超时、
开发回退、无版本、外部目录别名不进入缓存。P1 计算和 P2 判定每次重新执行。

缓存键包含有序输入及 ID、资源版本与路径、同目录索引和 taxonomy 文件的大小/纳秒
时间戳/inode、alias 内容、程序二进制摘要、实现摘要、运行参数及 BLAST/NCBI 环境和
配置。数据库必须使用不可变版本目录，运行中变更不写入缓存；缓存键文件名不含查询。
原始结果只保存在受控服务端目录，不输出到日志。跨目录 DBLIST 暂不缓存。

单项默认 128 MiB、总计 2 GiB、有效期 7 天；使用 OS 进程锁防止相同批次并发重复计算，
原子写入和载荷摘要防止半写/损坏复用，容量锁串行淘汰旧项。缓存不可写或损坏时执行
真实检索；有效期不是检索执行时限。当前按完整批次和 ID 精确匹配，不做跨批次合并。

WF02 新任务绑定 v4，v1/v2/v3 文件不变，旧任务继续按持久化版本路由。v4 只新增
`blast_cache.py`、修改 `specificity.py`，增加缓存以及 `timeout_seconds: null` 明确无
执行时限；有限默认仍为 600 秒。新部署的 null 配置传给旧 v3 网关时回退到其有限
默认，避免旧版无法初始化；不把 v4 行为注入旧任务。

服务器 `oligosmith-cache-accept-r05` 使用真实工具和已有小分卷，首次 2.2607 秒，重复
0.0066 秒，新 provider 0.0061 秒，版本变化后 2.2636 秒重新计算。四次解析后结果完全
一致，包含非空结果；P1 重新执行。证据位于宿主 PVC `cache-acceptance-r05/`，脱敏
汇总保存为 `deploy/kubernetes/evidence/offline-20261012/cache-acceptance.json`。
这些数值不代表完整 nt 首次检索提速。跨任务合批、本地 NVMe 和分片合并尚未实现。


最终全量回归 640 项，失败/错误/跳过均为 0，汇总 `cache-tests.json`。候选镜像为
`harbor-core.base.svc.cluster.local/qcyz/oligosmith@sha256:61b9ea5adafa96e49d9a23d3bf781e2c4547c2e12c251771d4819b0d740858d3`
（`offline-20261012-r04`），镜像证据 `cache-image.json`。生产仍使用既有 r02 镜像，
此次未修改生产副本、配额或配置。

隔离端到端验收 `oligosmith-runtime-acceptance-r03` 于 2026-10-12 17:44:25
（Asia/Shanghai）启动。使用候选镜像、16 CPU/32 GiB、缓存目录
`/evidence/cache-runtime-r03`、`timeout_seconds: null`，无 Job activeDeadlineSeconds。
输入沿用服务器已有数据；日志仅含阶段、耗时与状态，原始结果留在宿主 PVC
`acceptance-20261012-r03/`。两个设计阶段返回 PARTIALLY_SUCCEEDED，完整库阶段
正在运行；随后同输入完整路线重复一次验证工作流级复用，再执行 WF02 v4 与恢复绑定检查。
不能将 Job 完成直接解释为科学通过，最终仍需检查各阶段状态和报告。

清单为 `cache-workflow-config.json`、`cache-workflow-acceptance.json`，脚本为
`accept-cache-workflows.py`。缓存小分卷验收脚本 `verify-blast-cache.py` 和
`cache-acceptance.json` 不等同于完整工作流验收。跨批次合批尚未实施。


### 9.13 验收终态原因核对（2026-10-13）

`oligosmith-runtime-acceptance-r03` 于 2026-10-12 21:35:53（Asia/Shanghai）退出 0。
这只代表验收脚本调用结束，不能代表工作流全部终态通过。脱敏证据为
`deploy/kubernetes/evidence/offline-20261012/workflow-cause-audit-20261013.json`。

WF01 首次和重复路线均 technical_search_complete=true、search_complete=false，
唯一 incomplete reason 为 `DEC_01_DEC_02_POLICY_APPROVAL_PENDING`。M4 当前代码
显式保留此标记，并非读取一个尚未配置的审批开关。两次均有 30 个合格且可交付候选，
候选不完整计数为 0；Top10 的三个检索维度均 PASS，报告完整性 COMPLETE。
因此 PARTIALLY_SUCCEEDED 是现行策略口径导致，不能归因于 nt 超时、数据库缺失或缓存。

WF02 两个子任务均 PASS，各有 5 个候选；附加控制成员生成 1 个方案并以 CAUTION
进入组合池。25 个组合均已完成 P4，工具失败为空、无无效组合、无严格兼容或风险组合。
全部 25 个组合同时未通过 R17 长度差硬门槛及 P4 硬兼容检查，光谱检查全部 PASS。
这说明资源和工具已完成计算，失败来自组合筛选结果。现有放宽路径仍要求 R17 PASS，
不会自动取消硬门槛，不应通过修改阈值来伪造成功。

另一个独立问题是验收驱动：`accept-cache-workflows.py` 只调用一次
`run_wf02_v01_until_blocked`。服务入口保留阶段执行边界，只有调用进入时已经处于
READY_FOR_RELAXATION 才推进放宽阶段；第一次执行到 N20 返回后脚本便保存结果并退出。
因此该状态是 RUN_RELAXATION 的可执行入口，不是 WAITING_USER，也没有后续后台任务
替该脚本续跑。放宽、终态报告等后续阶段尚未验收。应修正验收驱动按持久化 next_action
继续到真正终态/交互/资源阻断，并如实验收无完整兼容结果的分支；本次核对不改阈值，
不改科学状态、不重新执行，也未切换生产部署。


### 9.14 WF02 续跑与失败终态验证（2026-10-13）

修正验收驱动 `workflow-acceptance-driver.py`，遵循运行时
`is_background_boundary`，跨 READY/CONTINUE 阶段继续推进，遇到真实终态、交互或
外部阻断即返回；无进展 token 则报错，避免空转。没有执行时限。3 个专项测试覆盖跨阶段
进入失败终态、用户边界停止、无进展检测。`accept-cache-workflows.py` 已引用该驱动；
重新创建其脚本 ConfigMap 时必须同时包含 `workflow-acceptance-driver.py`。

续跑沿用原 `acceptance-20261012-r03/jobs` 和 WF02 v4 绑定。N21/N22 两轮各评估
25 个组合，接受 0、拒绝 25；N23 未找到部分面板。最终在 N26 到达
`FAILED_NO_COMPATIBLE_PANEL`，next_action=`REVIEW_FAILURE_GUIDANCE`。
报告包含失败指导和两个独立子任务方案，完整/风险/部分面板数量均为 0。
规则快照与续跑前完全相同，没有通过修改阈值制造通过结果。

`oligosmith-workflow-resume-r01` 已完成状态推进；其额外幂等性断言最初采用导出前的
resume token，CSV/PDF 延迟生成会更新元数据导致该断言失败。按真实导出契约将比较基准
移到导出完成后，`oligosmith-workflow-resume-r02` 验证通过。HTML/CSV/PDF 分别为
12321/1349/3378 字节，签名/内容格式检查通过；服务重新加载后终态和导出 SHA256
保持一致。脚本 `resume-cache-workflows.py`，清单 `cache-workflow-resume.json`。
脱敏证据为 `deploy/kubernetes/evidence/offline-20261012/workflow-terminal-20261013.json`，
服务端完整证据位于 `acceptance-20261012-r03/continuation-20261013-r02/`。

本轮已补齐真实无兼容结果分支的推进、报告导出和恢复验收。WF01 技术完成但策略标记
仍保留；WF02 无兼容完整面板，不能写为科学成功路线验收通过。尚未覆盖真实成功面板的
N27–N32 完整路线和 HiMat 可视验收。候选镜像尚未切换生产，生产仍为 1/1 就绪。


### 9.15 WF01 / WF02 后台提交与主动查询（2026-10-13，已部署）

生产 `him-ws/oligosmith` 已切换到 `offline-20261013-r06`，固定镜像摘要
`sha256:527a4bbbf8c8ae56a022e506246fcecdd6cda3f41f93b6564ae4f1cd51b56c8f`，Recreate 单副本 1/1 Ready。

- HiMat 建单/恢复工具立即入队并返回任务编号，结束本轮；计算不依赖客户端连接。
- 用户说“查询进度”时，按当前任务只调用一次 `get_wf01_batch` / `get_wf02_batch`；查询不重启、不推进任务、不等待科学计算锁。
- WF02 补充模板或旧内标交互后也只提交后台执行。底层同步方法保留供内部兼容，REST 原始建单接口仍仅建单；`run-until-blocked` 提交后台，`progress` 为只读查询。
- WF01 `wf01-background/<batch_id>/state.json` 保存队列状态及阶段；`checkpoint.json` 保存未提交阶段的恢复输入。系统文件锁防止同一任务重复执行，科学结果仍由原有阶段引擎持久化。失败不会因查询自动重试。
- 同一服务进程的两个队列共用科学执行锁；任务排队时仍可查询。保持单副本/单服务进程，当前不作为分布式多副本队列发布。
- 生产资源 requests 16 CPU / 16Gi、limits 16 CPU / 32Gi；BLAST 16 线程，`timeout_seconds: null`，无分模式时限。缓存目录 `/data/jobs/blast-cache`，总容量上限 2Gi；数据库仍只读挂载。
- 648 项回归全部通过。`oligosmith-async-verify-r06` 独立 Job Complete：受控科学计算替身下验证 HTTP 提交后断开、运行中查询、重复请求不重算、后台完成；Linux 下另有 5 项失败/检查点恢复/互斥测试通过。这属于后台机制验收，不替代第 9.13–9.14 节真实科学工作流证据。
- 生产 `/health`、`/ready` 均 200；在线 MCP 初始化已包含“查询进度”规则。WF01/WF02 历史任务经 REST 和 MCP 查询后科学状态文件 SHA256 不变，未重新启动旧任务。旧 WF02 等待外部资源状态保留。

证据：`deploy/kubernetes/evidence/async-20261013/`。部署补丁：`deploy/kubernetes/async-deployment.patch.json`。
本地回滚副本及补丁位于 `output/async-release-20261013/`，包含旧 Deployment 和旧 ConfigMap；回滚前确认无活动计算，再依次应用 `rollback-configmap.patch.json`（merge）和 `rollback-deployment.patch.json`（strategic）。
