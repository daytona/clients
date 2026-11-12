**Header values follow one rule** — the idempotency key (`idempotency_key` /
`idempotencyKey`, sent as `Idempotency-Key` on create and the reservation
calls) and the ETag (`if_match` / `ifMatch`, sent as `If-Match`) must be
non-empty printable ASCII with no leading or trailing whitespace, never
stripped, since an idempotency key or ETag the SDK altered would not be the
caller's value; anything else is `BadRequestError` naming the argument before
any request. Both SDKs refuse the same values, so neither sends header bytes
the other would not and neither fails with a transport error (Python reported
a padded value as a `ConnectionError`, or for a create as
`SandboxCreateConnectionError`, and TypeScript sent it).
