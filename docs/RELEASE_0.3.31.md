# OligoSmith 0.3.31

## WF-01 双输入模式修复

- WF-01 设计输入现在明确支持两条并列路径：上传 FASTA 文件，或直接粘贴/手动输入序列。
- 直接序列在 `prepare_wf01_design_input` 阶段保存为服务端内部 artifact；表单只携带
  `sourceFile.inputArtifactId`，不会把序列正文放入后续模型上下文。
- 用户确认后，`preflight_wf01_batch` 和 `create_wf01_batch` 可从原始表单自动恢复直接序列引用，
  不再错误要求 HiMat 上传文件引用，也不要求模型再次复制 FASTA。
- 上传文件路径保持兼容：先通过 `download_himat_file(file_id)` 取得 `input_artifact_id`，再传给预检和建批。
- 新增 artifact、MCP 输入归一化、JSON Schema 和端到端协议测试，覆盖裸序列、FASTA 文本、上传文件及
  引用冲突校验。

## 安全与兼容性

- 两种输入方式都使用同一套不透明 artifact 引用和后端解析机制。
- MCP 结果、审计日志和前端确认表单均不回显完整生物序列。
- 科学计算、报告、附件发布和已有上传文件流程保持不变。
