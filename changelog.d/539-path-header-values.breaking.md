**Path, body and header values are checked** — in both SDKs, before any
request, with `BadRequestError` naming the argument: every id or name a call
puts into a request path (images, snapshots, volumes, secrets, reservations,
`get_limits()` / `getLimits()`) must be a string that is not blank and has no
lone surrogate (TypeScript sent `/secrets/undefined`, Python raised a raw
`TypeError`, and neither refused a blank id); the name `images.promote()` and
`create_snapshot()` / `createSnapshot()` send in the request body must be a
string that is not blank; `sandbox.volumes.pull()` takes a volume id and a
request id of 1 to 128 ASCII letters, digits, underscores or hyphens; a header
value (`region`, sent as `x-daytona-region`; `idempotency_key` /
`idempotencyKey`, sent as `Idempotency-Key` on create and the reservation
calls; `if_match` / `ifMatch`, sent as `If-Match`) must be printable ASCII, so
both SDKs refuse the same values (a raw `UnicodeEncodeError` or `URIError`, or
a header error, escaped in at least one SDK).
