**`list()` and `search()`** — the client's `list()` and `iter()` take only a
cursor and a limit and always read the read-your-writes sandbox list; filters
moved to `search()`. `search()` takes `states` with SDK sandbox states
(`["started", "paused"]`) instead of the API's `status` values; `"unknown"` is
refused. Python `ListSandboxesQuery` / `SearchSandboxesQuery` are renamed
`ListSandboxesOptions` / `SearchSandboxesOptions`, and TypeScript exports
`FindByNameOptions`. `find_by_name()` / `findByName()` still returns every
match.
