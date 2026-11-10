**CLI: lifecycle actions with `--no-wait` print the sandbox** — `start`, `stop`,
`pause`, `resume`, `fork` and `delete` with `--no-wait` print the sandbox detail
too instead of the operation `{"id", "status"}` (a sandbox the delete already
removed still prints the operation).
