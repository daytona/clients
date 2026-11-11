**`v0`: `run_commands` / `runCommands` list quoting** — with the 0.231.1
bundle, a list argument containing a quote no longer produces a broken
`RUN` line: double quotes are escaped once instead of twice, and single quotes
are left as written.
