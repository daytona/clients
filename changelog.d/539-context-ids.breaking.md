**Interpreter context ids are path ids** — `interpreter.connect_context()` /
`connectContext()` and the contexts `list_contexts()` / `listContexts()`
return refuse a context id that could not be closed (blank, `.` or `..`, or
containing a surrogate code point) with `BadRequestError` (400, `BAD_REQUEST`,
source `SDK`), the rule the context's deletion applies; a blank id was bound
before and failed only on `close()`.
