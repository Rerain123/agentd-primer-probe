# P5 runtime contract

Use `WF-03` locally or `POST /v1/wf03/internal-control` on the server.

| Input | Meaning |
|---|---|
| `host_species` | Required; pig, cattle or chicken for built-in priorities |
| `preferred_genes` | Optional ordered override of ACTB, GAPDH, 18S rRNA |
| `gene_sequences` / `gene_fasta_paths` | Versioned user-supplied homolog sequences |
| `existing_targets` | Optional target oligos for cross-dimer evaluation |

P5 requires at least five homologous sequences. Conservation ≥0.99 passes,
0.97–0.99 cautions, and lower values are not selected. P5 returns
`NOT_CONFIGURED` when source data are missing. It uses Cy5/BHQ-2 by default;
an existing Cy5 target must be resolved by the multiplex layer. P1, MAFFT and
BLAST+ availability determine full capability; P6 is optional and must be
reported as `not_evaluated` when unavailable.
