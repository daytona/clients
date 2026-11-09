**Bounded watch streams** — events that arrive before `start` are delivered
after it, at most 4096 of them, and the stream may send at most 1 MiB before
`start`, counted as the bytes arrive (every frame, comment-only heartbeats
included), so one event that never ends is refused too: a stream that sends
more `fs` or `overflow` events, handled by a callback or not, or more bytes,
before `start` is refused with `BadRequestError` from `watch_dir()` /
`watchDir()`. After `start`, each event is bounded by the client's
`max_response_bytes` / `maxResponseBytes` (left unset, Python `None` /
TypeScript `undefined` or `null`, it keeps the configured cap, 1 GiB by
default; `0` means no limit; any other value is a `BadRequestError`): one
event over it ends the watch with `PayloadTooLargeError`, and with `0` events
are unbounded. A malformed event ends the watch with `BadRequestError`.
