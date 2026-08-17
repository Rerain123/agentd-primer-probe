# P1 runtime contract

Use the installed CLI locally:

```powershell
.venv\Scripts\agentd.exe run --workflow WF-01 --input request.json --output result.json
.venv\Scripts\agentd.exe run --workflow WF-04 --input request.json --output result.json
```

Use the server endpoints `POST /v1/wf01/design` and `POST /v1/wf04/validate`.
All results must be schema `3.0.0-phase3`.

| Operation | Required input | Main output |
|---|---|---|
| New design | `target_id` and `sequence`/`sequences`/`fasta_path` | `candidates[]` with P1 Tm and oligos |
| Existing validation | `forward_primer`, `reverse_primer`; optional `probe`, `template` | one validated candidate with rules |

P1 uses Primer3 SantaLucia 1998 conditions configured in
`config/defaults.json`: 50 mM monovalent salt, 3 mM divalent salt, 0.8 mM
dNTP, 250 nM primers and 200 nM probes. The workflow owns R-01 to R-12
verdicts; P1 does not independently make a clinical decision.
