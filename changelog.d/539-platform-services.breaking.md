**Platform services** — the client accessors are plural: `volume` → `volumes`
and `secret` → `secrets`, with the service classes `ImagesService`,
`SnapshotsService`, `VolumesService`, `SecretsService`, `ReservationsService`
and `MacModelsService` (Python `Async…` twins included). `volumes.create()`
takes `name` and `size_gib` / `sizeGib` (TypeScript `{ name, sizeGib }`) and
creates a disk volume; `volumes.get(name_or_id, create=…, id_only=…)` /
`get(nameOrId, { create, idOnly })`; `volumes.delete()` takes a name or id.
TypeScript `macModels.catalog(region)` becomes `catalog({ region })`.
`sandbox.snapshot_filesystem()` / `snapshotFilesystem()` is renamed
`capture_image()` / `captureImage()`. `ports.set_public()` / `setPublic()` is
removed: `ports.expose(port, public=…)` also changes an exposed port's
visibility. The client-level network-policy and sandbox-secret methods are
removed; use the sandbox's `get_network_policy()`, `update_network_policy()`,
`reset_network_policy()`, `get_secrets()` and `replace_secrets()` (camelCase in
TypeScript). Python `LifecyclePolicyConfig` is renamed `LifecyclePolicyOptions`.
