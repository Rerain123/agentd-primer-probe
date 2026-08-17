# Agent D P1～P6 Skill 目录

项目中的 `skills/` 包含可加载的原子能力 Skill。每个 Skill 均通过现有 Agent D CLI 或 HTTP API 调用同一套代码、配置和 Schema，不复制科学阈值或算法实现。

| Skill | 能力 | 服务入口 |
|---|---|---|
| `agentd-p1-primer3` | 引物/探针设计与热力学 | WF-01、WF-04 |
| `agentd-p2-specificity` | 靶标、宿主与近缘种特异性 | WF-01、WF-04、WF-07 |
| `agentd-p3-mgb` | MGB 探针修正 Tm 与规则 | WF-01、WF-04 |
| `agentd-p4-multiplex` | 多重面板兼容性 | WF-02、WF-06 |
| `agentd-p5-internal-control` | 宿主内标设计 | WF-03、WF-02、WF-06 |
| `agentd-p6-viennarna` | 扩增子二级结构 | WF-01、WF-03、WF-04、WF-06、WF-07 |

每个目录包含：

- `SKILL.md`：触发条件、执行步骤、边界和风险控制；
- `agents/openai.yaml`：显示名称和默认调用提示；
- `references/contract.md`：输入、输出、状态与阈值摘要。

安装到支持 Codex Skill 的运行时时，保持每个子目录为一个独立 Skill；服务器源码安装包也会携带 `skills/`。科学工作流仍应优先调用 API/CLI，不能把 Skill 文本当作第二套计算实现。
