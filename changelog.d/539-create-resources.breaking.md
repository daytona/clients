**Create options** — sandbox sizing is one `resources` entry:
`Resources(cpu=, cpu_max=, memory=, memory_max=, disk=, disk_max=)` on Linux
creates and `MacosResources(cpu=, memory=, disk=)` on `create_macos` (TypeScript
`{ resources: { … } }`); the flat `cpu` / `memory` / `disk` arguments are
removed. Memory and disk are GiB that must convert to a whole number of MiB,
else `BadRequestError`, and sandbox sizes read back exactly (MiB / 1024, no
rounding). `create_from_snapshot()` / `createFromSnapshot()` takes no
resources, CPU selectors, environment, volumes, secrets or network policy: the
snapshot carries them. `create_timeout_seconds` / `createTimeoutSeconds` must
be an integer of at least `-1`, `labels` and `env` must map strings to
strings, and an invalid image or snapshot source is a `BadRequestError` (it
was a `TypeError`). TypeScript: `envVars` is removed (use `env`) and every
acquisition route (`create*()`, `get()`, `fork()`) defaults `autoDelete` to
`false`. Python: `create()` gains `user` and loses `toolbox_url`; build-log
callbacks are typed `BuildLogsCallback` / `BuildLogEventCallback`.
