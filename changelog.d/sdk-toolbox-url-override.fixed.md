**Toolbox URL override checked up front** — `get()`'s `toolbox_url` /
`toolboxUrl` (Go `ToolboxURL`) follows one rule in every SDK. It must be an
absolute `http` / `https` URL with a host and a port up to 65535, with no
query or fragment and no empty authority. The host is an IPv6 literal or a
name of ASCII letters, digits, `-._~!$&'()*+,;=` and non-ASCII characters (no
`%`, so no encoded host or IPv6 zone ID, and no `\`, `|`, `<` and the like).
The URL may contain no whitespace (Python's `str.isspace()` set, a space in
the path included, which Python used to percent-encode), no C0 or C1 control
character, DEL or U+FEFF. A `{sandbox_id}` / `{sandboxId}` placeholder is
allowed only in the path. Python and TypeScript refuse it before the sandbox is
read instead of failing on the first toolbox call; Go, which already checked
the scheme, host, query and fragment, refuses the rest too.
