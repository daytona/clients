**Idempotency keys: one length limit and one wire form** — an explicit
idempotency key (sandbox creates, `reservations.create()` / `create_bulk()` /
`createBulk()` / `renew()`) over 256 bytes, the API's limit, is refused with
`BadRequestError` before any request in all three SDKs. A create now sends its
key only as the `Idempotency-Key` header in every SDK (TypeScript and Go also
sent it as `idempotency_key` in the body; the API reads the header first), and
a key the SDK generates is a random lowercase UUID v4 everywhere (TypeScript
generated undashed hex, Go a base32 string). Keys derived from a sandbox name
are unchanged, so a retry still replays across SDKs.
