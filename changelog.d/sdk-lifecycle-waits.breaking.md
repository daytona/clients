**Lifecycle waits, in seconds** — `stop()` and `delete()` now wait for
`stopped` / `destroyed` (a sandbox that disappears during deletion counts as
deleted), like `start()`, `pause()` and `resume()`. Every lifecycle method
takes `timeout` (Python) / `timeoutSeconds` (TypeScript) in seconds, default
60, `0` for no deadline; running out raises `RequestTimeoutError` and a
terminal state raises `SandboxTerminalStateError`. TypeScript `{ timeout }` in
milliseconds is renamed `{ timeoutSeconds }` on the lifecycle methods,
`wait()`, `images.get()` / `images.pull()` / `snapshots.get()`, build-log
following, `docker.waitUntilReady()` and `sandbox.volumes.pull()`.
`wait_for_state()` / `waitForState()` is no longer public: use `wait(until=...)`.
`create*()` returns the sandbox however its `wait_seconds` / `waitSeconds`
wait ends (check `sandbox.state` and `sandbox.error`); `SandboxCreateTimeoutError`
is removed. Request failures, a server 408 included, still raise. `fork()`
takes `wait_seconds` / `waitSeconds` (default 60, fractions allowed, `0` returns
once accepted) instead of `timeout`, waits for the state the fork settles in
(the source's state for Linux, `started` or `stopped` for macOS) and returns
the fork however the wait ends. `create_from_build()` / `createFromBuild()`
bounds the image build by its own `build_timeout` / `buildTimeoutSeconds`
(unbounded by default) and gives the create the full `wait_seconds` /
`waitSeconds`. The SDKs no longer refuse lifecycle calls by sandbox class or
persistence before sending them: the API decides.
