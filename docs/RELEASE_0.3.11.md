# Agent D 0.3.11 发布说明

## 新版 WF01 v0.2 接入 MAFFT

- 多条输入序列默认作为同一靶标：M1 保留全部模板，M2 调用 MAFFT 并等待完成。
- MAFFT 输出由 Python 计算保守区，Primer3 按“保守区 × 用户/default/L1/L2 去重参数档”执行。
- 单条输入序列跳过 MAFFT，直接使用整条模板进入 Primer3。
- 候选的引物、探针和扩增子坐标会从保守区局部坐标映射回首条参考序列。
- M2 结果新增 MAFFT 状态、比对方法、序列数、保守区及保守性元数据，便于审计。

## 输入兼容

- `sequence_mode` 默认值为 `alignment_target`。
- 原“多记录分别作为独立靶标”的行为仍可通过 `sequence_mode=independent_targets` 显式启用。

## 部署边界

- 本版本不新增数据库依赖，也不要求继续下载数据文件。
- 服务器镜像沿用现有 `mafft_executable=mafft` 配置；部署后只需验证容器内 MAFFT 可执行并运行 WF01 多序列冒烟测试。
