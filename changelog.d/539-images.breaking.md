**Images: one set of options and bounded waits** — `images.build()` and
`images.pull()` take the same image options in both SDKs (`name`, `region`,
`cpu_arch` / `cpuArch`, `idle_expiry_seconds` / `idleExpirySeconds`); Python
`pull()` gains the last two and Python `build()` loses `timeout`: the build
submission is an ordinary request, bounded by the request timeout.
`images.build()` refuses anything but an `ImageBuilder` with
`BadRequestError` (Python raised `TypeError`). `images.get()` /
`snapshots.get()` with `wait="ready"` and a timeout repeat the server's long
poll until the artifact settles or the whole budget is spent; the wait
budget, not the request timeout, governs each hold (at most 60 s), and a
first hold still unanswered at the deadline is `RequestTimeoutError` (source
`SDK`). `images.pull()` polls the same way and pauses up to a second between
early non-terminal answers. Their `wait` must be `"ready"` or omitted.
