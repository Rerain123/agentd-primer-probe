---
name: agentd-p4-multiplex
description: "Check multiplex qPCR panel fluorescence, instrument channels, cross-dimers, Tm spread, and amplicon spacing with Agent D P4. Use for multiplex qPCR, panel compatibility, dye/channel assignment, ROX, VIC/HEX, 多重PCR、荧光通道、交叉二聚体 or 面板兼容性."
---

# Agent D P4 Multiplex Compatibility

Use P4 through WF-02 or WF-06 after each target has a candidate assay. P4
verifies a supplied channel assignment; it does not invent one independently.

## Execute

1. Collect two to four targets, their sequences or catalog identifiers, the
   instrument model, and whether ROX is a passive reference.
2. Run `WF-02` (`POST /v1/wf02/multiplex`) or multi-target `WF-06`.
3. Inspect `multiplex.panels[].compatibility`, channel assignment, relaxation
   steps and all listed issues.
4. Accept only `COMPATIBLE` panels as strict recommendations. Preserve
   `MARGINAL` and `INCOMPATIBLE` outputs with their causes; do not erase an
   issue by changing a dye in prose.
5. If no strict panel exists, use the recorded relaxation or split-tube
   recommendation and send any redesign back through WF-01/WF-02.

## Boundaries

- Require the actual qPCR instrument; generic “multiplex compatible” claims
  are insufficient.
- P4 does not replace P2 specificity checks or P1 single-assay rule checks.
- Read [references/contract.md](references/contract.md) before selecting a
  panel.
