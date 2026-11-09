**Interpreter context ids are path ids** — `interpreter.connect_context()` /
`connectContext()` refuse a context id that could not be closed with
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`), using the same rule as
context deletion: a blank id, `.` or `..`, and in Python any surrogate code
point, in TypeScript an unpaired UTF-16 surrogate (a paired one is an astral
character, accepted by both SDKs). A blank id was bound before and failed only
on `close()`. `list_contexts()` / `listContexts()` apply the same rule to
every context the sandbox returns, so one refused id now fails the whole call
with that error, where the other contexts were returned before.
