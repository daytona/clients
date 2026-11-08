**Callback-only directory watches** — `watch_dir(path, on_event, *,
recursive=False, on_overflow=None, on_exit=None)` requires its callback in
Python, like TypeScript's `watchDir(path, onEvent, options)`, and keeps no
event queue: `get_new_events()`, `overflow_count`, `last_callback_error` and
the handle's reader and record methods are gone. In both SDKs callbacks run
one at a time and are awaited; a failing `on_event` / `onEvent` or overflow
callback ends the watch with that error (`exit_error` / `exitError`, passed to
the exit callback) instead of being recorded or ignored; a failing exit
callback is contained; `stop()` returns once a callback that is already
running has finished, and a callback can stop its own watch. Events carry
`timestampMs` / `timestamp_ms`.
