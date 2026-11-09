**Callback-only directory watches** —
`watch_dir(path, on_event, *, recursive=False, on_overflow=None, on_exit=None)`
requires its callback in Python, like TypeScript's
`watchDir(path, onEvent, options)`, and keeps no event queue:
`get_new_events()`, `overflow_count`, `last_callback_error` and the handle's
reader and record methods are gone. Events carry `timestampMs` /
`timestamp_ms`.
