# OligoSmith Dify 确定性流程验证台

`oligosmith-agentd-workflow.yml` 用来展示和验收 WF-01 / WF-02 的完整流程，不是面向终端用户的对话 Agent，也不是第二套科学计算实现。

## 架构边界

- Dify 应用类型为 **Workflow**，不包含 LLM、Agent、FunctionCalling、模型 Provider 或 Marketplace 插件。
- Dify 只负责：显式选择操作、校验输入、调用 AgentD、展示状态码和原始结果。
- WF-01、WF-02 的状态机、交互、重试、版本和报告仍由 `AgentDService` 维护。
- P1–P6 继续使用仓库内独立工具包；DSL 不复制算法、阈值或科学规则。
- 验收工作流通过 AgentD REST API 调用。REST 与 MCP 共用同一个 `AgentDService`，因此验证的是同一套业务状态和工具链。
- 生产对话入口后续仍可采用“大模型 + MCP”；它与本文件的“确定性流程验收”职责不同。

## 导入与配置

1. 在 Dify Studio 新建或导入 **Workflow** 应用，选择 `oligosmith-agentd-workflow.yml`。
2. 配置环境变量：
   - `AGENTD_BASE_URL`：Dify 能访问的 AgentD 根地址，例如 `http://agentd:8000`。
   - `AGENTD_BEARER_TOKEN`：现有 Bearer JWT，只保存在 Dify 工作区中。
3. 不需要安装 OpenAI、Agent Strategy 或 MCP Marketplace 插件。
4. 先用 `WF01_STATUS` / `WF02_STATUS` 验证网络与鉴权，再逐阶段执行创建、推进、交互和报告操作。

> 如果 Dify 运行在容器中，`localhost` 指向 Dify 容器自身，通常不能用于访问宿主机 AgentD；请使用容器网络服务名或可路由地址。

## 输入约定

- `operation`：固定操作列表，覆盖 WF-01 和 WF-02 的创建、状态、运行、交互、版本、报告和导出接口。
- `payload_json`：POST/PATCH 请求体，必须是 JSON 对象；GET 可填写 `{}`。
- `batch_id`：批次级操作必填。
- `draft_id`：WF-02 草稿查询或补充输入必填。
- `target_id`：WF-02 靶标级交互必填。
- `panel_id`：WF-02 panel 查询必填。
- `format_name`：WF-01 支持 `html/process_html/rules_html/scientific_data/csv/pdf`；WF-02 支持 `html/csv/pdf`。

输出统一为：

- `operation`：本次验收动作。
- `status_code`：AgentD HTTP 状态码；本地输入校验错误为 400。
- `response_body`：AgentD 原始 JSON/文本响应，用于与 HTML 流程节点逐项核对。

## 推荐验收顺序

### WF-01 回归基线

1. `WF01_PREFLIGHT → WF01_CREATE`
2. `WF01_STATUS → WF01_REVISE_TARGETS → WF01_CONFIRM`
3. `WF01_RUN`，或按需执行 `WF01_RUN_M2` 到 `WF01_RUN_M6`
4. 若阻塞：`WF01_INTERACTIONS → WF01_RESPOND → WF01_RUN`
5. `WF01_REPORT → WF01_VERSION → WF01_EXPORT`

这里的外部 Step 编号与后端 M 编号不是一一对应关系。当前标准路线不包含外部 Step 4；外部 Step 5 由后端 `M4 / WF01_RUN_M4` 执行，随后 `M5` 只做状态汇总，`M6` 生成并发布报告。不要把 `WF01_RUN_M5` 误标为外部 Step 5。

### WF-02 普通多重

1. 阶段 1：`WF02_PREFLIGHT → WF02_CREATE`；缺项时 `WF02_DRAFT_STATUS → WF02_DRAFT_RESPOND`。
2. 阶段 2–5：反复执行 `WF02_RUN → WF02_STATUS`，观察 N06–N32 的状态推进。
3. 靶标级阻塞：`WF02_TARGET_RESPOND → WF02_RUN`。
4. 内标阻塞：`WF02_INTERNAL_RESPOND → WF02_RUN`。
5. 阶段 6：`WF02_REPORT → WF02_PANEL → WF02_VERSION → WF02_EXPORT`。

## 重点检查项

- WF-02 阶段 2 复用最新 WF-01 公共服务，不复制旧 WF-01 v0.2。
- 0 候选靶标记录 FAIL 和覆盖缺口，但不应中断其余靶标循环。
- P5 以多工具组合的公共入口直接被 WF-02 调用，不回到旧 WF-03 编排。
- P6 证据只写入 WF-02 快照，不修改 WF-01 结果。
- 默认仪器只在用户未指定时使用，后续专家确认可通过规则/配置调整。
- 草稿、靶标、内标三类人工交互均能提交后恢复运行。
- 报告、版本和导出由 AgentD 统一生成，Dify 不自行形成科研结论。

## 状态码判读

- 2xx：接口契约通过，继续核对返回的节点/状态。
- 400/422：输入字段或 JSON 契约不符合要求。
- 401/403：Bearer JWT 或服务权限配置问题。
- 404：ID、路由或导出格式不匹配。
- 409：当前状态不允许该操作，通常提示流程顺序或版本冲突。
- 5xx：AgentD 或下游工具链异常，应结合后端日志定位具体 P 工具/节点。
