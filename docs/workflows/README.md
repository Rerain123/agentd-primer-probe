# 工作流导航

本目录只收录“单个工作流”的现行实现说明和施工台账。跨工作流的分层与隔离规则在 [architecture/](../architecture/README.md)。

| 工作流 | 现行文档 | 主要代码/快照 | 契约与验证入口 |
|---|---|---|---|
| WF-01 | [当前实现与运行路线](wf01/WF01_CURRENT_IMPLEMENTATION.md) | `src/agentd/wf01_v02*.py` | `schemas/wf01-*`、`tests/test_wf01_*`、`skills/oligosmith-wf01/` |
| WF-02 | [v0.1 实施计划与节点台账](wf02/WF02_V0.1_IMPLEMENTATION_PLAN.md) | `src/agentd/wf02_*.py`、`src/agentd_workflow_snapshots/wf02_wf01_v1/` | `schemas/wf02-*`、`tests/test_wf02_*`、`skills/oligosmith-wf02/` |
| WF-03 | [当前实现](wf03/WF03_CURRENT_IMPLEMENTATION.md) | `src/agentd_workflow_snapshots/wf03_v1/` | `schemas/wf03-*`、`tests/test_wf03_*`、`skills/oligosmith-wf03/` |
| WF-04 | [施工入口](wf04/README.md) | `src/agentd_workflow_snapshots/wf04_v1/` | `schemas/wf04-*`、`tests/test_wf04_*`、`skills/oligosmith-wf04/` |
| WF-05 | [当前实现](wf05/WF05_CURRENT_IMPLEMENTATION.md) | `src/agentd_workflow_snapshots/wf05_v1/` | `schemas/wf05-*`、`tests/test_wf05*`、`skills/oligosmith-wf05/` |

WF-06 和 WF-07 没有现行公开入口，其阶段信息只在 [历史文档](../history/HISTORY_PHASES.md) 中查询。

## 单工作流修改顺序

1. 读对应现行文档与 `skills/oligosmith-wfXX/SKILL.md`。
2. 定位输入/输出 Schema、服务入口和现有窄测试。
3. 如涉及快照，先读 [工作流隔离架构](../architecture/WORKFLOW_ISOLATION_ARCHITECTURE.md)。
4. 修改代码后同步 Schema、测试和该工作流的唯一现行文档。
5. 发布事实记入 [CHANGELOG](../history/CHANGELOG.md)，不新建每版发布说明。
