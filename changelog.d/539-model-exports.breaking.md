**Model exports changed** — removed exports include `SandboxInfo`,
`SandboxStateSnapshot`, `SnapshotDetail`, `ImageListItem`,
`ImageBuildResponse` and the TypeScript DTO aliases; Python now exports
`SandboxState`, `JsonValue`, `CpuArch` and `ImageStatus`. `NAME_LABEL` and
`idempotency_key_for_name` / `idempotencyKeyForName` are no longer exported
(named creates are still get-or-create).
