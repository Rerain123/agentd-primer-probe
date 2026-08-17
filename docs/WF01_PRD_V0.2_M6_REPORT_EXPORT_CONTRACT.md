# WF-01 PRD v0.2 M6：完整报告与导出契约

M6 只读取 M2～M5 已保存的设计、规则、特异性和状态证据，不重新运行
Primer3、BLAST 或评分，也不改变科学结论。其职责是构建一份完整、可校验的
canonical report，并默认生成面向交付的候选 HTML、科学过程详情 HTML、
21条规则验证 HTML 和科学工具数据 ZIP；
CSV、PDF 在用户明确请求时按需生成。

接口：

- `POST /v1/wf01/tasks/{batch_id}/run-m6`
- `GET /v1/wf01/tasks/{batch_id}/report`
- `GET /v1/wf01/tasks/{batch_id}/exports/{format_name}`
- `AgentDService.execute_wf01_v02_m6(batch_id)`
- 纯组装器 `WF01V02CanonicalReportAssembler`

## 单一数据源

每个批次只生成一份完整 canonical report：

- Schema：`wf01-prd-v0.2-canonical-report-v1`
- 导出契约：`wf01-prd-v0.2-export-v3`
- 数据范围固定为 `COMPLETE_BATCH`
- 内容以 `content_sha256` 标识
- 用户下载格式固定为 `html`、`csv`、`pdf`
- M6 默认生成 `html`、`process_html`、`rules_html` 和 `scientific_data`；`csv`、`pdf` 为按需格式
- canonical JSON 作为内部 API 和可审计载体，不提供用户 JSON 下载端点

HTML、CSV 和 PDF 必须携带或记录同一个 `report_id` 与
`canonical_content_sha256`。交付 HTML 只在文档元数据中记录这两个同源标识，
不内嵌 canonical JSON；CSV 每行记录报告 ID 和内容摘要，PDF 附加
`canonical-report.json`。完整 canonical report 仍保存在服务端，作为审计和按需导出的数据源。

## AC-RPT-01：完整科学字段

每个出现过的 Primer3 候选都进入 `all_primer3_candidates`，并包含：

- R01～R21 的逐条状态、实际值、阈值、来源和说明；
- 正反向引物、普通 TaqMan 探针及扩增子；
- Tm、GC%、长度、发卡/二聚体和 Primer3 热力学计算元数据；
- 三维评分、扣分、最终分和排名；
- 特异性对象、数据库版本、阈值、命中详情和证据状态；
- 扩增子二级结构的明确状态；
- 候选来源和去重后的全部 occurrence。

R21 表示“报告字段完整”，不表示实验成功或特异性已完成。任何必需字段缺失时
M6 完整性校验失败，禁止生成一份表面完整的报告。

报告固定带有免责声明：结果仅为计算机模拟的科研设计建议，尚未经过实验验证，
不得直接用于临床诊断、治疗、生产放行或其他关键决策。

## AC-RPT-02：在线筛选与导出隔离

`GET .../report` 支持按靶标、候选集合和分页返回只读展示视图。展示视图明确
返回：

```text
export_data_scope = COMPLETE_BATCH
canonical_target_total = 完整批次靶标数
```

筛选和分页不写回 canonical report，也不参与导出。HTML 读取 M6 已固化文件；
CSV/PDF 首次下载时从固化的 canonical report 生成。因此在界面只查看一个靶标后
下载，不会改变任何已生成文件。交付 HTML 的范围由报告契约固定为“每个靶标前 10 名”，
不是由在线筛选或分页造成的裁剪；CSV/PDF 仍包含其既定的完整数据范围。

## AC-RPT-03：结果集合与证据

每个靶标完整保留：

- `top10`
- `all_qualified_candidates`
- `all_deliverable_candidates`
- `all_specificity_incomplete_candidates`
- `all_excluded_candidates`
- `all_primer3_candidates`
- ExplorationManifest、ExplorationUnit 和参数运行日志
- 三类特异性对象、数据库、阈值、查询序列和命中证据
- M5 的完成范围、未完成范围、原因和建议

canonical report 不会因为候选进入 Top10 而删除其余候选，也不会因为外部工具失败而删除已完成
的搜索记录。交付 HTML 是 canonical report 的前 10 名人类可读投影，不承担过程审计和完整数据展示职责。

## AC-RPT-04：本期外能力

以下字段必须显式存在，不能留空或暗示已经完成：

| 能力 | 状态 |
|---|---|
| MGB | `NOT_APPLICABLE` |
| 多重 qPCR | `NOT_APPLICABLE` |
| 内标 | `NOT_APPLICABLE` |
| 保守性分析 | `NOT_EVALUATED` |
| 扩增子二级结构 | `NOT_EVALUATED` |

## 六种用户文件

| 格式 | 内容 |
|---|---|
| HTML | 独立可读的交付页面；每个靶标仅展示前 10 名候选的关键指标、引物探针序列、扩增子序列，以及每套候选已通过、需关注、未通过和未完成的检测项目；不展示规则代码、内部字段名、搜索过程或完整 canonical JSON |
| 过程 HTML | 中文科学过程详情；展示工具与数据清单、任务输入、Primer3 搜索轮次、全部候选、逐项检测、评分、特异性命中和未完成项目；不展示 JSON、命令行或内部字段名 |
| 21条规则验证 HTML | 以最终交付候选为范围，固定展示 R-01～R-21；无问题标记 `✓`，警告、失败、未执行或证据不足标记 `✗` 并说明问题；已判定不适用的规则标记 `✓` 且明示“不适用” |
| 科学工具数据 ZIP | 包含过程 HTML、21条规则验证 HTML、中文说明和九份中文表头 CSV；不包含 JSON、程序代码、命令行、代码式日志或密钥信息；不可变原始证据仅保留在服务端内部 |
| CSV | 完整候选结果；每行包含报告/批次/靶标标识、排名、引物探针序列、扩增子、得分、判定和证据等级 |
| PDF | 可读的批次和靶标报告、候选与规则/证据摘要、免责声明，以及完整 JSON 附件 |

交付 HTML 使用中文业务语言。GC、PCR、TaqMan、Tm、bp、nt 等通用科学缩写可以保留；
内部枚举、JSON 字段名、规则编号、候选内部 ID 和运行日志不得作为正文展示。
检测结论必须使用中文业务名称逐项解释；不能只显示“通过/未通过”的数量，也不能用
内部规则编号代替检测项目。软性警告必须与硬性未通过区分，并说明触发原因。
退火温度或保守性没有经过当前流程实际计算时，必须明确显示“需梯度实验确定”或
“未评估”，不得从其他评分字段推断或虚构数值。科学过程详情是独立附件，
不嵌入或污染面向交付的 Top10 主报告。

候选 HTML、过程 HTML、21条规则验证 HTML、科学工具数据 ZIP 与 PDF 使用报告生成时的北京时间命名。
使用连字符代替时分秒中的冒号，保证 Linux、Windows 和对象存储均可安全使用；
CSV 继续使用任务名命名。

CSV 使用带 BOM 的 UTF-8，便于常见表格客户端直接打开；PDF 使用 CJK 字体并带页眉、
页脚。Linux 镜像安装 `fonts-noto-cjk`，运行依赖包含 ReportLab 和 pypdf。

## 状态、持久化与不可覆盖

M6 只能在 M5 完成后运行。执行时：

1. 短暂进入 `REPORTING`；
2. 组装并校验完整 canonical report；
3. 从 canonical report 的每个靶标前 10 名生成交付 HTML；
4. 从同一数据源生成中文科学过程详情 HTML 和人类可读科学工具数据 ZIP；
5. 保存 canonical JSON 和四个默认附件；
6. 记录每个附件的大小、SHA-256、MIME 类型和附件路径；
7. 恢复 M5 的科学批次状态并记录 M6 完成事件。

用户明确请求 CSV/PDF 后，`get_wf01_export` 从已保存的 canonical report
生成并持久化相应文件，再返回真实附件信息。

同一批次不允许原地覆盖 M6 结果。需要改变输入或重新运行科学阶段时，由 M7
创建新任务版本。

## 当前限制

- M6 不补做或替代 M4 特异性。服务器本地版本化数据库或 BLAST+ 不可用时，
  对应证据继续显示 `NOT_EVALUATED` 或不完整范围。
- DEC-01/DEC-02 等未批准策略仍会原样进入报告，不会被 M6 升级为 PASS。
- 任务版本、差异和靶标级复用由 M7 创建；M6 将其原样写入报告及各实际生成的导出。

## 验证

自动化测试：`tests/test_wf01_v02_m6.py`

覆盖：

- AC-RPT-01：canonical report 保留 R01～R21、热力学元数据、二级结构状态和免责声明；
- AC-RPT-02：筛选视图不修改固化 HTML/CSV/PDF；交付 HTML 固定只显示每个靶标前 10 名；
- AC-RPT-03：canonical report 保留 Top10、所有合格、所有排除、搜索记录和命中证据；交付 HTML 不展示过程明细；
- AC-RPT-04：本期外能力的显式状态；
- canonical report JSON Schema；
- 三种文件的同源标识、按需生成和 PDF 附件；
- API、持久化、不可覆盖和依赖打包。
