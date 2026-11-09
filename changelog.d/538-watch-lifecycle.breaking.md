**Watch callbacks, `stop()` and the exit callback** — in both SDKs callbacks
run one at a time and are awaited; a failing `on_event` / `onEvent` or
overflow callback ends the watch with that error (`exit_error` / `exitError`,
passed to the exit callback) instead of being recorded or ignored; a failing
exit callback is contained; `stop()` returns once a callback that is already
running has finished, and so has the exit callback, in every runtime (Python
returned before an async `on_exit` finished, TypeScript without
`AsyncLocalStorage` too); a callback (or the exit callback) can stop its own
watch and wait for its exit without waiting for itself, also from a task it
started (Python deadlocked on `create_task`, `gather` and, on 3.10 and 3.11,
`wait_for`). With the synchronous Python client that includes a plain callback
calling its own watch's `stop()`, and a task it started doing so after the
callback returned, while calling another watch's `stop()` / `wait_for_exit()`
raises a `DaytonaError`. In TypeScript runtimes without `AsyncLocalStorage`
(browsers) only the synchronous part of a callback or `onExit` is recognised
as the watch's own: one that awaits its own `stop()` after an `await` waits
for itself, so there it must call `stop()` without awaiting it. The exit
callback runs only for a watch `watch_dir()` / `watchDir()` returned, exactly
once: a failure before the watch starts (a refused connection, an HTTP error,
a stream that ends or breaks before `start`, and in Python a cancellation of
the task awaiting `watch_dir()`; TypeScript's `watchDir()` takes no
`AbortSignal`, so its pending call cannot be cancelled) is raised from that
call and never calls it (TypeScript called it for these, Python for a stream
that broke before `start`). To stop a watch from an event, signal the code
that holds the handle (an `asyncio.Event`, or a `threading.Event` with the
synchronous Python client) and call `stop()` there; this works in every case.
As a shorthand, callbacks are now held until `watch_dir()` / `watchDir()` has
returned its handle, so after a direct `await` a callback can call `stop()` on
it (Python delivered events that arrived with `start` first, so such a
callback could fail with a `NameError`). When the call runs inside another
task (Python `asyncio.gather`, `asyncio.create_task`, or `asyncio.wait_for` on
Python 3.10–3.11; TypeScript `Promise.all` or a later await), or with the
synchronous Python client, the first events can reach the callback before the
caller holds the handle.
