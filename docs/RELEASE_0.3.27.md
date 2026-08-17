# OligoSmith 0.3.27

## 输入确认表单兼容性测试

- `prepare_wf01_design_input` 继续通过 MCP `structuredContent` 返回严格的
  `type + data` 业务对象。
- 同一份表单 JSON 也写入 `content[0].text`，兼容当前只把 MCP 文本内容交给
  模型的 HiMat 客户端，用于验证此前的字段缺失判断是否由 `content=[]` 引起。
- 文本镜像只包含设计输入字段和上传文件名，不包含 FASTA 正文、文件下载地址、
  服务器路径或 `input_artifact_id`。
- 增加服务端重复调用保护：同一已认证调用方在同一会话轮次内短时间重复提交完全相同的
  `prepare_wf01_design_input` 参数时，只有第一次返回可渲染表单；后续调用返回
  `DUPLICATE_SUPPRESSED` 控制结果，避免出现两个输入表单。
- MCP 客户端可通过请求 `_meta.turnId`（也兼容常见 message/conversation/thread/chat ID）
  明确轮次边界；未提供时使用短时调用窗口兜底。不同认证调用方不会互相去重。
- `prepare_wf01_design_input` 新增必填 `file_names` 数组。单文件的
  `sourceFile.fileName` 使用附件原始显示名；一次上传多个文件时固定显示“多个文件”。
- 移除 `uploaded.fasta` 伪文件名兜底；旧 REST 兼容请求未提供任何文件名时显示“未获取文件名”。
- 修复 M6 完成结果在 HiMat 中被错误序列化为
  `{"content":[],"structuredContent":...}` 的问题。进程内工具调用现在直接返回裸
  `primer_design_output`，标准 MCP 响应仍把它放入顶层 `structuredContent`。
- 同一份最终输出 JSON 镜像到 `content[0].text`。当前 HiMat 文本兼容通道必须将该字符串原样作为
  唯一的助手裸 JSON 输出，不得添加进度说明、代码围栏或 MCP 包装字段。
- 用户确认后的下载、预检、建批和 M2-M6 调用改为明确的静默工具链约束；中间工具调用采样不得产生或
  复述任何进度文字，避免 HiMat 将旧助手内容逐轮拼接后形成指数式重复。
- 预检兼容 HiMat 转发的 `confirmed=true` 表单快照；确认仍由独立的
  `primer_design_input_update` 校验。输入错误不再向模型返回完整表单示例，并禁止当前轮次自行重建和重试。
- `download_himat_file`、输出表单与报告发布工具保持不变。
