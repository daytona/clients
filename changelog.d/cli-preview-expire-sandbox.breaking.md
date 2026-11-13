**CLI: `preview-url expire` takes only the sandbox id** —
`daytona sandbox port preview-url expire <sandbox-id>` expires every signed
preview URL of the sandbox, on every port, and takes no port or token. Its JSON
is `{"sandbox_id", "revoked": true}`.
