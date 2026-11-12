**Create options** — sandbox sizing is one `resources` entry: `Resources(cpu=,
cpu_max=, memory=, memory_max=, disk=, disk_max=)` on Linux creates and
`MacosResources(cpu=, memory=, disk=)` on `create_macos` (TypeScript `{
resources: { … } }`); the flat `cpu` / `memory` / `disk` arguments are
removed. Memory and disk are GiB that must convert to a whole number of MiB,
else `BadRequestError`, and sandbox sizes read back exactly (MiB / 1024, no
rounding). `create_from_snapshot()` / `createFromSnapshot()` takes no
resources, CPU selectors, environment, volumes, secrets or network policy: the
snapshot carries them. Every create option value is checked before any
request, and `create_from_build()` / `createFromBuild()` checks them before
the build, so a caller error costs no build: `create_timeout_seconds` /
`createTimeoutSeconds` must be an integer from `-1` to 2^53 - 1; memory and
disk sizes and their maximums must convert to at most the contract's int32
MiB, and core counts must be finite numbers (a Python integer too large for a
float is refused); `labels` and `env` must map strings to strings; `secrets`
must be a list of names, `ports` and `volumes` lists of `PortMapping` /
`VolumeMount` entries; `spot`, `user`, `cpu_arch` / `cpuArch` and the resource
core counts must have their types. A mistyped value is a `BadRequestError`,
where Python used to drop it silently and TypeScript to forward it, and an
invalid image or snapshot source is a `BadRequestError` (it was a
`TypeError`). Python `PortMapping` and `VolumeMount` are keyword-only
(`VolumeMount(volume_id=..., mount_path=...)`). TypeScript: `envVars` is
removed (use `env`) and every acquisition route (`create*()`, `get()`,
`fork()`) defaults `autoDelete` to `false`. Python: `create()` gains `user`
and loses `toolbox_url`; build-log callbacks are typed `BuildLogsCallback` /
`BuildLogEventCallback`.
