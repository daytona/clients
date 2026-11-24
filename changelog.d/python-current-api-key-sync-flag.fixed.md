**Python `current_api_key()` and `get_limits()` no longer require `sync_to_v0`** —
the current API key response's `sync_to_v0`, which the SDK neither reads nor
returns, is no longer required or type-checked, so a response without it (or
with `null`) no longer fails `current_api_key()`, nor `get_limits()`, which reads
the API key's organization through `current_api_key()`. The fields the SDK
returns keep every check, as in TypeScript and Go.
