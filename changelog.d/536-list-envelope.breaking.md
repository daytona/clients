**CLI: one list shape** — every list prints `{"items": [...]}` with an array:
also an empty `--all` result (it printed `null`), `billing plan list` when the
API's `plans` is null or missing, and `cpu-type list --region` / `mac-model
list --region` (they printed the raw catalog). `sandbox search`, which asks
one API, prints `items`, `total` and `total_relation`, plus `next_cursor`
when another page exists, and no longer an always-false `incomplete`.
