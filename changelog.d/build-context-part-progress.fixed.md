**Build-context parts are bounded by progress, not by a fixed time** — a part
of a `build_from_context()` / `buildFromContext()` upload now fails only once
no bytes move for the client's request timeout (`0`: never), and is retried as
before; a large part on a slow link is no longer cut while it moves. Python
used to give each request phase five minutes and TypeScript set no bound; a
part that stops moving now fails with `ClientTimeoutError` (408,
`REQUEST_TIMEOUT`, source `SDK`). In a browser, where an upload's progress
cannot be seen, a part stays unbounded.
