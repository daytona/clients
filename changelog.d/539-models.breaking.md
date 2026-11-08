**SDK-owned, read-only models** — public models no longer expose generated
wire types. Python models are frozen, keyword-only dataclasses with tuples and
timezone-aware `datetime` values; TypeScript models are readonly camelCase
interfaces (for example `Image.sandboxClass` / `layerCount`,
`Secret.allowedDomains`, `ProjectBudget.monthlyLimitCents`, and
`ProjectApiKeyWithContext` → `ProjectApiKey`). Removed exports include
`SandboxInfo`, `SandboxStateSnapshot`, `SnapshotDetail`, `ImageListItem`,
`ImageBuildResponse` and the TypeScript DTO aliases. Sandbox metadata is
read-only on the handle (update it through methods such as `set_labels()`,
reload with `refresh_data()`, which takes no arguments); `target` and
`error_reason` / `errorReason` are removed (use `region` and `error`). A
sandbox or snapshot class the SDK does not know reads as `"unknown"`, with
the API's value in `raw_sandbox_class` / `rawSandboxClass`. `to_json()` /
`toJSON()` return the same camelCase `SandboxHandleJSON` in both SDKs, with no
URL or credential; reconnect with `get(json["sandboxId"])`. Constructors of
the exported handle, model and service classes are internal, `NAME_LABEL` and
`idempotency_key_for_name` / `idempotencyKeyForName` are no longer exported
(named creates are still get-or-create), and Python
`ImageBuilder.render_dockerfile()` is renamed `to_dockerfile()`.
