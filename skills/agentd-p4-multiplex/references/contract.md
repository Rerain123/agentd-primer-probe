# P4 runtime contract

Use `WF-02` with `targets`, `instrument`, optional `rox_as_reference`, and
optional `include_internal_control`. The default instrument is `CFX96`.

P4 checks 14 dyes, quenchers, configured instruments, all full and 3′-local
heterodimer pairs, primer/probe Tm spread, amplicon GC spread, and WF-02
amplicon-length separation.

| Strict condition | Effect |
|---|---|
| Duplicate dye, VIC plus HEX, emission gap under 15 nm | INCOMPATIBLE |
| TAMRA quencher, ROX passive-reference conflict, too many channels | INCOMPATIBLE |
| Full cross-dimer below -7 kcal/mol | INCOMPATIBLE |
| Tm/GC/3′ dimer caution | MARGINAL unless another failure exists |
| Amplicon separation below 20 bp | INCOMPATIBLE |

`multiplex.channel_assignment` is the assigned report-channel map. The full
P4 report appears under `multiplex.panels[].compatibility` when a compatible or
marginal panel exists.
