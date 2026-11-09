**Toolbox timestamps are RFC 3339** — both SDKs read a toolbox timestamp only
as an RFC 3339 timestamp with the same field ranges (date and time joined by
`T`, hour 00-23, no leap second, offset within ±23:59; a fraction of a second
is allowed) and refuse any other value with `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) naming the field.
