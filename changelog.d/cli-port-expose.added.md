**CLI: `sandbox port expose`, `unexpose` and `endpoints`** — `daytona sandbox
port expose <id> <port> --public` makes a preview URL reachable by anyone with
no credential (without `--public` it makes the port private again), `sandbox
port unexpose` removes the endpoint, and `sandbox port endpoints` lists the
exposed ports with their URLs and visibility: the CLI twins of
`sandbox.ports.expose()`, `unexpose()` and `list_endpoints()`.
