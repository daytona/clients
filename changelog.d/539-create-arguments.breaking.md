**Create and fork arguments are checked** — in both SDKs, before any request,
with `BadRequestError`: the client lifecycle verbs and `get()` refuse anything
but a sandbox or a non-blank id; `region` must be a string; `memory`, `disk`
and their maximums must be positive; a port's `proto` is `tcp` or `udp`; a
volume mount's `volume_id` / `volumeId` and `mount_path` / `mountPath` must
not be blank; `fork(name=...)` refuses an empty name. A name or id counts as
blank by Python's whitespace rules in both SDKs (TypeScript's `trim()` differs
on U+001C–U+001F, U+0085 and U+FEFF). TypeScript `null` on every create and
fork option does exactly what Python's `None` does for that parameter: it is
omitted (`name`, `labels`, `ports`, `volumes`, `env`, `secrets`, `resources`,
`spot`, `user`, `lifecycle_policy` / `lifecyclePolicy`, `region`, the
timeouts, …), refused (`wait_seconds=None` / `waitSeconds: null`, which
TypeScript took as the default), or, for the network policy, sent as an
explicit null. The same holds for the fields of nested create options: a
`null` resource size or core count, or a volume mount's `subpath` /
`readOnly`, is omitted, as Python's `None` is, while a `null` port or
protocol, mount id or path is refused. TypeScript: `autoDelete` must be a
boolean; inherited option keys are forwarded as they are validated; and a name
that needs hashing where there is no Web Crypto (a browser page outside a
secure context) is `UNSUPPORTED_OPERATION` (400, source `SDK`).
