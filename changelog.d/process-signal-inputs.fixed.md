**Process signals are checked before they are sent** — `kill()` in the
Python, TypeScript and Go SDKs matches bare signal names case-insensitively
(`"term"` sends `SIGTERM`) and refuses, with `BadRequestError` before any
request, an empty or unknown signal, a `SIG`-prefixed name, an escalation
delay over 600 seconds (Python and TypeScript; Go already did), and an
escalation target (`escalate_to` / `escalateTo` / `EscalateTo`) given without
an escalation delay, which was silently dropped.
