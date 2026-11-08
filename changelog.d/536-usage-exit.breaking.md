**CLI: every usage mistake exits 2** — a wrong number of arguments, an
unknown command, `daytona help <unknown>`, a missing required flag
(`registry set` without `--username`), flags that exclude each other
(`login --token` with `--device`, `--browser` or `--client-id`; `--device`
with `--browser`) and an empty `--region=` exit 2, like an unknown flag (they
exited 1 or 0, or the flag was ignored).
