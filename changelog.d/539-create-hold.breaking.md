**A create's server hold is never cut** — a create that asks the server to
hold its response (`wait_seconds` / `waitSeconds` above `0`) is bounded by the
larger of the request timeout and the hold plus 5 seconds, so a request
timeout shorter than the hold no longer turns a healthy create into
`SandboxCreateRequestTimeoutError`. A create with `wait_seconds=0` /
`waitSeconds: 0` stays an ordinary request, bounded by the request timeout.
