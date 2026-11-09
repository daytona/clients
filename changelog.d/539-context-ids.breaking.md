**Interpreter context ids are path ids** — `interpreter.connect_context()` /
`connectContext()` and `list_contexts()` / `listContexts()` refuse a context
id that could not be closed (blank, `.` or `..`, or containing a surrogate
code point) with `BadRequestError` (400, `BAD_REQUEST`, source `SDK`), using
the same rule as context deletion; a blank id was bound before and failed only
on `close()`.
