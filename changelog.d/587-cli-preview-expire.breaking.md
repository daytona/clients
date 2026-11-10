**CLI: `preview-url expire` no longer echoes the token** — its JSON is
`{"sandbox_id", "port", "revoked": true}` with `port` a number, and the token is
omitted.
