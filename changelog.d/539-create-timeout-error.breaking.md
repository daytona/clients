**`SandboxCreateTimeoutError` is removed** — a create request that times out
raises `SandboxCreateRequestTimeoutError`, which carries the idempotency key,
and a server 408 on the create request is the server's `RequestTimeoutError`
in both SDKs (TypeScript reported it as a client-side create timeout).
