# OligoSmith Agent D 0.3.18

## 附件发布修复

- 新增 RustFS S3 发布器，M6 HTML 以及按需导出的 PDF、CSV、ZIP 均可上传至
  `wens/data/<fileName>`。
- `primer_design_output` 的 JSON 结构不变，`fileUrl` 继续为 `/data/<fileName>`。
- 上传后使用 `HEAD Object` 校验文件大小和 SHA-256 元数据。
- RustFS 启用时，HTML 未上传成功不会返回可下载的完成卡片。
- 部署验证新增 RustFS `HEAD Bucket` 连通性与权限检查。
- RustFS 账号仅通过 `deploy/.env` 注入，发布包不包含凭据。
