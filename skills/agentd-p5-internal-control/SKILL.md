---
name: agentd-p5-internal-control
description: "Design and validate a host qPCR internal control with Agent D P5. Use for ACTB, GAPDH, 18S rRNA, housekeeping genes, internal controls, 内标、管家基因、ACTB、GAPDH or 18S rRNA requests."
---

# Agent D P5 Internal Control

Use P5 through WF-03, WF-02 or WF-06. It selects a host housekeeping-gene
assay from user-supplied or server-catalog sequences; it does not fetch an
unversioned sequence from the public internet during a production run.

## Execute

1. Require `host_species`; supported built-in priorities are pig, cattle and
   chicken.
2. Supply at least five homologous sequences through `gene_sequences`,
   `gene_fasta_paths`, or configured catalog resource
   `internal:<species>:<gene>:sequences`.
3. Run `WF-03` (`POST /v1/wf03/internal-control`). Include existing target
   oligos for a cross-dimer check when building a panel.
4. Report selected gene, RefSeq identifier, conservation, candidate assays,
   target-cross check, specificity check, P6 status and warnings.
5. Treat `NOT_CONFIGURED` as an integration gap, not a successful internal
   control. For 18S rRNA preserve the 50–100 nM limiting-primer strategy.

## Boundaries

- Sequence conservation is necessary but does not establish expression
  stability; require experimental geNorm/NormFinder-style confirmation.
- P5 does not assign a final multiplex dye when P4 reports a channel conflict.
- Read [references/contract.md](references/contract.md) for sources and
  verdict rules.
