# 第三阶段容器部署

推荐使用 Compose 完成服务器部署：

```powershell
Copy-Item deploy\.env.example deploy\.env
docker compose --env-file deploy\.env -f deploy\docker-compose.yml config
docker compose --env-file deploy\.env -f deploy\docker-compose.yml build
docker compose --env-file deploy\.env -f deploy\docker-compose.yml up -d
```

正式交付推荐直接使用：

```bash
bash deploy/scripts/install.sh
```

脚本会完成预检、镜像构建、容器内 readiness、启动和 HTTP 验收。需要先构建镜像再准备数据库时使用 `--build-only`；离线镜像已经载入时使用 `--no-build`。

启动前应修改 `deploy/.env` 中的数据目录，并将实际数据库版本和容器内 BLAST 前缀写入 `deploy/server-config/database-catalog.json`。Compose 会以只读方式挂载数据库和序列目录，以独立卷保存任务记录。

启动后检查：

```powershell
curl http://localhost:8020/health
curl http://localhost:8020/ready
curl http://localhost:8020/v1/tool-status
```

容器包含 Primer3、ViennaRNA、BLAST+ 和 MAFFT，但不包含大型生物数据库。`/data/blastdb` 应挂载经审核和版本化的靶标、宿主及近缘种数据库。

`/health` 只表示进程存活；生产模式下 `/ready` 还要求 Primer3、MAFFT、BLAST+ 和目录中所有 `required=true` 的资源可用。

只有靶标、宿主和交叉数据库均完成真实 BLAST+ 检查后，候选才可能标记为 `blast_verified`。

完整服务器部署推荐使用 `deploy/docker-compose.yml`，其中已经配置生产配置文件、数据库目录、只读 BLAST/序列卷、任务持久化卷和 readiness 检查。历史架构说明见 [`docs/history/HISTORY_PHASES.md`](../../docs/history/HISTORY_PHASES.md)。

服务器安装包与运维步骤见 `docs/operations/SERVER_INSTALL.md`，数据目录约定见 `deploy/DATA_LAYOUT.md`。
