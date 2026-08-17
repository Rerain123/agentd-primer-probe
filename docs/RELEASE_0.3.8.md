# Agent D 0.3.8 发布说明

## 核心变更

- 新增 `run_wf01_until_blocked` MCP 工具，由 Python 状态机自动执行
  M2-M6；大模型不再逐阶段分析候选数据。
- 遇到 `WAIT_FOR_USER` 时自动停止，用户提交
  `agent_interaction_response` 后默认自动恢复，直至下一次交互或 M6 完成。
- `get_wf01_batch`、`run_wf01_stage` 和批次创建结果改为
  `wf01-agent-view-v1` 摘要，不再把完整候选池写入模型上下文。
- `get_wf01_report` 默认返回报告摘要；指定靶标时每页最多返回 25 个压缩候选。
- 新增 `get_wf01_candidate`，仅在显式请求时展开一个候选的规则详情。
- 增加 `MCP_MAX_TOOL_RESULT_BYTES` 工具响应保护，默认上限为 65536 字节。

## 上下文体积验证

对造成问题的真实 M2 MCP 响应进行回归验证：

- 原始文件：3,958,748 字节；
- 内部紧凑批次 JSON：1,322,763 字节；
- 新智能体摘要：1,270 字节；
- 智能体可见响应缩减 99.9%。

完整科学数据、候选规则证据和 M6 canonical report 仍保存在服务端，
HTML、XLSX、PDF 导出不受影响。

## 兼容性

- REST 完整批次和 canonical report 契约保持不变。
- `run_wf01_stage` 继续保留，定位为兼容/诊断工具。
- MCP 工具总数由 24 增加到 26。
- HiMat 正常流程应在批次创建后调用一次 `run_wf01_until_blocked`。

## 验证

- 完整自动化测试集：153 项通过。
- MCP 中文结构化字段仍按 UTF-8 原样往返。
