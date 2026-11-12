**TypeScript: wait budgets in seconds** — on the lifecycle methods,
`images.pull()`, build-log following, `docker.waitUntilReady()` and
`sandbox.volumes.pull()`, `{ timeout }` in milliseconds becomes `{
timeoutSeconds }` in seconds (divide old values by 1000); on `fork()`, `{
timeout }` in milliseconds becomes `{ waitSeconds }` in seconds (divide by
1000 too); on `wait()`, `images.get()` and `snapshots.get()`, which already
took seconds, only the key is renamed to `timeoutSeconds` (keep the values).
