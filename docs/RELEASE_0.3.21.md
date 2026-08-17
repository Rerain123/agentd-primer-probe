# OligoSmith 0.3.21

本版本为 WF-01 增加独立的科学过程详情报告和人类可读科学工具数据包。

## 默认交付附件

M6 完成后自动生成并上传三个附件：

1. `YYYY-MM-DD_HH-mm-ss_引物探针设计报告.html`
2. `YYYY-MM-DD_HH-mm-ss_科学过程详情报告.html`
3. `YYYY-MM-DD_HH-mm-ss_科学工具原始数据.zip`

Top10 候选报告仍是主附件。过程报告展示工具、输入参数、全部候选、逐项检测、
评分、特异性命中及未完成项目。

## 人类可读数据

- 用户下载的 ZIP 只包含中文说明、科学过程 HTML 和九份中文表头 CSV。
- ZIP 不包含 JSON、程序代码、内部字段名、命令行、代码式日志或访问密钥。
- MAFFT、Primer3、BLAST 等不可变原始证据继续保存在服务端内部，用于完整性校验和审计。
- 三个默认附件均在 M6 返回前上传 RustFS，并将完整下载 URL 写入
  `primer_design_output.data.attachments`。

## 兼容性

- `primaryAttachmentId` 仍为 `attachment_html`。
- CSV 和 PDF 继续按需生成。
- 内部 UUID、用户任务编号和原有 canonical report 数据结构保持不变。
- `install.sh` 通过 `bash` 调用预检和验收脚本，Windows 上传并解压后不再依赖子脚本的可执行位。
