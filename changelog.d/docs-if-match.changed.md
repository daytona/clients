**If-Match on network policy and secrets updates** — a stale ETag raises
`PreconditionFailedError` (412; Go `ErrPreconditionFailed`); an empty
`if_match` / `ifMatch`, or one that is not printable ASCII or has leading or
trailing spaces, raises `BadRequestError` (400; Go `ErrBadRequest`) before any
request; any other value is sent as given, and the API answers 400, not 412,
for some values it cannot parse. A
create-only secret write that finds the secret raises
`SecretAlreadyExistsError` (Go `ErrSecretAlreadyExists`), a
`PreconditionFailedError`.
