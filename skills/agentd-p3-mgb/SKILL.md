---
name: agentd-p3-mgb
description: "Evaluate an MGB qPCR probe with Agent D P3 corrected Tm and MGB-specific rules. Use for requests involving MGB probes, short probes, FAM 5-prime G quenching, MGB Tm correction, MGB探针 or MGB修正Tm."
---

# Agent D P3 MGB Probe Evaluation

Use P3 only for a probe that will carry an MGB modification. It combines the
P1 unmodified Tm with a clearly labelled public-evidence engineering
approximation; it is not Primer Express output.

## Execute

1. Require a probe sequence and set `use_mgb: true` in `WF-01` or `WF-04`.
2. Provide `fluorophore` and optional zero-based `snp_position` when known.
3. Read `candidates[].mgb_assessment`, including base Tm, correction,
   corrected Tm, all hard rules, soft rules, confidence and estimated error.
4. Reject hard-rule failures. Keep CAUTION results only with their warnings and
   a redesign or wet-lab confirmation recommendation.
5. Return the corrected value as an approximation with its ±3°C typical error;
   do not call it experimentally measured Tm.

## Boundaries

- Apply P3 only to 13–20 nt MGB probes. Use standard P1 probe evaluation for
  ordinary TaqMan/BHQ probes.
- Do not use P3 for specificity, fluorophore-panel compatibility or final assay
  acceptance.
- Read [references/contract.md](references/contract.md) for exact rule and
  verdict behavior.
