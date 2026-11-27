**Recording status** — `recording.stop()`, `get()` and `list()` (Python,
TypeScript, Go) document that a returned recording may have no usable file: a
recording is final once its end time is set (`stop()` always returns a final
one; `duration_limit_reached` and `size_limit_reached` are also reported while
a limit-ended recording is being finalized), and only a final `completed`,
`duration_limit_reached` or `size_limit_reached` recording has a usable file
(`failed` has none, `interrupted` may be partial). `recording.start()`
documents its `DESKTOP_NOT_READY` and `ADDON_UNAVAILABLE` failures.
