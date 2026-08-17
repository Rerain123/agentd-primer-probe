# P6 runtime contract

P6 runs indirectly through WF-01, WF-03, WF-04, WF-06 and WF-07. Inspect
`candidates[].amplicon_structure` or `internal_control.candidates[]`.

The adapter loads ViennaRNA DNA Mathews 2004 parameters on every protected
call, folds DNA after T→U conversion at 60°C and 50 mM Na+, and supports up to
500 nt.

| MFE ΔG | Status |
|---|---|
| ≥ -20 kcal/mol | PASS |
| -30 to below -20 kcal/mol | CAUTION |
| < -30 kcal/mol | WARNING |
| Tool/amplicon unavailable | `not_evaluated` |

The full adapter can return suboptimal structures; workflow results normally
carry the MFE assessment. This is a soft rule with approximate predictive
power and must not replace PCR optimization.
