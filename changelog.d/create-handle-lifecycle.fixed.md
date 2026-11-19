**Lifecycle policy on created sandboxes** — the sandbox returned by `create()`,
`create_from_snapshot()` / `createFromSnapshot()`, `create_macos()` /
`createMacos()` and `create_from_build()` / `createFromBuild()` carries
`lifecycle_policy` / `lifecyclePolicy`, including the `timeout_at` /
`timeoutAt` deadline, from the create response. Before, the field stayed empty
until `refresh_data()` / `refreshData()` or `get()`, so a sandbox with a
timeout looked like it had none. The deadline is the one the create set:
starting the sandbox restarts it, and `refresh_data()` / `refreshData()` reads
the new one.
