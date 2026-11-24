**Python `current_api_key()` and `get_limits()` ignore the key-sync flag** —
the current API key response's `sync_to_v0`, which the SDK neither reads nor
returns, is no longer required or type-checked, so a response without it (or
with `null`) no longer fails, nor does `get_limits()`'s lookup of the API
key's organization. The fields the SDK returns keep every check, as in
TypeScript and Go.
