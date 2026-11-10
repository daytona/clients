**TypeScript: `undefined` for an absent value, never `null`** — TypeScript
models use an optional `undefined` for an absent value: `Image.name` and
`diskMib`, `Sandbox.image` / `snapshot` / `macBootMode`, `ImageRef.name` /
`SnapshotRef.name` (omitted from `toJSON()` when unknown, as from Python's
`to_json()`), `NetworkPolicy.allowDomains`, `DockerStatus.processId` /
`reason`, `PullVolumeResult.manifestId` and `git.getConfig()` for a key that
is not set.
