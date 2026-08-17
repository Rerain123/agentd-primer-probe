---
name: agentd-p2-specificity
description: "Assess qPCR assay target, host-genome, and cross-species specificity through Agent D P2 BLAST orchestration. Use for requests mentioning BLAST verification, host exclusion, cross-reactivity, non-specific amplification, 宿主排除、特异性验证、近缘种交叉 or BLAST 数据库."
---

# Agent D P2 Specificity

Use P2 to orchestrate BLAST+ and P1 thermodynamics through Agent D. P2 is
database-dependent: never claim `blast_verified` without actual versioned
target and cross-species databases. A host database is additionally required
when the user supplied a host and requested host-genome exclusion.

## Route the request

- Validate a supplied assay: run `WF-04` with `target_db`, `host_db` and
  `cross_db` when available.
- Design and verify a new assay: run `WF-01` with the same database fields.
- Diagnose suspected off-target amplification: run `WF-07`, which invokes
  existing-assay validation first.

## Execute

1. Check P1, C6 and C0 status. In production require BLAST+ and a valid
   versioned database catalog.
2. Pass the target species and all configured database resources. Pass host
   species when the user supplied it; do not infer a host or substitute a
   database from another host species.
3. Always inspect `check_target` and `check_cross_species`. Inspect
   `check_host` when host exclusion was requested; otherwise require an
   explicit not-applicable result.
4. Report the method and evidence level exactly as returned. `NOT_CHECKED`,
   exact-FASTA fallback, or mixed methods cannot be presented as BLAST proof.
5. On a FAIL, keep the hit evidence and route redesign to WF-01 rather than
   editing an oligo sequence manually.

## Boundaries

- P2 does not design primers or assign a wet-lab specificity conclusion.
- Host is optional workflow context. Missing host must not prevent target and
  broad-background specificity evaluation.
- Do not send sequences to a public BLAST service. P2 only uses BLAST+ with
  locally configured, versioned target, host and cross/background databases.
- Read [references/contract.md](references/contract.md) for database and
  verdict semantics.
