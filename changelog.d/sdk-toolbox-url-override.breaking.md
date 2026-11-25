**Toolbox URL override checked up front** — `get()`'s `toolbox_url` /
`toolboxUrl` (Go `ToolboxURL`) follows one rule in every SDK. It must be an
absolute `http` / `https` URL with a host, an optional port up to 65535, no
query or fragment, no empty authority and no `\` in the authority. The host,
as written, is an IPv6 literal or a name of ASCII letters, digits,
`-._~!$&'()*+,;=` and non-ASCII characters: no `%` (so no percent-encoded
host or IPv6 zone ID), and no `|`, `<`, `"`, `^` or `{`. A non-ASCII host
must also be one the SDK's URL parser accepts, and the user-info part follows
each parser's own rules. The URL may contain no whitespace (Python's
`str.isspace()` set; a space in the path, which Python used to
percent-encode, included), no C0 or C1 control character, DEL or U+FEFF,
and no lone surrogate (in Go, no invalid UTF-8). A
`{sandbox_id}` / `{sandboxId}` placeholder is allowed only in the path. Python
and TypeScript refuse such a URL before the sandbox is read instead of failing
on the first toolbox call; Go, which already checked the scheme, host, query
and fragment, refuses the rest too.
