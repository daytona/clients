**Waits return in a requested state** — `wait()` and the lifecycle methods
(Python, TypeScript and Go) no longer return a handle in another state when
the read that follows a target state reported by the events stream shows
something else: `destroying`, `destroyed` or `error` raises
`SandboxTerminalStateError`, and any other state keeps the wait going under
the same deadline. A failed read still keeps the reported target. The create
and fork waits also keep waiting, within `wait_seconds` / `waitSeconds`,
instead of stopping at that read; they still return the handle however
their wait ends.
