**Lifecycle waits, in seconds** — `stop()` and `delete()` now wait for
`stopped` / `destroyed` (a sandbox that disappears during deletion counts as
deleted), like `start()`, `pause()` and `resume()`. Every lifecycle method
takes `timeout` (Python) / `timeoutSeconds` (TypeScript) in seconds, default
60, `0` for no deadline; running out raises `RequestTimeoutError` (source
`SDK`), even when a held read is still unanswered at the deadline, and a
terminal state raises `SandboxTerminalStateError`. Once the target state has
been observed, a failed metadata refresh afterwards no longer turns that
success into an error. The lifecycle methods no longer refuse calls by sandbox
class or persistence before sending them: the API decides.
`wait_for_state()` / `waitForState()` is no longer public: use
`wait(until=...)`, whose states are checked before any request (an invalid
one is a `BadRequestError`). Python `wait(timeout=None)` is gone: pass `0` to
wait without a deadline.
