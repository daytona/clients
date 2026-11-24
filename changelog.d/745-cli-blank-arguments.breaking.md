**CLI: blank positional arguments are refused** — an empty or whitespace-only
argument where a command takes an id, a name, a host, a port or a path
(`daytona sandbox get ''`, `sandbox create-from-snapshot ' '`,
`reservation get ''`, `registry get ''`, …) is a usage error (exit 2) before
any credential check or request. Breaking for scripts that pass an unset
variable: before, the value reached the API (a not-found or collection
answer), and the optional `[id|name]` of `org get`, `org use`, `project get`,
`project use` and the budget commands fell back to the saved selection; leave
the argument out to use the saved selection. The `sandbox search` query,
`config set`'s value, the arguments after `--` and the `ssh` arguments are
unchanged.
