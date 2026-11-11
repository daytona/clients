**TypeScript `buildFromContext()` takes `timeoutSeconds`** — the counterpart of
Python's `build_from_context(timeout=...)`: it bounds the build request that
follows the upload, in seconds, in place of the client's request timeout; `0`,
or a value above about 24.8 days, is no limit.
