**TypeScript: `watchDir().stop()` exits cleanly under fetch polyfills** — fetch
polyfills such as Remix's `@remix-run/web-fetch` reject an aborted request
with their own `AbortError`, not a `DOMException`, so `stop()` reported an
`exitError`, a request timeout or caller abort on the fetch transport surfaced
as a `ConnectionError`, and an aborted
`downloadFile()` / `uploadFile()` was not reported as
`TransferCancelledError`. Every abort and timeout check now matches the error
by name. TypeScript only: Python has no fetch polyfills.
