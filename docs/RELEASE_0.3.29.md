# OligoSmith 0.3.29

## 最终结果展示

- `primer_design_output` 继续通过 MCP 顶层 `structuredContent` 返回，HiMat 的附件卡片协议、
  报告地址和 RustFS 发布逻辑保持不变。
- 最终工具结果不再把整份 `primer_design_output` JSON 复制到 `content[0].text`，避免宿主已经
  渲染附件卡片后，模型又把同一 JSON 作为普通文字重复输出。
- `content[0].text` 改为服务端确定性生成的中文完成总结；模型只需原样输出该总结，不得复述
  `structuredContent` 或自行重建候选。
- 完成总结按每个靶标展示真实排名前三的候选，包括候选编号、得分、结论、证据等级、正反向引物、
  探针和扩增子长度。不同靶标的排名不混合比较。

## 未改变的范围

- `primer_design_input` 表单兼容通道、输入去重、原始上传文件名和静默工具链保持不变。
- `agent_interaction` 继续只通过结构化通道返回，不生成重复文字选项。
- M2-M6 科学计算、报告生成、附件发布、下载 URL、任务数据和输入 artifact 均保持兼容。

