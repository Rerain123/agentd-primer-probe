# RustFS 附件发布

Agent D 的任务状态和 canonical report 继续保存在 `agentd-jobs` Docker volume 中。
WF-01 默认只发布两份 HTML 和一份 ZIP，21 条规则验证报告保存在 ZIP 内；按需导出沿用既有发布流程。WF-02 在终态报告阶段只上传 HTML，
CSV/PDF 在用户明确请求对应格式后按需生成并上传，
解决 HiMat 后端不能读取 Agent D 容器内部文件的问题。

## 当前 Kubernetes 配置

新服务器当前使用：

| 项目 | 值 |
|---|---|
| Endpoint | `https://rustfs.ne.scqcyz.cn` |
| Public base URL | `https://rustfs.ne.scqcyz.cn` |
| Bucket | `oligosmith` |
| Object prefix | `data` |
| 控制台 | `https://rustfs.ne.scqcyz.cn/rustfs/console/browser/` |
| Kubernetes Secret | `him-ws/oligosmith-secrets` |

`oligosmith` bucket 当前设置为公网只读：匿名访问者只能下载对象；上传、覆盖和删除仍由应用专用
AK/SK 授权。Secret 中的凭据键为 `RUSTFS_ACCESS_KEY_ID` 和
`RUSTFS_SECRET_ACCESS_KEY`，不得在文档、日志或终端输出中展示实际值。

Deployment 中的非敏感变量为：

```dotenv
RUSTFS_ENABLED=true
RUSTFS_ENDPOINT_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_BUCKET=oligosmith
RUSTFS_REGION=us-east-1
RUSTFS_OBJECT_PREFIX=data
RUSTFS_PUBLIC_BASE_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_VERIFY_TLS=true
RUSTFS_CONNECT_TIMEOUT_SECONDS=5
RUSTFS_READ_TIMEOUT_SECONDS=30
```

滚动更新后可执行一次会自动清理测试对象的写入验收：

```bash
kubectl -n him-ws exec deployment/oligosmith -- python -c \
'import json; from agentd.object_storage import RustFSArtifactPublisher; print(json.dumps(RustFSArtifactPublisher.from_environment().probe_write(), ensure_ascii=False, indent=2))'
```

期望 `status=ready`、`write_verified=true`、`test_object_deleted=true`。已确认出现
`InvalidAccessKeyId` 时，应更新 Kubernetes Secret 中的 AK/SK，而不是重新创建 bucket；出现
`HeadBucket 403` 时，应检查凭据、bucket 名和账号的 bucket 权限。

## 地址映射

- RustFS endpoint：`RUSTFS_ENDPOINT_URL`
- bucket：`RUSTFS_BUCKET`
- object key：`<RUSTFS_OBJECT_PREFIX>/<fileName>`
- 公网基址：`RUSTFS_PUBLIC_BASE_URL`
- `primer_design_output.data.attachments[].fileUrl`：
  `<RUSTFS_PUBLIC_BASE_URL>/<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/<percent-encoded fileName>`

`fileUrl` 是可直接下载的完整 URL。显示用的 `fileName` 保留中文；URL 中的中文及其他
非 ASCII 字符按 UTF-8 百分号编码。对应文件在返回之前已经存在于 HiMat 与 Agent D
都能访问的 RustFS 存储桶中，而不是只存在 Agent D 容器里。
终态输出契约接受后端生成的 HTTP(S) 完整 URL，不绑定具体域名、桶名或对象前缀；实际地址
始终由以上部署变量决定。既有的本地只读附件路由仍保持兼容。

WF-02 使用稳定且不会跨批次冲突的对象名：

```text
data/wf02-<batch_id>-multiplex-report.html
data/wf02-<batch_id>-multiplex-report.csv
data/wf02-<batch_id>-multiplex-report.pdf
```

附件卡片继续显示友好的 `wf02-multiplex-report.*` 文件名，但 `fileUrl` 指向以上对象。

## 部署变量

```dotenv
RUSTFS_ENABLED=true
RUSTFS_ENDPOINT_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_ACCESS_KEY_ID=<专用 Access Key>
RUSTFS_SECRET_ACCESS_KEY=<专用 Secret Key>
RUSTFS_BUCKET=<专用 bucket>
RUSTFS_REGION=us-east-1
RUSTFS_OBJECT_PREFIX=data
RUSTFS_PUBLIC_BASE_URL=https://rustfs.ne.scqcyz.cn
RUSTFS_VERIFY_TLS=true
RUSTFS_CONNECT_TIMEOUT_SECONDS=5
RUSTFS_READ_TIMEOUT_SECONDS=30
```

`RUSTFS_PUBLIC_BASE_URL` 是浏览器和 HiMat 后端实际访问对象时使用的公网基址；它可以与
S3 API endpoint 相同，也可以配置为反向代理或 CDN 地址。Compose 部署将密钥写入服务器
`deploy/.env`；当前 Kubernetes 部署通过 Secret 注入。密钥不得写入代码、部署包、普通环境变量
截图或日志。应使用专用最小权限账号，只授权：

- 读取 `<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/*`
- 写入 `<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/*`
- 查询 `<RUSTFS_BUCKET>` bucket

不要在 Agent D 中使用 RustFS 管理员密钥。

## 成功与失败边界

1. 文件先保存到 `agentd-jobs` 持久卷。
2. 通过 S3 SigV4 path-style 请求上传 RustFS。
3. 执行 `HEAD Object`，比对文件大小和 SHA-256 用户元数据。
4. 校验通过后才把附件写入 `primer_design_output`。

RustFS 启用时，默认 HTML 上传或校验失败会使对应报告阶段保持可重试，不会返回无法获取的
“已完成附件”。按需 CSV/PDF 上传失败只影响该次导出请求，已完成流程状态不回退。本地文件会保留，
便于排障和重试。旧版本已完成的 WF-02 批次在首次查询时只补传或刷新 HTML；其他格式在明确请求时处理，
不重新执行流程。

## 验证

`deploy/scripts/verify.sh` 在 `RUSTFS_ENABLED=true` 时会创建一个随机命名的小型验收对象，
执行写入、`HEAD Object` 完整性校验和精确删除，从而同时校验 endpoint、AK/SK、
bucket 读写权限和清理权限。生成报告后可在 RustFS 控制台检查：

```text
<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/YYYY-MM-DD_HH-mm-ss_引物探针设计报告.html
<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/YYYY-MM-DD_HH-mm-ss_科学过程详情报告.html
<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/YYYY-MM-DD_HH-mm-ss_科学工具原始数据.zip
<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/wf02-<batch_id>-multiplex-report.html
<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/wf02-<batch_id>-multiplex-report.csv
<RUSTFS_BUCKET>/<RUSTFS_OBJECT_PREFIX>/wf02-<batch_id>-multiplex-report.pdf
```

`21条规则验证报告.html` 是上述 WF-01 ZIP 的内部成员，不再以独立 RustFS 对象返回。
