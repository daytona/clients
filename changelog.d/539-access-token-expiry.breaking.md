**Access token expiries are RFC 3339 timestamps** — `create_access_token()` /
`createAccessToken()` refuse an `expires_at` that is not an RFC 3339 timestamp
with `BadRequestError` naming the field, in both SDKs; the SDKs disagreed
before (Python took `2030-01-01` or a date-time without an offset as UTC,
TypeScript any string `Date.parse` reads, such as `Jan 1 2030` or `2030`). The
handle's own token mint reads an `expires_at` string that is not an RFC 3339
timestamp as absent rather than failing the call that needed the token, and
uses the token until a 401 re-mints it; a missing or non-string `expires_at`
still fails the mint with `BadRequestError`.
