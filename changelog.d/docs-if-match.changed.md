**If-Match on network policy and secrets updates** — a stale ETag raises
`PreconditionFailedError` (412; Go `ErrPreconditionFailed`); an empty
`if_match` / `ifMatch`, or one that is not printable ASCII or has leading or
trailing spaces, raises `BadRequestError` (400; Go `ErrBadRequest`) before any
request, and a value the API cannot parse is a 400 too, never a 412. A
create-only secret write that finds the secret raises
`SecretAlreadyExistsError` (Go `ErrSecretAlreadyExists`), a
`PreconditionFailedError`.
