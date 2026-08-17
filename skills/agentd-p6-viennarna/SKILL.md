---
name: agentd-p6-viennarna
description: "Assess qPCR amplicon secondary structure with Agent D P6 ViennaRNA. Use for RNAfold, amplicon MFE, secondary structure, stable hairpins, 二级结构、扩增子结构、ViennaRNA、RNAfold or ΔG structure-risk requests."
---

# Agent D P6 ViennaRNA Structure Assessment

Use P6 as an optional structure-risk assessment within WF-01, WF-03, WF-04,
WF-06 or WF-07. It evaluates a DNA amplicon under fixed PCR-oriented
conditions and never blocks a workflow merely because ViennaRNA is absent.

## Execute

1. Check `P6_ViennaRNA` tool status and confirm DNA parameters are available.
2. Run a workflow that produces or validates an amplicon; provide `template`
   for WF-04/WF-07 when an existing assay needs structure assessment.
3. Read `amplicon_structure` on each candidate or internal-control candidate.
4. Preserve the calculation conditions, MFE ΔG, structure status and warnings.
5. If P6 is unavailable or an amplicon is missing, return `not_evaluated` and
   retain the workflow result as partial evidence rather than fabricating ΔG.

## Boundaries

- Accept amplicons no longer than 500 nt.
- Treat WARNING/CAUTION as redesign or experimental-review evidence, not as a
  direct claim of failed amplification.
- Read [references/contract.md](references/contract.md) for parameters and
  threshold semantics.
