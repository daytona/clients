**Path, body and header values are checked** — in both SDKs, before any
request, with `BadRequestError` naming the argument: every id or name a call
puts into a request path (images, snapshots, volumes, secrets, reservations,
the organization id of `get_limits()` / `getLimits()`, the id `get()` and the
client lifecycle verbs take) must be a string that is not blank and has no
lone surrogate (TypeScript sent `/secrets/undefined`, Python raised a raw
`TypeError`, and neither refused a blank id); the name `images.promote()` and
`create_snapshot()` / `createSnapshot()` send in the request body must be a
string that is not blank; `sandbox.volumes.pull()` takes a volume id and a
request id of 1 to 128 ASCII letters, digits, underscores or hyphens; a header
value (`region`, sent as `x-daytona-region`; `idempotency_key` /
`idempotencyKey`, sent as `Idempotency-Key` on create and the reservation
calls; `if_match` / `ifMatch`, sent as `If-Match`) must be non-empty printable
ASCII with no leading or trailing whitespace; it is never stripped, since an
idempotency key or ETag the SDK altered would not be the caller's value. Both
SDKs refuse the same values, so neither sends header bytes the other would not
and neither fails with a transport error (Python reported a padded value as a
`ConnectionError`, or for a create as `SandboxCreateConnectionError`, and
TypeScript sent it). A blank `region` selects the default region on every call
that takes one, as `create()` does (Python's `find_by_name()` sent an empty
`x-daytona-region` header).
