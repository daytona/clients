**Computer-use responses are checked** — the computer-use parsers, the
`start()` / `stop()` status map, a window id (up to 2^53 - 1) and a
`cpuUtilization` (between 0 and 1) are checked in both SDKs; a violation
raises `BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the field,
instead of being filled with an empty value (the parsers raised a 500
`InternalServerError` before).
