**Toolbox date-time fields are RFC 3339** — both SDKs read a toolbox date-time
field (process records, file info, git commits, system metrics, computer-use
recordings) only as an RFC 3339 timestamp with the same field ranges (date and
time joined by `T`, hour 00-23, no leap second, offset within ±23:59; a
fraction of a second is allowed), and refuse any other value with
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the field. The
`timestamp_ms` / `timestampMs` of log frames and filesystem watch events are
Unix-millisecond numbers, not RFC 3339 strings.
