# WF-01 PRD v0.2 M1：Agent 输入与批次 API 契约

M1 新增下列接口，不调用 Primer3，也不修改旧的 `/v1/wf01/design`：

- `POST /v1/wf01/tasks/preflight`
- `POST /v1/wf01/tasks`
- `GET /v1/wf01/tasks/{batch_id}`

## 请求结构

```json
{
  "task_name": "ASFV_batch_001",
  "intent": "普通 TaqMan 独立设计",
  "sequence_mode": "alignment_target",
  "input_artifact_id": "himat-0123456789abcdef0123456789abcdef",
  "batch_defaults": {
    "target_organism": {"name": "African swine fever virus"},
    "hosts": [{"name": "pig"}],
    "related_species": [{"taxid": "12345"}],
    "background_organisms": [{"accession": "NC_000000.0"}],
    "design_parameters": {
      "product_min_bp": 70,
      "product_max_bp": 150
    }
  },
  "target_overrides": {
    "target_002": {
      "hosts": [{"name": "chicken"}],
      "design_parameters": {"product_min_bp": 90, "product_max_bp": 140}
    }
  }
}
```

MCP 正常路径先调用 `download_himat_file(file_id)`，再使用返回的 `input_artifact_id`；模型不读取文件正文或本地路径。内部 REST/服务兼容路径仍支持 `fasta_text`、`sequence_files[].fasta_text`、`template_records`（对象数组）或单个 `sequence`。`sequence_mode` 默认为 `alignment_target`：多条记录属于同一靶标并在 M2 进入 MAFFT；需要把各记录作为独立靶标时，显式设置为 `independent_targets`。

`natural_language` 可作为辅助字段。M1 只提取带明确标签的值，例如 `靶标物种：African swine fever virus；宿主：猪`；未标注的自由文本不会被猜测性映射。后续 LLM 解析层应将结果填入同一 canonical 请求字段，并保留解析来源。

## 预检输出要点

- `alignment_target` 下，多条记录合并为一个 `target_001`，按顺序保存在 `templates`，首条为参考序列；`independent_targets` 下才按记录生成独立靶标。
- 路由只由序列和靶标决定：仅序列为 `DESIGN_ONLY`；序列加靶标为 `FULL_SPECIFICITY`。宿主是可选的附加排除范围，不是进入完整流程的前置条件。
- `issues` 使用 `BLOCKER`、`CONFIRMATION`、`WARNING`；存在前两类时 `can_auto_run=false`。
- M1 对名称、taxid、accession 做输入规范化。M4 再补充可审计的数据库/nt 真实解析、版本和命中信息。
- 只有猪、牛、鸡、鸭、鹅、鸽可在本期预检中标为“宿主库支持”；其他宿主返回 `UNSUPPORTED_HOST_DATABASE` 确认项。
- 输入包含 MGB、多重、内标、简并、二级结构或非 10 的 `top_candidates` 时返回范围阻塞项。

## 固定参数边界

`design_parameters` 使用扁平字段。允许的字段为：

- `product_min_bp`、`product_max_bp`：70-200。
- `primer_min_size`、`primer_opt_size`、`primer_max_size`：18-28。
- `primer_min_tm`、`primer_opt_tm`、`primer_max_tm`：50-65。
- `primer_min_gc`、`primer_max_gc`：0-100。
- `probe_min_size`、`probe_opt_size`、`probe_max_size`：22-32。
- `probe_tm_delta_min`、`probe_tm_delta_max`：8-10。

参数缺省时由 M2 的 default/L1/L2 档处理；用户值与默认值合并后仍须遵守上述硬边界和单调顺序。

## M1 的终态

- 无阻塞/确认问题时，创建批次后状态为 `QUEUED`。M2 将消费该批次并启动独立设计。
- 有问题时状态为 `PENDING_CONFIRMATION`，但批次仍会持久化，前端可在修正后创建新版本或更新确认流程。
- M1 不会把 `QUEUED` 误写为“搜索完成”，不会产生 Primer3 候选或特异性 PASS。
