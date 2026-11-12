**A create's server hold is never cut** — a create that asks the server to
hold its response (`wait_seconds` / `waitSeconds` above `0`) is bounded by the
larger of the request timeout and the hold plus 5 seconds, so a request
timeout shorter than the hold no longer turns a healthy create into
`SandboxCreateRequestTimeoutError`. A create with `wait_seconds=0` /
`waitSeconds: 0` stays an ordinary request, bounded by the request timeout. A
wait above the timer maximum (about 24.8 days) means no limit: the request
body carries 2147483 seconds in both SDKs, and the POST has no client
deadline. Both SDKs read the server's `wait_cap_seconds` the same way, never
refusing it once the sandbox exists: a finite number is used as given, a
negative one counts as `0`, and any other value counts as `0`.
