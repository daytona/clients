**CLI machine output** — off a terminal the default output is JSON (pass
`--output table` for a table); usage mistakes (wrong number of arguments, an
unknown command) exit 2 like an unknown flag; in JSON mode an error is one
stderr object `{"error": {"exit_code", "code", "message", "source",
"trace_id", "hint"}}`; every list prints `{"items": [...]}` with an array, also
with `--all` and for `cpu-type list` / `mac-model list --region`; `image logs
--follow -o json` streams NDJSON `{"data": ...}` and YAML follow is refused;
toolbox-derived output (`sandbox process list|get|wait|logs`, `sandbox docker
status`) is snake_case (`exit_code`, `next_cursor`, `process_id`).
