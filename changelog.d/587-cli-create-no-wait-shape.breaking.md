**CLI: `--no-wait` prints the sandbox, never its access token** — `sandbox
create`, `create-from-snapshot` and `create-macos` with `--no-wait` printed the
create acknowledgement, which carries the sandbox's access token; they now
print the sandbox as `sandbox get` does (read once after the create), the same
shape a waited create prints; if that read fails, the create still succeeds
and prints `{"id", "name", "status"}`. `start`, `stop`, `pause`, `resume`, `fork` and
`delete` with `--no-wait` print the sandbox detail too instead of the
operation `{"id", "status"}` (a sandbox the delete already removed still
prints the operation).
