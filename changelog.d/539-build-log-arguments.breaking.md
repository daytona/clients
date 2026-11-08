**Build-log following checks its arguments** — `follow_build_logs()` /
`followBuildLogs()`, on the client's `images` service and on the sandbox,
refuse a missing or non-callable `on_logs` / `onLogs` and a non-callable
`on_event` / `onEvent` with `BadRequestError` before any request (both SDKs
raised a raw `TypeError`, and accepted a non-callable `on_event` until the
first event). Python refuses an invalid `timeout` with `BadRequestError` (it
raised `ValueError`) and reads an integer too large for a float as no limit. A
resume `cursor` follows one rule in both SDKs and both directions: a non-empty
string of at most 2048 characters with no C0 control character or DEL. A
caller's cursor that breaks it is `BadRequestError` naming `cursor` before any
request (it was the build-log protocol error, by a different rule in each SDK:
Python accepted a tab, and TypeScript sent a non-string); a server-sent cursor
that breaks it stays the build-log protocol error, now with the same
characters in both SDKs (Python accepted a tab).
