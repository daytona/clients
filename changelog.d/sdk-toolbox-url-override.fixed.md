**Toolbox URL override checked up front** — `get()`'s `toolbox_url` /
`toolboxUrl` must be an absolute `http` / `https` URL with a host, a valid
port, no query or fragment, and no whitespace (a `{sandbox_id}` /
`{sandboxId}` placeholder only in its path). It is refused before the sandbox
is read instead of failing on the first toolbox call.
