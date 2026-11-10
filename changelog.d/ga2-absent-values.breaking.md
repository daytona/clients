**One spelling for an absent value** — TypeScript models use an optional
`undefined` for an absent value, never `null`: `Image.name` and `diskMib`,
`Sandbox.image` / `snapshot` / `macBootMode`, `ImageRef.name` /
`SnapshotRef.name` (omitted from `toJSON()` when unknown, in Python's
`to_json()` too), `NetworkPolicy.allowDomains`, `DockerStatus.processId` /
`reason`, `PullVolumeResult.manifestId` and `git.getConfig()` for a key that is
not set; `Sandbox.state` is typed `SandboxState` (always set, `'unknown'`
before a read). Python's `Sandbox.raw_state` is `None` instead of `""` before
a status is known, matching TypeScript's `rawState`.
