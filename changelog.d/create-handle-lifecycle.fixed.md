**The sandbox `create()` returns carries its lifecycle policy** — `create()`,
`create_from_snapshot()` / `createFromSnapshot()` and `create_macos()` /
`createMacos()` fill `lifecycle_policy` / `lifecyclePolicy`, including the
`timeout_at` / `timeoutAt` deadline, from the create response. Before, the
field stayed empty until `refresh_data()` / `refreshData()` or `get()`, so a
sandbox with a timeout looked like it had none.
