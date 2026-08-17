# OligoSmith 0.3.19

本版本修正 HiMat 附件地址契约。Agent D 在 RustFS 上传及完整性校验成功后，将完整、可直接下载的
RustFS 对象 URL 保存到导出元数据，并原样用于 `primer_design_output` 和 `get_wf01_export`。

## 变更

- `attachments[].fileUrl` 从容器兼容路径 `/data/<fileName>` 改为完整 RustFS URL。
- URL 结构为 `<RUSTFS_PUBLIC_BASE_URL>/<bucket>/<object key>`。
- 中文文件名在 `fileUrl` 中按 UTF-8 百分号编码；`fileName` 仍保持中文。
- HTML、PDF、CSV 和科学原始数据 ZIP 共用同一发布地址逻辑。
- `get_wf01_export` 的 `file_url`、`download_url` 和 HTML `preview_url` 使用持久化的发布 URL，
  不再根据本地文件名重新拼接地址。
- 新增 `RUSTFS_PUBLIC_BASE_URL` 部署变量，默认值为
  `https://rustfs.ne.scqcyz.cn`。

生产示例：

```json
{
  "fileName": "2026-08-08_11-07-17_引物探针设计报告.html",
  "fileUrl": "https://rustfs.ne.scqcyz.cn/wens/data/2026-08-08_11-07-17_%E5%BC%95%E7%89%A9%E6%8E%A2%E9%92%88%E8%AE%BE%E8%AE%A1%E6%8A%A5%E5%91%8A.html"
}
```
