**Toolbox URL override checked up front** — `get()`'s `toolbox_url` /
`toolboxUrl` (Go `ToolboxURL`) must be an absolute `http` / `https` URL with a
host and a port up to 65535, with no query or fragment, no whitespace or
control character (a space in the path included, which Python used to
percent-encode), no backslash or empty authority, and no `%` in the host (an
encoded host or an IPv6 zone ID); a `{sandbox_id}` / `{sandboxId}`
placeholder is allowed only in the path. Python and TypeScript refuse it
before the sandbox is read instead of failing on the first toolbox call, and
Go, which already checked the scheme, host, query and fragment, refuses the
port, whitespace and `%` cases too.
