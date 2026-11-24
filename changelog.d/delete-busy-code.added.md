**`DELETE_LOCK_TIMEOUT` error code** — a sandbox delete the API cannot start
because the sandbox is busy now answers HTTP 429 with code
`DELETE_LOCK_TIMEOUT` instead of `OVERLOADED`. The Python and TypeScript SDKs
raise it as `OverloadedError`, so existing handlers keep catching it; read
`retry_after_seconds` and retry. In Go, match `ErrDeleteLockTimeout`
(`ErrOverloaded` no longer matches this response).
