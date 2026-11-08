**Wrong arguments are `BadRequestError` before any request** — in both SDKs:
`docker.wait_until_ready()` / `waitUntilReady()` checks its timeout's type
(a Python integer too large for a float means no limit, as for the other
waits); the client lifecycle verbs and `get()` refuse anything but a sandbox
or a non-blank id; `reservations.update()` needs a boolean `auto_renew` /
`autoRenew`; `volumes.delete()` takes a non-empty name or id (not a `Volume`);
`ports.expose()` needs a boolean `public`; `reservations.create()` /
`create_bulk()` (`createBulk()`) check `region`, `model`, `auto_renew` /
`autoRenew` and the counts, so none is silently left out of the request; `create_access_token()` /
`createAccessToken()` operations must be strings; a port's `proto` is `tcp`
or `udp`; the image options of `images.build()` / `pull()`,
`capture_image()` and `create_from_build()` (checked before the build) must
have their types;
`region` must be a string; `memory`, `disk` and their maximums must be
positive; `fork(name=...)` refuses an empty name; `secrets.set()` requires
`allowed_domains` / `allowedDomains` to be a list of strings; Python
`follow_build_logs()` refuses an invalid `timeout` (it raised `ValueError`).
TypeScript `null` on every create and fork option does exactly what Python's
`None` does for that parameter: it is omitted (`name`, `labels`, `ports`,
`volumes`, `env`, `secrets`, `resources`, `spot`, `user`, `lifecycle_policy`
/ `lifecyclePolicy`, `region`, the timeouts, …), refused (`wait_seconds=None`
/ `waitSeconds: null`, rather than taken as the default), or, for the network
policy, sent as an explicit null. A name counts as blank by Python's whitespace
rules in both SDKs. TypeScript: `autoDelete` must be a boolean; an omitted
required options object (`reservations.create()` / `createBulk()` /
`update()`, `sandbox.volumes.pull()`, `updateLifecyclePolicy()`,
`followBuildLogs()`) is refused; inherited option keys are forwarded as they
are validated; and a name that needs hashing where there is no Web Crypto (a
browser page outside a secure context) is `UNSUPPORTED_OPERATION` (400,
source `SDK`) before any request.
