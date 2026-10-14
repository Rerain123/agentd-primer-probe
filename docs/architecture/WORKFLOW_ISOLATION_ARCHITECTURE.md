# 工作流代码隔离架构

## 目标

每个已发布工作流绑定自己的实现版本、依赖版本和状态目录。修改另一个工作流时，不允许通过共享实现、共享状态或隐式导入改变既有工作流的行为。

## 运行边界

`agentd.service.AgentDService` 只负责公共入口、输入文件交接、版本路由和公共异常转换。工作流规则、执行步骤、报告组装与终态渲染由对应版本包负责。

当前绑定关系：

| 工作流 | 版本包 | 来源 | 状态目录 |
|---|---|---|---|
| WF-02 新任务的 WF-01/P5 依赖 | `agentd_workflow_snapshots.wf02_wf01_v4` | v3 + 成功检索缓存、显式无时限 | `workflow-snapshots/wf02-wf01-execution-snapshot-v4` |
| WF-02 运行参数版既有任务 | `agentd_workflow_snapshots.wf02_wf01_v3` | v2 + 可配置 BLAST 线程及分模式限时 | `workflow-snapshots/wf02-wf01-execution-snapshot-v3` |
| WF-02 离线接入版既有任务 | `agentd_workflow_snapshots.wf02_wf01_v2` | v1 + 宿主预检、离线序列状态与来源修订 | `workflow-snapshots/wf02-wf01-execution-snapshot-v2` |
| WF-02 既有任务的 WF-01/P5 依赖 | `agentd_workflow_snapshots.wf02_wf01_v1` | `1.1.10` | `workflow-snapshots/wf02-wf01-execution-snapshot-v1` |
| WF-03 | `agentd_workflow_snapshots.wf03_v1` | `1.1.13` | `workflow-snapshots/wf03-runtime-snapshot-v1` |
| WF-04 | `agentd_workflow_snapshots.wf04_v1` | `1.1.13` 冻结依赖 + 当前 WF-04 开发修订 | `workflow-snapshots/wf04-runtime-snapshot-v1` |
| WF-05 | `agentd_workflow_snapshots.wf05_v1` | `wf05-v0.1` 最小依赖快照 | `workflow-snapshots/wf05-runtime-snapshot-v1` |

WF-02 读取 WF-01 子任务输出时，只通过 `wf01-candidate-contract-v1` 选择上游已经发布的候选集合。下游不得重新解释上游状态字段。

2026-10-12 离线接入版记录 v2 依赖，随后源码新增 v3 运行参数修订；恢复执行时按父任务持久化的
`dependency_contracts.wf01_execution.snapshot_version` 选取网关。v1 或缺少历史绑定的任务
走 v1；未知版本拒绝执行，不自动落入新版本。P5、P6、P4、重设计及子任务交互同样遵循
父任务绑定。v2 仅修订 `wf01_v02.py` 与 `internal_control.py`，文件集合、摘要与 v1 差异
由 `tests/test_offline_workflow_resources.py` 校验；v1 原文件保持不变。
v3 只修改 `specificity.py` 及注入 BLAST 配置的 `service.py`、`internal_control.py`、
`workflows/wf01.py`，不改科学阈值；`tests/test_blast_runtime.py` 校验精确摘要、文件差异和
v1/v2 仍使用原 600 秒、单线程行为。v3 保持不变。源码默认新任务走 v4，线上版本以运维发布记录为准。
v4 只增加 `blast_cache.py` 和修订 `specificity.py`；精确摘要、成功缓存、失效和旧版路由由
`tests/test_blast_cache.py` 校验。旧 v3 不识别的新 null 限时配置由路由层回退为其有限默认。

## 不可变版本规则

1. 已发布的 `agentd_workflow_snapshots/<name>_vN` 不做功能修改。
2. 需要改变某个工作流时，复制现有版本包为新的 `vN+1`，只修改新包。
3. 在服务路由中显式迁移该工作流；其他工作流的绑定保持不变。
4. 旧任务继续从原版本目录读取；新任务只写入新版本目录。
5. 版本包内部只能使用相对导入，不得导回可变的顶层 `agentd` 实现。

开发初期经明确确认可以直接修订当前工作流，但修改范围必须限制在该工作流自己的实现文件，并同步独立运行包与开发源。被其他工作流复用的实现和已冻结依赖不得随之修改；直接开发修订需登记精确文件哈希，以便隔离测试阻止无关文件漂移。正式冻结后恢复上述不可变版本规则。

来自历史发布包的版本快照在绑定时记录来源版本、来源 ZIP 的 SHA-256、精确文件数和确定性树摘要；后续完整性测试直接校验仓库内的绑定快照，不依赖可删除的 `dist/` 构建产物。WF-04 的热修与当前开发修订另登记精确文件哈希。项目内新建的最小版本包记录精确文件集合和确定性树摘要。`tests/test_workflow_snapshot_integrity.py` 同时检查两类快照，并统一检查不存在导回共享实现的绝对导入。WF-05 还额外校验 C0/C5/C7/P1 快照与项目既有工具字节级一致；快照存在改写时只使 WF-05 闭合失败，不影响其他工作流的状态。

Git 必须保留所有快照文件的原始字节，包含 Python、HTML、CSS 和 JavaScript；根 `.gitattributes` 对整个 `src/agentd_workflow_snapshots/` 禁用文本换行转换。2026-10-14 交接检查发现旧规则未覆盖旧版本快照的静态资源，Windows 检出时可能导致树摘要不一致，现已统一保护。该调整不改快照源码、绑定关系或登记哈希；交付前应对从 Git 导出的文件重新运行完整性测试。

## 状态兼容

WF-03、WF-04 和 WF-05 的权威结果分别写入各自版本目录。根状态目录保留非权威兼容副本，便于旧调用方按原路径检查任务文件；它不是硬链接，对兼容副本的意外写入不会改变版本目录中的权威结果。服务读取时始终优先使用版本目录。

终态附件也由工作流自己的版本化渲染器写入对应目录。公共下载入口只负责根据任务定位文件，不参与报告内容生成。

## 允许共享的公共设施

以下能力可以作为稳定边界共享，但不能包含工作流判断逻辑：

- 公共 API/MCP 路由；
- 输入文件下载与内部引用转换；
- 公共异常类型转换；
- 对象存储发布接口；
- 工作流版本选择和旧任务兼容查找。

任何会影响步骤、筛选、排序、重试或报告内容的代码都必须放在工作流自己的版本包内。
