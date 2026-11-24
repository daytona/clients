**Lifecycle policy on created sandboxes** — the sandbox returned by `create()`,
`create_from_snapshot()` / `createFromSnapshot()`, `create_macos()` /
`createMacos()` and `create_from_build()` / `createFromBuild()` (and Go's
`Create`, `CreateFromSnapshot`, `CreateMacOS` and `CreateFromBuild`) carries
`lifecycle_policy` / `lifecyclePolicy` / `LifecyclePolicy`, including the
`timeout_at` / `timeoutAt` / `TimeoutAt` deadline, from the create response. Before, the field stayed empty
until `refresh_data()` / `refreshData()` or `get()`, so a sandbox with a
timeout looked like it had none. The deadline is the one the create set:
starting the sandbox restarts it, and `refresh_data()` / `refreshData()` reads
the new one.
