**LSP removed from the v2 SDKs** — the Python and TypeScript v2 SDKs no
longer expose LSP: `sandbox.lsp`, `LspServer` / `LspService` (Python also
`AsyncLspServer` / `AsyncLspService`) and the `Lsp*` types are removed. The
0.x SDK is unchanged. The upgrade tool now reports 0.x LSP calls
(`create_lsp_server()` / `createLspServer()` and every LSP server method) as
having no v2 equivalent.
