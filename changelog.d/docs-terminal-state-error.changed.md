**`SandboxTerminalStateError` state** — documented in Python, TypeScript and
Go: the reported state can be one the operation fell back to rather than its
target (a failed pause reports `started`, a resume that cannot finish reports
`stopped`), which means the operation did not complete.
