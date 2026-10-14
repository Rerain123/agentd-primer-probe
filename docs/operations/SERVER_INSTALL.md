# Agent D 服务器安装与交付手册

当前新服务器使用 Rancher/Kubernetes，并由 Codeup `test` 分支触发自动部署。下面先记录当前
生产形态的发布步骤；第 2 节以后保留 Docker Compose 源码包的手工、离线安装方法，两者不要混用。

完整的集群资源、环境变量、RustFS、MCP 和单终端验收命令见
[Kubernetes 与 HiMat 部署](KUBERNETES_HIMAT_DEPLOYMENT.md)。

## 1. 当前 Kubernetes 自动部署

当前发布目标：

| 项目 | 值 |
|---|---|
| 代码仓库 | `https://codeup.aliyun.com/6549a8ea3aaed849b035cf92/agent/oligosmith` |
| 触发分支 | `test` |
| Kubernetes 资源 | `him-ws/oligosmith` Deployment 与同名 Service |
| 集群内 MCP 基址 | `http://oligosmith.him-ws.svc.cluster.local:8000/mcp` |
| HiMat 用户入口 | `https://himat.ws.scqcyz.cn` |

仓库的 `Jenkinsfile` 只登记 `prod` 集群、`mataik8s` kubeconfig 凭据、`him-ws` Namespace 和
`oligosmith` Deployment 目标；仓库目前没有完整的 Deployment/Service YAML。最初导入 Rancher
的清单及当前挂载、环境变量以集群对象为准，修改前应导出脱敏配置留档。不要把 Secret 值提交到
仓库。

发布前确认测试通过且工作区中不包含 Secret、`.env`、数据库、报告或用户测试数据，然后只提交
本次明确修改的文件：

```bash
git status --short
git add <本次修改的文件>
git commit -m "<变更说明>"
git push codeup HEAD:test
```

推送成功只代表流水线已触发。流水线结束后在 Rancher kubectl 终端验证：

```bash
kubectl -n him-ws rollout status deployment/oligosmith --timeout=180s

kubectl -n him-ws exec deployment/oligosmith -- \
  python -c 'import agentd; print(agentd.__version__)'

kubectl -n him-ws get pods \
  -l app.kubernetes.io/name=oligosmith \
  -o custom-columns='NAME:.metadata.name,CREATED:.metadata.creationTimestamp,IMAGE:.spec.containers[0].image,IMAGE_ID:.status.containerStatuses[0].imageID'
```

镜像名当前使用 `:latest`，所以镜像名相同不能证明服务器已经更新。必须同时核对容器内版本、Pod
创建时间和 `imageID`，再执行健康、MCP 工具列表、RustFS 写入和 HiMat 端到端验收。

## 2. Compose 安装包边界

`agentd-primer-probe-server-1.2.15.zip` 是 Docker Compose 源码安装包，包含应用、MCP 服务、容器定义、服务器配置模板和运维脚本，不包含大型数据库、生产数据或离线 Docker 镜像。

首次联网构建需要服务器能够访问 Docker Hub、Debian 软件源和 PyPI。完全离线的服务器应先在同架构联网 Linux 主机上构建并导出镜像。

建议服务器条件：

- Linux x86_64；
- Docker Engine 24 或更高版本；
- Docker Compose v2；
- 至少 4 核 CPU、8 GB 内存；
- 应用盘至少 10 GB，数据库盘容量按 BLAST 数据规模单独规划；
- 默认仅监听 `127.0.0.1:8020`，由反向代理提供外部访问。

## 3. 校验并解压

将 ZIP 和同名 `.sha256` 文件放在同一目录：

```bash
sha256sum -c agentd-primer-probe-server-1.2.15.zip.sha256
unzip agentd-primer-probe-server-1.2.15.zip
cd agentd-primer-probe-server-1.2.15
sha256sum -c PACKAGE-MANIFEST.sha256
chmod +x deploy/scripts/*.sh
```

两个 SHA-256 检查分别验证整个压缩包和包内文件。任一失败都不应继续安装。

如果服务器使用已运行在宿主机 `8090` 端口的 NCBI BLAST MCP 服务，请在
`deploy/.env` 中保留以下配置：

```dotenv
BLAST_SEARCH_SERVICE_URL=http://host.docker.internal:8090
BLAST_SEARCH_TIMEOUT_SECONDS=360
BLAST_SEARCH_MAX_SEQS=100
```

当前 Compose 已包含 Linux `host-gateway` 映射。该服务只在本地
`cross/nt` 背景资源不可用时接管对应检查。目标资源没有本地专用条目时，P2 会从任务的不可变输入
快照生成临时 FASTA；已提供宿主时，对应资源仍使用本地版本化条目。

## 4. 配置服务器路径

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
vi deploy/.env
```

必须核对：

- `AGENTD_BIND_ADDRESS`：默认 `127.0.0.1`；只有完成防火墙、认证和访问控制后才改为 `0.0.0.0`；
- `AGENTD_PORT`：默认 `8020`；
- `AGENTD_BLASTDB_DIR`：宿主机 BLAST 数据库根目录；
- `AGENTD_SEQUENCE_DATA_DIR`：宿主机 FASTA 数据根目录；
- `AGENTD_REQUIRE_DATABASES`：生产科学验收应设为 `true`；仅验证交互和流程链路时可暂设
  `false`，安装器会允许缺库启动，但相应结果只能是 `NOT_EVALUATED`；
- `NCBI_ENTREZ_EMAIL`：P5 调用 NCBI E-utilities 时使用的联系邮箱；
- `NCBI_ENTREZ_API_KEY`：可选的 NCBI API Key；
- CPU、内存和镜像标签。

容器以 UID `10001` 的非 root 用户运行。挂载目录应允许目录遍历和文件读取，推荐目录权限 `755`、数据文件权限 `644`，不要把数据库写权限授予容器。

## 5. 准备数据库

数据布局见 `deploy/DATA_LAYOUT.md`。BLAST 目录填写的是数据库前缀，不是 FASTA 文件或目录。例如：

```text
/srv/agentd-data/blastdb/host/sus_scrofa/2026-07-01/db.nhr
/srv/agentd-data/blastdb/host/sus_scrofa/2026-07-01/db.nin
/srv/agentd-data/blastdb/host/sus_scrofa/2026-07-01/db.nsq
```

对应目录配置为容器内前缀：

```json
{
  "kind": "blast_database",
  "path": "/data/blastdb/host/sus_scrofa/2026-07-01/db",
  "version": "2026-07-01",
  "required": true
}
```

可先只构建镜像，再使用镜像中的 `makeblastdb`：

```bash
bash deploy/scripts/install.sh --build-only
bash deploy/scripts/build_blast_db.sh \
  --input /srv/source/sus_scrofa.fasta \
  --output /srv/agentd-data/blastdb/host/sus_scrofa/2026-07-01/db \
  --title "Sus scrofa 2026-07-01"
```

脚本拒绝覆盖已有前缀。数据库更新应建立新的版本目录，然后修改 `deploy/server-config/database-catalog.json`；不要原地覆盖旧索引。

完成目录配置后检查所有必需资源：

```bash
python3 deploy/scripts/validate_catalog.py \
  --env-file deploy/.env \
  --catalog deploy/server-config/database-catalog.json
```

加入 `--include-optional` 可同时检查可选资源。P5 默认通过 NCBI Entrez 获取管家基因序列，因此目录校验不再要求独立 ACTB FASTA。管理员配置可选的 `internal:<species>:<gene>:sequences` 覆盖时，其中至少应包含 5 条同物种同源序列。

## 6. 联网安装

数据库和版本目录准备完成后：

```bash
bash deploy/scripts/install.sh
```

若当前环境尚未配置数据库、仅用于验证 HiMat 交互链路，可在 `deploy/.env` 中保留
`AGENTD_REQUIRE_DATABASES=false` 后执行同一命令。安装器会明确发出降级警告。正式科学
验收前必须补齐版本化资源并改为 `true`。

脚本依次执行：

1. Docker/Compose、配置、端口和挂载目录预检；
2. 构建包含 Primer3、ViennaRNA、BLAST+ 和 MAFFT 的镜像；
3. 在最终容器环境中执行 readiness；
4. 启动服务；
5. 验证 `/health`、`/ready`、JWT 拒绝策略、全量 MCP 和五个受限 MCP 工具集。

初次构建希望刷新基础镜像时使用：

```bash
bash deploy/scripts/install.sh --pull
```

## 7. 离线安装

在同 CPU 架构的联网 Linux Docker 主机上：

```bash
bash deploy/scripts/install.sh --build-only
bash deploy/scripts/export_offline_image.sh
```

把生成的 `*.tar.gz`、`.sha256`、源码安装包和数据库复制到离线服务器，然后：

```bash
sha256sum -c agentd-primer-probe-1.2.15-linux-image.tar.gz.sha256
gzip -dc agentd-primer-probe-1.2.15-linux-image.tar.gz | docker load
bash deploy/scripts/install.sh --no-build
```

源码 ZIP 本身不含离线镜像，因此只有 ZIP 时不能保证在断网服务器完成首次安装。

## 8. 验收与运维

重复执行部署后验收：

```bash
bash deploy/scripts/verify.sh --wait 180
```

常用运维命令：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f agentd
docker compose --env-file deploy/.env -f deploy/docker-compose.yml restart agentd
docker compose --env-file deploy/.env -f deploy/docker-compose.yml stop
```

`stop`、`restart` 和普通 `down` 不删除任务卷。不要执行 `docker compose down -v`，除非已经确认并备份任务记录。

## 9. 升级与回滚

升级前备份以下内容：

- `deploy/.env`；
- `deploy/server-config/`；
- 数据库目录及其版本清单；
- `agentd-jobs` 持久化卷或平台侧任务存储。

新版本使用新的 `AGENTD_IMAGE_TAG`。先执行 `--build-only` 和容器内 readiness，再切换服务。数据库必须保留旧版本目录，回滚时恢复旧镜像标签和旧目录配置即可；不要用覆盖式建库代替版本切换。

## 10. 上线边界

- `/health` 只代表进程存活；`/ready` 才代表科学工具和必需数据库已准备好。
- readiness 通过不代表所有动态病原靶标都已入库；每个生产靶标仍需登记 `target:<id>:sequences` 和 `target:<id>:blast`。
- 默认接口没有实现组织级身份认证。对外开放前必须放在受控内网或具有 TLS、认证、限流和审计的网关之后。
- 输出仍属于 `in_silico` 或 `blast_verified` 证据，不替代湿实验验证。
