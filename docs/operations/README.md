# 运维与接入导航

| 任务 | 文档 |
|---|---|
| 新服务器 Rancher/Kubernetes、HiMat 内部接入和 Codeup `test` 自动部署 | [Kubernetes 与 HiMat 部署](KUBERNETES_HIMAT_DEPLOYMENT.md) |
| 连接 MCP、配置 JWT、查工具范围 | [MCP 服务](MCP_SERVER.md) |
| 理解请求、反向代理、容器和外部依赖 | [服务器请求与容器部署](SERVER_REQUEST_AND_DEPLOYMENT.md) |
| 首次安装、离线交付、升级和回滚 | [服务器安装与交付](SERVER_INSTALL.md) |
| 配置报告附件的 RustFS 发布 | [RustFS 附件发布](RUSTFS_ARTIFACT_STORAGE.md) |
| 查询模型工具调用的脱敏事实 | [智能体工具审计](AGENT_AUDIT_LOG.md) |

生产密钥、JWT Secret、RustFS AK/SK 和真实 `.env` 不属于文档或 Git。任何部署变更都要同步检查 `deploy/` 和 `tests/test_deployment_package.py`。
