**Model exports changed** — the generated wire aliases and a few helpers are
no longer exported; use the SDK's own models. Python `daytona.v2`:
`ImageListItem` and `ImageBuildResponse` → `Image` (what `images.list()`,
`images.get()` and `images.build()` return); `SnapshotDetail` and
`SandboxStateSnapshot` → `Snapshot`; `SandboxInfo` → the `Sandbox` handle's
properties, or `SandboxHandleJSON` from `to_json()`;
`ProjectApiKeyWithContext` → `ProjectApiKey`. TypeScript `@daytona/sdk/v2`:
`ImageDto` → `Image`; `SnapshotDetail` and `SandboxStateSnapshot` →
`Snapshot`; `SandboxInfo` and `SandboxDto` → the `Sandbox` handle's getters,
or `SandboxHandleJSON` from `toJSON()`; `SandboxLifecycleDto` →
`SandboxLifecycle`; `ArtifactClass` → `SnapshotClass` (a snapshot) or
`Image.sandboxClass` (a string); `ProjectApiKeyWithContext` → `ProjectApiKey`;
`PortListResponse` and `PortInUseResponse` → the values `ports.list()` (a
`ListResult<number>`) and `ports.isInUse()` (a boolean) return;
`WaitForStateOptions` → `LifecycleWaitOptions`; `VolumeRefOptions` →
`GetVolumeOptions` / `DeleteVolumeOptions`; `CpuTypeInfo` has no replacement
(no call returned it). Both SDKs: `NAME_LABEL` and `idempotency_key_for_name`
/ `idempotencyKeyForName` are removed with no replacement (named creates are
still get-or-create). Python now also exports `SandboxState`, `JsonValue`,
`CpuArch` and `ImageStatus`. Renamed service classes, list and search option
types and errors are in their own entries (platform services, list and search,
Python renames, `create*()` and `fork()`).
