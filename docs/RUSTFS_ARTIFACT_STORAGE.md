# RustFS 附件发布

Agent D 的科学任务状态和 canonical report 继续保存在 `agentd-jobs` Docker
volume 中。M6 生成 Top10 HTML、科学过程详情 HTML 和科学工具数据 ZIP 后，
服务会同步上传四份默认附件到 RustFS，解决 HiMat 后端不能读取 Agent D 容器内部文件的问题。
后续按需生成的 PDF、CSV 使用相同发布流程。

## 地址映射

- RustFS endpoint：`https://rustfs.ne.scqcyz.cn`
- bucket：`wens`
- object key：`data/<fileName>`
- 公网基址：`https://rustfs.ne.scqcyz.cn`
- `primer_design_output.data.attachments[].fileUrl`：
  `https://rustfs.ne.scqcyz.cn/wens/data/<percent-encoded fileName>`

`fileUrl` 是可直接下载的完整 URL。显示用的 `fileName` 保留中文；URL 中的中文及其他
非 ASCII 字符按 UTF-8 百分号编码。对应文件在返回之前已经存在于 HiMat 与 Agent D
都能访问的 RustFS 存储桶中，而不是只存在 Agent D 容器里。

## 部署变量

```dotenv
RUSTFS_ENABLED=true
RUSTFS_ENDPOINT_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_ACCESS_KEY_ID=<专用 Access Key>
RUSTFS_SECRET_ACCESS_KEY=<专用 Secret Key>
RUSTFS_BUCKET=wens
RUSTFS_REGION=us-east-1
RUSTFS_OBJECT_PREFIX=data
RUSTFS_PUBLIC_BASE_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_VERIFY_TLS=true
RUSTFS_CONNECT_TIMEOUT_SECONDS=5
RUSTFS_READ_TIMEOUT_SECONDS=30
```

`RUSTFS_PUBLIC_BASE_URL` 是浏览器和 HiMat 后端实际访问对象时使用的公网基址；它可以与
S3 API endpoint 相同，也可以配置为反向代理或 CDN 地址。密钥仅写入服务器 `deploy/.env`，
不得写入代码、部署包或日志。应使用专用最小权限账号，只授权：

- 读取 `wens/data/*`
- 写入 `wens/data/*`
- 查询 `wens` bucket

不要在 Agent D 中使用 RustFS 管理员密钥。

## 成功与失败边界

1. 文件先保存到 `agentd-jobs` 持久卷。
2. 通过 S3 SigV4 path-style 请求上传 RustFS。
3. 执行 `HEAD Object`，比对文件大小和 SHA-256 用户元数据。
4. 校验通过后才把附件写入 `primer_design_output`。

RustFS 启用时，任一默认附件上传或校验失败都会使 M6 失败，不会再返回虚假的
“已完成附件”。本地文件会保留，便于排障和重试。

## 验证

`deploy/scripts/verify.sh` 在 `RUSTFS_ENABLED=true` 时会创建一个随机命名的小型验收对象，
执行写入、`HEAD Object` 完整性校验和精确删除，从而同时校验 endpoint、AK/SK、
bucket 读写权限和清理权限。生成报告后可在 RustFS 控制台检查：

```text
wens/data/YYYY-MM-DD_HH-mm-ss_引物探针设计报告.html
wens/data/YYYY-MM-DD_HH-mm-ss_科学过程详情报告.html
wens/data/YYYY-MM-DD_HH-mm-ss_21条规则验证报告.html
wens/data/YYYY-MM-DD_HH-mm-ss_科学工具原始数据.zip
```
