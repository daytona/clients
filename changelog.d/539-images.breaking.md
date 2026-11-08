**Images: one set of options and bounded waits** — `images.build()` and
`images.pull()` take the same image options in both SDKs (`name`, `region`,
`cpu_arch` / `cpuArch`, `idle_expiry_seconds` / `idleExpirySeconds`); Python
`pull()` gains the last two and Python `build()` loses `timeout`: the build
submission is an ordinary request, bounded by the request timeout.
`images.build()` refuses anything but an `ImageBuilder` with `BadRequestError`
(Python raised `TypeError`). `images.get()` / `snapshots.get()` with
`wait="ready"` and a timeout repeat the server's long poll until the artifact
settles (`ready`, `failed` or `error`, returned for the caller to check); the
wait budget, not the request timeout, governs each hold (at most 60 s), and no
read runs past the wait's deadline. A budget that runs out first raises
`RequestTimeoutError` (408, `REQUEST_TIMEOUT`, source `SDK`), as
`images.pull()` and `sandbox.wait()` do, never an unsettled observation; a
budget above the timer maximum (about 24.8 days) waits without a deadline in
both SDKs. Without a timeout (omitted or `0`), `wait="ready"` makes one hold
of the server's default length and returns what the server reports. Their
`wait` must be `"ready"` or omitted, and their timeout is checked with or
without `wait` (Python ignored an invalid one without it). `images.pull()`
takes a boolean `wait` instead (Python `wait: bool = True`, TypeScript `wait?:
boolean`), waits for the image with the same bounded holds and pauses up to a
second between early non-terminal answers.
