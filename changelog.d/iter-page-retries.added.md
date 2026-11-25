**Iterators retry a transiently failed page** — every `iter()` (Python and
TypeScript, plus `iter_endpoints()` / `iterEndpoints()`) and every Go
`ListAll` / `SearchAll` / `ListEndpointsAll` reads a page again after a
dropped connection or an HTTP 429 (other than `LIMIT_EXCEEDED`), 502, 503 or
504, up to three reads of that page, waiting at least the server's retry hint
(the error body's or the `Retry-After` header). A hint above 10 seconds ends
the walk at once, without a retry.
