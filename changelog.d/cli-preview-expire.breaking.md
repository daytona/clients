**CLI: `preview-url expire` no longer echoes the token** — its JSON is
`{"sandbox_id", "port", "revoked": true}` with `port` a number; a blank token
and a port outside 1-65535 (also on `preview-url`) are usage errors before any
request.
