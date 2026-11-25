**If-Match on network policy and secrets updates** — a stale ETag raises
`PreconditionFailedError` (412); an empty `if_match` / `ifMatch`, or one that
is not printable ASCII or has leading or trailing spaces, raises
`BadRequestError` (400) before any request.
