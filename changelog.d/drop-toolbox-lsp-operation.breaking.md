**`toolbox:lsp` is no longer an access-token operation** — the platform no
longer has an LSP toolbox area, so `create_access_token()` /
`createAccessToken()` no longer document `toolbox:lsp`, and requesting it is
answered with a 400 like any unknown operation.
