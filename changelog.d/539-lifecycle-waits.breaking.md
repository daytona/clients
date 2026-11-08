**Lifecycle waits, in seconds** — `stop()` and `delete()` now wait for
`stopped` / `destroyed` (a sandbox that disappears during deletion counts as
deleted), like `start()`, `pause()` and `resume()`. Every lifecycle method
takes `timeout` (Python) / `timeoutSeconds` (TypeScript) in seconds, default
60, `0` for no deadline. Every wait budget of the client, the lifecycle and
the platform services (the lifecycle methods by handle and by id, `wait()`,
`images.pull()`, `images.get()` / `snapshots.get()` with `wait="ready"`,
`docker.wait_until_ready()`, `create_from_build()`'s build timeout,
`follow_build_logs()` and fork's `wait_seconds`) reads a value above the timer
maximum, about 24.8 days, as no deadline in both SDKs (before, both SDKs
treated it as a real deadline: a 3,000,000 s budget ended after about 34.7
days, in TypeScript given as 3,000,000,000 ms for every one of these options
but `wait()`, which took seconds; `images.get()` / `snapshots.get()` made one
long poll of at most 60 s whatever the budget, and `create_from_build()` had
no build timeout of its own); running out raises `RequestTimeoutError` (source
`SDK`), even when a held read is still unanswered at the deadline, and a
terminal state raises `SandboxTerminalStateError`. A terminal state the events
hub reports is confirmed with one read while the wait has budget left, unless
that state already came from a full read or is `destroyed` (without that read,
or when it fails, the reported state raises); the read decides, in both SDKs:
a target state returns (for `delete()` a 404 too), a terminal state raises
with the read state, and any other state keeps waiting under the same
deadline, so a `delete()` right after a sandbox ended in `error` returns once
the sandbox is gone. Once the target state has been observed, a failed
metadata refresh afterwards no longer turns that success into an error. The
lifecycle methods no longer refuse calls by sandbox class or persistence
before sending them: the API decides. `wait_for_state()` / `waitForState()` is
no longer public: use `wait(until=...)`, whose states are checked before any
request (an invalid one is a `BadRequestError`). Python `wait(timeout=None)`
is gone: pass `0` to wait without a deadline.
