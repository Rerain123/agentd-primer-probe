# WF-04 施工入口

WF-04 用于验证已有引物/探针，当前为确定性六节点后端，并使用隔离快照。本页只聚合施工入口，不复制一套科学规则。

| 类型 | 入口 |
|---|---|
| 快照实现 | `src/agentd_workflow_snapshots/wf04_v1/` |
| 顶层兼容/编排 | `src/agentd/workflows/wf04_v01.py`、`src/agentd/mcp_server.py`、`src/agentd/service.py` |
| JSON Schema | `schemas/wf04-v01-input.schema.json`、`wf04-v01-report.schema.json`、`wf04-v01-retry.schema.json` |
| 模型 Skill | `skills/oligosmith-wf04/SKILL.md` |
| 回归测试 | `tests/test_wf04_mcp.py`、`test_wf04_v01_backend.py`、`test_wf04_regressions.py`、`test_wf04_resource_resolution.py` |
| 隔离边界 | [工作流隔离架构](../../architecture/WORKFLOW_ISOLATION_ARCHITECTURE.md) |

行为变化时，先以 Schema 和测试固定契约，再修改快照并验证绑定/哈希。如需增加较长的现行说明，在本目录中建立唯一文档，并由本页指向它。
