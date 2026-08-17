# OligoSmith 0.3.26

本版本新增 HiMat 上传文件的服务端输入传递能力。模型只调度文件引用，不读取 FASTA 正文；报告生成、附件发布和现有输出下载地址保持不变。

## HiMat 输入文件工具

- 新增 MCP 工具 `download_himat_file(file_id)`。
- 下载基地址默认为 `https://himat.ne.scqcyz.cn/api/files/download`，可通过 `HIMAT_FILE_DOWNLOAD_BASE_URL` 覆盖。
- 工具只返回 `input_artifact_id`、文件名、类型、大小和 SHA-256，不返回文件正文、本地路径或源下载地址。
- 输入文件保存在 `AGENTD_STATE_DIR/input-artifacts/`，预检和建批可复用同一个引用。

## WF-01 输入边界

- `preflight_wf01_batch` 与 `create_wf01_batch` 支持 `payload.input_artifact_id`。
- FASTA 下载、UTF-8 解码和解析全部在服务端内部完成。
- 模型可见的预检结果移除 `template.sequence` 与 `templates[].sequence`，保留记录名、长度、校验和、来源文件和校验状态。
- 内部 REST/服务兼容路径继续支持原有内联序列输入。

## 部署与验收

- 新增 `HIMAT_FILE_DOWNLOAD_BASE_URL`、`HIMAT_FILE_DOWNLOAD_TIMEOUT_SECONDS` 和 `AGENTD_INPUT_FILE_MAX_BYTES` 配置。
- 部署验收脚本改为检查公开工具 `download_himat_file`，不再错误检查仅供内部调用的 `run_wf01_stage`。
- MCP 公开工具总数为 27。

## 未改变的范围

- M2-M7 科学阶段执行方式不变。
- 报告生成、RustFS 附件发布、报告 URL 和导出工具不变。
- MCP 鉴权方式不变。
