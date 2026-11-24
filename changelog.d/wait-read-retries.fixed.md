**Waits retry transient read failures** — sandbox waits (`wait()`, the
lifecycle methods and the create and fork waits), Docker readiness
(`wait_until_ready()` / `waitUntilReady()` / `WaitUntilReady`) and image and
snapshot readiness (`get(..., wait="ready")`, `pull()`) now retry a read that
fails transiently (no response, a stalled read, HTTP 429 or a 5xx other than
501) with backoff (0.2 s doubling to 5 s, or the server's `Retry-After` when
longer) within the same wait budget, the same in the Python, TypeScript and
Go SDKs. A budget that runs out raises the wait's own timeout with the last
failed read as its cause; authentication and other permanent errors are still
raised at once. Python and TypeScript used to raise the first failed read, and
Go did so when the events stream was off.
