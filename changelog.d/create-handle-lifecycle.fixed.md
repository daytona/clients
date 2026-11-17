**Lifecycle policy on created sandboxes** — the sandbox returned by `create()`,
`create_from_snapshot()` / `createFromSnapshot()`, `create_macos()` /
`createMacos()` and `create_from_build()` / `createFromBuild()` carries
`lifecycle_policy` / `lifecyclePolicy`, including the `timeout_at` /
`timeoutAt` deadline, from the create response. Before, the field stayed empty
until `refresh_data()` / `refreshData()` or `get()`, so a sandbox with a
timeout looked like it had none. On a sandbox that has not started yet, the
deadline moves when it starts; `refresh_data()` / `refreshData()` reads the
new one.
