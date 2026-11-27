**Recording status** — `recording.stop()`, `get()` and `list()` (Python,
TypeScript, Go) document that a returned recording may have no usable file:
check `status` (`completed`, `duration_limit_reached`, `size_limit_reached`
are usable; `failed` has no file, `interrupted` may be partial) before using
it, and `recording.start()` documents its `DESKTOP_NOT_READY` and
`ADDON_UNAVAILABLE` failures.
