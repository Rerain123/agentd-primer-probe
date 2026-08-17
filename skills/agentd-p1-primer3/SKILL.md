---
name: agentd-p1-primer3
description: "Design or validate qPCR primer/probe oligos with Agent D P1 Primer3 thermodynamics. Use for requests about TaqMan primer/probe design, primer Tm, hairpins, dimers, or existing assay validation, including Chinese requests such as 引物设计、探针设计、Tm、二聚体、已有引物验证."
---

# Agent D P1 Primer3

Use P1 as the only thermodynamics and oligo-design authority. Invoke the
existing Agent D service; do not recreate Primer3 parameters, calculate Tm
with an alternate formula, or silently enable a fallback.

## Route the request

- Design a new assay: run `WF-01` (`POST /v1/wf01/design`).
- Validate supplied forward/reverse primers, optionally with a probe: run
  `WF-04` (`POST /v1/wf04/validate`).
- Include MGB correction only when the request explicitly requires an MGB
  probe; set `use_mgb: true` and let P3 evaluate it.
- Decline SYBR design as out of the current Agent D delivery scope; do not
  label a probe-based result as SYBR.

## Execute

1. Check `GET /v1/tool-status` or `agentd tool-status`; require `P1.available`.
2. Validate the request sequence as 5′→3′ DNA and preserve the caller's
   assay/target identifiers.
3. Submit the workflow request without overriding scientific thresholds.
4. Return candidate sequences, amplicon sequence and length, Tm method,
   salt/DNA concentration metadata, rules, evidence level, warnings and the
   experimental-validation disclaimer.
5. If P1 is unavailable, report the actual tool status. A development fallback
   may only be used when the caller explicitly requests it; its output remains
   `PARTIAL` and `in_silico_only`.

## Boundaries

- Leave MGB correction to P3, specificity to P2, multiplex compatibility to
  P4 and amplicon folding to P6.
- Treat all calculated values as design evidence, never clinical or production
  conclusions.
- Read [references/contract.md](references/contract.md) before composing a
  request or interpreting a P1 result.
