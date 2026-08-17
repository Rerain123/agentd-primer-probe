# Agent D 本地使用

本地模式只监听 `127.0.0.1`，不会向局域网或互联网开放。任务结果默认保存在项目同级的 `agentd-runtime/local-jobs/`。

## 启动

双击项目根目录的 `start-local.cmd`。启动成功后会自动打开：

```text
http://127.0.0.1:8000/
```

根路径的中文页面和新版智能体后端均使用至少 80 nt 的输入限制。新版智能体
后端通过 `/v1/wf01/tasks` 批次 API 接收输入；M1～M6 的科学流程、状态和报告均以该
批次 API 为准。前端接入新版接口前，不应根据旧页面的输入框或展示结果判断
新版流程是否合规。

只填写序列即可启动设计；靶标留空时，Primer3 与规则评估照常执行，
特异性显示为“待补充靶标信息 / 未评估”。填写靶标后即运行完整适用范围的特异性检测；
宿主为可选信息，填写后额外运行宿主专库排除。
独立 Primer3 兼容接口为 `POST /v1/tools/primer3/design`。

需要调试原始 API 时，仍可访问：

```text
http://127.0.0.1:8000/docs
```

新版报告阶段的主要接口：

```text
POST /v1/wf01/tasks/{batch_id}/run-m6
POST /v1/wf01/tasks/{batch_id}/versions
GET  /v1/wf01/tasks/{batch_id}/report
GET  /v1/wf01/tasks/{batch_id}/exports/html
GET  /v1/wf01/tasks/{batch_id}/exports/csv
GET  /v1/wf01/tasks/{batch_id}/exports/pdf
```

创建新版本时，`target_changes` 只列出发生改变的靶标。未列出的靶标保留已完成
的 M2～M5 结果；列出的靶标重新经过 M1 预检，并从 M2 开始运行。请求示例和字段
约束见 `docs/WF01_PRD_V0.2_M7_VERSIONED_REDESIGN_CONTRACT.md`。

## 整体测试

服务启动后双击 `test-local.cmd`。脚本依次提交 WF-01～WF-07，并检查 HTTP 状态、结果错误和任务保存。

本地缺少 BLAST+、MAFFT 或正式数据库时，结果会显示 `PARTIAL / in_silico_only`。这表示计算链路已经运行，但特异性或群体保守性证据尚不完整。

## BLAST 数据边界

特异性评估只使用部署环境中的 BLAST+ 与版本化靶标、宿主和 `nt`/背景
数据库。项目不提供向公共 BLAST 服务提交序列的远程旁路。

## 停止

双击 `stop-local.cmd`。该脚本只停止由 `start-local.cmd` 启动并记录 PID 的 Agent D 进程。

## 命令行方式

```powershell
.\start-local.cmd
.\test-local.cmd
.\stop-local.cmd
```
