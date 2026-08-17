# WF-01 上传文件输入确认契约

`primer_design_input` 仅用于暂未改造的上传文件兼容链路：由 Agent D 确定性生成并交给 HiMat
直接渲染。它不是科学预检结果，也不是 M1-M5 批次内的 `agent_interaction`。

直接粘贴或手动输入序列不适用本契约。模型应从用户原话中只提取明确出现的序列、靶标物种、宿主
和可选任务名，并直接调用 `create_wf01_batch(sequence=..., target_organism=..., host=...,
task_name=...)`；不先调用 `prepare_wf01_design_input`，也不显示输入确认表单。

## 对外返回格式

对外业务对象顶层严格只有 `type` 和 `data`。不得增加 `flowControl`、`interactionId`、
`deduplicationKey`、说明文字或其他字段。

```json
{
  "type": "primer_design_input",
  "data": {
    "status": "WAITING_USER",
    "confirmed": false,
    "canStart": true,
    "summaryText": "猪瘟病毒（CSFV）·宿主：鸭·覆盖多个分型/毒株",
    "sourceFile": {
      "fileName": "CSFV_5UTR.fasta",
      "fileType": "FASTA"
    },
    "designInput": {
      "targetOrganism": "猪瘟病毒（CSFV）",
      "host": "鸭",
      "coverageRequirement": "覆盖多个分型/毒株"
    },
    "issues": []
  }
}
```

HiMat 可以在传输层把该业务对象包装成 `block_data`，但 Agent D 的 MCP
`structuredContent` 本身不得生成 `block_data` 或 `blockId`。

## MCP 单次调用与轮次规则

这些规则属于 MCP 服务指令和 `prepare_wf01_design_input` 工具说明，不属于返回 JSON：

1. 仅当用户上传文件时，同一个新设计请求最多调用一次 `prepare_wf01_design_input`。
2. 上传文件时，必须通过 `file_names` 数组逐一传入当前用户消息中所有上传附件的原始显示文件名；
   单文件传一个名称，多文件传全部名称，不得省略、改写或虚构为 `uploaded.fasta`。
3. 无论表单字段是否完整，工具返回后均不得重试、再次调用或改换字段名调用。
4. 工具返回后，智能体不得复述、包装或改写 JSON，不得输出任何助手文字，不得继续调用其他工具。
5. 当前助手轮次必须立即结束，等待前端在新的 `role=user` 消息中提交
   `primer_design_input_update`。
6. 只有用户明确开始另一项新设计时，才能在新的用户请求中再次调用本工具。

MCP 系统指令和工具说明负责约束模型只调用一次；服务端同时提供短时重复调用保护。去重键由认证调用方、
可选的 MCP 请求 `_meta` 轮次标识和完整工具参数共同组成。相同键在保护窗口内再次到达时，服务端返回
`DUPLICATE_SUPPRESSED` 控制结果，不再返回 `primer_design_input`，因此宿主不会渲染第二张表单。

推荐 HiMat 在同一用户轮次的所有工具调用中传递相同的 `_meta.turnId`；也兼容 `messageId`、
`conversationId`、`threadId`、`chatId` 及其 snake_case 形式。未传递轮次标识时，服务端按认证调用方和
完整参数在短时窗口内兜底去重。保护窗口由 `MCP_DESIGN_INPUT_DEDUP_SECONDS` 配置，默认 10 秒，设为
`0` 可关闭。该机制不是全局锁：不同认证调用方、不同轮次或不同参数均可正常生成新表单。

## 字段来源与保真

HiMat 把用户输入以及可选的上传文件名和 `file_id` 交给 Agent D。Agent D 只负责把现有信息映射为：

- `targetOrganism`
- `host`
- `coverageRequirement`
- `sourceFile.fileName`
- `sourceFile.inputMode`（存在时）
- `sourceFile.inputArtifactId`（上传客户端已经提供内部引用时）

`sourceFile.fileName` 的显示规则是确定性的：`file_names` 只有一个元素时原样显示该文件名；有两个或
更多元素时统一显示 `多个文件`。服务端同时兼容 `sourceFiles`、`sequence_files`、`attachments`、
`files`、`uploadedFiles`、`inputFiles` 等已有 payload 附件字段。新 MCP 调用必须使用独立的必填
`file_names` 参数，以避免模型遗漏附件元数据。没有文件名也没有序列正文的旧 REST
兼容请求显示 `未获取文件名`，不再伪造为 `uploaded.fasta`。

除去首尾空白外，前三个用户字段不得改写、翻译、扩写或补充别名。用户未提供的字段保持空字符串，
并在 `issues` 中生成 `BLOCKER`；此时 `canStart=false`。所有字段齐全时 `issues=[]` 且
`canStart=true`。

上传 FASTA 正文不放入前端表单或后续模型上下文。用户确认后，模型调用
`download_himat_file(file_id)`，再将工具返回的 `input_artifact_id` 放入后续工具的
`payload.input_artifact_id`。

文件保存、UTF-8 解码和 FASTA 解析均在服务端内部完成。

## 用户确认消息

用户确认或修改表单后，前端在新一轮发送：

```json
{
  "type": "primer_design_input_update",
  "data": {
    "designInput": {
      "targetOrganism": "猪瘟病毒（CSFV）",
      "host": "鸭",
      "coverageRequirement": "覆盖多个分型/毒株"
    }
  }
}
```

未修改字段可以省略；如果用户原样确认、没有修改任何字段，`data` 可以为空，或 `designInput` 可以是
空对象，服务端从原始表单继承全部字段。该消息不复制 `status`、`confirmed`、`canStart`、`summaryText`、
`sourceFile` 或 `issues`。仅发送“开始设计”“确认开始设计”等按钮文字不构成结构化提交。

`create_wf01_batch` 必须同时收到原始 `primer_design_input`、真实的
`primer_design_input_update`，并在 `payload` 中提供同一个 `input_artifact_id`。MCP 适配层在服务端解析对应 artifact，
使用用户更新覆盖表单预填值，再映射到 M1 canonical 字段；模板序列正文不会进入模型上下文。

## 滚动升级兼容

新返回值始终严格使用 `type + data`。为避免部署过程中已经打开的旧表单无法继续提交，MCP 输入适配层
仍可读取 0.3.21 及更早版本保存的 `flowControl` 和 `interactionId`；这些旧字段只用于兼容读取，
不会再次出现在新表单中。

部分 HiMat 提交路径会在转发表单快照前把 `data.confirmed` 改为 `true`。MCP 输入适配层兼容
`confirmed=false/true` 两种布尔快照；真实确认仍必须由独立的 role=user
`primer_design_input_update` 证明，因此该兼容不会绕过确认边界。若第一次预检只是工具参数选择了错误的
历史对象，智能体从当前上下文复用最近一次原始表单和当前结构化更新，修正参数后重试一次，不要求用户
再次确认。错误响应不附带可供模型重建的完整示例；若当前上下文确实缺少结构化对象，才请求宿主转发。

机器可读定义通过 MCP resource `oligosmith://contracts/primer-design-input` 和
`oligosmith://contracts/primer-design-input-update` 发布。
