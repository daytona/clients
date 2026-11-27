**`PreconditionFailedError`** — documented in Python, TypeScript and Go: a
stale `If-Match` ETag on a conditional write (network policy, sandbox
secrets) is 412, a malformed one is a 400 `BadRequestError`, and a
create-only secret write that finds the secret raises
`SecretAlreadyExistsError`.
