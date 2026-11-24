**Filesystem watch errors** — the Python and TypeScript SDKs now report the
typed error from a server watch error event (such as a macOS access denial),
instead of a generic connection error when the stream closes. An error before
the watch starts fails the watch call directly.
