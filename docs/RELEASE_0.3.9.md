# Agent D 0.3.9 发布说明

> 此版本已被 0.3.10 取代。0.3.9 的文字兜底曾要求默认读取全部候选，
> 不符合“默认 Top10、用户明确请求后才查看全部”的产品约定，请勿再部署 0.3.9。

## 修复内容

- M6 以 HTML 作为主交付物，PDF 调整为不阻断流程的可选附件。
- HTML、XLSX、PDF 改为逐格式独立渲染和保存；任一格式失败时保留其他成功格式。
- canonical report 在导出前持久化，避免格式渲染异常导致完整科学结果丢失。
- `m6_execution` 新增 `export_status` 和 `export_failures`，明确记录部分导出失败。
- PDF 可见表格单元格增加长度上限，完整数据仍保存在 PDF 内嵌的
  `canonical-report.json`，避免超长字段触发 ReportLab `LayoutError`。
- HTML 不可用时，智能体结果新增确定性的文字兜底：按页读取全部
  `all_deliverable_candidates`，而不是只返回前 3 条或前 10 条预览。
- `get_wf01_report` 新增 `candidate_scope="deliverable"`，用于完整文字结果分页。

## 行为约定

- HTML 成功、PDF 失败：M6 为 `COMPLETED`，导出状态为 `PARTIAL`，用户仍可直接打开 HTML。
- 所有文件导出失败：canonical report 仍保留；智能体必须按分页计划输出全部候选的文字结果。
- PDF、XLSX 的失败不再导致 HTML 下载入口消失。

## 验证

- PDF 异常隔离、HTML 主附件和文字分页兜底均有自动化回归测试。
- 使用 33 条候选及超长字段生成 37 页压力测试 PDF，代表页面无表格溢出或布局异常。
