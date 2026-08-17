# 引物探针设计完成消息契约

WF-01 的 M6 报告生成成功后，Agent D 在 API/MCP 结果的 `ui_message` 中确定性返回
`primer_design_output`。MCP 工具必须把该 UI 对象提升为顶层结构化结果，宿主可直接据此生成
`block_data` 并返回后端。为兼容当前仍从 MCP 文本结果生成助手消息的 HiMat 链路，工具的
`content[0].text` 同时返回与 `structuredContent` 完全相同的紧凑裸 JSON。

会话智能体必须逐字输出 `content[0].text`，禁止改写为 Markdown、说明文字、URL 列表或添加任何
前后缀。HiMat 根据该业务对象渲染报告预览和下载表单。首次设计完成、再次查看报告、请求下载地址，
以及按需生成 CSV/PDF 后，都必须返回同一份 `primer_design_output` 卡片契约。

用户可见任务编号使用 `WF01-YYMMDD-HHMM-XXXX` 格式，例如
`WF01-260808-1306-8EC6`。完成消息的 `summaryText` 和 HTML 报告首页显示该编号。
内部 `batch_id` 继续使用完整 UUID，供 API、MCP 工具和持久化目录使用，不展示为主要任务编号。

```json
{
  "data": {
    "status": "COMPLETED",
    "reportTitle": "猪瘟病毒（CSFV）引物探针设计报告",
    "summaryText": "任务编号 WF01-260808-1306-8EC6。已完成设计，共生成 3 套候选方案。",
    "primaryAttachmentId": "attachment_html",
    "attachments": [
      {
        "attachmentId": "attachment_html",
        "fileName": "2026-08-07_19-50-15_引物探针设计报告.html",
        "fileType": "HTML",
        "mimeType": "text/html; charset=utf-8",
        "fileUrl": "https://rustfs.ne.scqcyz.cn/wens/data/2026-08-07_19-50-15_%E5%BC%95%E7%89%A9%E6%8E%A2%E9%92%88%E8%AE%BE%E8%AE%A1%E6%8A%A5%E5%91%8A.html"
      },
      {
        "attachmentId": "attachment_process_html",
        "fileName": "2026-08-07_19-50-15_科学过程详情报告.html",
        "fileType": "HTML",
        "mimeType": "text/html; charset=utf-8",
        "fileUrl": "https://rustfs.ne.scqcyz.cn/wens/data/2026-08-07_19-50-15_%E7%A7%91%E5%AD%A6%E8%BF%87%E7%A8%8B%E8%AF%A6%E6%83%85%E6%8A%A5%E5%91%8A.html"
      },
      {
        "attachmentId": "attachment_rules_html",
        "fileName": "2026-08-07_19-50-15_21条规则验证报告.html",
        "fileType": "HTML",
        "mimeType": "text/html; charset=utf-8",
        "fileUrl": "https://rustfs.ne.scqcyz.cn/wens/data/2026-08-07_19-50-15_21%E6%9D%A1%E8%A7%84%E5%88%99%E9%AA%8C%E8%AF%81%E6%8A%A5%E5%91%8A.html"
      },
      {
        "attachmentId": "attachment_scientific_data",
        "fileName": "2026-08-07_19-50-15_科学工具原始数据.zip",
        "fileType": "ZIP",
        "mimeType": "application/zip",
        "fileUrl": "https://rustfs.ne.scqcyz.cn/wens/data/2026-08-07_19-50-15_%E7%A7%91%E5%AD%A6%E5%B7%A5%E5%85%B7%E5%8E%9F%E5%A7%8B%E6%95%B0%E6%8D%AE.zip"
      }
    ],
    "issues": []
  },
  "type": "primer_design_output"
}
```

附件由 Agent D 逐项从已生成的 `m6_execution.exports` 构造，不得捏造文件、URL 或下载格式。
MCP `structuredContent` 必须严格返回上述业务对象：顶层只有 `data` 和 `type`，顺序固定为
`data`、`type`。`content[0].text` 必须是该对象的紧凑 JSON 镜像，解析后与 `structuredContent`
完全相同；不得让模型自行重组附件、提取 URL 或虚构执行状态。
不得由 Agent D 添加宿主传输字段 `block_data` 或 `blockId`，也不得把 MCP 的
`content`、`structuredContent` 字段嵌入业务对象。
生产环境中，每项 `fileUrl` 必须是上传成功后保存的完整 RustFS 对象 URL；
`fileName` 保持原始中文显示名，`fileUrl` 中的中文文件名按 UTF-8 百分号编码。
M6 默认生成候选 HTML、科学过程详情 HTML、21条规则验证 HTML 和科学工具数据 ZIP；候选 HTML 仍是主附件。
四个默认附件和按需 PDF 都使用报告生成时的北京时间命名；文件名不使用冒号，以兼容 Windows 下载。
该 HTML 是面向用户的精简交付报告：每个靶标只展示排名前 10 的候选详情，包括关键评分、
引物探针序列和扩增子序列，并逐项说明已通过、需关注、未通过和未完成的检测项目；
不得只给数量或暴露内部规则编号。不展示内部代码、完整计算过程或 canonical JSON。
过程详情报告使用中文表格展示输入、工具、候选生成、逐项检测、评分和特异性证据。
科学工具数据 ZIP 只包含中文说明、过程 HTML、21条规则验证 HTML 和中文表头 CSV；不得包含 JSON、内部字段名、
命令行或代码式日志。工具的不可变原始证据继续保存在服务端内部，用于校验和审计。
启用 `MCP_PUBLIC_WF01_ARTIFACTS=true` 后，普通浏览器可直接访问这些只读附件，无需 Bearer JWT。

生产环境附件地址固定按
`<RUSTFS_PUBLIC_BASE_URL>/<bucket>/<percent-encoded object key>` 构造；当前为
`https://rustfs.ne.scqcyz.cn/wens/data/<percent-encoded fileName>`。对应对象必须在返回完成消息前
上传到 `wens/data/<fileName>` 并通过完整性校验。本地未启用 RustFS 的开发环境可继续使用
`/data/<fileName>` 兼容地址，但不得作为生产完成消息返回。
`get_wf01_export` 返回的
`file_url`、`download_url` 和 HTML `preview_url` 必须返回完全相同的完整 URL；
`/v1/wf01/tasks/{batch_id}/exports/{format}` 仅作为内部兼容 API，不得展示给用户。
该 URL 必须直接指向共享 RustFS 对象，不得只指向 Agent D 容器内部文件。
不同批次复用同一文件名时，源地址必须返回最近实际生成且仍存在的文件，不能因同名记录报错。

CSV 和 PDF 仅在用户通过自然语言明确请求相应格式后，由智能体调用 `get_wf01_export` 按需生成。
CSV 文件名使用 `<任务名>_引物探针候选结果.csv`，PDF 文件名使用
`<任务名>_引物探针设计报告.pdf`；生成成功后才可作为附件返回。CSV/PDF 生成失败不得影响已完成
的 HTML 主报告。如果没有 HTML，
智能体默认只输出 `text_fallback.default_results` 中按排名排序的前 10 条候选。只有用户明确提出
“查看全部候选”时，才执行 `all_candidates_on_request`，按页调用 `get_wf01_report`，并以
`candidate_scope="deliverable"` 取回和展示全部可交付候选。

科学过程详情 HTML、21条规则验证 HTML 和科学工具数据 ZIP 随 M6 自动生成、上传并加入默认
`primer_design_output`。CSV 和 PDF 仍仅在用户明确请求后按需生成。

M6 未成功时，不得伪造 `COMPLETED` 消息。若上层需要展示失败，可发送 `status: FAILED`，
但 `primaryAttachmentId` 必须为 `null`、`attachments` 必须为空，并在 `issues` 中给出错误。
`issues` 的接口类型为 `string[]`；错误码需要保留时使用 `CODE: message` 字符串。

机器可读 schema 通过 MCP resource `oligosmith://contracts/primer-design-output` 发布。
