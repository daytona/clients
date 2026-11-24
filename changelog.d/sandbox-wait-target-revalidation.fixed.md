**Waits return in a requested state** — `wait()` and the lifecycle methods
(Python, TypeScript and Go) no longer return a handle in another state when
the read that follows a target state reported by the events stream shows
something else: `destroyed` or `error` raises `SandboxTerminalStateError`
(and `destroying` too, unless the wait targets `destroyed`), and any other
state keeps the wait going under the same deadline. A failed read still keeps the reported target. The create
and fork waits also keep waiting, within `wait_seconds` / `waitSeconds`,
instead of stopping at that read; they still return the handle however
their wait ends.
