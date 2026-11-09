**CLI: every usage mistake exits 2** — a wrong number of arguments, an
unknown command, `daytona help <unknown>`, a missing required flag
(`registry set` without `--username`), flags that exclude each other
(`login --token` with `--device`, `--browser` or `--client-id`; `--device`
with `--browser`), an empty `--region=`, and flags that would be ignored
(`sandbox update --clear-labels` with `--label`, `sandbox process signal
--escalate-to` without `--escalate-after`) exit 2, like an unknown flag (they
exited 1 or 0, or the flag was ignored).
