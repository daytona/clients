**Callback-only directory watches** —
`watch_dir(path, on_event, *, recursive=False, on_overflow=None, on_exit=None)`
requires its callback in Python, like TypeScript's
`watchDir(path, onEvent, options)`, and keeps no event queue:
`get_new_events()`, `overflow_count`, `last_callback_error` and the handle's
reader and record methods are gone. In both SDKs callbacks run one at a time
and are awaited; a failing `on_event` / `onEvent` or overflow callback ends
the watch with that error (`exit_error` / `exitError`, passed to the exit
callback) instead of being recorded or ignored; a failing exit callback is
contained; `stop()` returns once a callback that is already running has
finished, and so has the exit callback, in every runtime (Python returned
before an async `on_exit` finished, TypeScript without `AsyncLocalStorage`
too); a callback (or the exit callback) can stop its own watch and wait for
its exit without waiting for itself, also from a task it started (Python
deadlocked on `create_task`, `gather` and, on 3.10 and 3.11, `wait_for`). With
the synchronous Python client that includes a plain callback calling its own
watch's `stop()`, and a task it started doing so after the callback returned,
while calling another watch's `stop()` / `wait_for_exit()` raises a
`DaytonaError`. In TypeScript runtimes without `AsyncLocalStorage` (browsers)
only the synchronous part of a callback or `onExit` is recognised as the
watch's own: one that awaits its own `stop()` after an `await` waits for
itself, so there it must call `stop()` without awaiting it (the SDK cannot
tell such a call from an external one, which must wait). The exit callback
runs only for a watch `watch_dir()` / `watchDir()` returned, exactly once: a
failure before the watch starts (a refused connection, an HTTP error, a stream
that ends or breaks before `start`, a cancellation) is raised from that call
and never calls it (TypeScript called it for these, Python for a stream that
broke before `start`). To stop a watch from an event, signal the code that
holds the handle (an `asyncio.Event`, or a `threading.Event` with the
synchronous Python client) and call `stop()` there; this works in every case.
As a shorthand, callbacks are now held until `watch_dir()` / `watchDir()` has
returned its handle, so after a direct `await` a callback can call `stop()` on
it (Python delivered events that arrived with `start` first, so such a
callback could fail with a `NameError`). When the call runs inside another
task (Python `asyncio.gather`, `asyncio.create_task`, or `asyncio.wait_for` on
Python 3.10–3.11; TypeScript `Promise.all` or a later await), or with the
synchronous Python client, the first events can reach the callback before the
caller holds the handle. Events that arrive before `start` are delivered after
it, at most 4096 of them, and the stream may send at most 1 MiB before
`start`, counted as the bytes arrive, so one event that never ends is refused
too: a stream that sends more `fs` or `overflow` events, handled by a callback
or not, or more bytes, before `start` is refused with `BadRequestError` from
`watch_dir()` / `watchDir()`; every frame counts, comment-only heartbeats
included. After `start`, each event is bounded by the client's
`max_response_bytes` / `maxResponseBytes` (left unset, Python `None` /
TypeScript `undefined` or `null`, it keeps the configured cap, 1 GiB by
default; `0` means no limit; any other value is a `BadRequestError`): one
event over it ends the watch with `PayloadTooLargeError`, and with `0` events
are unbounded. Events carry `timestampMs` / `timestamp_ms`, and a malformed
event ends the watch with `BadRequestError`. A watch whose stream ends on its
own, or is cut short, ends with `ConnectionError` (source `SDK`) in both SDKs
(Python raised a 500 `InternalServerError`, TypeScript a plain `DaytonaError`
or the runtime's own error), and a stream that fails to close after the
caller's own `stop()` no longer becomes the exit error (Python could report a
`RuntimeError` when `stop()` raced the reader's close).
