**Migration tool: LSP server suggestions** — both tools render an LSP server's
`start()` on the sandbox that created it, or as a manual move when that
sandbox is unknown, instead of on the server itself or a `sandbox`
placeholder, which was wrong.
