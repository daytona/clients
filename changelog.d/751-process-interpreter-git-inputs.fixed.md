**Process, interpreter and git arguments are checked before any request** —
in Python and TypeScript, a shell command (the `command` argument of
`pty.start()`, `process.start()` / `run()`, `sandbox.exec()` and session
commands, or TypeScript's `{ cmd }`) or interpreter code that is not a string,
an empty interpreter `language`, and a non-boolean git flag (`allow_empty` /
`allowEmpty`, `bare`, `set_upstream` / `setUpstream`, `staged`, `worktree`)
raise `BadRequestError` instead of reaching the sandbox or being read as
truthy; shell-free `argv` is unchanged. Go's `Interpreter.CreateContext`
refuses an empty language the same way (`errors.Is(err,
daytona.ErrBadRequest)`). `interpreter.connect_context()` /
`connectContext()` refuses a `language` that differs from a context identity's
language, as `run()` already did. Error messages name the called method's own
parameters: `process.run()` / `sandbox.exec()` without a command says
`provide exactly one of command or argv`, `pty.delete()` names itself, and
TypeScript `connectSession(null)` / `connect(null)` name `sessionId` /
`processId`.
