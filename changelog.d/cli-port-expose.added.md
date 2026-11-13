**CLI: `sandbox port expose`, `unexpose` and `endpoints`** — `daytona sandbox
port expose <id> <port> --public` makes a preview URL reachable by anyone with
no credential (without `--public` it makes the port private again), `sandbox
port unexpose` removes the endpoint, and `sandbox port endpoints` lists the
exposed ports with their URLs and visibility: the CLI twins of
`sandbox.ports.expose()`, `unexpose()` and `list_endpoints()`. `sandbox port
preview-url` now notes on stderr that a private port's token goes in the
`X-Daytona-Preview-Token` header and names the two browser options, `--signed`
(short-lived) and `expose --public`; a refused `--signed` link points to
`expose --public` in its hint, and `sandbox create --port` says it is a
platform port mapping, not a preview URL.
