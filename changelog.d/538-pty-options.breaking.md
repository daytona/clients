**PTY options** — `pty.start()` takes a nested terminal size in both SDKs:
Python `pty.start(command=None, *, terminal=TerminalSize(cols=..., rows=...),
cwd, env, name, user, shell, login, stdin="open", execution_timeout,
keep_logs="until_cleanup")` replaces `cols=` / `rows=`, and TypeScript
`pty.start()` / `pty.attach()` take a dedicated `PtyStartOptions` without
`cmd` or `argv`. The terminal is 80×24 when omitted, in both. Plain processes
take no terminal: TypeScript `ProcessStartOptions` / `RunOptions` lose
`cmd`, `argv`, `terminal` and `kind`, and Python `process.start()` /
`run()` and `sandbox.exec()` lose `terminal=`. `TerminalSize` is an SDK type
(a frozen keyword-only dataclass in Python).
