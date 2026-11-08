**Platform services** — the client accessors are plural: `volume` → `volumes`
and `secret` → `secrets`, with the service classes `ImagesService`,
`SnapshotsService`, `VolumesService`, `SecretsService`, `ReservationsService`
and `MacModelsService` (Python `Async…` twins included). `volumes.create()`
takes `name` and `size_gib` / `sizeGib` (TypeScript `{ name, sizeGib }`, a
positive integer) and creates a disk volume; `volumes.get(name_or_id,
create=…, id_only=…)` / `get(nameOrId, { create, idOnly })`;
`volumes.delete()` takes a name or id. `images.build()` and `images.pull()`
take the same image options in both SDKs (`name`, `region`, `cpu_arch` /
`cpuArch`, `idle_expiry_seconds` / `idleExpirySeconds`); Python `pull()`
gains the last two and Python `build()` loses `timeout`: the build
submission is an ordinary request, bounded by the request timeout.
`images.get()` / `snapshots.get()` with `wait="ready"` and a timeout repeat
the server's long poll until the artifact settles or the whole budget is
spent; each hold is bounded by the request timeout. `secrets.set()` requires
`allowed_domains` / `allowedDomains` to be a list of strings.
`sandbox.snapshot_filesystem()` / `snapshotFilesystem()` is renamed
`capture_image()` / `captureImage()`. `ports.set_public()` / `setPublic()` is
removed: `ports.expose(port, public=…)` also changes an exposed port's
visibility. The client-level network-policy and sandbox-secret methods are
removed; use the sandbox's `get_network_policy()`, `update_network_policy()`,
`reset_network_policy()`, `get_secrets()` and `replace_secrets()` (camelCase in
TypeScript, where the `ifMatch` options are the exported `IfMatchOptions`).
`if_match` / `ifMatch` must be a non-empty string. A network-policy or
sandbox-secrets response without its `ETag` header is a `BadRequestError`
(400, `BAD_REQUEST`, source `SDK`). Python `LifecyclePolicyConfig` is renamed
`LifecyclePolicyOptions`, and `mac_models.list()` returns a tuple. A
sandbox-token mint answered with 404 now falls back to the API credential for
that request only; only 405 and 501 stop the handle from minting.
