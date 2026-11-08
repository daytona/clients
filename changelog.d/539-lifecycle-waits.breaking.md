**Lifecycle waits, in seconds** — `stop()` and `delete()` now wait for
`stopped` / `destroyed` (a sandbox that disappears during deletion counts as
deleted), like `start()`, `pause()` and `resume()`. Every lifecycle method
takes `timeout` (Python) / `timeoutSeconds` (TypeScript) in seconds, default
60, `0` for no deadline; running out raises `RequestTimeoutError` and a
terminal state raises `SandboxTerminalStateError`. Once the target state has
been observed, a failed metadata refresh afterwards no longer turns that
success into an error. Python `wait(timeout=None)` is gone: pass `0` to wait
without a deadline. TypeScript: on the lifecycle methods, `fork()`,
`images.pull()`, build-log following, `docker.waitUntilReady()` and
`sandbox.volumes.pull()`, `{ timeout }` in milliseconds becomes
`{ timeoutSeconds }` in seconds (divide old values by 1000); on `wait()`,
`images.get()` and `snapshots.get()`, which already took seconds, only the key
is renamed to `timeoutSeconds` (keep the values). `wait_for_state()` /
`waitForState()` is no longer public: use `wait(until=...)`.
`create*()` and `fork()` now return the sandbox however the wait after a
successful request ends: the `wait_seconds` / `waitSeconds` budget runs out,
a terminal state is reached, or a read during the wait fails. Check
`sandbox.state` and `sandbox.error`. Only a failed create or fork request
raises, so a create that raised created no sandbox, except a create whose
request timed out (`SandboxCreateRequestTimeoutError`, which carries the
idempotency key to retry with) and a cancelled create, which may already have
created one. `SandboxCreateTimeoutError` is removed, and a server 408 on the
create request is the server's `RequestTimeoutError` in both SDKs. `fork()`
takes `wait_seconds` / `waitSeconds` (default 60, fractions allowed, `0`
returns once the fork is accepted) instead of `timeout`, and its detail read
counts against that budget. The fork waits for the state it settles in: a fork
of a running Linux sandbox `started`; otherwise it rests, `paused` for a
memory fork of a paused source and `stopped` for the rest. A macOS fork
settles at `started` or `stopped`. `create_from_build()` / `createFromBuild()`
bounds the image build wait by its own `build_timeout` /
`buildTimeoutSeconds` (unbounded by default), starting once the build
submission (an ordinary request, bounded by the request timeout) is accepted.
The create then gets the full `wait_seconds` / `waitSeconds`. An image that
ends in `failed` or `error` raises `ImageBuildFailedError`. The lifecycle
methods no longer refuse calls by sandbox class or persistence before sending
them: the API decides.
