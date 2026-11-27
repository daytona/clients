**Toolbox URL override checked up front** — `get()` refuses a `toolbox_url` /
`toolboxUrl` (Go `ToolboxURL`) that is not an absolute `http` / `https` URL
with a host, an optional port up to 65535 and no query or fragment, raising
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`; Go `ErrBadRequest`)
before any request instead of failing on the first toolbox call. The same
rule holds in every SDK: no whitespace or control characters anywhere (a URL
with a space in its path must now be percent-encoded, `%20`; Python used to
encode it), no `\` in the authority, and a host that is an IPv6 literal or a
plain name (no `%`, `|`, `<`, `"`, `^` or `{`). A `{sandbox_id}` /
`{sandboxId}` placeholder is allowed only in the path.
