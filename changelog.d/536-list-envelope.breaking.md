**CLI: one list shape** — every list prints `{"items": [...]}` with an array:
also an empty `--all` result (it printed `null`), `billing plan list` when the
API's `plans` is null, and `cpu-type list --region` / `mac-model list
--region` (they printed the raw catalog).
