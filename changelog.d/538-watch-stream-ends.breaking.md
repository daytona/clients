**Watch streams that end** — a watch whose stream ends on its own, or is cut
short, ends with `ConnectionError` (source `SDK`) in both SDKs (Python raised
a 500 `InternalServerError`, TypeScript a plain `DaytonaError` or the
runtime's own error), and a stream that fails to close after the caller's own
`stop()` no longer becomes the exit error (Python could report a
`RuntimeError` when `stop()` raced the reader's close).
