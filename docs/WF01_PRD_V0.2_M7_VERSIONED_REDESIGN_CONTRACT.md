# WF-01 PRD v0.2 M7：版本化重设计与靶标级复用契约

M7 实现 AC-VER-01。它不修改来源任务，而是从一个已完成 M5 状态汇总的
WF-01 PRD v0.2 批次创建新的任务版本。变更的靶标从 M2 开始重新运行；未变更
靶标复用来源版本的 M2～M5 结果，并在事件、报告和导出中保留来源链。

接口：

- `POST /v1/wf01/tasks/{batch_id}/versions`
- `AgentDService.create_wf01_v02_version(batch_id, payload)`

## 请求

`target_changes` 必须是非空数组，每个靶标最多出现一次。支持的 M1 输入字段：

- `template.record_name`、`template.description`、`template.sequence`、`template.source_file`
- `target_organism`
- `hosts`
- `related_species`
- `background_organisms`
- `design_parameters`

示例：

```json
{
  "reason": "替换 target_001 的模板序列后重新设计",
  "target_changes": [
    {
      "target_id": "target_001",
      "template": {
        "sequence": "ACGT..."
      }
    }
  ]
}
```

可选的 `task_name` 只改变新版本的任务名称，不会代替任何靶标变更。空变更和
与来源输入完全相同的变更都会被拒绝，避免制造没有科学差异的版本。

## 创建规则

1. 读取来源任务，但绝不回写来源 JSON、M6 报告或导出文件。
2. 把来源批次的全部靶标重建为 M1 输入，再应用明确列出的变更，确保重复记录名、
   模板长度、IUPAC、对象和参数硬边界重新得到批次级校验。
3. 逐靶标比较 M1 相关输入。模板序列的差异只在 diff 中保存长度和 SHA-256，
   不重复嵌入整段序列。
4. 有差异的靶标标记为 `RERUN_REQUIRED`，不复制任何旧的 M2～M5 结果。
5. 无差异的靶标标记为 `REUSED`，保留完整 M2～M5 快照和来源结果 SHA-256。
6. 新版本保存直接来源 `source_batch_id`、`source_version`，以及同一谱系内单调递增的
   `task_version` 与 `lineage_root_batch_id`。

来源批次必须已经完成 M5。来源 M6 报告可存在也可不存在；新批次的 M6 报告不会
复制来源文件，而是在新版本完成 M5 后重新生成。

## 后续阶段执行

新版本若预检无阻塞，状态为 `QUEUED`，按通常顺序调用 M2、M3、M4、M5、M6：

- M2、M3、M4 和 M5 仅对 `RERUN_REQUIRED` 靶标执行对应计算；
- `REUSED` 靶标不调用 Primer3、特异性或 M5 汇总引擎，保留原有结果；
- 每一阶段写入 `TARGET_M{2..5}_REUSED` 事件，连同来源批次和靶标 ID；
- M5 重新汇总整个新批次，因此批次状态仍正确反映“复用结果 + 新跑结果”的组合；
- M6 生成新的 `report_id` 和完整导出，报告 `version_diff.status=M7_APPLIED`，并列出
  重跑、复用靶标和字段差异。

如果新输入需要确认或有阻塞，新批次为 `PENDING_CONFIRMATION`，不会使用旧结果掩盖
新的输入问题。

## 当前边界

M2 的搜索策略仍是 `WHOLE_TEMPLATE_ONLY`，区域切片与排除区策略受 DEC-01 约束。
因此 M7 不接受“区域”作为伪支持字段；在区域策略正式定义并进入 M2 前，任何区域
改变都必须先完成对应的科学策略实现，不能被错误标记为已重跑。

批次默认的批量隐式修改也不作为 M7 输入。调用方必须把每个受影响靶标明确放进
`target_changes`，使重跑范围可审计。

## 验证

自动化测试：`tests/test_wf01_v02_m7.py`

覆盖：

- AC-VER-01：源任务不可变、新版本/来源链、字段 diff、变更靶标重跑和未变靶标复用；
- 无有效差异时拒绝创建空版本；
- M2～M5 只调用变更靶标，并记录四类复用事件；
- M6 报告和 HTML 导出显示 M7 差异；
- HTTP API 和能力声明。
