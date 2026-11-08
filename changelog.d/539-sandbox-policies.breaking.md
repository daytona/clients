**Network policy and sandbox secrets live on the sandbox** — the client-level
network-policy and sandbox-secret methods are removed; use the sandbox's
`get_network_policy()`, `update_network_policy()`, `reset_network_policy()`,
`get_secrets()` and `replace_secrets()` (camelCase in TypeScript, where the
`ifMatch` options are the exported `IfMatchOptions`). `if_match` / `ifMatch`
must be a non-empty string. A network-policy or sandbox-secrets response
without its `ETag` header is a `BadRequestError` (400, `BAD_REQUEST`, source
`SDK`).
