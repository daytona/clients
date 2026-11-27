**Process signals are checked before they are sent** — `kill()` in the
Python, TypeScript and Go SDKs matches bare signal names case-insensitively
(`"term"` sends `SIGTERM`) and refuses, before any request, an unknown or
`SIG`-prefixed name and an escalation target (`escalate_to` / `escalateTo` /
`EscalateTo`) given without an escalation delay, which was silently dropped.
Python and TypeScript also refuse an empty signal and an escalation delay over
600 seconds (Go already refused the latter; its empty `Signal` is the zero
value and still means `TERM`). The error is `BadRequestError` in Python and
TypeScript, and an error matching `errors.Is(err, daytona.ErrBadRequest)` in
Go, whose message now names `EscalateTo` when the escalation target is the
invalid value.
