**Bounded watch streams** — before `start` the SDK holds only events a
callback will receive (`fs` events, and `overflow` ones when `on_overflow` /
`onOverflow` is set) and delivers them after it, at most 4096 events; every
`fs` and `overflow` event counts toward that limit, handled by a callback or
not, and other frames are not delivered. Before `start` the stream may also
send at most 1 MiB, counted as the bytes arrive (every frame, comment-only
heartbeats included), so one event that never ends is refused too. Each event
is bounded by the client's `max_response_bytes` / `maxResponseBytes` (left
unset, Python `None` / TypeScript `undefined` or `null`, it keeps the
configured cap, 1 GiB by default; `0` means no limit; any other value is a
`BadRequestError`), before and after `start`. Before `start`, whichever bound
is passed first decides the error raised from `watch_dir()` / `watchDir()`:
more events or more than 1 MiB is `BadRequestError` (400, `BAD_REQUEST`,
source `SDK`), and one event over a client cap below the remaining budget is
`PayloadTooLargeError` (413, `PAYLOAD_TOO_LARGE`, source `SDK`). After
`start`, one event over the client cap ends the watch with
`PayloadTooLargeError`, and with `0` events are unbounded. A malformed event
ends the watch with `BadRequestError`.
