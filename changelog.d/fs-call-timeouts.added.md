**Per-call timeout for long file-system calls** — `fs.remove()`, `fs.grep()`,
`fs.glob()`, `fs.replace()` and `fs.chmod()` take a per-call timeout that
replaces the client's request timeout for that call, so a recursive remove or
a search over a large tree no longer has to fit the default 60 seconds.
Python takes `timeout=` (seconds, sync and async) and TypeScript
`timeoutSeconds` (in `RemoveOptions`, `FilePermissionsOptions` and the new
`FileSearchOptions` / `ReplaceOptions`), where `0`, or a value above about
24.8 days, means no limit. Go takes `Timeout` in `RemoveFileOptions` and
`FilePermissionsOptions`, and a variadic last `*FileSearchOptions` /
`*ReplaceOptions` on `Grep`, `Glob` and `Replace`, where zero keeps the
request timeout and `daytona.NoTimeout`, or a duration above about 24.8 days,
means no limit. A timeout ends the call, not the work in the sandbox.
