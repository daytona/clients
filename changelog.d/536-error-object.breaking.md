**CLI: machine-readable errors** — with JSON or YAML output (the default off a
terminal) an error is one line on stderr, `{"error": {"exit_code", "code",
"message", "source", "trace_id", "hint"}}`, carrying the server's code,
source and trace id. `source` is `CLI` for a local error, `PROCESS` or `SSH`
for a passed-through exit status, and omitted when a server error names none.
