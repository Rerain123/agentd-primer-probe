# P2 runtime contract

Use `WF-01` for a new design and `WF-04` for an existing assay. In the API,
use `/v1/wf01/design` or `/v1/wf04/validate`.

| P2 mode | Database field | Result semantics |
|---|---|---|
| Target confirmation | `target_db` | Forward and reverse need qualified target hits |
| Host exclusion (optional) | `host_db` + `host_species` | When a host is supplied, high-risk host hits fail the assay; otherwise this mode is not applicable |
| Cross species | `cross_db` + `target_species` | Hits produce a cross-reactivity caution |

The service uses `blastn-short`; target/cross e-value is `1e-5`, host e-value
is `0.1`. A normal production result has `specificity.method = blastn`, target
and cross/background modes evaluated, and host mode evaluated only when a host
was supplied. Database versions and path resolution are stored in
`provenance.database_resolution` when a catalog supplied them.

## Database boundary

P2 only runs BLAST+ against locally configured, versioned target, host and
cross/background databases. No public remote BLAST submission or asynchronous
review endpoint is part of this contract.
