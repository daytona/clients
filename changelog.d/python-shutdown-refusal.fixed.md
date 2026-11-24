**Python requests during interpreter shutdown raise `ClientClosedError`** — a
request made from an `atexit` handler (such as a sandbox `delete()` registered
for cleanup) used to fail with a bare `RuntimeError`, or to succeed only when
an old connection happened to be reusable. The SDK now refuses it before
sending anything, with a `ClientClosedError` (`CLIENT_CLOSED`) that explains
why; a request from a thread still running after the program ended gets the
same error when it needs a thread pool Python has already stopped. Clean up
sandboxes before the program exits, for example in a `try`/`finally`.
