# P3 运行契约

运行 `WF-01` 或 `WF-04` 并设置 `use_mgb: true`；`WF-04` 还接受 `probe_type: "MGB"`、`fluorophore` 和 `snp_position`。

P3 返回 `mgb_assessment`，其中包含以下硬规则：

- R-MGB-01：探针 5′ 端不得为 G；FAM 探针的第 2 位也不得为 G。
- R-MGB-02：长度必须为 13–20 nt。
- R-MGB-03：不得出现 4 个连续相同碱基。
- R-MGB-04：末端 4 个碱基最多包含 2 个 G。
- R-MGB-05：GC 含量必须为 30–80%。

修正后 Tm 的目标范围为 65–72°C。Tm 低于 60°C、高于 78°C，或任一硬规则失败时返回 `FAIL`；Tm 偏离目标范围或出现软规则警告时返回 `CAUTION`。分段修正是基于公开证据的近似，不是厂商专有模型。
