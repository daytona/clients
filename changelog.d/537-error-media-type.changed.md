**Error codes come only from JSON error bodies** — both SDKs read an error
response's code, source and details only when it declares the media type
`application/json` (matched case-insensitively, parameters ignored:
`Application/JSON; charset=utf-8` is JSON, `application/jsonl` and
`text/plain` are not), on every error path, buffered and streamed. Any other
body raises the status's own error (for example `NotFoundError`) with the body
as its message and no server code; before, a body that merely looked like an
error envelope was read for one.
