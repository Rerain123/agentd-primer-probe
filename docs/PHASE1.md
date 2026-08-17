# Agent D 第一阶段实现说明

## 实现边界

第一阶段交付两个可运行 Workflow：

- WF-01：单靶标 TaqMan 引物探针设计。
- WF-04：已有引物探针热力学和特异性验证。

专业能力包括 P1 Primer3 适配器、P2 特异性编排、C1 序列 IO、C5 MAFFT 适配器、C6 BLAST+ 适配器、C7 统一 JSON Schema，以及 R-01～R-21 规则引擎。

P3～P6、WF-02/03/05/06/07 和简并碱基正式策略不属于本阶段。其中 P6 对应的扩增子二级结构规则会稳定输出 `NOT_EVALUATED`，不会被静默视为通过。

## 科学计算边界

- Primer3、BLAST+、MAFFT 可用时调用真实工具。
- 开发降级功能必须由请求中的 `allow_fallback=true` 或 CLI 的 `--allow-fallback` 显式开启。
- 开发降级 Tm 使用 Wallace 近似，只用于验证工程链路；不标记为 SantaLucia。
- 开发降级特异性只做本地 FASTA 精确匹配，不产生 BLAST e-value。
- 任何降级结果均为 `PARTIAL` 和 `in_silico_only`。
- 未配置宿主或近缘种数据库时输出 `NOT_CHECKED`，不默认通过。
- P1 在导入 `primer3-py` 前通过子进程探测原生扩展。DLL、ABI 或路径兼容性错误只会让 P1 进入不可用/降级状态，不会导致 API 主进程崩溃。
- Windows 安装路径包含非 ASCII 字符时，P1 会将已安装的 `primer3` 包镜像到版本化 ASCII 临时缓存，再加载其热力学参数；缓存路径可通过 `AGENTD_ASCII_CACHE_DIR` 覆盖。

## Workflow 数据流

```text
输入校验
  → C1序列规范化
  → C5多序列比对/保守区
  → P1候选设计
  → R-01～R-18规则过滤
  → P2靶标/宿主/交叉检查
  → R-19～R-21证据与完整性
  → 候选评分、排序、自检
  → C7 Schema验证
  → JSON结果和任务记录
```

## 验收检查

1. 同一输入和同一配置产生可复现的候选顺序。
2. 每个候选输出完整的 R-01～R-21 判定记录。
3. 每项判定包含实际值、阈值、状态和数据来源。
4. P1/P2/C5 不可用时不会伪造科学结果。
5. WF-01 无候选时返回结构化失败报告和各阶段淘汰计数。
6. WF-04 支持模板可选；无模板时相关规则为 `NOT_EVALUATED`。
7. 输出通过 C7 结构校验并可由 CLI、API 和任务查询共同消费。
