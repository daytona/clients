**Callback-only directory watches** — `watch_dir(path, on_event, *,
recursive=False, on_overflow=None, on_exit=None)` requires its callback in
Python, like TypeScript's `watchDir(path, onEvent, options)`, and keeps no
event queue: `get_new_events()`, `overflow_count`, `last_callback_error` and
the handle's reader and record methods are gone. In both SDKs callbacks run
one at a time and are awaited; a failing `on_event` / `onEvent` or overflow
callback ends the watch with that error (`exit_error` / `exitError`, passed to
the exit callback) instead of being recorded or ignored; a failing exit
callback is contained; `stop()` returns once a callback that is already
running has finished, and so has the exit callback (Python returned before
an async `on_exit` finished); a callback (or the exit callback) can stop its
own watch and wait for its exit without waiting for itself, also from a task
it started (Python deadlocked on `create_task`, `gather` and, on 3.10 and
3.11, `wait_for`); with the synchronous Python client that includes a plain
callback calling its own watch's `stop()`, while calling another watch's
`stop()` / `wait_for_exit()` raises `BridgeError`.
Events carry `timestampMs` / `timestamp_ms`, and a malformed event ends the
watch with `BadRequestError`.
