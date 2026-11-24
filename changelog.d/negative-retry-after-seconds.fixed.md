**Negative `retry_after_seconds` is dropped** — an error envelope whose
`retry_after_seconds` is below 0 (the contract's minimum) now leaves
`retry_after_seconds` / `retryAfterSeconds` unset in Python and TypeScript, as
`retryAfterMs` already does, instead of carrying the negative value.
