**Desktop resolution in status** — computer use `status()` reports the
`resolution` the desktop runs at, or, when it is not running, the one the next
start uses (TypeScript `status.resolution`, Python `status.resolution`, a
`ComputerUseResolution`); it is absent on Windows and macOS and from older
sandboxes. `start()` and `restart()` with a `resolution` now read back the size
the desktop runs at and raise `UnsupportedOperationError` when it differs: a
sandbox whose guest predates resolution selection ignores the request.
