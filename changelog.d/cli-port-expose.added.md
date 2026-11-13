**CLI: `sandbox port expose` and `unexpose`** — `daytona sandbox port expose
<id> <port> --public` makes a preview URL reachable with no credential (without
`--public` it makes the port private again), and `sandbox port unexpose`
removes the endpoint: the CLI twins of `sandbox.ports.expose()` and
`unexpose()`. `sandbox port preview-url` now notes on stderr that its token
goes in the `X-Daytona-Preview-Token` header and names `expose --public` for a
browser URL; a refused `--signed` link gets the same pointer as its hint, and
`sandbox create --port` says it is a platform port mapping, not a preview URL.
