**Process tags follow the toolbox rules** — TypeScript `process.killAll()`
takes `timeoutSeconds` in seconds instead of the millisecond `timeout` of the
first tag release (divide by 1000; `timeout` is refused with a
`BadRequestError` naming `timeoutSeconds`, as is any other unknown key), like
Python's `kill_all(timeout=...)`, which was already in seconds. In both SDKs
the timeout must be more than 0 and at most 60 seconds (the toolbox's 1-60000
ms), a positive value is sent as at least 1 ms, and a tag on `start()`,
`run()`, `exec()`, `pty.start()`, `create_session()` / `createSession()`,
`list()` or `kill_all()` / `killAll()` must be a safe identifier
(`[A-Za-z0-9._-]`, 1-128 characters, not `.` or `..`); anything else raises
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) before any request
instead of a toolbox 400 or, for Python's `0`, a request the toolbox refused.
`killAll()` returns a frozen `readonly string[]`, the tag-filtered `list()`
page is frozen like every other, and a kill response without a string
`processIds` list, or a process record whose `tag` is not a string, is
`BadRequestError` naming the field.
