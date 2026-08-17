# OligoSmith 0.3.30

## 中途交互裸 JSON 修复

- `agent_interaction` 继续通过 MCP 顶层 `structuredContent` 返回，HiMat 可直接据此生成
  `block_data` 并渲染用户决策弹窗。
- 同一 `agent_interaction` 现在也以紧凑裸 JSON 原样写入 `content[0].text`，兼容当前仍将
  MCP 文本结果交给模型生成助手消息的 HiMat 编排链。
- MCP 指令和相关工具说明要求模型逐字输出该 JSON，并立即结束轮次；禁止改写成自然语言问题、
  文字版选项、Markdown 代码块或添加任何前后缀。
- 新增协议测试，校验文本通道能解析为与 `structuredContent` 完全相同的对象，且不包含 MCP
  外层包装字段。

## 未改变的范围

- `primer_design_input` 仍使用结构化表单和同值 JSON 兼容通道。
- `primer_design_output` 仍由 `structuredContent` 驱动附件卡片，`content[0].text` 只返回中文
  完成总结和每个靶标的真实 Top 3，不重复输出整份结果 JSON。
- 科学计算、报告生成、附件发布、输入 artifact、下载地址和用户交互回复协议保持不变。
