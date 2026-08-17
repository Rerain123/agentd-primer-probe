# P3 runtime contract

Run `WF-01` or `WF-04` with `use_mgb: true`; `WF-04` also accepts
`probe_type: "MGB"`, `fluorophore`, and `snp_position`.

P3 returns `mgb_assessment` with the following hard checks:

- R-MGB-01: probe 5′ is not G; FAM also rejects position 2 G.
- R-MGB-02: length is 13–20 nt.
- R-MGB-03: no four identical consecutive bases.
- R-MGB-04: final four bases contain at most two G.
- R-MGB-05: GC is 30–80%.

The corrected Tm target is 65–72°C. Below 60°C, above 78°C, or any hard-rule
failure returns `FAIL`; a non-target Tm or soft-rule warning returns `CAUTION`.
The piecewise correction is a public-evidence approximation, not a proprietary
manufacturer model.
