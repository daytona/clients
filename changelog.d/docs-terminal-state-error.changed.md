**`SandboxTerminalStateError` state** — documented in Python, TypeScript and
Go: the reported state can be the one the operation started from (a failed
pause reports `started`, a resume that cannot finish reports `stopped`), which
means the operation did not complete.
