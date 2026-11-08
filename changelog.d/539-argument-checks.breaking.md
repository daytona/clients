**Wrong arguments are `BadRequestError` before any request** — in both SDKs:
`docker.wait_until_ready()` / `waitUntilReady()` checks its timeout's type;
the client lifecycle verbs refuse anything but a sandbox or a non-blank id;
`region` must be a string; `memory`, `disk` and their maximums must be
positive; `fork(name=...)` refuses an empty name; `secrets.set()` requires
`allowed_domains` / `allowedDomains` to be a list of strings; Python
`follow_build_logs()` refuses an invalid `timeout` (it raised `ValueError`).
`wait_seconds=None` / `waitSeconds: null` is refused rather than taken as the
default, while TypeScript reads `null` like Python's `None` where that has a
documented meaning (`createTimeoutSeconds`, `buildTimeoutSeconds`,
`idleExpirySeconds`, `region`). A name counts as blank by Python's whitespace
rules in both SDKs. TypeScript: `autoDelete` must be a boolean; an omitted
required options object (`reservations.create()` / `createBulk()` /
`update()`, `sandbox.volumes.pull()`, `updateLifecyclePolicy()`,
`followBuildLogs()`) is refused; inherited option keys are forwarded as they
are validated; and a name that needs hashing where there is no Web Crypto (a
browser page outside a secure context) is `UNSUPPORTED_OPERATION` (400,
source `SDK`) before any request.
