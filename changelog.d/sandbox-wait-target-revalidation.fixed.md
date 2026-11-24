**Waits return in a requested state** — `wait()`, the lifecycle methods and
the create and fork waits (Python, TypeScript and Go) no longer return a
handle in another state when the read that follows a target state reported
by the events stream shows something else: a terminal state raises
`SandboxTerminalStateError`, and any other state keeps the wait going under
the same deadline. A failed read still keeps the reported target.
